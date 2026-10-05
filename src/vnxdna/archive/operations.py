"""VNX4 archive engine: build, list, inspect, locate, verify and extract multi-file archives.

Pipeline per file (deterministic order = UTF-8 byte order of archive paths)::

    read chunk (bounded) → SHA-256 (file, incremental) → chunk ID (content address / keyed HMAC)
      → dedup lookup → zstd (kept only if smaller) → AES-256-GCM (index-bound nonce) → body

Workers (threads) compute chunk IDs and compression in parallel; the parent
assigns chunk indices, encrypts and writes strictly in input order, so the
output is identical for every worker count. At most ``2 × workers`` chunks are
in flight, so memory is bounded by the chunk size, not the input size.
"""
from __future__ import annotations

import hashlib
import os
import stat
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import zstandard

from vnxdna.archive import container as ct
from vnxdna.archive import crypto, merkle
from vnxdna.core.errors import VNXConfigurationError, VNXFormatError, VNXIntegrityError, VNXOutputError, VNXResourceError
from vnxdna.core.util import atomic_output


@dataclass
class ArchiveOptions:
    chunk_size: int = 1 << 20
    compression: str = "zstd"          # zstd | none
    level: int = 3
    workers: int = 1
    preserve_metadata: bool = False    # store mode and mtime (off by default for determinism)
    dedup: bool = True
    key: bytes | None = None
    passphrase: str | None = None
    scrypt: dict = field(default_factory=lambda: dict(crypto.SCRYPT_DEFAULT))
    follow_symlinks: bool = False

    def validate(self) -> None:
        if not ct.MIN_CHUNK_SIZE <= self.chunk_size <= ct.MAX_CHUNK_SIZE:
            raise VNXConfigurationError(f"chunk_size must be in [{ct.MIN_CHUNK_SIZE}, {ct.MAX_CHUNK_SIZE}]")
        if self.compression not in ("zstd", "none"):
            raise VNXConfigurationError("compression must be zstd or none")
        if not 1 <= self.level <= 22:
            raise VNXConfigurationError("zstd level must be in 1..22")
        if not 1 <= self.workers <= 256:
            raise VNXConfigurationError("workers must be in 1..256")
        if self.key is not None and self.passphrase is not None:
            raise VNXConfigurationError("use a key file or a passphrase, not both")


@dataclass
class Entry:
    path: str          # archive path
    source: Path | None
    type: int
    size: int
    mode: int
    mtime_ns: int


# ============================================================================ input collection
def collect_entries(inputs: list[str | os.PathLike], *, preserve_metadata: bool = False,
                    follow_symlinks: bool = False) -> tuple[list[Entry], list[str]]:
    """Walk inputs. A file is stored under its base name, a directory under its own name (tar-like)."""
    entries: dict[str, Entry] = {}
    warnings: list[str] = []
    for item in inputs:
        src = Path(item)
        try:
            st = src.stat() if follow_symlinks else src.lstat()
        except OSError as error:
            raise VNXFormatError(f"cannot read input {src}: {error.strerror or error}", stage="input") from None
        name = src.resolve().name if src.name in ("", ".", "..") else src.name
        if stat.S_ISLNK(st.st_mode):
            warnings.append(f"skipped symlink {src}")
            continue
        if stat.S_ISREG(st.st_mode):
            _add(entries, Entry(name, src, ct.TYPE_FILE, st.st_size, *(_meta(st, preserve_metadata))))
        elif stat.S_ISDIR(st.st_mode):
            _walk(src, name, entries, warnings, preserve_metadata, follow_symlinks)
        else:
            warnings.append(f"skipped special file {src}")
    if len(entries) > ct.MAX_FILES:
        raise VNXResourceError(f"too many entries ({len(entries)} > {ct.MAX_FILES})")
    ordered = sorted(entries.values(), key=lambda e: e.path.encode("utf-8"))
    return ordered, warnings


def _meta(st: os.stat_result, preserve: bool) -> tuple[int, int]:
    return (stat.S_IMODE(st.st_mode), st.st_mtime_ns) if preserve else (0, 0)


def _add(entries: dict[str, Entry], e: Entry) -> None:
    try:
        ct.validate_archive_path(e.path)
    except VNXFormatError as error:
        raise VNXFormatError(f"cannot archive {e.source}: {error}", stage="input") from None
    if e.path in entries:
        raise VNXFormatError(f"two inputs map to the same archive path {e.path!r}", stage="input")
    entries[e.path] = e


