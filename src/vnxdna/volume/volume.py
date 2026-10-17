"""A disk-like volume on top of the codec (V13, docs/V13_ENGINE_ARCHITECTURE.md §5): ``write``/``append``/``seek``/
``read``/``read_at``/``flush``, a hot-extent cache, a write-back buffer, a write-ahead journal and ``scrub``.

A volume is a directory::

    volume.json                  vnx.volume/1: extent size, codec options, software version
    journal.log                  vnx.volume-journal/1: one CRC-32-framed record per committed extent version
    extents/e<id>-v<ver>.fasta   the strands of extent <id>, version <ver>: an ordinary VNX archive of its bytes

The logical byte space is split into extents of ``extent_bytes``; every extent except the last holds exactly that many
bytes. A write dirties the extents it touches (read-modify-write of the current content); ``flush`` encodes every
dirty extent as a new version, publishes its strand file atomically, syncs the directory and only then journals the
commit record (extent id, version, length, SHA-256 of the bytes, archive ID, SHA-256 of the strand file). Opening a
volume replays the journal up to its last verified record and, in read-write mode, removes extent files no committed
record names (the leftovers of an interrupted flush); the state is always the last committed one. ``read_at`` serves
dirty extents from the write-back buffer, hot ones from a byte-bounded LRU cache, and decodes the others from their
strand files (an ordinary decode: container SHA-256 verified by the decoder, extent SHA-256 checked against the
journal) before caching them. ``scrub`` re-verifies every committed extent and reports; it never repairs.

The strands stay files on disk: the "disk" vocabulary here describes a software codec, not physical DNA. A strand
file may be replaced by a SIMULATED (or, later, real) read pool of the same extent; the decoder reads both. One writer
at a time (an exclusive lock file); readers may open a volume that another process writes, and see its last commit.
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path

from vnxdna._version import __version__
from vnxdna.archive.operations import ArchiveOptions
from vnxdna.core.errors import VNXConfigurationError, VNXFormatError, VNXIntegrityError, VNXOutputError
from vnxdna.core.fsio import fsync_dir
from vnxdna.core.util import atomic_output, canonical_json, parse_canonical_json
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.pipeline.stream import MAX_EXTENT, MIN_EXTENT, decode_extent, encode_extent
from vnxdna.recovery.options import DecodeOptions
from vnxdna.volume.journal import Journal

VOLUME_FORMAT = "vnx.volume/1"
_EXTENT_FILE = re.compile(r"^e(\d+)-v(\d+)\.(fasta|fastq)$")


@dataclass
class VolumeOptions:
    extent_bytes: int = 1 << 20
    cache_bytes: int = 64 << 20          # decoded hot extents kept in memory (LRU, bounded in bytes)
    write_back_bytes: int = 8 << 20      # dirty bytes held before an automatic flush (0 = flush on every write)
    workers: int = 1                     # decode workers and engine threads for extent reads
    dna: DNAOptions = field(default_factory=DNAOptions)
    archive: ArchiveOptions = field(default_factory=lambda: ArchiveOptions(archive_id="content"))
    decode: DecodeOptions = field(default_factory=DecodeOptions)

    def validate(self) -> None:
        for name, v, lo in (("extent_bytes", self.extent_bytes, MIN_EXTENT), ("cache_bytes", self.cache_bytes, 0),
                            ("write_back_bytes", self.write_back_bytes, 0)):
            if not isinstance(v, int) or isinstance(v, bool) or v < lo:
                raise VNXConfigurationError(f"{name} must be an integer >= {lo}")
        if self.extent_bytes > MAX_EXTENT:
            raise VNXConfigurationError(f"extent_bytes must be <= {MAX_EXTENT}")
        if not 1 <= self.workers <= 256:
            raise VNXConfigurationError("workers must be in 1..256")
        self.dna.resolve()
        self.archive.validate()
        self.decode.validate()


class Volume:
    """Open with :meth:`create` or :meth:`open`; use as a context manager (``close`` flushes)."""

    def __init__(self, path: Path, mode: str, options: VolumeOptions, meta: dict, journal: Journal, lock_fd: int | None,
                 key: bytes | None, passphrase: str | None):
        self.path, self.mode, self.options, self.meta, self.journal = path, mode, options, meta, journal
        self._lock_fd = lock_fd
        self._key, self._passphrase = key, passphrase
        self.extent_bytes = int(meta["extent_bytes"])
        self.committed: dict[int, dict] = {}         # extent id -> its last committed record
        self.versions = 0
        for rec in journal.records:
            if rec.get("op") == "commit":
                self.committed[int(rec["extent"])] = rec
                self.versions += 1
        self._dirty: dict[int, bytearray] = {}
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._cache_bytes = 0
        self.pos = 0
        self.closed = False
        self.metrics = {"cache_hits": 0, "cache_misses": 0, "cache_evictions": 0, "bytes_written": 0, "bytes_read": 0,
                        "extents_encoded": 0, "extents_decoded": 0, "bytes_encoded": 0, "bytes_decoded": 0,
                        "encode_seconds": 0.0, "decode_seconds": 0.0, "flushes": 0, "orphans_removed": 0}
        self._work = Path(tempfile.mkdtemp(prefix="vnx-volume-", dir=None))

    # ------------------------------------------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, path: str | os.PathLike, options: VolumeOptions | None = None, *, overwrite: bool = False) -> "Volume":
        opt = options or VolumeOptions()
        opt.validate()
        root = Path(path)
        if root.exists():
            if not overwrite:
                raise VNXOutputError(f"volume directory already exists: {root} (use --force to replace it)")
            shutil.rmtree(root)
        root.mkdir(parents=True)
        (root / "extents").mkdir()
        meta = {"format": VOLUME_FORMAT, "extent_bytes": opt.extent_bytes, "software": __version__,
                "dna": {"profile": opt.dna.profile, "outer_code": opt.dna.outer_code,
                        "data_symbols": opt.dna.data_symbols, "parity_symbols": opt.dna.parity_symbols},
                "encrypted": opt.archive.key is not None or opt.archive.passphrase is not None,
                "created": int(time.time())}
        with atomic_output(root / "volume.json", overwrite=True) as tmp:
            tmp.write_bytes(canonical_json(meta) + b"\n")
        Journal(root / "journal.log").create()
        fsync_dir(root)
        return cls.open(root, "rw", options=opt, key=opt.archive.key, passphrase=opt.archive.passphrase)

    @classmethod
    def open(cls, path: str | os.PathLike, mode: str = "r", *, options: VolumeOptions | None = None, key: bytes | None = None,
             passphrase: str | None = None) -> "Volume":
        if mode not in ("r", "rw"):
            raise VNXConfigurationError("mode must be 'r' or 'rw'")
        root = Path(path)
        try:
            meta = parse_canonical_json((root / "volume.json").read_bytes().rstrip(b"\n"), "volume.json")
        except (OSError, ValueError) as error:
            raise VNXFormatError(f"{root} is not a VNX volume: {error}", stage="volume") from None
        if meta.get("format") != VOLUME_FORMAT or not isinstance(meta.get("extent_bytes"), int):
            raise VNXFormatError(f"{root} is not a {VOLUME_FORMAT} volume", stage="volume")
        # the extent size is the volume's (volume.json); the options bring cache/write-back sizes, workers and codec options
        opt = replace(options or VolumeOptions(), extent_bytes=int(meta["extent_bytes"]))
        if key is None and passphrase is None:
            key, passphrase = opt.archive.key, opt.archive.passphrase       # a key given through the options
        if meta.get("encrypted") and key is None and passphrase is None:
            raise VNXConfigurationError("this volume is encrypted; pass the key or passphrase")
        if key is not None or passphrase is not None:
            opt = replace(opt, archive=replace(opt.archive, key=key, passphrase=passphrase, archive_id="options"))
        opt.validate()
        lock_fd = None
        if mode == "rw":
            lock_fd = os.open(root / "volume.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(lock_fd)
                raise VNXOutputError(f"volume {root} is open for writing by another process", stage="volume") from None
        journal = Journal(root / "journal.log")
        try:
            journal.replay()
        except VNXFormatError:
            if lock_fd is not None:
                os.close(lock_fd)
            raise
        vol = cls(root, mode, opt, meta, journal, lock_fd, key, passphrase)
        if mode == "rw":
            vol._remove_orphans()
        return vol

    def _remove_orphans(self) -> None:
        """Extent files no committed record names: leftovers of a flush interrupted before its journal record."""
        named = {rec["file"] for rec in self.journal.records if rec.get("op") == "commit"}
        for p in sorted((self.path / "extents").iterdir()):
            if p.name in named:
                continue
            if _EXTENT_FILE.match(p.name) or p.name.endswith(".partial") or p.name.startswith("."):
                p.unlink()
                self.metrics["orphans_removed"] += 1

    def close(self) -> None:
        if self.closed:
            return
        try:
            if self.mode == "rw" and self._dirty:
                self.flush()
        finally:
            self.closed = True
            if self._lock_fd is not None:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
                self._lock_fd = None
            shutil.rmtree(self._work, ignore_errors=True)

    def __enter__(self) -> "Volume":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None and self.mode == "rw":
            self._dirty.clear()                 # a failed operation never commits half-written state
        self.close()

    # ------------------------------------------------------------------------------------------------ geometry
    @property
    def size(self) -> int:
        """Logical size: the end of the last extent, committed or dirty."""
        end = 0
        for i, rec in self.committed.items():
            end = max(end, i * self.extent_bytes + int(rec["length"]))
        for i, buf in self._dirty.items():
            end = max(end, i * self.extent_bytes + len(buf))
        return end

    def _check_open(self, writing: bool = False) -> None:
        if self.closed:
            raise VNXConfigurationError("the volume is closed")
        if writing and self.mode != "rw":
            raise VNXConfigurationError("the volume is open read-only")

    # ------------------------------------------------------------------------------------------------ extents
    def _load(self, i: int) -> bytes:
        """Current content of extent ``i`` (dirty buffer, cache, or a verified decode); empty bytes past the end."""
        if i in self._dirty:
            return bytes(self._dirty[i])
        if i in self._cache:
            self._cache.move_to_end(i)
            self.metrics["cache_hits"] += 1
            return self._cache[i]
        rec = self.committed.get(i)
        if rec is None:
            return b""
        self.metrics["cache_misses"] += 1
        t = time.perf_counter()
        opt = replace(self.options.decode, workers=self.options.workers)
        data = decode_extent(self.path / "extents" / rec["file"], rec, opt, key=self._key, passphrase=self._passphrase,
                             work=self._work)
        self.metrics["decode_seconds"] += time.perf_counter() - t
        self.metrics["extents_decoded"] += 1
        self.metrics["bytes_decoded"] += len(data)
        self._cache_put(i, data)
        return data

    def _cache_put(self, i: int, data: bytes) -> None:
        if self.options.cache_bytes <= 0:
            return
        if i in self._cache:
            self._cache_bytes -= len(self._cache.pop(i))
        while self._cache and self._cache_bytes + len(data) > self.options.cache_bytes:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= len(old)
            self.metrics["cache_evictions"] += 1
        if len(data) <= self.options.cache_bytes:
            self._cache[i] = data
            self._cache_bytes += len(data)

    def _cache_drop(self, i: int) -> None:
        old = self._cache.pop(i, None)
        if old is not None:
            self._cache_bytes -= len(old)

    def _dirty_bytes(self) -> int:
        return sum(len(b) for b in self._dirty.values())

    # ------------------------------------------------------------------------------------------------ positioning
    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        self._check_open()
        base = {os.SEEK_SET: 0, os.SEEK_CUR: self.pos, os.SEEK_END: self.size}.get(whence)
        if base is None:
            raise VNXConfigurationError("whence must be SEEK_SET, SEEK_CUR or SEEK_END")
        new = base + int(offset)
        if new < 0:
            raise VNXConfigurationError("negative seek position")
        self.pos = new
        return new

    def tell(self) -> int:
        return self.pos

    # ------------------------------------------------------------------------------------------------ reads
    def read_at(self, offset: int, length: int) -> bytes:
        """Bytes ``[offset, offset + length)`` of the volume (shorter at the end), touching only the extents they lie
        in; uncommitted writes are visible to their writer."""
        self._check_open()
        if offset < 0 or length < 0:
            raise VNXConfigurationError("offset and length must be non-negative")
        end = min(offset + length, self.size)
        if end <= offset:
            return b""
        out = bytearray()
        e = self.extent_bytes
        i = offset // e
        while i * e < end:
            data = self._load(i)
            a = max(offset, i * e) - i * e
            b = min(end, (i + 1) * e) - i * e
            out += data[a:b]
            i += 1
        self.metrics["bytes_read"] += len(out)
        return bytes(out)

    def read(self, length: int = -1) -> bytes:
        """Read from the cursor (``length`` < 0 = to the end) and advance it."""
        if length < 0:
            length = max(0, self.size - self.pos)
        data = self.read_at(self.pos, length)
        self.pos += len(data)
        return data

    # ------------------------------------------------------------------------------------------------ writes
    def write(self, data: bytes) -> int:
        """Write at the cursor (a gap beyond the end reads as zeros) and advance it; dirty extents are held in the
        write-back buffer and flushed when it exceeds ``write_back_bytes``."""
        self._check_open(writing=True)
        data = bytes(data)
        if not data:
            return 0
        e = self.extent_bytes
        pos = self.pos
        size = self.size
        if pos > size:                                      # zero-fill the gap, extent by extent
            self._fill_zeros(size, pos)
        i = pos // e
        done = 0
        while done < len(data):
            a = (pos + done) - i * e
            take = min(e - a, len(data) - done)
            cur = self._dirty.get(i)
            if cur is None:
                cur = bytearray(self._load(i))
                self._dirty[i] = cur
            if len(cur) < a:
                cur += bytes(a - len(cur))
            cur[a:a + take] = data[done:done + take]
            self._cache_drop(i)
            done += take
            i += 1
        self.pos = pos + len(data)
        self.metrics["bytes_written"] += len(data)
        if self._dirty_bytes() > self.options.write_back_bytes:
            self.flush()
        return len(data)

    def _fill_zeros(self, start: int, end: int) -> None:
        e = self.extent_bytes
        i = start // e
        while i * e < end:
            a = max(start, i * e) - i * e
            b = min(end, (i + 1) * e) - i * e
            cur = self._dirty.get(i)
            if cur is None:
                cur = bytearray(self._load(i))
                self._dirty[i] = cur
            if len(cur) < b:
                cur += bytes(b - len(cur))
            cur[a:b] = bytes(b - a)
            self._cache_drop(i)
            i += 1

    def append(self, data: bytes) -> int:
        self._check_open(writing=True)
        self.pos = self.size
        return self.write(data)

    def flush(self) -> dict:
        """Encode every dirty extent as a new committed version (crash-consistent: strand file published atomically
        and synced before its journal record is appended and synced). Returns what was committed."""
        self._check_open(writing=True)
        committed = []
        t0 = time.perf_counter()
        for i in sorted(self._dirty):
            buf = self._dirty[i]
            rec = self._commit_extent(i, bytes(buf))
            committed.append({"extent": i, "version": rec["version"], "length": rec["length"], "strands": rec["strands"]})
            self._cache_put(i, bytes(buf))
            del self._dirty[i]
        self.metrics["flushes"] += 1
        return {"committed": committed, "seconds": round(time.perf_counter() - t0, 4)}

    def _commit_extent(self, i: int, data: bytes) -> dict:
        prev = self.committed.get(i)
        version = int(prev["version"]) + 1 if prev else 1
        name = f"e{i}-v{version}.fasta"
        target = self.path / "extents" / name
        src = self._work / f"x{i}.bin"
        src.write_bytes(data)
        t = time.perf_counter()
        try:
            info = encode_extent(src, target, self.options.dna, self.options.archive, work=self._work)
        finally:
            src.unlink(missing_ok=True)
        fsync_dir(target.parent)                                            # the file is durable before its record
        self.metrics["encode_seconds"] += time.perf_counter() - t
        self.metrics["extents_encoded"] += 1
        self.metrics["bytes_encoded"] += len(data)
        rec = self.journal.append({"op": "commit", "extent": i, "version": version, "file": name, "length": info["length"],
                                   "sha256": info["sha256"], "archive_id": info["archive_id"], "archive_tag": info["archive_tag"],
                                   "strands": info["strands"], "bases": info["bases"], "strands_sha256": info["strands_sha256"],
                                   "time": int(time.time())})
        self.committed[i] = rec
        self.versions += 1
        return rec

    # ------------------------------------------------------------------------------------------------ status
    def extents(self) -> list[dict]:
        return [dict(self.committed[i]) for i in sorted(self.committed)]

    def stat(self) -> dict:
        self._check_open()
        m = self.metrics
        enc_b = sum(int(r["length"]) for r in self.committed.values())
        return {"format": VOLUME_FORMAT, "path": str(self.path), "mode": self.mode, "extent_bytes": self.extent_bytes,
                "size": self.size, "extents": len(self.committed), "versions": self.versions,
                "dirty": {"extents": len(self._dirty), "bytes": self._dirty_bytes()},
                "cache": {"entries": len(self._cache), "bytes": self._cache_bytes, "limit": self.options.cache_bytes,
                          "hits": m["cache_hits"], "misses": m["cache_misses"], "evictions": m["cache_evictions"]},
                "journal": {"records": len(self.journal.records), "bad_tail_bytes": self.journal.bad_tail_bytes,
                            "orphans_removed": m["orphans_removed"]},
                "io": {"bytes_written": m["bytes_written"], "bytes_read": m["bytes_read"], "flushes": m["flushes"],
                       "extents_encoded": m["extents_encoded"], "extents_decoded": m["extents_decoded"],
                       "encode_seconds": round(m["encode_seconds"], 4), "decode_seconds": round(m["decode_seconds"], 4),
                       "bytes_encoded": m["bytes_encoded"], "bytes_decoded": m["bytes_decoded"],
                       "encode_mb_s": round(m["bytes_encoded"] / m["encode_seconds"] / 1e6, 3) if m["encode_seconds"] else None,
                       "decode_mb_s": round(m["bytes_decoded"] / m["decode_seconds"] / 1e6, 3) if m["decode_seconds"] else None},
                "committed_bytes": enc_b, "encrypted": bool(self.meta.get("encrypted")), "software": self.meta.get("software")}

    def scrub(self) -> dict:
        """Re-verify every committed extent: the strand file hashes as journaled, decodes, and the bytes hash as
        journaled. Reports per extent; repairs nothing."""
        self._check_open()
        from vnxdna.core.util import sha256_file
        results = []
        for i in sorted(self.committed):
            rec = self.committed[i]
            p = self.path / "extents" / rec["file"]
            row = {"extent": i, "version": rec["version"], "file": rec["file"], "strands_file": None, "decoded": False,
                   "error": None}
            if not p.is_file():
                row["strands_file"] = "missing"
                row["error"] = "strand file missing"
                results.append(row)
                continue
            row["strands_file"] = "intact" if sha256_file(p) == rec["strands_sha256"] else "changed"
            try:
                opt = replace(self.options.decode, workers=self.options.workers)
                decode_extent(p, rec, opt, key=self._key, passphrase=self._passphrase, work=self._work)
                row["decoded"] = True
            except (VNXIntegrityError, VNXFormatError, VNXConfigurationError) as error:
                row["error"] = f"{type(error).__name__}: {error}"[:300]
            except Exception as error:  # noqa: BLE001 - a scrub reports every failure, never raises mid-way
                row["error"] = f"{type(error).__name__}: {error}"[:300]
            results.append(row)
        named = {rec["file"] for rec in self.committed.values()}
        orphans = sorted(p.name for p in (self.path / "extents").iterdir() if p.name not in named)
        ok = all(r["decoded"] and r["error"] is None for r in results)
        return {"status": "VERIFIED" if ok else "DAMAGED", "extents": len(results), "verified": sum(r["decoded"] for r in results),
                "failed": [r["extent"] for r in results if not r["decoded"]],
                "changed_files": [r["extent"] for r in results if r["strands_file"] == "changed"], "orphans": orphans,
                "results": results}


__all__ = ["VOLUME_FORMAT", "VolumeOptions", "Volume"]
