"""The ``.vxdna`` container file version 2 (archive format 5): streaming, footer-indexed, atomic.

Byte layout (big-endian integers)::

    offset          size  field
    0               8     magic 89 56 58 44 4E 41 0D 0A ("\\x89VXDNA\\r\\n", same as version 1)
    8               2     container file version = 2
    10              2     flags = 0 (nonzero is rejected)
    12              4     reserved = 0
    16              B     body: stored chunks, in chunk order
    16+B            L     canonical format-5 manifest JSON
    16+B+L          I     chunk index (56 bytes per chunk)
    16+B+L+I        J     plaintext index (36 bytes per chunk, AES-GCM sealed when encrypted)
    end-64          8     B (body length)
    end-56          4     L
    end-52          4     I
    end-48          4     J
    end-44          4     reserved = 0
    end-40          8     trailer magic "VXDNAEND"
    end-32          32    SHA-256 of every preceding byte of the file

Why this layout: the body is written as chunks are produced and the index goes
at the end, so ``store`` never holds more than a few chunks in memory and
never needs a second pass. The file is first written as ``<output>.partial``
and renamed into place only after the trailer is written and fsynced. A
killed process leaves only the ``.partial`` file, which has no valid trailer
and is never accepted as an archive.

The trailer SHA-256 detects accidental corruption of the file. It is not
authentication: the manifest seal (HMAC when encrypted) covers the manifest
and, through their recorded SHA-256, both index tables. Every chunk has its own
SHA-256 in the index, which is what random access relies on.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import InvalidInputError, MetadataError, OutputError, UnsupportedFormatError
from .manifest import INDEX_DTYPE, MAX_CHUNKS, PLAIN_DTYPE

MAGIC = b"\x89VXDNA\r\n"
FILE_VERSION = 2
HEADER_BYTES = 16
TRAILER_BYTES = 64
TRAILER_MAGIC = b"VXDNAEND"
MAX_MANIFEST_BYTES = 1 << 20
READ_BLOCK = 4 << 20


def container_version(path: str | os.PathLike) -> int | None:
    """1 or 2 for a VNX-DNA container file, None for anything else."""
    try:
        with Path(path).open("rb") as handle:
            head = handle.read(10)
    except OSError:
        return None
    if len(head) < 10 or head[:8] != MAGIC:
        return None
    return int.from_bytes(head[8:10], "big")


def header_bytes() -> bytes:
    return MAGIC + FILE_VERSION.to_bytes(2, "big") + (0).to_bytes(2, "big") + (0).to_bytes(4, "big")


def publish(tmp: Path, target: Path, *, overwrite: bool) -> None:
    """Rename a finished temporary file into place.

    Without ``overwrite`` the file is hard-linked (fails if ``target`` appeared
    meanwhile) instead of renamed, so a file created after the up-front
    existence check is never replaced silently. File systems without hard
    links fall back to the rename.
    """
    if overwrite:
        os.replace(tmp, target)
        return
    try:
        os.link(tmp, target)
    except FileExistsError:
        raise OutputError(f"output already exists: {target} (created while this command ran; use --force to overwrite)") from None
    except OSError:
        # hard links unsupported here (EPERM, EXDEV, ENOTSUP, ENOSYS, EINVAL, EACCES on some FUSE/SMB mounts ...):
        # fall back to a checked rename (a file created in the instant between the check and the rename is replaced)
        if target.exists():
            raise OutputError(f"output already exists: {target} (created while this command ran; use --force to overwrite)") from None
        os.replace(tmp, target)
        return
    os.unlink(tmp)


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class ContainerWriter:
    """Writes a version-2 container to ``<target>.partial`` and publishes it atomically.

    ``file_hash`` covers every byte written so far (for the trailer) and
    ``body_hash`` the stored chunks (for ``stored_sha256``). On resume the
    caller re-feeds the existing prefix through :meth:`rehash_prefix`.
    """

    def __init__(self, target: str | os.PathLike, *, overwrite: bool = False, resumable: bool = False):
        self.target = Path(target)
        if self.target.exists() and self.target.is_dir():
            raise OutputError(f"output is a directory: {self.target}")
        if self.target.exists() and not overwrite:
            raise OutputError(f"output already exists: {self.target} (use --force to overwrite)")
        # A resumable store needs a predictable name (``<output>.partial``, next to its checkpoint); the caller removes a
        # stale one first and the file is then created exclusively. Every other writer (decode) uses a private, uniquely
        # named temporary file: a fixed name was opened with O_TRUNC through any symlink planted there, and an input
        # that happened to be called ``<output>.partial`` was truncated (V3 release review).
        self.resumable = resumable
        self.partial = self.target.with_name(self.target.name + ".partial") if resumable else None
        self.overwrite = overwrite
        self.file_hash = hashlib.sha256()
        self.body_hash = hashlib.sha256()
        self.body_bytes = 0
        self.handle = None

    def start_fresh(self) -> None:
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            if self.resumable:
                fd = os.open(self.partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            else:
                fd, tmp = tempfile.mkstemp(prefix="." + self.target.name + ".", suffix=".partial", dir=self.target.parent)
                self.partial = Path(tmp)
        except OSError as error:
            raise OutputError(f"cannot write {self.partial or self.target}: {error.strerror or error}") from None
        self.handle = os.fdopen(fd, "wb")
        head = header_bytes()
        self.handle.write(head)
        self.file_hash.update(head)

    def resume_at(self, body_bytes: int) -> None:
        """Reopen an existing partial file, truncated to ``body_bytes`` of body, and rehash its prefix."""
        try:
            fd = os.open(self.partial, os.O_RDWR)
        except OSError as error:
            raise OutputError(f"cannot reopen {self.partial}: {error.strerror or error}") from None
        self.handle = os.fdopen(fd, "r+b")
        size = os.fstat(fd).st_size
        if size < HEADER_BYTES + body_bytes:
            raise InvalidInputError("partial archive is shorter than its checkpoint records; it cannot be resumed")
        self.handle.seek(0)
        if self.handle.read(HEADER_BYTES) != header_bytes():
            raise InvalidInputError("partial archive has an invalid header; it cannot be resumed")
        self.handle.truncate(HEADER_BYTES + body_bytes)
        self.file_hash.update(header_bytes())
        self.handle.seek(HEADER_BYTES)
        remaining = body_bytes
        while remaining:
            block = self.handle.read(min(READ_BLOCK, remaining))
            if not block:
                raise InvalidInputError("partial archive ended early while rehashing")
            self.file_hash.update(block)
            self.body_hash.update(block)
            remaining -= len(block)
        self.body_bytes = body_bytes
        self.handle.seek(HEADER_BYTES + body_bytes)

    def read_body(self, offset: int, size: int) -> bytes:
        """Read back stored bytes of the partial body (used to verify a checkpoint)."""
        assert self.handle is not None
        position = self.handle.tell()
        self.handle.seek(HEADER_BYTES + offset)
        data = self.handle.read(size)
        self.handle.seek(position)
        return data

    def write_chunk(self, stored: bytes) -> int:
        assert self.handle is not None
        offset = self.body_bytes
        self.handle.write(stored)
        self.file_hash.update(stored)
        self.body_hash.update(stored)
        self.body_bytes += len(stored)
        return offset

    def flush(self) -> None:
        assert self.handle is not None
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def finish(self, manifest_bytes: bytes, index_bytes: bytes, plain_bytes: bytes) -> dict:
        """Write the footer and trailer, fsync, and atomically rename to the target."""
        assert self.handle is not None
        footer = manifest_bytes + index_bytes + plain_bytes
        tail = (self.body_bytes.to_bytes(8, "big") + len(manifest_bytes).to_bytes(4, "big") + len(index_bytes).to_bytes(4, "big")
                + len(plain_bytes).to_bytes(4, "big") + (0).to_bytes(4, "big") + TRAILER_MAGIC)
        self.handle.write(footer)
        self.file_hash.update(footer)
        self.handle.write(tail)
        self.file_hash.update(tail)
        digest = self.file_hash.digest()
        self.handle.write(digest)
        self.handle.flush()
        os.fsync(self.handle.fileno())
        size = self.handle.tell()
        self.handle.close()
        self.handle = None
        try:
            publish(self.partial, self.target, overwrite=self.overwrite)
        except OutputError:
            raise
        except OSError as error:
            raise OutputError(f"cannot publish {self.target}: {error.strerror or error}") from None
        fsync_dir(self.target.parent)
        return {"container_bytes": size, "container_sha256_trailer": digest.hex()}

    def close(self, *, remove: bool) -> None:
        if self.handle is not None:
            try:
                self.handle.close()
            finally:
                self.handle = None
        if remove and self.partial is not None:
            self.partial.unlink(missing_ok=True)


@dataclass
class ContainerFileV2:
    """An opened version-2 container. Stored chunks are read on demand (random access)."""

    path: Path
    size: int
    body_bytes: int
    manifest_bytes: bytes
    index_bytes: bytes
    plain_bytes: bytes
    stored_trailer: bytes

    @classmethod
    def open(cls, path: str | os.PathLike) -> "ContainerFileV2":
        p = Path(path)
        if not p.is_file():
            raise InvalidInputError(f"container not found: {p}")
        size = p.stat().st_size
        if size < HEADER_BYTES + TRAILER_BYTES:
            raise InvalidInputError(f"{p} is too short to be a version-2 container")
        with p.open("rb") as handle:
            head = handle.read(HEADER_BYTES)
            if head[:8] != MAGIC:
                raise InvalidInputError("not a VNX-DNA container (bad magic)")
            version = int.from_bytes(head[8:10], "big")
            if version != FILE_VERSION:
                raise UnsupportedFormatError(f"container file version {version}; this reader handles version {FILE_VERSION}")
            if head[10:16] != bytes(6):
                raise UnsupportedFormatError("container uses reserved flags or fields this decoder does not understand")
            handle.seek(size - TRAILER_BYTES)
            tail = handle.read(TRAILER_BYTES)
            if tail[24:32] != TRAILER_MAGIC:
                raise InvalidInputError("container has no valid trailer: it is truncated or an unfinished (.partial) archive")
            body = int.from_bytes(tail[0:8], "big")
            m_len = int.from_bytes(tail[8:12], "big")
            i_len = int.from_bytes(tail[12:16], "big")
            j_len = int.from_bytes(tail[16:20], "big")
            if tail[20:24] != bytes(4):
                raise UnsupportedFormatError("container trailer uses reserved fields")
            if m_len > MAX_MANIFEST_BYTES:
                raise MetadataError("container manifest length is implausible")
            if i_len > MAX_CHUNKS * INDEX_DTYPE.itemsize or j_len > MAX_CHUNKS * PLAIN_DTYPE.itemsize + 16:
                raise MetadataError("container index length is implausible")
            if HEADER_BYTES + body + m_len + i_len + j_len + TRAILER_BYTES != size:
                raise InvalidInputError(f"container is truncated or has trailing data ({size} bytes; the trailer implies "
                                        f"{HEADER_BYTES + body + m_len + i_len + j_len + TRAILER_BYTES})")
            handle.seek(HEADER_BYTES + body)
            footer = handle.read(m_len + i_len + j_len)
        return cls(p, size, body, footer[:m_len], footer[m_len:m_len + i_len], footer[m_len + i_len:], tail[32:])

    _fd: int | None = None
    _fd_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def read_stored(self, offset: int, size: int) -> bytes:
        """Read stored bytes with ``pread`` (thread-safe; one descriptor per opened container)."""
        if offset < 0 or size < 0 or offset + size > self.body_bytes:
            raise InvalidInputError("chunk lies outside the container body")
        if self._fd is None:
            with self._fd_lock:  # worker threads race on the first read; open exactly one descriptor (VNX-DNA 2.0 leaked)
                if self._fd is None:
                    self._fd = os.open(self.path, os.O_RDONLY)
        data = os.pread(self._fd, size, HEADER_BYTES + offset)
        if len(data) != size:
            raise InvalidInputError("container body ended early")
        return data

    def close(self) -> None:
        with self._fd_lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def verify_trailer(self) -> bool:
        """Stream the whole file through SHA-256 and compare with the trailer (bounded memory)."""
        return self.verify_trailer_and_body()[0]

    def verify_trailer_and_body(self) -> tuple[bool, str | None]:
        """One streaming pass: (whole-file SHA-256 equals the trailer, SHA-256 hex of the body or None if unreadable)."""
        h = hashlib.sha256()
        body = hashlib.sha256()
        remaining = self.size - 32
        position = 0
        body_start, body_end = HEADER_BYTES, HEADER_BYTES + self.body_bytes
        with self.path.open("rb") as handle:
            while remaining:
                block = handle.read(min(READ_BLOCK, remaining))
                if not block:
                    return False, None
                h.update(block)
                lo, hi = max(body_start - position, 0), min(body_end - position, len(block))
                if lo < hi:
                    body.update(block[lo:hi])
                position += len(block)
                remaining -= len(block)
        return h.digest() == self.stored_trailer, body.hexdigest()
