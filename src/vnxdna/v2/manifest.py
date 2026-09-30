"""Archive format 5: manifest schema, binary chunk index and plaintext index.

Why a binary index
    The format-4 manifest lists every chunk as JSON, so it grows with the file
    and must be complete before anything is written. Format 5 keeps the JSON
    manifest a few kilobytes long whatever the file size and moves per-chunk
    records into two fixed-width binary tables, written after the body:

    * the **chunk index** (clear, 56 bytes per chunk): where each stored chunk
      is, its size, codec, ECC stripes and stored SHA-256. Decoding, ECC and
      verification of ciphertext need nothing else, and no key;
    * the **plaintext index** (36 bytes per chunk): plaintext size and SHA-256
      per chunk. Clear for unencrypted archives, AES-256-GCM sealed for
      encrypted ones.

    The manifest records the SHA-256 of both tables, so the manifest seal
    (HMAC-SHA256 when encrypted) authenticates them.

Canonical JSON, strict schema, ``required_features`` and the single open
``extensions`` object follow the format-4 rules (see ``docs/V2_FORMAT.md``).
"""
from __future__ import annotations

import base64
import hashlib
import re
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ..container.manifest import _format_validation_error, canonical_bytes, digest_payload, parse_json  # noqa: F401
from ..dna.mapping import get_mapping
from ..errors import MetadataError, UnsupportedFormatError
from .frame import CRC_BYTES, FRAME_FORMAT, HEADER_BYTES

FORMAT_MAGIC = "VNX-DNA"
FORMAT_VERSION = 5
MAX_CHUNK_SIZE = 64 * 1024 * 1024
MAX_CHUNKS = 1 << 28
MAX_STORED_BYTES = 1 << 44
AEAD_TAG = 16

CODEC_NONE, CODEC_ZSTD, CODEC_ZLIB = 0, 1, 2
CODECS = {"none": CODEC_NONE, "zstd": CODEC_ZSTD, "zlib": CODEC_ZLIB}
CODEC_NAMES = {v: k for k, v in CODECS.items()}

INDEX_DTYPE = np.dtype([("offset", ">u8"), ("stored_size", ">u4"), ("first_stripe", ">u4"), ("stripe_count", ">u4"),
                        ("codec", "u1"), ("reserved", "u1", (3,)), ("stored_sha256", "u1", (32,))])
PLAIN_DTYPE = np.dtype([("size", ">u4"), ("sha256", "u1", (32,))])
assert INDEX_DTYPE.itemsize == 56 and PLAIN_DTYPE.itemsize == 36

FEATURES = frozenset({
    "stream-chunked-v2",      # independent chunks, per-chunk codec, binary chunk index in a footer
    "outer-cauchy-rs-v1",     # outer Cauchy RS erasure code over GF(256), one ECC group = one stripe
    "inner-rs-v1",            # per-strand RS (0 parity bytes permitted)
    "frame5-crc32-v1",        # frame format 5: 32-bit tag and stripe, CRC-32
    "scrambler-shake128-v2",  # variant-selected keystream screening, VNX-DNA/5 label
    "mapping-2bit", "mapping-rotation3", "mapping-codebook8",
    "aes-256-gcm-hkdf-v2",    # chunked AEAD and manifest MAC with VNX-DNA/5 labels
})

