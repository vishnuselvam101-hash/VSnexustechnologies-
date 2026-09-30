"""Build a format-4 container: chunking → compression → AES-256-GCM → manifest.

The container is the *logical* archive, before any DNA exists. It fixes and
authenticates every parameter that later stages need (outer erasure code,
strand geometry, DNA mapping, constraints), so the DNA encoding of a
container is fully determined by the container itself.

::

    plaintext ── split into chunk_size pieces (chunk i covers bytes [i·cs, (i+1)·cs))
      chunk ── compress (zlib | zstd | none) ── AES-256-GCM (if a key is given) ── stored chunk
      manifest = parameters + per-chunk stored size/SHA-256 + content description
                 (name, size, SHA-256, per-chunk plaintext SHA-256; sealed with AES-GCM when encrypted)
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import __version__
from ..dna.constraints import ConstraintSpec
from ..dna.strand import FRAME_FORMAT, StrandGeometry
from ..ecc.cauchy import CauchyErasureCode
from ..errors import ConfigurationError
from . import compression, crypto
from . import manifest as mf


@dataclass(frozen=True)
class StoreOptions:
    """Every parameter of a new archive. Defaults are the documented production profile."""

    chunk_size: int = 256 * 1024
    compression: str = "zstd"
    compression_level: int = 9
    data_shards: int = 64
    parity_shards: int = 16
    mapping: str = "2bit"
    payload_bytes: int = 40
    inner_parity_bytes: int = 8
    constraints: ConstraintSpec = field(default_factory=ConstraintSpec)
    store_name: bool = True
    timestamp: str | None = None  # recorded verbatim; None keeps output byte-deterministic

    def validate(self) -> StrandGeometry:
        if not isinstance(self.chunk_size, int) or isinstance(self.chunk_size, bool) or not 1 <= self.chunk_size <= 1 << 30:
            raise ConfigurationError("chunk_size must be an integer in 1 .. 2^30")
        compression.validate(self.compression, self.compression_level)
        CauchyErasureCode(self.data_shards, self.parity_shards)
        if self.timestamp is not None and (not isinstance(self.timestamp, str) or len(self.timestamp) > 64):
            raise ConfigurationError("timestamp must be a string of at most 64 characters")
        if not isinstance(self.constraints, ConstraintSpec):
            raise ConfigurationError("constraints must be a ConstraintSpec")
        return StrandGeometry(self.mapping, self.payload_bytes, self.inner_parity_bytes)

    def public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["constraints"] = self.constraints.to_dict()
        return d


@dataclass
class Container:
    """An in-memory format-4 container: canonical manifest bytes plus stored chunks."""

    manifest: mf.Manifest
    manifest_bytes: bytes
    stored_chunks: list[bytes]

    @property
    def body(self) -> bytes:
        return b"".join(self.stored_chunks)


def _derive_archive_id(data_sha: bytes, options: StoreOptions, name: str | None) -> bytes:
    """Deterministic ID for unencrypted archives: same input + options → same archive."""
    material = mf.canonical_bytes({"options": options.public_dict(), "name": name, "format_version": mf.FORMAT_VERSION})
    return hashlib.sha256(b"VNX-DNA/4 archive-id\x00" + material + data_sha).digest()[:16]


def _safe_name(name: str | None) -> str | None:
    """Keep only a plain, bounded file name (no directories, no control characters)."""
    if name is None:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch >= " " and ch != "\x7f")
    if base in ("", ".", ".."):
        return None
    encoded = base.encode("utf-8")
    if len(base) > 255 or len(encoded) > 1020:
        stem, dot, ext = base.rpartition(".")
        keep = ("." + ext) if dot and len(ext) <= 16 else ""
        base = base[: 255 - len(keep)] + keep
    return base


def build_container(data: bytes, options: StoreOptions = StoreOptions(), key: bytes | None = None,
                    name: str | None = None) -> Container:
    """Create a container for ``data``. Encrypts iff ``key`` (32 bytes) is given."""
    geometry = options.validate()
    code = CauchyErasureCode(options.data_shards, options.parity_shards)
    name = _safe_name(name) if options.store_name else None
    data_sha = hashlib.sha256(data).digest()
    if key is not None:
        salt = crypto.new_salt()
        archive_id = os.urandom(16)  # fresh per archive → fresh AEAD key → unique nonces
        keys = crypto.ArchiveKeys.derive(key, salt)
    else:
        salt, keys = None, None
        archive_id = _derive_archive_id(data_sha, options, name)

    chunk_count = max(1, -(-len(data) // options.chunk_size))
    stripe_bytes = code.data_shards * geometry.payload_bytes
    content_chunks, chunk_records, stored_parts = [], [], []
    next_stripe = 0
    for index in range(chunk_count):
        plain = data[index * options.chunk_size:(index + 1) * options.chunk_size]
        content_chunks.append({"index": index, "offset": index * options.chunk_size, "size": len(plain),
                               "sha256": hashlib.sha256(plain).hexdigest()})
        stored = compression.compress(plain, options.compression, options.compression_level)
        if keys is not None:
            stored = crypto.seal(keys, archive_id, crypto.DOMAIN_CHUNK, index, chunk_count, stored)
        stripes = -(-len(stored) // stripe_bytes)
        chunk_records.append({"index": index, "stored_size": len(stored), "stored_sha256": hashlib.sha256(stored).hexdigest(),
                              "first_stripe": next_stripe, "stripe_count": stripes})
        next_stripe += stripes
        stored_parts.append(stored)
    if next_stripe >= 1 << 24:
        raise ConfigurationError("input too large for the 24-bit stripe index; increase data_shards or payload_bytes")
    stored_all = b"".join(stored_parts)
    content = {"name": name, "size": len(data), "sha256": data_sha.hex(), "chunks": content_chunks}
    encrypted = keys is not None
    raw: dict[str, Any] = {
        "format": mf.FORMAT_MAGIC,
        "format_version": mf.FORMAT_VERSION,
        "encoder": {"name": "vnxdna", "version": __version__},
        "required_features": sorted(mf.required_features(options.mapping, encrypted)),
        "archive_id": archive_id.hex(),
        "created_at": options.timestamp,
        "chunk_size": options.chunk_size,
        "compression": {"algorithm": options.compression, "level": options.compression_level},
        "encryption": {"algorithm": "AES-256-GCM" if encrypted else "none",
                       "kdf": "HKDF-SHA256" if encrypted else None,
                       "salt": salt.hex() if salt else None,
                       "key_check": keys.check.hex() if keys else None},
        "stored_size": len(stored_all),
        "stored_sha256": hashlib.sha256(stored_all).hexdigest(),
        "chunks": chunk_records,
        "content": None if encrypted else content,
        "sealed_content": None,
        "erasure_code": {"algorithm": "cauchy-rs-gf256", "data_shards": code.data_shards, "parity_shards": code.parity_shards,
                         "stripe_count": next_stripe},
        "strand": {"frame_format": FRAME_FORMAT, "mapping": geometry.mapping, "payload_bytes": geometry.payload_bytes,
                   "inner_ecc": "reed-solomon-gf256", "inner_parity_bytes": geometry.inner_parity_bytes, "crc": "crc32",
                   "scrambler": "shake128", "frame_bytes": geometry.frame_bytes, "strand_nt": geometry.strand_nt},
        "constraints": options.constraints.to_dict(),
        "dna_manifest": {"enabled": True, "scheme": "cauchy-rs-gf256-8+8"},
        "extensions": {},
    }
    if keys is not None:
        import base64
        sealed = crypto.seal(keys, archive_id, crypto.DOMAIN_SEALED, 0, 1, mf.canonical_bytes(content))
        raw["sealed_content"] = base64.b64encode(sealed).decode("ascii")
    payload = mf.digest_payload(raw)
    raw["seal"] = {"manifest_sha256": hashlib.sha256(payload).hexdigest(),
                   "manifest_hmac_sha256": crypto.mac(keys, payload) if keys is not None else None}
    manifest = mf.validate(raw)
    return Container(manifest, mf.canonical_bytes(raw), stored_parts)