def _walk(root: Path, prefix: str, entries: dict, warnings: list, preserve: bool, follow: bool) -> None:
    stack = [(root, prefix)]
    while stack:
        d, p = stack.pop()
        try:
            children = sorted(os.scandir(d), key=lambda x: x.name)
        except OSError as error:
            raise VNXFormatError(f"cannot list {d}: {error.strerror or error}", stage="input") from None
        if not children:
            st = d.stat()
            _add(entries, Entry(p, d, ct.TYPE_DIR, 0, *(_meta(st, preserve))))
        for c in children:
            try:
                name = c.name
                name.encode("utf-8")
            except UnicodeEncodeError:
                raise VNXFormatError(f"file name in {d} is not valid UTF-8", stage="input") from None
            st = c.stat(follow_symlinks=follow)
            path = f"{p}/{name}"
            if stat.S_ISLNK(st.st_mode):
                warnings.append(f"skipped symlink {c.path}")
            elif stat.S_ISREG(st.st_mode):
                _add(entries, Entry(path, Path(c.path), ct.TYPE_FILE, st.st_size, *(_meta(st, preserve))))
            elif stat.S_ISDIR(st.st_mode):
                stack.append((Path(c.path), path))
            else:
                warnings.append(f"skipped special file {c.path}")


# ============================================================================ build
@dataclass
class BuildReport:
    output: str
    files: int
    content_bytes: int
    unique_chunks: int
    chunk_refs: int
    stored_bytes: int
    container_bytes: int
    container_sha256: str
    archive_id: str
    merkle_root: str
    seconds: float
    warnings: list[str]
    stage_seconds: dict

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _chunk_source(entries: list[Entry], chunk_size: int) -> Iterator[tuple[int, bytes]]:
    """Yield (entry index, chunk bytes) in archive order; each file is re-checked for size changes."""
    for i, e in enumerate(entries):
        if e.type != ct.TYPE_FILE:
            continue
        got = 0
        try:
            with open(e.source, "rb") as f:
                while True:
                    block = f.read(chunk_size)
                    if not block:
                        break
                    got += len(block)
                    yield i, block
        except OSError as error:
            raise VNXFormatError(f"cannot read {e.source}: {error.strerror or error}", stage="input") from None
        if got != e.size:
            raise VNXFormatError(f"{e.source} changed size while being archived ({e.size} → {got})", stage="input",
                                 retryable=True)