Hex16 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]
Hex32 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Hex64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNeg = Annotated[int, Field(ge=0, le=MAX_STORED_BYTES)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Encoder(_Model):
    name: Literal["vnxdna"]
    version: Annotated[str, StringConstraints(max_length=64)]


class Compression(_Model):
    algorithm: Literal["none", "zlib", "zstd"]
    level: Annotated[int, Field(ge=0, le=22)]
    policy: Literal["auto"]  # compress each chunk; keep it raw when compression does not make it smaller


class Encryption(_Model):
    algorithm: Literal["none", "AES-256-GCM"]
    kdf: Literal["HKDF-SHA256"] | None
    salt: Hex32 | None
    key_check: Hex16 | None


class ChunkIndex(_Model):
    format: Literal["vnx-chunk-index-1"]
    entry_bytes: Literal[56]
    entries: Annotated[int, Field(ge=1, le=MAX_CHUNKS)]
    sha256: Hex64


class PlainIndex(_Model):
    format: Literal["vnx-plain-index-1"]
    entry_bytes: Literal[36]
    sealed: bool
    stored_bytes: NonNeg
    sha256: Hex64  # of the stored (possibly sealed) bytes


class Content(_Model):
    name: Annotated[str, StringConstraints(max_length=255)] | None
    size: NonNeg
    sha256: Hex64


class ErasureCode(_Model):
    algorithm: Literal["cauchy-rs-gf256"]
    data_shards: Annotated[int, Field(ge=1, le=255)]
    parity_shards: Annotated[int, Field(ge=0, le=255)]
    stripe_count: Annotated[int, Field(ge=0, le=(1 << 32) - 1)]


class StrandFormat(_Model):
    frame_format: Literal[5]
    mapping: Literal["2bit", "rotation3", "codebook8"]
    payload_bytes: Annotated[int, Field(ge=1, le=240)]
    inner_ecc: Literal["reed-solomon-gf256"]
    inner_parity_bytes: Annotated[int, Field(ge=0, le=64)]
    crc: Literal["crc32"]
    scrambler: Literal["shake128"]
    header_bytes: Literal[11]
    frame_bytes: Annotated[int, Field(ge=16, le=255)]
    strand_nt: Annotated[int, Field(ge=1, le=4096)]


class Constraints(_Model):
    gc_min_percent: Annotated[int, Field(ge=0, le=100)]
    gc_max_percent: Annotated[int, Field(ge=0, le=100)]
    max_homopolymer: Annotated[int, Field(ge=0, le=1000)]
    gc_window_nt: Annotated[int, Field(ge=0, le=4096)]
    forbidden_motifs: list[Annotated[str, StringConstraints(pattern=r"^[ACGT]{1,64}$")]]
    check_reverse_complement: bool
    max_tandem_repeat_nt: Annotated[int, Field(ge=0, le=4096)]


class DnaManifest(_Model):
    enabled: Literal[True]
    scheme: Literal["cauchy-rs-gf256-8+8"]


class Seal(_Model):
    manifest_sha256: Hex64
    manifest_hmac_sha256: Hex64 | None


class Manifest(_Model):
    format: Literal["VNX-DNA"]
    format_version: Literal[5]
    encoder: Encoder
    required_features: list[str]
    archive_id: Hex32
    created_at: Annotated[str, StringConstraints(max_length=64)] | None
    profile: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,32}$")]
    chunk_size: Annotated[int, Field(ge=1, le=MAX_CHUNK_SIZE)]
    chunk_count: Annotated[int, Field(ge=1, le=MAX_CHUNKS)]
    compression: Compression
    encryption: Encryption
    stored_size: NonNeg
    stored_sha256: Hex64
    chunk_index: ChunkIndex
    plain_index: PlainIndex
    content: Content | None
    sealed_content: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9+/]*={0,2}$")] | None
    erasure_code: ErasureCode
    strand: StrandFormat
    constraints: Constraints
    dna_manifest: DnaManifest
    extensions: dict[str, Any]
    seal: Seal

    @property
    def encrypted(self) -> bool:
        return self.encryption.algorithm != "none"

    @property
    def archive_tag(self) -> int:
        return int(self.archive_id[:8], 16)

    @property
    def stripe_bytes(self) -> int:
        return self.erasure_code.data_shards * self.strand.payload_bytes

    def sealed_bytes(self) -> bytes:
        if self.sealed_content is None:
            raise MetadataError("manifest has no sealed content")
        return base64.b64decode(self.sealed_content, validate=True)


