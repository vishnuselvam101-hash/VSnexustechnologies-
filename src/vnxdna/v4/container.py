"""VNX4 container file (``.vnx``): binary layout, streaming writer and validating reader.

Layout (all integers big-endian; normative spec: docs/VNX4_FORMAT.md)::

    header      16 B   magic "\\x89VNX4\\r\\n\\x1a" (8) | major (2) = 4 | minor (2) = 0 | flags (4) = 0
    body         B     stored chunks, concatenated in chunk-table order
    chunk table  C     84 B per stored chunk
    file table   F     variable-length file records, sorted by path (AES-GCM sealed when encrypted)
    refs         R     4 B per chunk reference (u32 chunk index), file order (sealed when encrypted)
    manifest     M     canonical JSON (≤ 1 MiB)
    trailer    112 B   B (8) | C (8) | F (8) | R (8) | M (8) | manifest MAC (32) | "VNX4END\\0" (8) | SHA-256 of all preceding bytes (32)

The writer streams the body, keeps the tables in memory (84 B per unique chunk
plus the file records) and writes everything else at the end, so one pass over
the input suffices and memory does not depend on the content size.
"""
from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import numpy as np

from . import crypto, merkle
from .errors import (VNXFormatError, VNXIntegrityError, VNXKeyError, VNXResourceError, VNXUnsupportedVersionError)
from .util import canonical_json, parse_canonical_json
from .version import FORMAT_VERSION, __version__

MAGIC = b"\x89VNX4\r\n\x1a"
HEADER_BYTES = 16
TRAILER_BYTES = 112
TRAILER_MAGIC = b"VNX4END\x00"
CHUNK_ENTRY_BYTES = 84
MAX_MANIFEST_BYTES = 1 << 20
MAX_CHUNK_SIZE = 64 << 20
MIN_CHUNK_SIZE = 4 << 10
MAX_PATH_BYTES = 4096
MAX_FILES = 10_000_000
FILE_RECORD_FIXED = 2 + 1 + 8 + 4 + 8 + 8 + 4 + 32   # path_len, type, size, mode, mtime_ns, ref_start, ref_count, sha256

CODEC_NONE = 0
CODEC_ZSTD = 1
CODECS = {CODEC_NONE: "none", CODEC_ZSTD: "zstd"}

TYPE_FILE = 0
TYPE_DIR = 1

CHUNK_DTYPE = np.dtype([("offset", ">u8"), ("stored", ">u4"), ("plain", ">u4"), ("codec", "u1"), ("reserved", "V3"),
                        ("stored_sha256", "V32"), ("chunk_id", "V32")])
assert CHUNK_DTYPE.itemsize == CHUNK_ENTRY_BYTES

KNOWN_FEATURES = {"vnx4-container", "chunk-fixed", "dedup-content-address", "merkle-rfc6962-sha256", "zstd", "aes-256-gcm",
                  "kdf-hkdf-sha256", "kdf-scrypt"}


# ============================================================================ records
@dataclass
class FileRecord:
    path: str
    type: int
    size: int
    mode: int
    mtime_ns: int
    ref_start: int
    ref_count: int
    sha256: bytes

    def pack(self) -> bytes:
        p = self.path.encode("utf-8")
        return (len(p).to_bytes(2, "big") + p + struct.pack(">BQIqQI", self.type, self.size, self.mode, self.mtime_ns,
                                                             self.ref_start, self.ref_count) + self.sha256)

    def to_dict(self) -> dict:
        return {"path": self.path, "type": "dir" if self.type == TYPE_DIR else "file", "size": self.size,
                "sha256": self.sha256.hex(), "chunks": self.ref_count, "mode": self.mode, "mtime_ns": self.mtime_ns}


def chunk_entry(offset: int, stored: int, plain: int, codec: int, stored_sha: bytes, chunk_id: bytes) -> bytes:
    return struct.pack(">QIIB3x", offset, stored, plain, codec) + stored_sha + chunk_id


def leaf_hashes(chunk_table: bytes) -> list[bytes]:
    return [merkle.leaf_hash(chunk_table[i:i + CHUNK_ENTRY_BYTES]) for i in range(0, len(chunk_table), CHUNK_ENTRY_BYTES)]