def build_archive(inputs: list[str | os.PathLike], output: str | os.PathLike, options: ArchiveOptions | None = None, *,
                  overwrite: bool = False, progress: Callable[[dict], None] | None = None,
                  archive_id: bytes | None = None, salt: bytes | None = None) -> BuildReport:
    """Create a VNX4 container. ``archive_id``/``salt`` overrides exist for tests only."""
    opt = options or ArchiveOptions()
    opt.validate()
    t0 = time.perf_counter()
    entries, warnings = collect_entries(inputs, preserve_metadata=opt.preserve_metadata, follow_symlinks=opt.follow_symlinks)
    if not entries:
        raise VNXFormatError("nothing to archive (no regular files or directories)", stage="input")
    encrypted = opt.key is not None or opt.passphrase is not None
    features = {"vnx4-container", "chunk-fixed", "merkle-rfc6962-sha256"}
    if opt.dedup:
        features.add("dedup-content-address")
    if opt.compression == "zstd":
        features.add("zstd")
    sealer = None
    if encrypted:
        salt = salt or os.urandom(crypto.SALT_BYTES)
        archive_id = archive_id or os.urandom(16)
        if opt.passphrase is not None:
            master = crypto.scrypt_master(opt.passphrase, salt, opt.scrypt)
            enc = {"algorithm": "AES-256-GCM", "kdf": "scrypt-hkdf-sha256", "scrypt": dict(opt.scrypt)}
            features.add("kdf-scrypt")
        else:
            master = opt.key
            enc = {"algorithm": "AES-256-GCM", "kdf": "key-file-hkdf-sha256"}
        keys = crypto.ArchiveKeys.derive(master, salt)
        enc.update({"salt": salt.hex(), "key_check": keys.key_check.hex()})
        features |= {"aes-256-gcm", "kdf-hkdf-sha256"}
        sealer = crypto.Sealer(keys, archive_id)
    else:
        enc = {"algorithm": "none"}
        if archive_id is None:
            # deterministic: derived from the options and the sorted entry list (paths, sizes, metadata)
            h = hashlib.sha256(b"VNX4 archive-id\x00")
            h.update(f"{opt.chunk_size}|{opt.compression}|{opt.level}|{opt.dedup}|{opt.preserve_metadata}".encode())
            for e in entries:
                h.update(e.path.encode() + b"\x00" + f"{e.type}|{e.size}|{e.mode}|{e.mtime_ns}".encode() + b"\x00")
            archive_id = h.digest()[:16]
    chunk_id_fn = sealer.chunk_id if sealer else crypto.public_chunk_id
    compress = opt.compression == "zstd"
    cctx_local: dict[int, zstandard.ZstdCompressor] = {}

    def work(item: tuple[int, bytes]) -> tuple[int, bytes, bytes, bytes, int]:
        idx, block = item
        cid = chunk_id_fn(block)
        stored, codec = block, ct.CODEC_NONE
        if compress:
            import threading
            tid = threading.get_ident()
            cctx = cctx_local.get(tid)
            if cctx is None:
                cctx = cctx_local[tid] = zstandard.ZstdCompressor(level=opt.level, write_checksum=False, write_content_size=True)
            z = cctx.compress(block)
            if len(z) < len(block):
                stored, codec = z, ct.CODEC_ZSTD
        return idx, block, cid, stored, codec

    file_sha = [hashlib.sha256() for _ in entries]
    file_refs: list[list[int]] = [[] for _ in entries]
    seen: dict[bytes, int] = {}
    pending_stored: list[tuple[bytes, int, int, bytes]] = []  # unique chunks waiting for their index (encrypt needs it)
    stage = {"read_hash_compress": 0.0, "encrypt_write": 0.0}
    with atomic_output(output, overwrite=overwrite) as tmp:
        with open(tmp, "wb", buffering=1 << 20) as f:
            writer = ct.ContainerWriter(f)
            content = 0
            refs_total = 0

            def consume(res: tuple[int, bytes, bytes, bytes, int]) -> None:
                nonlocal content, refs_total
                idx, block, cid, stored, codec = res
                file_sha[idx].update(block)
                content += len(block)
                refs_total += 1
                if opt.dedup and cid in seen:
                    file_refs[idx].append(seen[cid])
                    return
                t = time.perf_counter()
                index = len(writer.entries)
                if sealer is not None:
                    stored = sealer.seal(crypto.DOMAIN_CHUNK, index, 0, stored)  # count unknown while streaming: 0
                writer.add_chunk(stored, len(block), codec, cid)
                stage["encrypt_write"] += time.perf_counter() - t
                seen[cid] = index
                file_refs[idx].append(index)
                if progress and index % 64 == 0:
                    progress({"stage": "archive", "chunks": index + 1, "bytes": content, "elapsed": time.perf_counter() - t0})

            t_start = time.perf_counter()
            source = _chunk_source(entries, opt.chunk_size)
            if opt.workers == 1:
                for item in source:
                    consume(work(item))
            else:
                with ThreadPoolExecutor(max_workers=opt.workers) as pool:
                    window: deque = deque()
                    for item in source:
                        window.append(pool.submit(work, item))
                        if len(window) >= 2 * opt.workers:
                            consume(window.popleft().result())
                    while window:
                        consume(window.popleft().result())
            stage["read_hash_compress"] = time.perf_counter() - t_start - stage["encrypt_write"]
            del pending_stored
            records = []
            refs: list[int] = []
            for i, e in enumerate(entries):
                sha = file_sha[i].digest() if e.type == ct.TYPE_FILE else hashlib.sha256(b"").digest()
                records.append(ct.FileRecord(e.path, e.type, e.size, e.mode, e.mtime_ns, len(refs), len(file_refs[i]), sha))
                refs.extend(file_refs[i])
            file_table = b"".join(r.pack() for r in records)
            refs_bytes = np.asarray(refs, dtype=">u4").tobytes()
            if sealer is not None:
                file_table = sealer.seal(crypto.DOMAIN_FILE_TABLE, 0, 1, file_table)
                refs_bytes = sealer.seal(crypto.DOMAIN_REFS, 0, 1, refs_bytes)
            counts = {"files": len(entries), "chunks": len(writer.entries), "chunk_refs": len(refs), "content_bytes": content,
                      "stored_bytes": 0}
            comp = {"algorithm": opt.compression, "level": opt.level if compress else 0, "policy": "keep-if-smaller"}
            manifest = ct.base_manifest(archive_id=archive_id, chunk_size=opt.chunk_size, compression=comp, encryption=enc,
                                        counts=counts, features=features)
            digest, manifest = writer.finish(manifest, file_table, refs_bytes, sealer.mac if sealer else None)
        size = tmp.stat().st_size
    return BuildReport(str(output), len(entries), content, counts["chunks"], len(refs), manifest["counts"]["stored_bytes"], size,
                       digest.hex(), archive_id.hex(), manifest["integrity"]["merkle_root"], time.perf_counter() - t0, warnings,
                       {k: round(v, 6) for k, v in stage.items()})


