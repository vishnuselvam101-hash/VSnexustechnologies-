"""Format-5 archives on disk: streaming store (resumable), authenticated open, restore, extract, verify.

Memory model
    Every operation here processes one chunk at a time per worker, with a
    bounded number of chunks in flight (``2 × workers``). Peak memory is
    therefore about ``(2 × workers + 2) × chunk_size`` plus the index, which
    is 92 bytes per chunk (about 0.009 % of the input at the 1 MiB default).
    It does not grow with the file size. ``docs/LARGE_FILES.md`` has the
    measured values.

Store pipeline (per chunk, in a thread pool; zstd, AES-GCM and SHA-256
release the GIL)::

    read chunk i ──▶ SHA-256 (plaintext) ──▶ zstd; keep raw if not smaller ──▶ AES-256-GCM (optional)
                 ──▶ SHA-256 (stored) ──▶ append to <output>.partial ──▶ index entry

Resume
    Every ``checkpoint_interval`` chunks the partial file and the index sidecar
    are fsynced, then ``<output>.partial.ckpt`` is replaced atomically. The
    checkpoint describes a consistent prefix: its chunk count, body length, the
    SHA-256 of the sidecar prefix, the input's size and mtime, and a digest of
    the options. ``store --resume`` accepts it only if all of these still hold,
    the key matches (encrypted archives), every completed chunk's stored
    SHA-256 matches the partial body, and every completed chunk's plaintext
    SHA-256 matches the input. Otherwise it refuses and explains why.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import zstandard
import zlib

from .. import __version__
from ..container.builder import _safe_name
from ..container.compression import decompress
from ..errors import (AuthenticationError, ConfigurationError, IntegrityError, InvalidInputError, KeyRequiredError, MetadataError,
                      OutputError, VNXDNAError, WrongKeyError)
from . import crypto
from . import manifest as mf
from .container import ContainerFileV2, ContainerWriter, fsync_dir, publish
from .profiles import StoreOptionsV2

SIDECAR_ENTRY = mf.INDEX_DTYPE.itemsize + mf.PLAIN_DTYPE.itemsize  # 92 bytes per chunk
CHECKPOINT_FORMAT = "vnx-store-checkpoint-2"  # v2 (VNX-DNA 3): HMAC-bound when encrypted


def default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


# ======================================================================= loading and authentication
@dataclass
class LoadedV2:
    manifest: mf.Manifest
    manifest_bytes: bytes
    index: np.ndarray
    index_bytes: bytes
    plain_stored: bytes
    plain: np.ndarray | None
    content: mf.Content | None
    cipher: crypto.ChunkCipher | None
    authentication: str  # "hmac-sha256" | "digest-only" | "not-verified (no key)"

    def chunk_range(self, start: int, end: int) -> list[int]:
        cs = self.manifest.chunk_size
        return list(range(start // cs, (end - 1) // cs + 1)) if end > start else []


def load(manifest_bytes: bytes, index_bytes: bytes, plain_stored: bytes, key: bytes | None, *,
         require_key: bool = True) -> LoadedV2:
    """Parse, validate and authenticate a format-5 manifest and its two index tables."""
    raw = mf.parse_json(manifest_bytes)
    manifest = mf.validate(raw)
    if mf.canonical_bytes(raw) != manifest_bytes:
        raise MetadataError("manifest is not in canonical form")
    payload = mf.digest_payload(raw)
    digest_ok = hashlib.sha256(payload).hexdigest() == manifest.seal.manifest_sha256
    if not digest_ok and (not manifest.encrypted or key is None):
        raise MetadataError("manifest digest mismatch: the manifest is corrupted or was modified")
    index = mf.parse_chunk_index(manifest, index_bytes)
    if hashlib.sha256(plain_stored).hexdigest() != manifest.plain_index.sha256 or len(plain_stored) != manifest.plain_index.stored_bytes:
        raise MetadataError("plaintext index SHA-256 does not match the manifest (index corrupted or modified)")
    if not manifest.encrypted:
        content = manifest.content
        assert content is not None
        plain = mf.parse_plain_index(manifest, plain_stored, content)
        return LoadedV2(manifest, manifest_bytes, index, index_bytes, plain_stored, plain, content, None, "digest-only")
    if key is None:
        if require_key:
            raise KeyRequiredError("archive is encrypted; supply the key (--key-file or VNXDNA_KEY)")
        return LoadedV2(manifest, manifest_bytes, index, index_bytes, plain_stored, None, None, None, "not-verified (no key)")
    keys = crypto.ArchiveKeys.derive(key, bytes.fromhex(manifest.encryption.salt or ""))
    if not crypto.mac_equal(manifest.encryption.key_check or "", keys.check.hex()):
        if not digest_ok:
            raise AuthenticationError("manifest authentication failed: manifest corrupted or tampered (key check also mismatched)")
        raise WrongKeyError("wrong key: the key-check value does not match this archive")
    if not crypto.mac_equal(manifest.seal.manifest_hmac_sha256 or "", crypto.mac(keys, payload)):
        raise AuthenticationError("manifest authentication failed: HMAC mismatch (manifest tampered or corrupted)")
    cipher = crypto.ChunkCipher(keys, bytes.fromhex(manifest.archive_id), manifest.chunk_count)
    final_epoch = _final_epoch_of(manifest, index)
    content = mf.parse_content(_open_final(cipher, crypto.DOMAIN_CONTENT, manifest.sealed_bytes(), final_epoch))
    mf.check_content(manifest, content)
    plain = mf.parse_plain_index(manifest, _open_final(cipher, crypto.DOMAIN_PLAIN_INDEX, plain_stored, final_epoch), content)
    return LoadedV2(manifest, manifest_bytes, index, index_bytes, plain_stored, plain, content, cipher, "hmac-sha256")


def final_seal_epoch(index: np.ndarray) -> int:
    """The newest chunk AEAD epoch (0 for a store that was never resumed)."""
    return int(index["epoch"].max()) if index.size else 0


def _final_epoch_of(manifest: mf.Manifest, index: np.ndarray) -> int:
    """AEAD epoch of the sealed content record and plaintext index.

    Epoch 0 unless the manifest declares ``final-seal-epoch-v3`` (VNX-DNA 3, resumed encrypted stores), in which
    case it is the newest chunk epoch from the authenticated chunk index. VNX-DNA 2.0 always used 0.
    """
    return final_seal_epoch(index) if mf.FEATURE_FINAL_SEAL_EPOCH in manifest.required_features else 0


def _open_final(cipher: crypto.ChunkCipher, domain: int, ciphertext: bytes, epoch: int) -> bytes:
    return cipher.open(domain, 0, ciphertext, count=1, epoch=epoch)


def check_body_length(cf: ContainerFileV2, loaded: LoadedV2) -> None:
    """The container body must be exactly the stored chunks the authenticated index describes.

    The index tiles ``[0, stored_size)`` contiguously and every chunk carries a
    SHA-256, so with this check every body byte is covered by a verified hash
    (VNX-DNA 2.0 did not compare the lengths, so padding after the last chunk went unnoticed).
    """
    if cf.body_bytes != loaded.manifest.stored_size:
        raise MetadataError(f"container body is {cf.body_bytes} bytes but the manifest records stored_size "
                            f"{loaded.manifest.stored_size} (padded, truncated or spliced container)")


def open_container(path: str | os.PathLike, key: bytes | None, *, require_key: bool = True) -> tuple[ContainerFileV2, LoadedV2]:
    cf = ContainerFileV2.open(path)
    try:
        loaded = load(cf.manifest_bytes, cf.index_bytes, cf.plain_bytes, key, require_key=require_key)
        check_body_length(cf, loaded)
    except BaseException:
        cf.close()
        raise
    return cf, loaded


# ======================================================================= per-chunk transforms
_local = threading.local()


def _compressor(algorithm: str, level: int):
    cache = getattr(_local, "compressors", None)
    if cache is None:
        cache = _local.compressors = {}
    if (algorithm, level) not in cache:
        cache[(algorithm, level)] = zstandard.ZstdCompressor(level=level, write_content_size=True, write_checksum=False)
    return cache[(algorithm, level)]


def seal_chunk(plain: bytes, index: int, algorithm: str, level: int, cipher: crypto.ChunkCipher | None,
               epoch: int = 0) -> tuple[bytes, int, bytes, bytes]:
    """plaintext → (stored bytes, codec, plaintext SHA-256, stored SHA-256). Compression is kept only if it helps."""
    plain_sha = hashlib.sha256(plain).digest()
    data, codec = plain, mf.CODEC_NONE
    if algorithm == "zstd" and plain:
        packed = _compressor(algorithm, level).compress(plain)
        if len(packed) < len(plain):
            data, codec = packed, mf.CODEC_ZSTD
    elif algorithm == "zlib" and plain:
        packed = zlib.compress(plain, level)
        if len(packed) < len(plain):
            data, codec = packed, mf.CODEC_ZLIB
    if cipher is not None:
        data = cipher.seal(crypto.DOMAIN_CHUNK, index, data, epoch=epoch)
    return data, codec, plain_sha, hashlib.sha256(data).digest()


def check_stored(loaded: LoadedV2, c: int, stored: bytes) -> None:
    record = loaded.index[c]
    if len(stored) != int(record["stored_size"]) or hashlib.sha256(stored).digest() != record["stored_sha256"].tobytes():
        raise IntegrityError(f"chunk {c}: stored bytes do not match the index SHA-256", details={"chunk": c})


def open_chunk(loaded: LoadedV2, c: int, stored: bytes, *, stored_checked: bool = False) -> bytes:
    """Verified plaintext of chunk ``c``: stored SHA-256 → AES-GCM → bounded decompression → plaintext SHA-256."""
    if loaded.plain is None:
        raise KeyRequiredError("archive is encrypted; a key is required to recover plaintext")
    if not stored_checked:
        check_stored(loaded, c, stored)
    data = loaded.cipher.open(crypto.DOMAIN_CHUNK, c, stored, epoch=int(loaded.index[c]["epoch"])) if loaded.cipher is not None else stored
    size = int(loaded.plain[c]["size"])
    codec = int(loaded.index[c]["codec"])
    plain = data if codec == mf.CODEC_NONE else decompress(data, mf.CODEC_NAMES[codec], size)
    if len(plain) != size or hashlib.sha256(plain).digest() != loaded.plain[c]["sha256"].tobytes():
        raise IntegrityError(f"chunk {c}: plaintext SHA-256 mismatch", details={"chunk": c})
    return plain


def ordered_map(fn: Callable, items: Iterable, workers: int) -> Iterator:
    """``map`` over a thread pool with at most ``2 × workers`` items in flight, results in input order."""
    if workers <= 1:
        for item in items:
            yield fn(item)
        return
    window = 2 * workers
    with ThreadPoolExecutor(workers) as pool:
        pending: deque = deque()
        for item in items:
            pending.append(pool.submit(fn, item))
            if len(pending) >= window:
                yield pending.popleft().result()
        while pending:
            yield pending.popleft().result()


def verified_plaintext(loaded: LoadedV2, stored_chunks: Iterable[tuple[int, bytes]], workers: int) -> Iterator[tuple[int, bytes]]:
    """(chunk, stored) pairs → (chunk, verified plaintext), in order, bounded memory."""
    return ordered_map(lambda item: (item[0], open_chunk(loaded, item[0], item[1])), stored_chunks, workers)


class AtomicOutput:
    """Streams bytes into a same-directory temporary file and renames it into place on success only."""

    def __init__(self, target: str | os.PathLike, *, overwrite: bool = False):
        self.target = Path(target)
        if self.target.exists() and self.target.is_dir():
            raise OutputError(f"output is a directory: {self.target}")
        if self.target.exists() and not overwrite:
            raise OutputError(f"output already exists: {self.target} (use --force to overwrite)")
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="." + self.target.name + ".", suffix=".partial", dir=self.target.parent)
        except OSError as error:
            raise OutputError(f"cannot write {self.target}: {error.strerror or error}") from None
        self.tmp = Path(tmp)
        self.overwrite = overwrite
        self.handle = os.fdopen(fd, "wb")
        self.hash = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> None:
        self.handle.write(data)
        self.hash.update(data)
        self.size += len(data)

    def commit(self) -> None:
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        publish(self.tmp, self.target, overwrite=self.overwrite)
        fsync_dir(self.target.parent)

    def abort(self) -> None:
        try:
            self.handle.close()
        finally:
            self.tmp.unlink(missing_ok=True)

    def __enter__(self) -> "AtomicOutput":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.abort()


def write_verified_plaintext(loaded: LoadedV2, chunks: Iterator[tuple[int, bytes]], output: str | os.PathLike, *,
                             overwrite: bool) -> dict[str, Any]:
    """Write every chunk's plaintext to ``output`` and publish it only if the whole-object SHA-256 matches."""
    assert loaded.content is not None
    expected = 0
    with AtomicOutput(output, overwrite=overwrite) as out:
        for c, plain in chunks:
            if c != expected:
                raise IntegrityError(f"chunk {expected} is missing from the recovered stream")
            out.write(plain)
            expected += 1
        if expected != loaded.manifest.chunk_count:
            raise IntegrityError(f"only {expected} of {loaded.manifest.chunk_count} chunks were recovered")
        recomputed = out.hash.hexdigest()
        if recomputed != loaded.content.sha256 or out.size != loaded.content.size:
            raise IntegrityError("whole-object SHA-256 mismatch after reconstruction",
                                 details={"expected_sha256": loaded.content.sha256, "recovered_sha256": recomputed})
        out.commit()
    return {"size": out.size, "expected_sha256": loaded.content.sha256, "recovered_sha256": recomputed, "sha256_match": True,
            "name": loaded.content.name}