def validate_archive_path(path: str) -> str:
    """Archive paths are relative POSIX paths without '.', '..', empty components, NUL or backslashes."""
    raw = path.encode("utf-8", errors="strict")
    if not raw or len(raw) > MAX_PATH_BYTES:
        raise VNXFormatError(f"archive path has invalid length: {path!r}")
    if path.startswith("/") or "\\" in path or "\x00" in path:
        raise VNXFormatError(f"unsafe archive path {path!r}")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise VNXFormatError(f"unsafe archive path {path!r}")
    if any(ord(c) < 32 for c in path):
        raise VNXFormatError(f"control character in archive path {path!r}")
    return path


def parse_file_table(data: bytes, count: int, refs_total: int) -> list[FileRecord]:
    out: list[FileRecord] = []
    pos = 0
    prev: bytes | None = None
    for _ in range(count):
        if pos + 2 > len(data):
            raise VNXFormatError("file table truncated")
        n = int.from_bytes(data[pos:pos + 2], "big")
        end = pos + 2 + n + FILE_RECORD_FIXED - 2
        if n == 0 or n > MAX_PATH_BYTES or end > len(data):
            raise VNXFormatError("file table record has an invalid path length or is truncated")
        raw = data[pos + 2:pos + 2 + n]
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise VNXFormatError("file table path is not UTF-8") from None
        validate_archive_path(path)
        if prev is not None and raw <= prev:
            raise VNXFormatError("file table is not strictly sorted by path (duplicate or reordered entries)")
        prev = raw
        typ, size, mode, mtime, ref_start, ref_count = struct.unpack(">BQIqQI", data[pos + 2 + n:pos + 2 + n + 33])
        sha = data[pos + 2 + n + 33:end]
        if typ not in (TYPE_FILE, TYPE_DIR):
            raise VNXFormatError(f"unknown file type {typ} for {path!r}")
        if typ == TYPE_DIR and (size or ref_count):
            raise VNXFormatError(f"directory entry {path!r} has content")
        if ref_start + ref_count > refs_total:
            raise VNXFormatError(f"file {path!r} references chunks outside the reference table")
        out.append(FileRecord(path, typ, size, mode, mtime, ref_start, ref_count, sha))
        pos = end
    if pos != len(data):
        raise VNXFormatError("file table has trailing bytes")
    return out


# ============================================================================ writer
class ContainerWriter:
    """Streaming writer. Call :meth:`add_chunk` for unique chunks, then :meth:`finish`."""

    def __init__(self, handle: BinaryIO):
        self.f = handle
        self.hash = hashlib.sha256()
        self.body = 0
        self.entries: list[bytes] = []
        self._write(MAGIC + struct.pack(">HHI", FORMAT_VERSION[0], FORMAT_VERSION[1], 0))

    def _write(self, data: bytes) -> None:
        self.f.write(data)
        self.hash.update(data)

    def add_chunk(self, stored: bytes, plain_size: int, codec: int, chunk_id: bytes) -> int:
        index = len(self.entries)
        self.entries.append(chunk_entry(self.body, len(stored), plain_size, codec, hashlib.sha256(stored).digest(), chunk_id))
        self._write(stored)
        self.body += len(stored)
        return index

    def finish(self, manifest: dict, file_table: bytes, refs: bytes, mac_fn=None) -> tuple[bytes, dict]:
        """Write tables, manifest and trailer. ``manifest`` is completed here (tables, Merkle root)."""
        table = b"".join(self.entries)
        manifest = dict(manifest)
        manifest["tables"] = {
            "chunk_table": {"entry_bytes": CHUNK_ENTRY_BYTES, "entries": len(self.entries), "sha256": hashlib.sha256(table).hexdigest()},
            "file_table": {"bytes": len(file_table), "sha256": hashlib.sha256(file_table).hexdigest()},
            "refs": {"bytes": len(refs), "sha256": hashlib.sha256(refs).hexdigest()},
        }
        manifest["integrity"] = {"merkle": "rfc6962-sha256", "leaf": "chunk-table-entry",
                                 "merkle_root": merkle.root_from_leaves(leaf_hashes(table)).hex()}
        manifest["counts"]["stored_bytes"] = self.body
        man = canonical_json(manifest)
        if len(man) > MAX_MANIFEST_BYTES:
            raise VNXResourceError("manifest exceeds 1 MiB")
        mac = mac_fn(man) if mac_fn else hashlib.sha256(man).digest()
        for part in (table, file_table, refs, man):
            self._write(part)
        head = struct.pack(">QQQQQ", self.body, len(table), len(file_table), len(refs), len(man)) + mac + TRAILER_MAGIC
        self._write(head)
        digest = self.hash.digest()
        self.f.write(digest)
        return digest, manifest