# ============================================================================ chunk access
def bounded_zstd(stored: bytes, plain_size: int, what: str) -> bytes:
    """Decompress at most ``plain_size + 1`` bytes (V3's bounded stream reader).

    ``ZstdDecompressor.decompress(max_output_size=...)`` is *not* a bound when the frame header declares a content size
    (it allocates the declared size), so a hostile chunk could force a large allocation; the stream reader cannot.
    """
    from vnxdna.archive.compression import decompress as v3_decompress
    from vnxdna.core.taxonomy import IntegrityError
    try:
        return v3_decompress(stored, "zstd", plain_size)
    except IntegrityError as error:
        raise VNXIntegrityError(f"{what}: {error}") from None


def read_chunk(c: ct.Container, index: int, f=None, *, check_id: bool = True) -> bytes:
    """Read, verify and decode one chunk. Fails closed on any mismatch."""
    if c.encrypted and c.sealer is None:
        raise VNXFormatError("archive is encrypted; a key is required to read content", stage="crypto")
    off, n = c.chunk_range(index)
    e = c.chunk_table[index]
    own = f is None
    if own:
        f = open(c.path, "rb")
    try:
        stored = os.pread(f.fileno(), n, off)
    finally:
        if own:
            f.close()
    if len(stored) != n:
        raise VNXFormatError(f"chunk {index} is truncated")
    if hashlib.sha256(stored).digest() != bytes(e["stored_sha256"]):
        raise VNXIntegrityError(f"chunk {index}: stored SHA-256 mismatch (corrupted body)", details={"chunk": index})
    if c.sealer is not None:
        stored = c.sealer.open(crypto.DOMAIN_CHUNK, index, 0, stored, f"chunk {index}")
    plain_size = int(e["plain"])
    if int(e["codec"]) == ct.CODEC_ZSTD:
        data = bounded_zstd(stored, plain_size, f"chunk {index}")
    else:
        data = stored
    if len(data) != plain_size:
        raise VNXIntegrityError(f"chunk {index}: decoded size {len(data)} differs from the table ({plain_size})")
    if check_id:
        cid = c.sealer.chunk_id(data) if c.sealer else crypto.public_chunk_id(data)
        if cid != bytes(e["chunk_id"]):
            raise VNXIntegrityError(f"chunk {index}: content does not match its chunk ID", details={"chunk": index})
    return data


def iter_file(c: ct.Container, rec: ct.FileRecord) -> Iterator[bytes]:
    with open(c.path, "rb") as f:
        for idx in c.file_chunks(rec).tolist():
            yield read_chunk(c, idx, f)