# ======================================================================= store
def _options_digest(options: StoreOptionsV2) -> str:
    return hashlib.sha256(mf.canonical_bytes(options.public_dict())).hexdigest()


def _derive_archive_id(data_sha: bytes, options: StoreOptionsV2, name: str | None) -> bytes:
    material = mf.canonical_bytes({"options": options.public_dict(), "name": name, "format_version": mf.FORMAT_VERSION})
    return hashlib.sha256(b"VNX-DNA/5 archive-id\x00" + material + data_sha).digest()[:16]


def _sha_prefix(path: Path, length: int) -> str:
    h = hashlib.sha256()
    remaining = length
    with path.open("rb") as handle:
        while remaining:
            block = handle.read(min(4 << 20, remaining))
            if not block:
                raise InvalidInputError(f"{path} is shorter than its checkpoint records")
            h.update(block)
            remaining -= len(block)
    return h.hexdigest()


_CHECKPOINT_MAC_LABEL = b"VNX-DNA/5 store checkpoint\x00"


def _checkpoint_mac(keys: "crypto.ArchiveKeys | None", body: dict[str, Any]) -> str | None:
    """HMAC-SHA256 of the checkpoint under the archive MAC key (encrypted stores only).

    The checkpoint records the AEAD epoch; an unkeyed digest alone would let
    anyone with write access roll the epoch back and make a resume reuse nonces.
    """
    return crypto.mac(keys, _CHECKPOINT_MAC_LABEL + mf.canonical_bytes(body)) if keys is not None else None