def base_manifest(*, archive_id: bytes, chunk_size: int, compression: dict, encryption: dict, counts: dict,
                  features: set[str], extensions: dict | None = None) -> dict:
    return {"format": "VNX4", "format_version": list(FORMAT_VERSION), "archive_id": archive_id.hex(), "created_at": None,
            "encoder": {"name": "vnxdna", "version": __version__}, "required_features": sorted(features),
            "chunking": {"algorithm": "fixed", "chunk_size": chunk_size}, "compression": compression, "encryption": encryption,
            "counts": counts, "extensions": extensions or {}}


# ============================================================================ reader
@dataclass
class Container:
    """An opened, structurally validated container. Content is verified lazily per chunk (and fully by ``verify``)."""

    path: Path
    size: int
    manifest: dict
    manifest_bytes: bytes
    chunk_table: np.ndarray            # structured CHUNK_DTYPE
    chunk_table_bytes: bytes
    files: list[FileRecord]
    refs: np.ndarray                   # u32 chunk indices
    trailer_sha256: bytes
    offsets: dict = field(default_factory=dict)
    sealer: crypto.Sealer | None = None

    @property
    def encrypted(self) -> bool:
        return self.manifest["encryption"]["algorithm"] != "none"

    @property
    def archive_id(self) -> bytes:
        return bytes.fromhex(self.manifest["archive_id"])

    def file(self, path: str) -> FileRecord:
        lo, hi = 0, len(self.files)
        key = path.encode("utf-8")
        while lo < hi:
            mid = (lo + hi) // 2
            k = self.files[mid].path.encode("utf-8")
            if k < key:
                lo = mid + 1
            else:
                hi = mid
        if lo < len(self.files) and self.files[lo].path == path:
            return self.files[lo]
        raise VNXFormatError(f"no such file in archive: {path!r}", stage="lookup")

    def file_chunks(self, rec: FileRecord) -> np.ndarray:
        return self.refs[rec.ref_start:rec.ref_start + rec.ref_count]

    def chunk_range(self, index: int) -> tuple[int, int]:
        e = self.chunk_table[index]
        return HEADER_BYTES + int(e["offset"]), int(e["stored"])


def _read_exact(f: BinaryIO, offset: int, n: int, what: str) -> bytes:
    data = os.pread(f.fileno(), n, offset)
    if len(data) != n:
        raise VNXFormatError(f"{what} is truncated")
    return data


def read_header_trailer(path: Path) -> tuple[int, tuple, bytes, bytes]:
    try:
        size = path.stat().st_size
        f = open(path, "rb")
    except OSError as error:
        raise VNXFormatError(f"cannot open {path}: {error.strerror or error}") from None
    with f:
        if size < HEADER_BYTES + TRAILER_BYTES:
            raise VNXFormatError(f"{path} is too small to be a VNX4 container ({size} bytes)")
        head = _read_exact(f, 0, HEADER_BYTES, "header")
        if head[:8] != MAGIC:
            if head[:6] == b"\x89VXDNA":
                raise VNXUnsupportedVersionError(f"{path} is a V1–V3 .vxdna container; use the vnx-dna (V3) commands",
                                                 hint="vnx-dna restore / vnx-dna verify")
            raise VNXFormatError(f"{path} is not a VNX4 container (bad magic)")
        major, minor, flags = struct.unpack(">HHI", head[8:16])
        if major != FORMAT_VERSION[0]:
            raise VNXUnsupportedVersionError(f"unsupported VNX container major version {major} (this reader: {FORMAT_VERSION[0]})")
        if minor > FORMAT_VERSION[1]:
            raise VNXUnsupportedVersionError(f"VNX4 minor version {minor} is newer than this reader ({FORMAT_VERSION[1]})")
        if flags:
            raise VNXUnsupportedVersionError(f"unsupported header flags 0x{flags:08x}")
        tail = _read_exact(f, size - TRAILER_BYTES, TRAILER_BYTES, "trailer")
    body, ct, ft, rf, mn = struct.unpack(">QQQQQ", tail[:40])
    mac, magic, digest = tail[40:72], tail[72:80], tail[80:112]
    if magic != TRAILER_MAGIC:
        raise VNXFormatError(f"{path}: trailer magic missing (truncated or unfinished file)")
    if HEADER_BYTES + body + ct + ft + rf + mn + TRAILER_BYTES != size:
        raise VNXFormatError(f"{path}: section sizes do not add up to the file size")
    if ct % CHUNK_ENTRY_BYTES or mn > MAX_MANIFEST_BYTES or mn == 0:
        raise VNXFormatError(f"{path}: invalid chunk-table or manifest size")
    return size, (body, ct, ft, rf, mn), mac, digest


