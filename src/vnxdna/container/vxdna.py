"""The ``.vxdna`` container file (container file format 1).

Byte layout (all integers big-endian)::

    offset      size  field
    0           8     magic  89 56 58 44 4E 41 0D 0A  ("\\x89VXDNA\\r\\n")
    8           2     container file version = 1
    10          2     flags = 0 (reserved; nonzero is rejected)
    12          4     manifest length  L   (≤ 64 MiB)
    16          8     body length      B   (must equal manifest.stored_size)
    24          L     canonical format-4 manifest JSON
    24+L        B     body: stored chunks concatenated in chunk order
    24+L+B      32    SHA-256 of bytes [0, 24+L+B)

The trailer detects accidental corruption of the file. It is *not*
authentication. The manifest seal (HMAC when encrypted) and the per-chunk
stored/plaintext SHA-256 values carry the security and integrity guarantees.
The layout is a pure function of the manifest bytes and the stored chunks.
Encoding a container to DNA and decoding it back therefore reproduces the
file byte for byte.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..errors import InvalidInputError, MetadataError, OutputError, UnsupportedFormatError
from . import manifest as mf

MAGIC = b"\x89VXDNA\r\n"
FILE_VERSION = 1
HEADER_BYTES = 24
TRAILER_BYTES = 32
MAX_CONTAINER_BYTES = 1 << 40


@dataclass
class ContainerFile:
    manifest_bytes: bytes
    body: bytes
    trailer_ok: bool


def serialize(manifest_bytes: bytes, body: bytes) -> bytes:
    header = MAGIC + FILE_VERSION.to_bytes(2, "big") + (0).to_bytes(2, "big") + len(manifest_bytes).to_bytes(4, "big") \
        + len(body).to_bytes(8, "big")
    blob = header + manifest_bytes + body
    return blob + hashlib.sha256(blob).digest()


def is_container(path: str | os.PathLike) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def parse(blob: bytes, *, strict: bool = True) -> ContainerFile:
    """Parse container bytes. With ``strict``, a trailer mismatch is an error."""
    if len(blob) < HEADER_BYTES + TRAILER_BYTES or blob[:8] != MAGIC:
        raise InvalidInputError("not a VNX-DNA container (bad magic or too short)")
    version = int.from_bytes(blob[8:10], "big")
    if version != FILE_VERSION:
        raise UnsupportedFormatError(f"unsupported container file version {version}; this decoder reads version {FILE_VERSION}")
    if int.from_bytes(blob[10:12], "big") != 0:
        raise UnsupportedFormatError("container uses reserved flags this decoder does not understand")
    m_len = int.from_bytes(blob[12:16], "big")
    b_len = int.from_bytes(blob[16:24], "big")
    if m_len > mf.MAX_MANIFEST_BYTES:
        raise MetadataError("container manifest length is implausible")
    expected = HEADER_BYTES + m_len + b_len + TRAILER_BYTES
    if len(blob) != expected:
        raise InvalidInputError(f"container is truncated or has trailing data ({len(blob)} bytes, header implies {expected})")
    trailer_ok = hashlib.sha256(blob[:-TRAILER_BYTES]).digest() == blob[-TRAILER_BYTES:]
    if strict and not trailer_ok:
        raise InvalidInputError("container file checksum mismatch: the file is corrupted (run `vnx-dna verify` for details)")
    return ContainerFile(blob[HEADER_BYTES:HEADER_BYTES + m_len], blob[HEADER_BYTES + m_len:HEADER_BYTES + m_len + b_len], trailer_ok)


def read(path: str | os.PathLike, *, strict: bool = True) -> ContainerFile:
    p = Path(path)
    if not p.is_file():
        raise InvalidInputError(f"container not found: {p}")
    if p.stat().st_size > MAX_CONTAINER_BYTES:
        raise InvalidInputError("container file is too large")
    try:
        blob = p.read_bytes()
    except OSError as error:
        raise InvalidInputError(f"cannot read {p}: {error.strerror or error}") from None
    return parse(blob, strict=strict)


def atomic_write(path: str | os.PathLike, data: bytes, *, overwrite: bool = False) -> None:
    """Write ``data`` to ``path`` via a same-directory temp file and ``os.replace``."""
    target = Path(path)
    if target.exists() and not overwrite:
        raise OutputError(f"output already exists: {target} (use --force to overwrite)")
    if target.exists() and target.is_dir():
        raise OutputError(f"output is a directory: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="." + target.name + ".", suffix=".tmp", dir=target.parent)
    except OSError as error:
        raise OutputError(f"cannot write {target}: {error.strerror or error}") from None
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, target)
    except OSError as error:
        Path(tmp).unlink(missing_ok=True)
        raise OutputError(f"cannot write {target}: {error.strerror or error}") from None
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
