"""Streaming encode and decode (V13, docs/V13_ENGINE_ARCHITECTURE.md §4): a byte stream of any length becomes DNA strands
with bounded memory, and comes back the same way.

The stream is cut into *extents* of ``extent_bytes``. Every extent is packed into an ordinary VNX archive (one file,
``EXTENT_NAME``; unchanged container format) and encoded to strands by the unchanged encoder, so a streamed pool is a
pool of ordinary archives, one per extent, each with its own archive ID and tag. A manifest (``vnx.stream/1``) records,
per extent, its length, SHA-256, archive ID and the byte range of its strands in the sink. Decoding reads the manifest
and rebuilds the stream extent by extent: by strand byte range while the sink is the file the encoder wrote (each
range is one archive's strands), and from the whole pool by archive ID otherwise (a sequenced, shuffled pool), one
pass per extent. Every extent is verified (container SHA-256 by the decoder, extent SHA-256 against the manifest)
before a byte of it is written; the whole-stream SHA-256 is checked at the end. Extent archive IDs are content-derived
(content-v1; random for encrypted extents), so distinct extents get distinct IDs; the 16-bit archive *tag* in the
strands is the ID's first two bytes, so a pool decode (sequenced pool, by archive ID) needs the extents' tags to be
distinct and reports ``ARCHIVE_TAG_AMBIGUOUS`` otherwise (``decode_stream`` checks the manifest's tags first). The
by-range path and :mod:`vnxdna.volume` (one strand file per extent) do not depend on tags.

Memory: at most ``2 x workers`` extents are in flight (bytes on disk in a work directory, strands in the workers), so
the peak resident memory does not grow with the stream. Workers are processes (one extent per task); each encodes its
extent with one process.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
import tempfile
import time
from collections import deque
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from vnxdna.archive import container as ct
from vnxdna.archive import operations as ar
from vnxdna.core.errors import VNXConfigurationError, VNXFormatError, VNXIntegrityError
from vnxdna.core.util import atomic_output, canonical_json, parse_canonical_json, sha256_file
from vnxdna.pipeline.encode import DNAOptions, encode_container
from vnxdna.recovery.options import DecodeOptions

STREAM_FORMAT = "vnx.stream/1"
EXTENT_NAME = "extent"                 #: the one file of every extent archive
MIN_EXTENT, MAX_EXTENT = 4096, 1 << 30


@dataclass
class StreamOptions:
    extent_bytes: int = 1 << 20
    workers: int = 1                    # extents encoded concurrently (processes); window = 2 x workers extents
    dna: DNAOptions = field(default_factory=DNAOptions)
    archive: ar.ArchiveOptions = field(default_factory=lambda: ar.ArchiveOptions(archive_id="content"))

    def validate(self) -> None:
        if (not isinstance(self.extent_bytes, int) or isinstance(self.extent_bytes, bool)
                or not MIN_EXTENT <= self.extent_bytes <= MAX_EXTENT):
            raise VNXConfigurationError(f"extent_bytes must be in [{MIN_EXTENT}, {MAX_EXTENT}]")
        if not 1 <= self.workers <= 256:
            raise VNXConfigurationError("workers must be in 1..256")
        self.dna.resolve()
        self.archive.validate()


# ============================================================================ one extent (shared with vnxdna.volume)
def encode_extent(data_path: str | os.PathLike, strands: str | os.PathLike, dna: DNAOptions, archive: ar.ArchiveOptions,
                  *, work: str | os.PathLike | None = None) -> dict:
    """Pack the bytes of ``data_path`` into an extent archive and encode it to ``strands`` (FASTA/FASTQ by suffix).
    Returns the extent record: length, sha256, archive_id, archive_tag, strands, bases, strands_sha256."""
    from dataclasses import replace
    data_path = Path(data_path)
    if archive.key is None and archive.passphrase is None and archive.archive_id != "content":
        # options-v1 IDs depend on paths, sizes and options only: every full extent would get the same archive ID and
        # tag. Unencrypted extents therefore use content-v1 IDs (encrypted archives always get a random ID).
        archive = replace(archive, archive_id="content")
    tmp = Path(tempfile.mkdtemp(prefix="vnx-extent-", dir=work))
    try:
        src = tmp / EXTENT_NAME                 # the archive entry is named after the file: always EXTENT_NAME
        if _same_fs(data_path, tmp):
            os.link(data_path, src)
        else:
            shutil.copyfile(data_path, src)
        archive_path = tmp / "a.vnx"
        rep = ar.build_archive([src], archive_path, archive, overwrite=True)
        enc = encode_container(archive_path, strands, dna, overwrite=True)
        return {"length": data_path.stat().st_size, "sha256": sha256_file(data_path), "archive_id": enc["archive_id"],
                "archive_tag": enc["archive_tag"], "strands": enc["strands"], "bases": enc["bases"],
                "strands_sha256": enc["file_sha256"], "container_sha256": rep.container_sha256}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _verify_extent_container(out: str | os.PathLike, rec: dict, *, key: bytes | None = None,
                             passphrase: str | None = None) -> bytes:
    """Open a decoded extent container, extract its one file, and return the bytes verified against the manifest record
    (length and SHA-256). Shared by the per-extent and the linear pool decode paths. Fails closed."""
    c = ct.open_container(out, key=key, passphrase=passphrase)
    f = c.file(EXTENT_NAME)
    data = b"".join(ar.iter_file(c, f))
    if len(data) != rec["length"] or hashlib.sha256(data).hexdigest() != rec["sha256"]:
        raise VNXIntegrityError(f"extent {rec.get('index', '?')}: decoded bytes do not match the manifest",
                                stage="stream", details={"extent": rec.get("index")})
    return data


def decode_extent(strands: str | os.PathLike, rec: dict, options: DecodeOptions, *, key: bytes | None = None,
                  passphrase: str | None = None, work: str | os.PathLike | None = None) -> bytes:
    """Decode the extent ``rec`` (manifest record) from ``strands`` (its own strand file, or a pool holding several
    archives) and return its bytes, verified against the record's SHA-256. Fails closed."""
    from dataclasses import replace
    from vnxdna.pipeline.decode import decode_reads
    tmp = Path(tempfile.mkdtemp(prefix="vnx-extent-", dir=work))
    try:
        out = tmp / "e.vnx"
        opt = replace(options, expect_archive_id=rec["archive_id"], archive_tag=int(rec["archive_tag"], 16))
        res = decode_reads(strands, out, opt, overwrite=True, key=key, passphrase=passphrase, workdir=tmp)
        if res.status != "SUCCESS":
            raise VNXIntegrityError(f"extent {rec.get('index', '?')} did not decode: status {res.status}",
                                    stage="stream", details={"extent": rec.get("index"), "status": res.status})
        return _verify_extent_container(out, rec, key=key, passphrase=passphrase)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _same_fs(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


# ============================================================================ source / sink helpers
def _open_source(source) -> tuple[BinaryIO | Iterator[bytes], bool]:
    """(binary stream or iterator of bytes, whether to close it)."""
    if source == "-":
        return sys.stdin.buffer, False
    if isinstance(source, (str, os.PathLike)):
        return open(source, "rb"), True
    if hasattr(source, "read"):
        return source, False
    if isinstance(source, (bytes, bytearray, memoryview)):
        return io.BytesIO(bytes(source)), False
    return iter(source), False


def _extents(src, size: int) -> Iterator[bytes]:
    """Yield the stream in pieces of exactly ``size`` bytes (the last one shorter), from a stream or an iterator."""
    if hasattr(src, "read"):
        while True:
            buf = bytearray()
            while len(buf) < size:
                chunk = src.read(size - len(buf))
                if not chunk:
                    break
                buf += chunk
            if not buf:
                return
            yield bytes(buf)
            if len(buf) < size:
                return
    else:
        buf = bytearray()
        for piece in src:
            buf += piece
            while len(buf) >= size:
                yield bytes(buf[:size])
                del buf[:size]
        if buf:
            yield bytes(buf)


def _task(index: int, data_path: str, strands_path: str, dna: DNAOptions, archive: ar.ArchiveOptions, work: str) -> dict:
    from dataclasses import replace
    rec = encode_extent(data_path, strands_path, replace(dna, workers=1), archive, work=work)
    return {"index": index, **rec}


# ============================================================================ encode
def encode_stream(source, sink: str | os.PathLike, *, options: StreamOptions | None = None, manifest: str | os.PathLike | None = None,
                  overwrite: bool = False, progress=None, workdir: str | os.PathLike | None = None) -> dict:
    """Encode a byte stream (path, ``"-"`` for stdin, a binary file object, bytes, or an iterable of bytes) into the
    strand file ``sink`` (``"-"`` = stdout; FASTA) and write the stream manifest (``manifest``, default
    ``<sink>.stream.json``; required for a stdout sink). Returns the manifest document."""
    opt = options or StreamOptions()
    opt.validate()
    t0 = time.perf_counter()
    to_stdout = sink == "-"
    if to_stdout and manifest is None:
        raise VNXConfigurationError("a stdout sink needs an explicit manifest path")
    sink_path = None if to_stdout else Path(sink)
    man_path = Path(manifest) if manifest is not None else Path(str(sink_path) + ".stream.json")
    if not overwrite:
        for p in ([sink_path] if sink_path else []) + [man_path]:
            if p.exists():
                raise VNXConfigurationError(f"output already exists: {p} (use --force to overwrite)")
    fmt = "fasta"
    if sink_path is not None and sink_path.suffix.lower() in (".fq", ".fastq"):
        fmt = "fastq"
    src, close = _open_source(source)           # V14 F (H-7): before the work directory, so a missing source leaks none
    try:
        work = Path(tempfile.mkdtemp(prefix="vnx-stream-", dir=workdir))
    except BaseException:
        if close and hasattr(src, "close"):
            src.close()
        raise
    pool = None
    records: list[dict] = []
    total = hashlib.sha256()
    out_hash = hashlib.sha256()
    state = {"offset": 0, "bytes": 0}
    try:
        with (atomic_output(sink_path, overwrite=overwrite) if sink_path else _stdout()) as out_path:
            out = sys.stdout.buffer if out_path is None else open(out_path, "wb")
            try:
                def take(rec: dict) -> None:
                    sp = Path(rec.pop("_strands"))
                    rec["strands_offset"] = state["offset"]
                    rec["strands_bytes"] = sp.stat().st_size
                    with open(sp, "rb") as f:
                        for block in iter(lambda: f.read(1 << 20), b""):
                            out.write(block)
                            out_hash.update(block)
                    state["offset"] += rec["strands_bytes"]
                    state["bytes"] += rec["length"]
                    (work / f"e{rec['index']}.bin").unlink()
                    sp.unlink()
                    records.append(rec)
                    if progress:
                        progress({"stage": "encode_stream", "extents": len(records), "bytes": state["bytes"],
                                  "elapsed": time.perf_counter() - t0})

                pending: deque = deque()
                if opt.workers > 1:
                    pool = ProcessPoolExecutor(max_workers=opt.workers)
                for i, data in enumerate(_extents(src, opt.extent_bytes)):
                    dp = work / f"e{i}.bin"
                    dp.write_bytes(data)
                    total.update(data)                      # extents are produced, and taken, in stream order
                    sp = work / f"s{i}.{fmt}"
                    args = (i, str(dp), str(sp), opt.dna, opt.archive, str(work))
                    if pool is None:
                        rec = _task(*args)
                        rec["_strands"] = str(sp)
                        take(rec)
                    else:
                        pending.append((pool.submit(_task, *args), sp))
                        if len(pending) >= 2 * opt.workers:
                            fut, sp0 = pending.popleft()
                            rec = fut.result()
                            rec["_strands"] = str(sp0)
                            take(rec)
                while pending:
                    fut, sp0 = pending.popleft()
                    rec = fut.result()
                    rec["_strands"] = str(sp0)
                    take(rec)
                out.flush()
            finally:
                if out_path is not None:
                    out.close()
        doc = {"format": STREAM_FORMAT, "extent_bytes": opt.extent_bytes, "strands_format": fmt,
               "total_bytes": state["bytes"], "sha256": total.hexdigest(), "extents": records,
               "strands_sha256": out_hash.hexdigest(), "strands_bytes": state["offset"],
               "dna": {"profile": opt.dna.profile, "outer_code": opt.dna.outer_code},
               "encrypted": opt.archive.key is not None or opt.archive.passphrase is not None}
        with atomic_output(man_path, overwrite=overwrite) as mp:
            mp.write_bytes(canonical_json(doc) + b"\n")          # canonical JSON: integers and strings only
        seconds = time.perf_counter() - t0
        return {**doc, "manifest": str(man_path), "output": "-" if to_stdout else str(sink_path),
                "seconds": round(seconds, 4), "mb_s": round(state["bytes"] / max(1e-9, seconds) / 1e6, 3)}
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
        if close:
            src.close()
        shutil.rmtree(work, ignore_errors=True)


class _stdout:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


# ============================================================================ decode
_MAX_RANGE = 1 << 62                         # strands_offset + strands_bytes (an off_t holds 2^63 - 1)


def load_manifest(path: str | os.PathLike) -> dict:
    try:
        doc = parse_canonical_json(Path(path).read_bytes().rstrip(b"\n"), "stream manifest")
    except (OSError, ValueError) as error:
        raise VNXFormatError(f"cannot read the stream manifest {path}: {error}", stage="stream") from None
    if doc.get("format") != STREAM_FORMAT or not isinstance(doc.get("extents"), list):
        raise VNXFormatError(f"{path} is not a {STREAM_FORMAT} manifest", stage="stream")

    def bad(msg: str) -> VNXFormatError:
        return VNXFormatError(f"stream manifest: {msg}", stage="stream")

    def count(rec: dict, k: str, i: int) -> int:
        v = rec[k]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise bad(f"extent {i}: {k!r} must be a non-negative integer")
        return v

    def hexstr(rec: dict, k: str, i: int, digits: int) -> str:
        v = rec[k]
        if not isinstance(v, str) or len(v) != digits or any(c not in "0123456789abcdef" for c in v):
            raise bad(f"extent {i}: {k!r} must be {digits} lowercase hex digits")
        return v

    total = 0
    for i, rec in enumerate(doc["extents"]):
        if not isinstance(rec, dict):
            raise bad(f"extent {i} is not an object")
        for k in ("index", "length", "sha256", "archive_id", "archive_tag", "strands_offset", "strands_bytes", "strands_sha256"):
            if k not in rec:
                raise bad(f"extent {i} lacks {k!r}")
        if rec["index"] != i:
            raise bad("extents out of order")
        total += count(rec, "length", i)
        # V14 F (H-6): a range beyond any file is a format error, not a raw ValueError from seek()
        if count(rec, "strands_offset", i) + count(rec, "strands_bytes", i) > _MAX_RANGE:
            raise bad(f"extent {i}: strand byte range beyond {_MAX_RANGE}")
        hexstr(rec, "sha256", i, 64)
        hexstr(rec, "strands_sha256", i, 64)
        hexstr(rec, "archive_tag", i, 4)
        if not isinstance(rec["archive_id"], str) or not rec["archive_id"]:
            raise bad(f"extent {i}: 'archive_id' must be a non-empty string")
    if isinstance(doc.get("total_bytes"), bool) or not isinstance(doc.get("total_bytes"), int) or doc["total_bytes"] != total:
        raise bad(f"'total_bytes' must equal the sum of the extent lengths ({total})")
    if not isinstance(doc.get("sha256"), str) or len(doc["sha256"]) != 64:
        raise bad("'sha256' must be the 64-hex-digit hash of the stream")
    if doc.get("strands_format", "fasta") not in ("fasta", "fastq"):        # V14 F (H-6): it names work files
        raise bad("'strands_format' must be 'fasta' or 'fastq'")
    return doc


def _range_matches(strands: Path, rec: dict) -> bool:
    """The sink still holds the extent's strands at the recorded byte range (then that range alone is decoded)."""
    try:
        with open(strands, "rb") as f:
            f.seek(rec["strands_offset"])
            h = hashlib.sha256()
            left = rec["strands_bytes"]
            while left > 0:
                block = f.read(min(1 << 20, left))
                if not block:
                    return False
                h.update(block)
                left -= len(block)
        return h.hexdigest() == rec["strands_sha256"]
    except OSError:
        return False


# ============================================================================ parallel per-extent pass 2 (V14 P6-B)
# With workers > 1 and every extent pooled, the linear pool path's per-extent pass 2 (consensus, outer decode,
# container + manifest checks) runs in up to ``workers`` forked worker processes: the extents' partitions are already
# on disk and independent, and each task is the same PoolDecoder.decode the serial loop runs. A task writes its
# manifest-verified bytes to a file in the stream's work directory (a per-call mkdtemp, so ``pool-<i>.bin`` never
# collides between concurrent decodes); the parent takes the results in manifest order, re-checks length + SHA-256
# against the manifest before a byte is written, and hands the caller None for a failed extent (the caller then takes
# the whole-pool decode_extent, exactly as in the serial loop). Bounded: at most ``workers`` tasks in flight.
#
# The pool comes from vnxdna.core.workers.process_pool (workers ignore SIGINT, die with their parent, and an interrupt
# kills them instead of joining the pool). Around a legacy fallback the pool is paused (shut down after its in-flight
# tasks, whose results are kept) so the fallback never forks next to the pool's manager thread, and restarted for the
# next extent. A pool that cannot start (fork failure, no "fork" start method) or breaks (a worker died) is given up:
# its unfinished extents and all later ones run serially in this process through the same PoolDecoder.decode.
_POOL_TASK_STATE: tuple | None = None
_POOL_FAILURES = (BrokenExecutor, OSError, ValueError, RuntimeError)   # start/submit failures of a process pool


def _pool_task_init(state: tuple) -> None:
    global _POOL_TASK_STATE
    _POOL_TASK_STATE = state


def _pool_task(rec: dict, dest: str) -> float:
    """One extent through PoolDecoder.decode in a worker process; the verified bytes go to ``dest``. Returns seconds."""
    assert _POOL_TASK_STATE is not None
    decoder, key, passphrase = _POOL_TASK_STATE
    t = time.perf_counter()
    data = decoder.decode(rec, lambda out_c: _verify_extent_container(out_c, rec, key=key, passphrase=passphrase))
    with open(dest, "wb") as f:
        f.write(data)
    return time.perf_counter() - t


class _ParallelPass2:
    """In-order results of the parallel per-extent pass 2 for ``pooled`` (see the comment above)."""

    def __init__(self, decoder, pooled: list[dict], workers: int, work: Path, key, passphrase):
        self.decoder = decoder
        self.pooled = pooled
        self.depth = min(workers, len(pooled))
        self.work = work
        self.key = key
        self.passphrase = passphrase
        self.ex: Any = None
        self.disabled = False           # the pool failed to start or broke: serial from here on
        self.window: deque = deque()    # (rec, dest, future or None)
        self.nxt = 0                    # next extent to submit
        self.taken = 0                  # next extent to return
        self.task_extents = 0           # extents whose bytes came from a worker task (observability)

    # ---------------------------------------------------------------- pool lifecycle
    def _start(self) -> None:
        import multiprocessing as mp
        from vnxdna.core.workers import process_pool
        try:
            self.ex = process_pool(self.depth, initializer=_pool_task_init,
                                   initargs=((self.decoder, self.key, self.passphrase),), mp_context=mp.get_context("fork"))
        except _POOL_FAILURES:
            self._give_up()

    def _stop(self) -> None:
        """Graceful: in-flight tasks finish (their results stay usable), the manager thread ends."""
        if self.ex is not None:
            try:
                self.ex.shutdown(wait=True)     # an interrupt meanwhile still finds self.ex for abort()
            except _POOL_FAILURES:
                pass
            self.ex = None

    def _give_up(self) -> None:
        self.disabled = True
        if self.ex is not None:
            try:
                self.ex.shutdown(wait=True, cancel_futures=True)
            except _POOL_FAILURES:
                pass
            self.ex = None

    def abort(self) -> None:
        """Interrupt: kill the workers, never join the pool."""
        self.disabled = True
        if self.ex is not None:
            ex, self.ex = self.ex, None
            ex.abort()

    def close(self) -> None:
        self._give_up()
        for _, dest, _ in self.window:
            dest.unlink(missing_ok=True)
        self.window.clear()

    def _fill(self) -> None:
        while not self.disabled and self.nxt < len(self.pooled) and len(self.window) < self.depth:
            if self.ex is None:
                self._start()
                if self.ex is None:
                    return
            rec = self.pooled[self.nxt]
            dest = self.work / f"pool-{self.nxt}.bin"
            try:
                fut = self.ex.submit(_pool_task, rec, str(dest))
            except _POOL_FAILURES:        # fork failure at the first submit, or a pool broken since the last result
                self._give_up()
                return
            self.window.append((rec, dest, fut))
            self.nxt += 1

    # ---------------------------------------------------------------- results
    def _serial(self, rec: dict) -> bytes | None:
        try:
            return self.decoder.decode(rec, lambda out_c: _verify_extent_container(out_c, rec, key=self.key,
                                                                                   passphrase=self.passphrase))
        except Exception:  # noqa: BLE001 - as the serial loop: the caller falls back to the legacy path
            return None

    def next(self) -> bytes | None:
        """The next extent's verified bytes, or None where its linear decode failed (the caller falls back)."""
        try:
            self._fill()
            if not self.window:                 # no pool (gave up): serial, in order
                rec = self.pooled[self.nxt]
                self.nxt += 1
                return self._serial(rec)
            rec, dest, fut = self.window.popleft()
            redo = False
            data = None
            try:
                seconds = fut.result()
                data = dest.read_bytes()
                if len(data) != rec["length"] or hashlib.sha256(data).hexdigest() != rec["sha256"]:
                    data = None                 # never write bytes the manifest does not vouch for
                else:
                    self.decoder.pass2_seconds += seconds
                    self.decoder.pass2_extents += 1
                    self.task_extents += 1
            except BrokenExecutor:
                redo = True                     # a worker died (not this extent's decode)
            except Exception:  # noqa: BLE001 - the extent's linear decode failed: the caller falls back, as serially
                data = None
            finally:
                dest.unlink(missing_ok=True)
            if redo:
                self._give_up()                 # the pool is broken: this and every later extent run here
                return self._serial(rec)
            if data is None:
                self._stop()                    # pause for the caller's legacy fallback; _fill restarts the pool
            return data
        except KeyboardInterrupt:
            self.abort()
            raise


def decode_stream(strands: str | os.PathLike, manifest: str | os.PathLike, output: str | os.PathLike, *,
                  options: DecodeOptions | None = None, key: bytes | None = None, passphrase: str | None = None,
                  overwrite: bool = False, progress=None, workdir: str | os.PathLike | None = None,
                  by_range: bool | None = None) -> dict:
    """Rebuild the stream described by ``manifest`` from ``strands`` into ``output`` (``"-"`` = stdout). ``by_range``:
    decode each extent from its recorded strand byte range (default: when the range still hashes as recorded), else
    from the whole pool by archive ID (one pass per extent, e.g. for a sequenced pool)."""
    doc = load_manifest(manifest)
    opt = options or DecodeOptions()
    opt.validate()
    t0 = time.perf_counter()
    strands = Path(strands)
    tags_checked = False

    def check_tags() -> None:
        """The pool path separates extents by their 16-bit archive tag: run once before the first pool-path extent."""
        nonlocal tags_checked
        if tags_checked:
            return
        tags_checked = True
        by_tag: dict[str, set[str]] = {}
        for e in doc["extents"]:
            by_tag.setdefault(e["archive_tag"], set()).add(e["archive_id"])
        dup = sorted(t for t, ids in by_tag.items() if len(ids) > 1)        # one tag, several different archives
        if dup:
            raise VNXFormatError(f"the pool path cannot separate extents sharing an archive tag ({', '.join(dup)}); decode "
                                 "from the encoder's strand file (by range) or keep extents in separate files",
                                 stage="stream", details={"archive_tags": dup})

    # One use-range decision per extent up front, so the pooled extents can share a single layout probe + pass 1.
    use_ranges = [(_range_matches(strands, rec) if by_range is None else by_range) for rec in doc["extents"]]
    pooled = [rec for rec, ur in zip(doc["extents"], use_ranges) if not ur]
    if pooled:
        check_tags()
    to_stdout = output == "-"
    out_path = None if to_stdout else Path(output)
    work: Path | None = None                    # V14 F (H-7): created inside the try below, so nothing leaks before it
    fmt = doc.get("strands_format", "fasta")
    total = hashlib.sha256()
    done = 0
    ranged = 0
    pool_fast = 0
    pool_fallback = 0
    pool_decoder = None
    # Linear pool decode (V14 P6-A): run the layout probe and pass 1 once for the whole shuffled pool, then decode each
    # pooled extent from its own archive-tag partition. Excluded configurations (and any setup failure) leave
    # pool_decoder None, and every pooled extent then takes the legacy whole-pool decode_extent path below. A per-extent
    # failure also falls back, so the output is byte-for-byte that of the legacy path.
    if pooled:
        from vnxdna.pipeline.decode import PoolDecoder, pool_decode_refusal
        if pool_decode_refusal(opt) is None:
            try:
                pool_decoder = PoolDecoder(strands, opt, [int(rec["archive_tag"], 16) for rec in pooled],
                                           key=key, passphrase=passphrase, workdir=workdir)
            except Exception:  # noqa: BLE001 - any pass-1/partition failure falls back to the per-extent path
                pool_decoder = None
    ahead = None
    try:
        work = Path(tempfile.mkdtemp(prefix="vnx-stream-", dir=workdir))
        # parallel pass 2 only when every extent is pooled: a by-range extent decoded in between could start its own
        # worker processes while this task pool's manager thread runs
        if pool_decoder is not None and opt.workers > 1 and 1 < len(pooled) == len(doc["extents"]):
            ahead = _ParallelPass2(pool_decoder, pooled, opt.workers, work, key, passphrase)
        with (atomic_output(out_path, overwrite=overwrite) if out_path else _stdout()) as tmp_out:
            out = sys.stdout.buffer if tmp_out is None else open(tmp_out, "wb")
            try:
                for rec, use_range in zip(doc["extents"], use_ranges):
                    if use_range:
                        part = work / f"r{rec['index']}.{fmt}"
                        with open(strands, "rb") as f, open(part, "wb") as g:
                            f.seek(rec["strands_offset"])
                            left = rec["strands_bytes"]
                            while left > 0:
                                block = f.read(min(1 << 20, left))
                                if not block:
                                    raise VNXFormatError(f"strand file shorter than the manifest's range of extent {rec['index']}",
                                                         stage="stream")
                                g.write(block)
                                left -= len(block)
                        data = decode_extent(part, rec, opt, key=key, passphrase=passphrase, work=work)
                        part.unlink()
                        ranged += 1
                    else:
                        data = None
                        if ahead is not None:
                            data = ahead.next()
                        elif pool_decoder is not None:
                            try:
                                data = pool_decoder.decode(
                                    rec, lambda out_c, rec=rec: _verify_extent_container(out_c, rec, key=key, passphrase=passphrase))
                            except Exception:  # noqa: BLE001 - any per-extent failure falls back, output still verified
                                data = None
                        if data is None:
                            data = decode_extent(strands, rec, opt, key=key, passphrase=passphrase, work=work)
                            pool_fallback += 1
                        else:
                            pool_fast += 1
                    assert data is not None
                    out.write(data)
                    total.update(data)
                    done += 1
                    if progress:
                        progress({"stage": "decode_stream", "extents": done, "of": len(doc["extents"]),
                                  "elapsed": time.perf_counter() - t0})
                out.flush()
            finally:
                if tmp_out is not None:
                    out.close()
            if total.hexdigest() != doc["sha256"]:
                raise VNXIntegrityError("the rebuilt stream does not hash as the manifest records", stage="stream")
        seconds = time.perf_counter() - t0
        rep = {"output": "-" if to_stdout else str(out_path), "extents": done, "by_range": ranged, "by_pool": done - ranged,
               "by_pool_linear": pool_fast, "by_pool_fallback": pool_fallback,
               "total_bytes": doc["total_bytes"], "sha256": doc["sha256"], "seconds": round(seconds, 4),
               "mb_s": round(doc["total_bytes"] / max(1e-9, seconds) / 1e6, 3)}
        if pool_decoder is not None:
            # the linear pool decode's cost split: one layout probe + pass 1, one partition, then pass 2 per extent
            # (partition/index is ~O(pool + extents); pass 2 touches only each extent's own partition)
            rep["pass1_seconds"] = round(pool_decoder.pass1_seconds, 4)
            rep["partition_seconds"] = round(pool_decoder.partition_seconds, 4)
            # summed per-extent pass-2 seconds; with parallel workers this is the sum of the worker tasks' times
            # (CPU-side work), not the wall time the caller waited (seconds - pass1_seconds - partition_seconds)
            rep["pool_pass2_seconds"] = round(pool_decoder.pass2_seconds, 4)
            if ahead is not None:
                rep["pool_pass2_task_extents"] = ahead.task_extents     # extents decoded by a worker process
            rep["pool_records"] = pool_decoder.records_total
            rep["pool_partition_records"] = dict(sorted(pool_decoder.partition_records.items()))
        return rep
    except KeyboardInterrupt:
        if ahead is not None:
            ahead.abort()                       # kill the workers; joining the pool after an interrupt can hang
        raise
    finally:
        if ahead is not None:
            ahead.close()                       # the task pool is down before the partitions are removed
        if pool_decoder is not None:
            pool_decoder.close()
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


__all__ = ["STREAM_FORMAT", "EXTENT_NAME", "StreamOptions", "encode_extent", "decode_extent", "encode_stream", "decode_stream",
           "load_manifest"]