# ============================================================================ verify / inspect / locate
def verify_container(path: str | os.PathLike, *, key: bytes | None = None, passphrase: str | None = None,
                     chunk: int | None = None, deep: bool = True, allow_unencrypted: bool = False) -> dict:
    """Verify structure + manifest + Merkle root, the whole-file trailer digest, every chunk and every file hash.

    With ``chunk`` only that chunk is checked, through its Merkle inclusion proof.
    """
    t0 = time.perf_counter()
    c = ct.open_container(path, key=key, passphrase=passphrase, allow_unencrypted=allow_unencrypted)
    report: dict = {"path": str(path), "archive_id": c.manifest["archive_id"], "merkle_root": c.manifest["integrity"]["merkle_root"],
                    "encrypted": c.encrypted, "key_supplied": c.sealer is not None, "checks": {}}
    leaves = ct.leaf_hashes(c.chunk_table_bytes)
    if chunk is not None:
        if not 0 <= chunk < len(c.chunk_table):
            raise VNXFormatError(f"chunk {chunk} out of range (0..{len(c.chunk_table) - 1})")
        proof = merkle.inclusion_proof(leaves, chunk)
        ok = merkle.verify_inclusion(leaves[chunk], chunk, len(leaves), proof, bytes.fromhex(report["merkle_root"]))
        if not ok:
            raise VNXIntegrityError(f"Merkle inclusion proof for chunk {chunk} failed")
        if c.encrypted and c.sealer is None:
            off, n = c.chunk_range(chunk)
            with open(c.path, "rb") as f:
                stored = os.pread(f.fileno(), n, off)
            if hashlib.sha256(stored).digest() != bytes(c.chunk_table[chunk]["stored_sha256"]):
                raise VNXIntegrityError(f"chunk {chunk}: stored SHA-256 mismatch")
            level = "stored-bytes"
        else:
            read_chunk(c, chunk)
            level = "content"
        report["checks"] = {"merkle_proof": True, "proof_hashes": len(proof), "chunk": chunk, "level": level}
        report["status"] = "VERIFIED"
        report["seconds"] = time.perf_counter() - t0
        return report
    h = hashlib.sha256()
    with open(c.path, "rb") as f:
        remaining = c.size - 32
        while remaining:
            block = f.read(min(remaining, 1 << 20))
            if not block:
                raise VNXFormatError("file shrank during verification")
            h.update(block)
            remaining -= len(block)
    if h.digest() != c.trailer_sha256:
        raise VNXIntegrityError("whole-file SHA-256 in the trailer does not match (container corrupted)")
    report["checks"]["trailer_sha256"] = True
    report["checks"]["manifest_and_tables"] = True
    report["checks"]["merkle_root"] = True
    if c.encrypted and c.sealer is None:
        with open(c.path, "rb") as f:
            for i in range(len(c.chunk_table)):
                off, n = c.chunk_range(i)
                if hashlib.sha256(os.pread(f.fileno(), n, off)).digest() != bytes(c.chunk_table[i]["stored_sha256"]):
                    raise VNXIntegrityError(f"chunk {i}: stored SHA-256 mismatch", details={"chunk": i})
        report["checks"]["chunks"] = "stored-bytes only (no key: content and file hashes not checked)"
        report["status"] = "VERIFIED_STORED"
    else:
        chunks_ok = set()
        if deep:
            for rec in c.files:
                if rec.type != ct.TYPE_FILE:
                    continue
                fh = hashlib.sha256()
                for data in iter_file(c, rec):
                    fh.update(data)
                if fh.digest() != rec.sha256:
                    raise VNXIntegrityError(f"file {rec.path!r}: SHA-256 mismatch", details={"file": rec.path})
                chunks_ok.update(c.file_chunks(rec).tolist())
        report["checks"]["chunks"] = len(chunks_ok)
        report["checks"]["files"] = sum(1 for r in c.files if r.type == ct.TYPE_FILE)
        report["status"] = "VERIFIED"
    report["seconds"] = time.perf_counter() - t0
    return report


def inspect_container(path: str | os.PathLike, *, key: bytes | None = None, passphrase: str | None = None,
                      allow_unencrypted: bool = False) -> dict:
    c = ct.open_container(path, key=key, passphrase=passphrase, allow_unencrypted=allow_unencrypted)
    m = c.manifest
    out = {"path": str(path), "container_bytes": c.size, "container_sha256_trailer": c.trailer_sha256.hex(), "manifest": m,
           "readable_file_table": bool(c.files) or m["counts"]["files"] == 0}
    if c.chunk_table.size:
        out["chunk_stats"] = {"stored_min": int(c.chunk_table["stored"].min()), "stored_max": int(c.chunk_table["stored"].max()),
                              "compressed": int((c.chunk_table["codec"] == ct.CODEC_ZSTD).sum())}
    return out


def list_container(path: str | os.PathLike, *, key: bytes | None = None, passphrase: str | None = None,
                   allow_unencrypted: bool = False) -> list[dict]:
    c = ct.open_container(path, key=key, passphrase=passphrase, require_key=True, allow_unencrypted=allow_unencrypted)
    return [r.to_dict() for r in c.files]