def _write_checkpoint(path: Path, state: dict[str, Any], keys: "crypto.ArchiveKeys | None" = None) -> None:
    body = {k: v for k, v in state.items() if k not in ("checkpoint_sha256", "checkpoint_hmac")}
    state = {**body, "checkpoint_sha256": hashlib.sha256(mf.canonical_bytes(body)).hexdigest(),
             "checkpoint_hmac": _checkpoint_mac(keys, body)}
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="ascii") as handle:
        handle.write(json.dumps(state, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_checkpoint(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise InvalidInputError(f"checkpoint {path} is unreadable: {error}") from None
    if isinstance(state, dict) and state.get("format") == "vnx-store-checkpoint-1":
        raise InvalidInputError(f"{path} was written by VNX-DNA 2.0, whose checkpoints are not authenticated; "
                                "run without --resume to start over")
    if not isinstance(state, dict) or state.get("format") != CHECKPOINT_FORMAT:
        raise InvalidInputError(f"{path} is not a VNX-DNA store checkpoint")
    body = {k: v for k, v in state.items() if k not in ("checkpoint_sha256", "checkpoint_hmac")}
    if hashlib.sha256(mf.canonical_bytes(body)).hexdigest() != state.get("checkpoint_sha256"):
        raise InvalidInputError(f"checkpoint {path} is corrupted (its SHA-256 does not match)")
    return state


def _max_sidecar_epoch(path: Path) -> int:
    """Largest AEAD epoch among all complete entries of an index sidecar (including those past the checkpoint)."""
    data = path.read_bytes()
    n = len(data) // SIDECAR_ENTRY
    if not n:
        return 0
    entries = np.frombuffer(data[: n * SIDECAR_ENTRY], dtype=np.uint8).reshape(n, SIDECAR_ENTRY)
    return int(entries[:, :mf.INDEX_DTYPE.itemsize].copy().view(mf.INDEX_DTYPE)["epoch"].max())


def store_file(input_path: str | os.PathLike, output_path: str | os.PathLike, *, options: StoreOptionsV2 = StoreOptionsV2(),
               key: bytes | None = None, overwrite: bool = False, resume: bool = False, workers: int = 0,
               checkpoint_interval: int = 64, progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Regular file → format-5 container, streaming, bounded memory, resumable."""
    started = time.perf_counter()
    geometry = options.validate()
    workers = workers or default_workers()
    if not isinstance(checkpoint_interval, int) or checkpoint_interval < 1:
        raise ConfigurationError("checkpoint_interval must be a positive integer")
    src = Path(input_path)
    if not src.is_file():
        raise InvalidInputError(f"input file not found (or not a regular file): {src}")
    st = src.stat()
    size = st.st_size
    cs = options.chunk_size
    n = max(1, -(-size // cs))
    # The container trailer stores the chunk-index length in 32 bits, which caps the chunk count below MAX_CHUNKS
    # (VNX-DNA 2.0 found this out only in finish(), after the whole input had been processed).
    limit = min(mf.MAX_CHUNKS, 0xFFFFFFFF // mf.INDEX_DTYPE.itemsize)
    if n > limit:
        raise ConfigurationError(f"{n} chunks exceed the format limit of {limit}; use a larger chunk size")
    # a name that is not valid UTF-8 (surrogate escapes from the OS) is stored with U+FFFD replacements
    name = _safe_name(src.name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")) if options.store_name else None
    stripe_bytes = options.data_shards * options.payload_bytes

    writer = ContainerWriter(output_path, overwrite=overwrite)
    ckpt_path = writer.partial.with_name(writer.partial.name + ".ckpt")
    sidecar_path = writer.partial.with_name(writer.partial.name + ".idx")
    options_sha = _options_digest(options)
    start_chunk = 0
    discarded_partial = False
    plain_hash = hashlib.sha256()
    next_stripe = 0
    epoch = 0  # AEAD epoch of the chunks this run seals; every resume uses a new one (see vnxdna.v2.crypto)
    resumed = False

    if resume and ckpt_path.exists():
        state = _read_checkpoint(ckpt_path)
        reasons = []
        if state["input"] != {"path": str(src.resolve()), "size": size, "mtime_ns": st.st_mtime_ns}:
            reasons.append("the input file changed (path, size or modification time differ)")
        if state["options_sha256"] != options_sha:
            reasons.append("the archive options differ from the interrupted run")
        if state["encrypted"] != (key is not None):
            reasons.append("encryption differs from the interrupted run")
        if state["chunk_count"] != n:
            reasons.append("the chunk count differs")
        if reasons:
            raise InvalidInputError("cannot resume: " + "; ".join(reasons) + ". Run without --resume to start over.")
        if key is not None:
            salt = bytes.fromhex(state["salt"])
            archive_id = bytes.fromhex(state["archive_id"])
            keys = crypto.ArchiveKeys.derive(key, salt)
            if keys.check.hex() != state["key_check"]:
                raise WrongKeyError("cannot resume: the key differs from the interrupted run")
            body = {k: v for k, v in state.items() if k not in ("checkpoint_sha256", "checkpoint_hmac")}
            if not crypto.mac_equal(str(state.get("checkpoint_hmac") or ""), _checkpoint_mac(keys, body) or ""):
                raise InvalidInputError("cannot resume: the checkpoint is not authenticated by this key "
                                        "(modified, or written by an older version). Run without --resume to start over.")
        done, body_bytes = state["chunks_done"], state["body_bytes"]
        if not sidecar_path.exists() or sidecar_path.stat().st_size < done * SIDECAR_ENTRY \
                or _sha_prefix(sidecar_path, done * SIDECAR_ENTRY) != state["sidecar_sha256"]:
            raise InvalidInputError("cannot resume: the index sidecar does not match the checkpoint")
        if key is not None:
            # The new epoch must exceed every epoch already used, not only the checkpoint's: an older (still authentic)
            # checkpoint may have been put back after a later resume sealed more chunks. Those chunks' sidecar entries
            # lie beyond the checkpoint's prefix and are about to be truncated; their epochs are read first.
            epoch = max(int(state.get("epoch", 0)), _max_sidecar_epoch(sidecar_path)) + 1
            if epoch > crypto.MAX_EPOCH:
                raise InvalidInputError(f"cannot resume: this archive was resumed {crypto.MAX_EPOCH} times; run without --resume")
        with sidecar_path.open("r+b") as handle:
            handle.truncate(done * SIDECAR_ENTRY)
            side = handle.read()
        entries = np.frombuffer(side, dtype=np.uint8).reshape(done, SIDECAR_ENTRY) if done else np.zeros((0, SIDECAR_ENTRY), np.uint8)
        index_part = entries[:, :mf.INDEX_DTYPE.itemsize].copy().view(mf.INDEX_DTYPE).reshape(done)
        plain_part = entries[:, mf.INDEX_DTYPE.itemsize:].copy().view(mf.PLAIN_DTYPE).reshape(done)
        if done and (int(index_part["offset"][-1]) + int(index_part["stored_size"][-1]) != body_bytes):
            raise InvalidInputError("cannot resume: checkpoint body length and index disagree")
        writer.resume_at(body_bytes)
        with src.open("rb") as handle:
            for c in range(done):
                record = index_part[c]
                stored = writer.read_body(int(record["offset"]), int(record["stored_size"]))
                if hashlib.sha256(stored).digest() != record["stored_sha256"].tobytes():
                    writer.close(remove=False)
                    raise InvalidInputError(f"cannot resume: chunk {c} of the partial archive is corrupted")
                plain = handle.read(cs)
                if hashlib.sha256(plain).digest() != plain_part[c]["sha256"].tobytes():
                    writer.close(remove=False)
                    raise InvalidInputError(f"cannot resume: chunk {c} of the input no longer matches the partial archive")
                plain_hash.update(plain)
        start_chunk = done
        resumed = True
        next_stripe = int(index_part["first_stripe"][-1]) + int(index_part["stripe_count"][-1]) if done else 0
    else:
        if writer.partial.exists() or ckpt_path.exists():
            discarded_partial = True
        for stale in (writer.partial, ckpt_path, sidecar_path):
            stale.unlink(missing_ok=True)
        if key is not None:
            salt, archive_id = crypto.new_salt(), os.urandom(16)
            keys = crypto.ArchiveKeys.derive(key, salt)
        writer.start_fresh()
        sidecar_path.write_bytes(b"")

    cipher = crypto.ChunkCipher(keys, archive_id, n) if key is not None else None
    base_state = {"format": CHECKPOINT_FORMAT, "input": {"path": str(src.resolve()), "size": size, "mtime_ns": st.st_mtime_ns},
                  "options_sha256": options_sha, "encrypted": key is not None, "chunk_count": n,
                  "archive_id": archive_id.hex() if key is not None else None,
                  "salt": salt.hex() if key is not None else None,
                  "key_check": keys.check.hex() if key is not None else None, "epoch": epoch}
    sidecar = sidecar_path.open("ab")
    compressed_chunks = 0

    def checkpoint(done: int) -> None:
        writer.flush()
        sidecar.flush()
        os.fsync(sidecar.fileno())
        _write_checkpoint(ckpt_path, {**base_state, "chunks_done": done, "body_bytes": writer.body_bytes,
                                      "sidecar_sha256": _sha_prefix(sidecar_path, done * SIDECAR_ENTRY)},
                          keys if key is not None else None)

    if resumed:
        # Persist the new AEAD epoch before anything is sealed with it. Otherwise an interruption before the next
        # periodic checkpoint would leave the old epoch on disk, and a second resume would reuse this run's nonces.
        checkpoint(start_chunk)

    def chunks() -> Iterator[tuple[int, bytes]]:
        with src.open("rb") as handle:
            handle.seek(start_chunk * cs)
            for c in range(start_chunk, n):
                want = min(cs, size - c * cs) if size else 0
                plain = handle.read(want)
                if len(plain) != want:
                    raise InvalidInputError(f"{src} changed while it was being read (short read at chunk {c})")
                plain_hash.update(plain)
                yield c, plain

    try:
        done = start_chunk
        work = ordered_map(lambda item: (item[0], seal_chunk(item[1], item[0], options.compression, options.compression_level, cipher,
                                                             epoch)), chunks(), workers)
        for c, (stored, codec, plain_sha, stored_sha) in work:
            offset = writer.write_chunk(stored)
            stripes = -(-len(stored) // stripe_bytes)
            entry = np.zeros(1, dtype=mf.INDEX_DTYPE)
            entry["offset"], entry["stored_size"], entry["first_stripe"], entry["stripe_count"] = offset, len(stored), next_stripe, stripes
            entry["codec"] = codec
            entry["epoch"] = epoch if cipher is not None else 0
            entry["stored_sha256"] = np.frombuffer(stored_sha, dtype=np.uint8)
            plain_entry = np.zeros(1, dtype=mf.PLAIN_DTYPE)
            plain_entry["size"] = min(cs, size - c * cs) if size else 0
            plain_entry["sha256"] = np.frombuffer(plain_sha, dtype=np.uint8)
            sidecar.write(entry.tobytes() + plain_entry.tobytes())
            next_stripe += stripes
            compressed_chunks += codec != mf.CODEC_NONE
            done = c + 1
            if done % checkpoint_interval == 0 and done < n:
                checkpoint(done)
            if progress is not None:
                progress(done, n)
        if next_stripe >= 1 << 32:
            raise ConfigurationError("archive exceeds 2^32 ECC stripes; use a larger payload or more data shards")
        after = src.stat()
        if after.st_size != size or after.st_mtime_ns != st.st_mtime_ns:
            raise InvalidInputError(f"{src} was modified while it was being stored; the archive was discarded")
        sidecar.flush()
        sidecar.close()
        side = sidecar_path.read_bytes()
        entries = np.frombuffer(side, dtype=np.uint8).reshape(n, SIDECAR_ENTRY)
        index_bytes = entries[:, :mf.INDEX_DTYPE.itemsize].tobytes()
        plain_clear = entries[:, mf.INDEX_DTYPE.itemsize:].tobytes()
        data_sha = plain_hash.digest()
        if key is None:
            archive_id = _derive_archive_id(data_sha, options, name)
        content = {"name": name, "size": size, "sha256": data_sha.hex()}
        # The sealed records use the newest chunk epoch, so a resume that re-finalises a changed input never
        # reuses the nonce of an earlier finalisation (readers derive the same epoch from the authenticated index).
        final_epoch = final_seal_epoch(entries[:, :mf.INDEX_DTYPE.itemsize].copy().view(mf.INDEX_DTYPE).reshape(n))
        plain_stored = (cipher.seal(crypto.DOMAIN_PLAIN_INDEX, 0, plain_clear, count=1, epoch=final_epoch)
                        if cipher is not None else plain_clear)
        raw: dict[str, Any] = {
            "format": mf.FORMAT_MAGIC, "format_version": mf.FORMAT_VERSION,
            "encoder": {"name": "vnxdna", "version": __version__},
            "required_features": sorted(mf.required_features(options.mapping, key is not None, final_epoch > 0)),
            "archive_id": archive_id.hex(), "created_at": options.timestamp, "profile": options.profile,
            "chunk_size": cs, "chunk_count": n,
            "compression": {"algorithm": options.compression, "level": options.compression_level, "policy": "auto"},
            "encryption": {"algorithm": "AES-256-GCM" if key is not None else "none",
                           "kdf": "HKDF-SHA256" if key is not None else None,
                           "salt": salt.hex() if key is not None else None,
                           "key_check": keys.check.hex() if key is not None else None},
            "stored_size": writer.body_bytes, "stored_sha256": writer.body_hash.hexdigest(),
            "chunk_index": {"format": "vnx-chunk-index-1", "entry_bytes": 56, "entries": n,
                            "sha256": hashlib.sha256(index_bytes).hexdigest()},
            "plain_index": {"format": "vnx-plain-index-1", "entry_bytes": 36, "sealed": key is not None,
                            "stored_bytes": len(plain_stored), "sha256": hashlib.sha256(plain_stored).hexdigest()},
            "content": None if key is not None else content,
            "sealed_content": (base64.b64encode(cipher.seal(crypto.DOMAIN_CONTENT, 0, mf.canonical_bytes(content), count=1,
                                                             epoch=final_epoch)).decode("ascii")
                               if cipher is not None else None),
            "erasure_code": {"algorithm": "cauchy-rs-gf256", "data_shards": options.data_shards,
                             "parity_shards": options.parity_shards, "stripe_count": next_stripe},
            "strand": {"frame_format": 5, "mapping": geometry.mapping, "payload_bytes": geometry.payload_bytes,
                       "inner_ecc": "reed-solomon-gf256", "inner_parity_bytes": geometry.inner_parity_bytes, "crc": "crc32",
                       "scrambler": "shake128", "header_bytes": 11, "frame_bytes": geometry.frame_bytes,
                       "strand_nt": geometry.strand_nt},
            "constraints": options.constraints.to_dict(),
            "dna_manifest": {"enabled": True, "scheme": "cauchy-rs-gf256-8+8"},
            "extensions": {},
        }
        payload = mf.digest_payload(raw)
        raw["seal"] = {"manifest_sha256": hashlib.sha256(payload).hexdigest(),
                       "manifest_hmac_sha256": crypto.mac(keys, payload) if key is not None else None}
        manifest = mf.validate(raw)
        mf.parse_chunk_index(manifest, index_bytes)  # self-check before publishing
        manifest_bytes = mf.canonical_bytes(raw)
        published = writer.finish(manifest_bytes, index_bytes, plain_stored)
    except VNXDNAError:
        sidecar.close()
        writer.close(remove=True)
        ckpt_path.unlink(missing_ok=True)
        sidecar_path.unlink(missing_ok=True)
        raise
    except BaseException:
        # interruption (Ctrl-C, disk error ...): keep the partial file and the last checkpoint for --resume;
        # without a checkpoint nothing can be resumed, so the partial files are removed
        sidecar.close()
        resumable = ckpt_path.exists()
        writer.close(remove=not resumable)
        if not resumable:
            sidecar_path.unlink(missing_ok=True)
        raise
    ckpt_path.unlink(missing_ok=True)
    sidecar_path.unlink(missing_ok=True)
    elapsed = time.perf_counter() - started
    return {"status": "SUCCESS", "operation": "store", "format_version": 5, "output": str(output_path),
            "archive_id": manifest.archive_id, "profile": options.profile, "encrypted": manifest.encrypted,
            "original_bytes": size, "original_sha256": data_sha.hex(), "stored_bytes": manifest.stored_size,
            "container_bytes": published["container_bytes"], "chunks": n, "chunk_size": cs,
            "chunks_compressed": compressed_chunks + (_count_compressed(index_bytes, start_chunk) if start_chunk else 0),
            "compression": options.compression, "compression_ratio": (manifest.stored_size / size) if size else None,
            "ecc_stripes": next_stripe, "resumed_from_chunk": start_chunk, "discarded_stale_partial": discarded_partial,
            "workers": workers, "elapsed_s": elapsed, "throughput_mb_s": size / elapsed / 1e6 if elapsed else None}


def _count_compressed(index_bytes: bytes, upto: int) -> int:
    index = np.frombuffer(index_bytes, dtype=mf.INDEX_DTYPE)
    return int((index["codec"][:upto] != mf.CODEC_NONE).sum())


# ======================================================================= restore / extract / verify
def container_stored_chunks(cf: ContainerFileV2, loaded: LoadedV2, chunks: Iterable[int],
                            stats: dict[str, int] | None = None) -> Iterator[tuple[int, bytes]]:
    for c in chunks:
        record = loaded.index[c]
        data = cf.read_stored(int(record["offset"]), int(record["stored_size"]))
        if stats is not None:
            stats["bytes_read"] = stats.get("bytes_read", 0) + len(data)
            stats["chunks_read"] = stats.get("chunks_read", 0) + 1
        yield c, data


def restore_file(path: str | os.PathLike, output_path: str | os.PathLike, *, key: bytes | None = None, workers: int = 0,
                 overwrite: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    workers = workers or default_workers()
    cf, loaded = open_container(path, key)
    try:
        stats: dict[str, int] = {}
        chunks = verified_plaintext(loaded, container_stored_chunks(cf, loaded, range(loaded.manifest.chunk_count), stats), workers)
        result = write_verified_plaintext(loaded, chunks, output_path, overwrite=overwrite)
    finally:
        cf.close()
    elapsed = time.perf_counter() - started
    return {"status": "SUCCESS", "operation": "restore", "input": str(path), "input_kind": "container-v2", "output": str(output_path),
            "archive_id": loaded.manifest.archive_id, "encrypted": loaded.manifest.encrypted,
            "manifest_authentication": loaded.authentication, **result, "output_sha256": result["recovered_sha256"],
            "chunks_verified": loaded.manifest.chunk_count, "elapsed_s": elapsed,
            "throughput_mb_s": result["size"] / elapsed / 1e6 if elapsed else None}


def extract_range(path: str | os.PathLike, output_path: str | os.PathLike, *, offset: int, length: int | None,
                  key: bytes | None = None, overwrite: bool = False) -> dict[str, Any]:
    """Random access on a container: read and verify only the chunks that cover [offset, offset+length)."""
    started = time.perf_counter()
    cf, loaded = open_container(path, key)
    try:
        size = loaded.content.size
        end = size if length is None else offset + length
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or not offset <= end <= size:
            raise InvalidInputError(f"invalid byte range offset={offset} length={length} for an object of {size} bytes")
        wanted = loaded.chunk_range(offset, end)
        stats: dict[str, int] = {}
        with AtomicOutput(output_path, overwrite=overwrite) as out:
            cs = loaded.manifest.chunk_size
            for c, plain in verified_plaintext(loaded, container_stored_chunks(cf, loaded, wanted, stats), 1):
                lo = max(offset - c * cs, 0)
                hi = min(end - c * cs, len(plain))
                out.write(plain[lo:hi])
            out.commit()
    finally:
        cf.close()
    elapsed = time.perf_counter() - started
    return {"status": "SUCCESS", "operation": "extract", "input": str(path), "input_kind": "container-v2", "output": str(output_path),
            "archive_id": loaded.manifest.archive_id, "range": {"offset": offset, "length": end - offset},
            "bytes": out.size, "output_sha256": out.hash.hexdigest(), "chunks_processed": wanted,
            "chunks_total": loaded.manifest.chunk_count, "container_bytes_read": stats.get("bytes_read", 0),
            "object_bytes": size, "elapsed_s": elapsed}


def verify_container(path: str | os.PathLike, *, key: bytes | None = None, against: str | os.PathLike | None = None,
                     workers: int = 0) -> dict[str, Any]:
    """Independent, streaming verification of a version-2 container (and optionally of a recovered file)."""
    workers = workers or default_workers()
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {"operation": "verify", "input": str(path), "input_kind": "container-v2", "checks": checks}

    def check(name: str, ok: bool, detail: str | None = None) -> None:
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", **({"detail": detail} if detail else {})})

    try:
        cf = ContainerFileV2.open(path)
    except VNXDNAError as error:
        check("container-structure", False, f"{error.category}: {error}")
        return {**report, "status": "FAIL", "error": error.category, "exit_code": error.exit_code, "message": str(error)}
    try:
        trailer_ok, body_sha = cf.verify_trailer_and_body()
        check("container-structure", True, f"{cf.size} bytes, body {cf.body_bytes} bytes")
        check("container-checksum", trailer_ok, "file SHA-256 matches the trailer" if trailer_ok else "file trailer SHA-256 mismatch")
        try:
            loaded = load(cf.manifest_bytes, cf.index_bytes, cf.plain_bytes, key, require_key=False)
            check_body_length(cf, loaded)
        except VNXDNAError as error:
            check("manifest-and-index", False, f"{error.category}: {error}")
            return {**report, "status": "FAIL", "error": error.category, "exit_code": error.exit_code, "message": str(error)}
        m = loaded.manifest
        body_ok = body_sha == m.stored_sha256
        check("stored-body-sha256", body_ok, "body SHA-256 matches the manifest's stored_sha256" if body_ok
              else "body SHA-256 differs from the manifest's stored_sha256")
        report.update({"archive_id": m.archive_id, "format_version": 5, "encrypted": m.encrypted, "chunks": m.chunk_count})
        check("manifest-and-index", True, f"schema, canonical form and digest; chunk index ({m.chunk_count} entries) consistent")
        auth = loaded.authentication
        check("manifest-authentication", auth != "not-verified (no key)",
              {"hmac-sha256": "HMAC-SHA256 verified (covers the manifest and both index tables)",
               "digest-only": "unencrypted archive: SHA-256 digest only (detects corruption, not tampering)",
               "not-verified (no key)": "encrypted archive: supply the key to authenticate"}[auth])
        failures: list[dict[str, Any]] = []
        stored_ok = 0
        plain_ok = 0
        digest = hashlib.sha256()
        size = 0

        def work(c: int) -> tuple[int, bytes | None, str | None, str | None]:
            record = loaded.index[c]
            try:
                stored = cf.read_stored(int(record["offset"]), int(record["stored_size"]))
                check_stored(loaded, c, stored)
            except VNXDNAError as error:
                return c, None, "stored", f"{error.category}: {error}"
            if loaded.plain is None:
                return c, b"", None, None
            try:
                return c, open_chunk(loaded, c, stored, stored_checked=True), None, None
            except VNXDNAError as error:
                return c, None, "plaintext", f"{error.category}: {error}"

        for c, plain, layer, message in ordered_map(work, range(m.chunk_count), workers):
            if layer == "stored":
                failures.append({"chunk": c, "layer": "stored", "message": message})
                continue
            stored_ok += 1
            if loaded.plain is None:
                continue
            if layer == "plaintext":
                failures.append({"chunk": c, "layer": "plaintext", "message": message})
                continue
            plain_ok += 1
            digest.update(plain)
            size += len(plain)
        check("stored-chunks", stored_ok == m.chunk_count, f"{stored_ok}/{m.chunk_count} stored chunks match their SHA-256")
        if loaded.plain is not None:
            check("plaintext-chunks", plain_ok == m.chunk_count,
                  f"{plain_ok}/{m.chunk_count} chunks authenticated, decompressed and SHA-256 verified")
            if plain_ok == m.chunk_count:
                recomputed = digest.hexdigest()
                match = recomputed == loaded.content.sha256 and size == loaded.content.size
                check("object-sha256", match, f"recomputed {recomputed}")
                report.update({"original_size": loaded.content.size, "recovered_size": size, "expected_sha256": loaded.content.sha256,
                               "recovered_sha256": recomputed})
            if against is not None:
                report["file_comparison"] = compare_file(loaded, against)
                fc = report["file_comparison"]
                check("file-matches-archive", fc["identical"],
                      "file is byte-identical to the archived object" if fc["identical"] else
                      f"{len(fc['mismatched_chunks'])} chunk(s) differ; size {fc['file_size']} vs {fc['expected_size']}")
        else:
            report["note"] = "encrypted archive verified at the ciphertext level only; supply the key to verify plaintext"
            if against is not None:  # VNX-DNA 2.0 ignored --file here silently
                check("file-matches-archive", False, "not checked: the plaintext index of an encrypted archive is sealed; supply the key")
        report["failures"] = failures[:100]
        report["failure_count"] = len(failures)
    finally:
        cf.close()
    ok = all(c["result"] == "PASS" for c in checks)
    report["status"] = "PASS" if ok else "FAIL"
    key_missing = report.get("encrypted") and "note" in report
    needs_key_only = {"manifest-authentication", "file-matches-archive"}
    damaged = any(c["result"] == "FAIL" and c["check"] not in needs_key_only for c in checks)
    if ok:
        report["exit_code"] = 0
    elif any("AUTHENTICATION" in (f.get("message") or "") for f in failures):
        report["exit_code"] = 4
    elif key_missing and not damaged:
        report["exit_code"] = 4  # everything checkable without the key passed; authentication needs the key (KEY_REQUIRED)
    else:
        report["exit_code"] = 1
    return report


def compare_file(loaded: LoadedV2, path: str | os.PathLike) -> dict[str, Any]:
    """Stream a file and compare it chunk by chunk with the plaintext index (never loads it whole)."""
    p = Path(path)
    if not p.is_file():
        raise InvalidInputError(f"file to compare not found: {p}")
    cs = loaded.manifest.chunk_size
    mismatched = []
    h = hashlib.sha256()
    size = 0
    with p.open("rb") as handle:
        for c in range(loaded.manifest.chunk_count):
            block = handle.read(int(loaded.plain[c]["size"]) if c < loaded.manifest.chunk_count - 1 else cs)
            h.update(block)
            size += len(block)
            if hashlib.sha256(block).digest() != loaded.plain[c]["sha256"].tobytes() or len(block) != int(loaded.plain[c]["size"]):
                mismatched.append(c)
        while True:
            extra = handle.read(4 << 20)
            if not extra:
                break
            h.update(extra)
            size += len(extra)
    digest = h.hexdigest()
    identical = not mismatched and digest == loaded.content.sha256 and size == loaded.content.size
    return {"file": str(p), "file_size": size, "expected_size": loaded.content.size, "file_sha256": digest,
            "expected_sha256": loaded.content.sha256, "mismatched_chunks": mismatched[:100], "identical": identical}