def open_container(path: str | os.PathLike, *, key: bytes | None = None, passphrase: str | None = None,
                   require_key: bool = False, allow_unencrypted: bool = False) -> Container:
    """Open and validate structure, manifest, tables and Merkle root. ``key``/``passphrase`` unlock sealed tables.

    A key or passphrase given for an unencrypted archive is refused (:class:`VNXKeyError`) unless ``allow_unencrypted``:
    the caller expects encrypted content, and an unencrypted archive substituted for it (an encryption downgrade) would
    otherwise be accepted silently, its manifest protected only by an unkeyed digest."""
    path = Path(path)
    size, (body, ct, ft, rf, mn), mac, digest = read_header_trailer(path)
    with open(path, "rb") as f:
        base = HEADER_BYTES + body
        table = _read_exact(f, base, ct, "chunk table")
        file_table = _read_exact(f, base + ct, ft, "file table")
        refs_raw = _read_exact(f, base + ct + ft, rf, "reference table")
        man_bytes = _read_exact(f, base + ct + ft + rf, mn, "manifest")
    m = parse_canonical_json(man_bytes, "manifest")
    validate_manifest(m)
    sealer = None
    enc = m["encryption"]
    if enc["algorithm"] == "none" and (key is not None or passphrase is not None) and not allow_unencrypted:
        raise VNXKeyError("a key or passphrase was given but this archive is not encrypted (possible encryption "
                          "downgrade); pass --allow-unencrypted to accept it", stage="crypto")
    if enc["algorithm"] != "none":
        if key is None and passphrase is None:
            if require_key:
                raise VNXKeyError("this archive is encrypted; pass --key-file or --passphrase-env", stage="crypto")
        else:
            salt = bytes.fromhex(enc["salt"])
            if passphrase is not None:
                if enc["kdf"] != "scrypt-hkdf-sha256":
                    raise VNXKeyError("archive was created with a key file, not a passphrase")
                key = crypto.scrypt_master(passphrase, salt, enc["scrypt"])
            elif enc["kdf"] != "key-file-hkdf-sha256":
                raise VNXKeyError("archive was created with a passphrase, not a key file")
            keys = crypto.ArchiveKeys.derive(key, salt)
            keys.check(enc["key_check"])
            sealer = crypto.Sealer(keys, bytes.fromhex(m["archive_id"]))
            if not _ct_eq(sealer.mac(man_bytes), mac):
                raise VNXIntegrityError("manifest MAC does not verify (tampered manifest)")
    elif not _ct_eq(hashlib.sha256(man_bytes).digest(), mac):
        raise VNXIntegrityError("manifest digest in the trailer does not match the manifest")
    t = m["tables"]
    if t["chunk_table"]["entries"] * CHUNK_ENTRY_BYTES != ct or hashlib.sha256(table).hexdigest() != t["chunk_table"]["sha256"]:
        raise VNXIntegrityError("chunk table does not match the manifest")
    if t["file_table"]["bytes"] != ft or hashlib.sha256(file_table).hexdigest() != t["file_table"]["sha256"]:
        raise VNXIntegrityError("file table does not match the manifest")
    if t["refs"]["bytes"] != rf or hashlib.sha256(refs_raw).hexdigest() != t["refs"]["sha256"]:
        raise VNXIntegrityError("reference table does not match the manifest")
    if m["counts"]["stored_bytes"] != body:
        raise VNXFormatError("body size differs from the manifest")
    chunks = np.frombuffer(table, dtype=CHUNK_DTYPE)
    _validate_chunk_table(chunks, body, m)
    root = merkle.root_from_leaves(leaf_hashes(table)).hex()
    if root != m["integrity"]["merkle_root"]:
        raise VNXIntegrityError("Merkle root of the chunk table does not match the manifest")
    counts = m["counts"]
    files: list[FileRecord] = []
    refs = np.zeros(0, dtype=np.uint32)
    if sealer is not None or enc["algorithm"] == "none":
        if sealer is not None:
            file_table = sealer.open(crypto.DOMAIN_FILE_TABLE, 0, 1, file_table, "file table")
            refs_raw = sealer.open(crypto.DOMAIN_REFS, 0, 1, refs_raw, "reference table")
        if len(refs_raw) != 4 * counts["chunk_refs"]:
            raise VNXFormatError("reference table size differs from the manifest")
        refs = np.frombuffer(refs_raw, dtype=">u4").astype(np.uint32)
        if refs.size and int(refs.max()) >= len(chunks):
            raise VNXFormatError("reference table points outside the chunk table")
        files = parse_file_table(file_table, counts["files"], int(refs.size))
        _validate_files(files, refs, chunks, m)
    return Container(path, size, m, man_bytes, chunks, table, files, refs, digest,
                     {"body": HEADER_BYTES, "chunk_table": HEADER_BYTES + body}, sealer)