def dna_location(ranges: list[list[int]], profile: str, container_size: int) -> dict:
    """Strand groups and strand-file record ranges holding container byte ranges (no index file needed: group g covers
    container bytes [g·K·P, (g+1)·K·P); strand records are the superblock strands, then each group's K+M symbols)."""
    from vnxdna.dnaenc.superblock import Superblock
    from vnxdna.dnaenc.layout import PROFILES
    lay, k, m = PROFILES[profile]
    span = k * lay.payload_bytes
    groups = sorted({g for off, n in ranges for g in range(off // span, (off + n - 1) // span + 1)})
    ks, ms = Superblock.symbols(lay.payload_bytes)
    total_groups = -(-container_size // span)
    return {"profile": profile, "groups": groups, "groups_total": total_groups,
            "strand_records": [[ks + ms + g * (k + m), k + m] for g in groups], "superblock_records": [0, ks + ms],
            "note": "records are 0-based positions in the strand file written by `vnx encode` with this profile"}


def locate(path: str | os.PathLike, name: str, *, key: bytes | None = None, passphrase: str | None = None,
           profile: str | None = None, allow_unencrypted: bool = False) -> dict:
    """Where a file's bytes live: chunk indices and container byte ranges (input to selective DNA decoding)."""
    t0 = time.perf_counter()
    c = ct.open_container(path, key=key, passphrase=passphrase, require_key=True, allow_unencrypted=allow_unencrypted)
    rec = c.file(name)
    idx = c.file_chunks(rec)
    ranges = [list(c.chunk_range(int(i))) for i in idx]
    out = {"file": rec.to_dict(), "chunks": idx.tolist(), "container_ranges": ranges,
           "bytes_to_read": int(sum(r[1] for r in ranges)), "container_bytes": c.size,
           "index_bytes": c.size - ct.HEADER_BYTES - c.manifest["counts"]["stored_bytes"], "lookup_seconds": time.perf_counter() - t0}
    if profile:
        out["dna"] = dna_location(ranges, profile, c.size)
    return out


# ============================================================================ extract
def _safe_target(root: Path, rel: str) -> Path:
    ct.validate_archive_path(rel)
    target = root.joinpath(*rel.split("/"))
    # refuse to traverse any existing symlink inside the output tree
    cur = root
    for part in rel.split("/")[:-1]:
        cur = cur / part
        if cur.is_symlink():
            raise VNXOutputError(f"refusing to extract through symlink {cur}")
        if cur.exists() and not cur.is_dir():
            raise VNXOutputError(f"output path component is not a directory: {cur}")
    if target.is_symlink():
        raise VNXOutputError(f"refusing to overwrite symlink {target}")
    return target


def extract(path: str | os.PathLike, output_dir: str | os.PathLike, *, key: bytes | None = None, passphrase: str | None = None,
            names: list[str] | None = None, overwrite: bool = False, apply_metadata: bool = False,
            allow_unencrypted: bool = False) -> dict:
    """Extract all or selected files. Every file is verified (chunk IDs + file SHA-256) before it is renamed into place."""
    t0 = time.perf_counter()
    c = ct.open_container(path, key=key, passphrase=passphrase, require_key=True, allow_unencrypted=allow_unencrypted)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    if names:
        records = [c.file(n) for n in names]
    else:
        records = list(c.files)
    done = []
    processed = 0
    for rec in records:
        target = _safe_target(root, rec.path)
        if rec.type == ct.TYPE_DIR:
            target.mkdir(parents=True, exist_ok=True)
            done.append(rec.path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not str(target.resolve().parent).startswith(str(root)):
            raise VNXOutputError(f"refusing to write outside the output directory: {rec.path!r}")
        with atomic_output(target, overwrite=overwrite) as tmp:
            fh = hashlib.sha256()
            with open(tmp, "wb") as out:
                for data in iter_file(c, rec):
                    fh.update(data)
                    out.write(data)
                    processed += len(data)
            if fh.digest() != rec.sha256:
                raise VNXIntegrityError(f"file {rec.path!r}: SHA-256 mismatch; not extracted", details={"file": rec.path})
            if apply_metadata and rec.mode:
                os.chmod(tmp, rec.mode & 0o777)
        if apply_metadata and rec.mtime_ns:
            os.utime(target, ns=(rec.mtime_ns, rec.mtime_ns))
        done.append(rec.path)
    return {"status": "EXTRACTED", "files": len(done), "bytes": processed, "output": str(root), "seconds": time.perf_counter() - t0}