def required_features(mapping: str, encrypted: bool) -> set[str]:
    features = {"stream-chunked-v2", "outer-cauchy-rs-v1", "inner-rs-v1", "frame5-crc32-v1", "scrambler-shake128-v2",
                f"mapping-{mapping}"}
    if encrypted:
        features.add("aes-256-gcm-hkdf-v2")
    return features


def sniff(raw: Any) -> int:
    """Return the format version of a parsed manifest (4 or 5) or raise."""
    if not isinstance(raw, dict):
        raise MetadataError("manifest must be a JSON object")
    if raw.get("format") != FORMAT_MAGIC:
        raise UnsupportedFormatError(f"not a VNX-DNA manifest (format={raw.get('format')!r})")
    version = raw.get("format_version")
    if isinstance(version, bool) or version not in (4, FORMAT_VERSION):
        raise UnsupportedFormatError(f"unsupported VNX-DNA format_version {version!r}; this decoder reads 5 and 4 (and legacy 1-3)")
    return version


def validate(raw: Any) -> Manifest:
    """Schema and semantic validation (seal and tables are checked separately)."""
    if sniff(raw) != FORMAT_VERSION:
        raise UnsupportedFormatError("this is a format-4 (V1) manifest; it is read by the V1 decoder")
    features = raw.get("required_features", [])
    if not isinstance(features, list):
        raise MetadataError("manifest schema violation: required_features must be a list")
    unknown = [f for f in features if isinstance(f, str) and f not in FEATURES]
    if unknown:
        raise UnsupportedFormatError(f"archive requires unsupported feature(s): {unknown}")
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as error:
        raise MetadataError("manifest schema violation: " + _format_validation_error(error)) from None
    _semantic_checks(manifest)
    return manifest


def _semantic_checks(m: Manifest) -> None:
    def fail(message: str) -> None:
        raise MetadataError("manifest inconsistency: " + message)

    required = required_features(m.strand.mapping, m.encrypted)
    if set(m.required_features) != required or len(m.required_features) != len(required):
        fail(f"required_features must be exactly {sorted(required)}")
    enc = m.encryption
    if m.encrypted:
        if enc.kdf != "HKDF-SHA256" or enc.salt is None or enc.key_check is None:
            fail("encrypted archive must record kdf, salt and key_check")
        if m.content is not None or m.sealed_content is None or m.seal.manifest_hmac_sha256 is None or not m.plain_index.sealed:
            fail("encrypted archive must have sealed content and plaintext index, no clear content, and a manifest HMAC")
    else:
        if enc.kdf is not None or enc.salt is not None or enc.key_check is not None:
            fail("unencrypted archive must not record key material")
        if m.content is None or m.sealed_content is not None or m.seal.manifest_hmac_sha256 is not None or m.plain_index.sealed:
            fail("unencrypted archive must have clear content and plaintext index and no HMAC")
    low, high = {"none": (0, 0), "zlib": (0, 9), "zstd": (1, 22)}[m.compression.algorithm]
    if not low <= m.compression.level <= high:
        fail("compression level out of range for algorithm")
    code = m.erasure_code
    if code.data_shards + code.parity_shards > 256:
        fail("data_shards + parity_shards exceeds 256")
    s = m.strand
    frame = HEADER_BYTES + s.payload_bytes + CRC_BYTES + s.inner_parity_bytes
    if s.frame_bytes != frame or frame > 255 or s.strand_nt != frame * get_mapping(s.mapping).nt_per_byte:
        fail("strand geometry fields are inconsistent")
    if s.inner_parity_bytes % 2:
        fail("inner_parity_bytes must be even")
    if m.constraints.gc_min_percent > m.constraints.gc_max_percent:
        fail("gc_min_percent exceeds gc_max_percent")
    if m.chunk_index.entries != m.chunk_count:
        fail("chunk_index.entries differs from chunk_count")
    expected_plain = m.chunk_count * PLAIN_DTYPE.itemsize + (AEAD_TAG if m.plain_index.sealed else 0)
    if m.plain_index.stored_bytes != expected_plain:
        fail("plain_index.stored_bytes does not match chunk_count")
    if m.content is not None:
        check_content(m, m.content)