def _ct_eq(a: bytes, b: bytes) -> bool:
    import hmac
    return hmac.compare_digest(a, b)


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise VNXFormatError(f"invalid manifest: {msg}")


def _int(v, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _hex(v, length: int) -> bool:
    """Lower-case hex string of exactly ``length`` characters (as the writer produces)."""
    return isinstance(v, str) and len(v) == length and all(c in "0123456789abcdef" for c in v)


def validate_manifest(m: dict) -> None:
    _require(m.get("format") == "VNX4", "format must be VNX4")
    fv = m.get("format_version")
    _require(isinstance(fv, list) and len(fv) == 2 and all(_int(x, 0, 65535) for x in fv), "format_version")
    if fv[0] != FORMAT_VERSION[0] or fv[1] > FORMAT_VERSION[1]:
        raise VNXUnsupportedVersionError(f"unsupported VNX4 manifest version {fv}")
    req = m.get("required_features")
    _require(isinstance(req, list) and all(isinstance(x, str) for x in req), "required_features")
    unknown = sorted(set(req) - KNOWN_FEATURES)
    if unknown:
        raise VNXUnsupportedVersionError(f"archive requires unsupported features: {unknown}")
    aid = m.get("archive_id")
    _require(isinstance(aid, str) and len(aid) == 32 and all(c in "0123456789abcdef" for c in aid), "archive_id")
    ch = m.get("chunking")
    _require(isinstance(ch, dict) and ch.get("algorithm") == "fixed" and _int(ch.get("chunk_size"), MIN_CHUNK_SIZE, MAX_CHUNK_SIZE),
             "chunking")
    comp = m.get("compression")
    _require(isinstance(comp, dict) and comp.get("algorithm") in ("zstd", "none"), "compression")
    enc = m.get("encryption")
    _require(isinstance(enc, dict) and enc.get("algorithm") in ("none", "AES-256-GCM"), "encryption")
    if enc["algorithm"] != "none":
        _require(enc.get("kdf") in ("key-file-hkdf-sha256", "scrypt-hkdf-sha256"), "encryption.kdf")
        _require(_hex(enc.get("salt"), 2 * crypto.SALT_BYTES), "encryption.salt")
        _require(_hex(enc.get("key_check"), 32), "encryption.key_check")
        if enc["kdf"] == "scrypt-hkdf-sha256":
            sp = enc.get("scrypt")
            _require(isinstance(sp, dict) and set(sp) == {"n", "r", "p"} and crypto.scrypt_params_ok(sp),
                     "scrypt parameters outside the caps")
    c = m.get("counts")
    _require(isinstance(c, dict), "counts")
    for k in ("files", "chunks", "chunk_refs", "content_bytes", "stored_bytes"):
        _require(_int(c.get(k), 0, 1 << 62), f"counts.{k}")
    _require(c["files"] <= MAX_FILES, "too many files")
    t = m.get("tables")
    _require(isinstance(t, dict) and {"chunk_table", "file_table", "refs"} <= set(t), "tables")
    for name, size_key in (("chunk_table", "entries"), ("file_table", "bytes"), ("refs", "bytes")):
        _require(isinstance(t[name], dict) and _int(t[name].get(size_key), 0, 1 << 62) and _hex(t[name].get("sha256"), 64),
                 f"tables.{name}")
    integ = m.get("integrity")
    _require(isinstance(integ, dict) and integ.get("merkle") == "rfc6962-sha256" and isinstance(integ.get("merkle_root"), str),
             "integrity")
    _require(isinstance(m.get("extensions"), dict), "extensions")


def _validate_chunk_table(chunks: np.ndarray, body: int, m: dict) -> None:
    n = len(chunks)
    if n != m["counts"]["chunks"]:
        raise VNXFormatError("chunk count differs from the manifest")
    if n == 0:
        if body:
            raise VNXFormatError("body present without chunks")
        return
    offsets = chunks["offset"].astype(np.int64)
    stored = chunks["stored"].astype(np.int64)
    plain = chunks["plain"].astype(np.int64)
    cs = m["chunking"]["chunk_size"]
    if offsets[0] != 0 or np.any(offsets[1:] != offsets[:-1] + stored[:-1]) or int(offsets[-1] + stored[-1]) != body:
        raise VNXFormatError("chunk table does not tile the body exactly")
    if np.any(plain > cs) or np.any(plain == 0):
        raise VNXFormatError("chunk plaintext size outside (0, chunk_size]")
    tag = crypto.TAG_BYTES if m["encryption"]["algorithm"] != "none" else 0
    if np.any(stored > plain + tag):
        raise VNXFormatError("stored chunk larger than its plaintext (keep-if-smaller policy violated)")
    codec = chunks["codec"]
    allowed = {CODEC_NONE} | ({CODEC_ZSTD} if m["compression"]["algorithm"] == "zstd" else set())
    if not set(np.unique(codec).tolist()) <= allowed:
        raise VNXFormatError("chunk codec not allowed by the manifest")
    if any(bytes(r) != b"\x00\x00\x00" for r in chunks["reserved"]):
        raise VNXFormatError("reserved chunk-table bytes are not zero")


def _validate_files(files: list[FileRecord], refs: np.ndarray, chunks: np.ndarray, m: dict) -> None:
    cs = m["chunking"]["chunk_size"]
    total = 0
    used = 0
    plain = chunks["plain"].astype(np.int64)
    for rec in files:
        if rec.type == TYPE_FILE:
            need = -(-rec.size // cs)
            if rec.ref_count != need:
                raise VNXFormatError(f"file {rec.path!r} has {rec.ref_count} chunk refs, expected {need}")
            if rec.ref_start != used:
                raise VNXFormatError(f"file {rec.path!r} references are not contiguous in file order")
            idx = refs[rec.ref_start:rec.ref_start + rec.ref_count]
            if need and int(plain[idx].sum()) != rec.size:
                raise VNXFormatError(f"chunk sizes of {rec.path!r} do not add up to its size")
            if need > 1 and np.any(plain[idx[:-1]] != cs):
                raise VNXFormatError(f"non-final chunk of {rec.path!r} is not full-size")
            used += rec.ref_count
            total += rec.size
    if used != refs.size:
        raise VNXFormatError("reference table has unreferenced trailing entries")
    if total != m["counts"]["content_bytes"]:
        raise VNXFormatError("file sizes do not add up to counts.content_bytes")
    if refs.size:
        if np.unique(refs).size != len(chunks):
            raise VNXFormatError("chunk table contains unreferenced chunks")