def check_content(m: Manifest, content: Content) -> None:
    expected = max(1, -(-content.size // m.chunk_size))
    if expected != m.chunk_count:
        raise MetadataError(f"manifest inconsistency: {content.size} bytes in chunks of {m.chunk_size} need {expected} chunks, "
                            f"not {m.chunk_count}")
    if content.name is not None and (re.search(r"[/\\\x00-\x1f\x7f]", content.name) or content.name in (".", "..")):
        raise MetadataError("manifest inconsistency: content name must be a plain file name")


def parse_content(data: bytes) -> Content:
    raw = parse_json(data)
    try:
        return Content.model_validate(raw)
    except ValidationError as error:
        raise MetadataError("sealed content schema violation: " + _format_validation_error(error)) from None


# ---------------------------------------------------------------- binary tables
def parse_chunk_index(m: Manifest, data: bytes) -> np.ndarray:
    """Validate the chunk index against the manifest (all checks vectorised) and return it."""
    def fail(message: str) -> None:
        raise MetadataError("chunk index inconsistency: " + message)

    if len(data) != m.chunk_count * INDEX_DTYPE.itemsize:
        fail(f"index is {len(data)} bytes, expected {m.chunk_count * INDEX_DTYPE.itemsize}")
    if hashlib.sha256(data).hexdigest() != m.chunk_index.sha256:
        raise MetadataError("chunk index SHA-256 does not match the manifest (index corrupted or modified)")
    index = np.frombuffer(data, dtype=INDEX_DTYPE)
    sizes = index["stored_size"].astype(np.int64)
    offsets = index["offset"].astype(np.int64)
    if offsets[0] != 0 or (offsets[1:] != np.cumsum(sizes)[:-1]).any():
        fail("stored chunks are not contiguous")
    if int(sizes.sum()) != m.stored_size:
        fail("stored chunk sizes do not add up to stored_size")
    if (sizes > m.chunk_size + AEAD_TAG).any():
        fail("a stored chunk is larger than chunk_size plus the AEAD tag (auto policy never expands a chunk)")
    stripes = index["stripe_count"].astype(np.int64)
    if (stripes != -(-sizes // m.stripe_bytes)).any():
        fail("stripe_count does not match stored_size")
    first = index["first_stripe"].astype(np.int64)
    if first[0] != 0 or (first[1:] != np.cumsum(stripes)[:-1]).any():
        fail("ECC stripes are not contiguous")
    if int(stripes.sum()) != m.erasure_code.stripe_count:
        fail("stripe counts do not add up to erasure_code.stripe_count")
    allowed = {CODEC_NONE} | ({CODECS[m.compression.algorithm]} if m.compression.algorithm != "none" else set())
    if not set(np.unique(index["codec"]).tolist()) <= allowed:
        fail("a chunk uses a codec the manifest does not allow")
    if index["reserved"].any():
        fail("reserved bytes are not zero")
    return index


def parse_plain_index(m: Manifest, data: bytes, content: Content) -> np.ndarray:
    if len(data) != m.chunk_count * PLAIN_DTYPE.itemsize:
        raise MetadataError("plaintext index has the wrong length")
    plain = np.frombuffer(data, dtype=PLAIN_DTYPE)
    sizes = plain["size"].astype(np.int64)
    expected = np.full(m.chunk_count, m.chunk_size, dtype=np.int64)
    expected[-1] = content.size - (m.chunk_count - 1) * m.chunk_size
    if (sizes != expected).any():
        raise MetadataError("plaintext index sizes do not tile the content size")
    return plain
