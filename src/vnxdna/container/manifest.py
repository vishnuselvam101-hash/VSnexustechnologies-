"""Format-4 manifest: strict schema, canonical serialization, seal.

Canonical form
    UTF-8 JSON with sorted keys, separators ``(',', ':')``, ASCII escaping,
    no NaN/Infinity, no floats anywhere, and no duplicate keys.
    ``manifest.json`` stores exactly the canonical bytes.

Seal
    ``seal.manifest_sha256`` = SHA-256 of the canonical manifest without the
    ``seal`` member. It detects accidental corruption and is not authentication.
    ``seal.manifest_hmac_sha256`` = HMAC-SHA256 of the same bytes under the
    HKDF-derived MAC key. It is present iff the archive is encrypted and gives
    authenticity: nobody without the key can alter any manifest field undetected.

Forward compatibility
    * ``format``/``format_version`` are checked first. Any other value is an
      :class:`UnsupportedFormatError`.
    * ``required_features`` lists every mechanism needed to decode. An unknown
      entry is an :class:`UnsupportedFormatError`, so the decoder never guesses.
    * ``extensions`` is the only open object. A decoder ignores unknown keys in it.
      It is still covered by the digest and MAC.
    * Every other object rejects unknown, missing or wrongly typed fields with a
      :class:`MetadataError` that names the offending path.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ..errors import MetadataError, UnsupportedFormatError

FORMAT_MAGIC = "VNX-DNA"
FORMAT_VERSION = 4
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_STORED_BYTES = 1 << 40

FEATURES = frozenset({
    "chunked-v1",             # independent compressed/encrypted chunks, stripe aligned
    "outer-cauchy-rs-v1",     # outer Cauchy RS erasure code over GF(256)
    "inner-rs-v1",            # per-strand RS (0 parity bytes permitted)
    "frame4-crc32-v1",        # in-band identity + CRC-32
    "scrambler-shake128-v1",  # variant-selected keystream screening
    "mapping-2bit", "mapping-rotation3", "mapping-codebook8",
    "aes-256-gcm-hkdf-v1",    # encryption and manifest MAC
})

Hex16 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]
Hex32 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Hex64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNeg = Annotated[int, Field(ge=0, le=MAX_STORED_BYTES)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Encoder(_Model):
    name: Literal["vnxdna"]
    version: Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+([a-z0-9.+-]{0,20})$")]


class Compression(_Model):
    algorithm: Literal["none", "zlib", "zstd"]
    level: Annotated[int, Field(ge=0, le=22)]


class Encryption(_Model):
    algorithm: Literal["none", "AES-256-GCM"]
    kdf: Literal["HKDF-SHA256"] | None
    salt: Hex32 | None
    key_check: Hex16 | None


class ChunkRecord(_Model):
    index: NonNeg
    stored_size: NonNeg
    stored_sha256: Hex64
    first_stripe: NonNeg
    stripe_count: NonNeg


class ContentChunk(_Model):
    index: NonNeg
    offset: NonNeg
    size: NonNeg
    sha256: Hex64


class Content(_Model):
    name: Annotated[str, StringConstraints(min_length=1, max_length=255)] | None
    size: NonNeg
    sha256: Hex64
    chunks: list[ContentChunk]


class ErasureCode(_Model):
    algorithm: Literal["cauchy-rs-gf256"]
    data_shards: Annotated[int, Field(ge=1, le=256)]
    parity_shards: Annotated[int, Field(ge=0, le=255)]
    stripe_count: Annotated[int, Field(ge=0, lt=1 << 24)]


class StrandFormat(_Model):
    frame_format: Literal[4]
    mapping: Literal["2bit", "rotation3", "codebook8"]
    payload_bytes: Annotated[int, Field(ge=1, le=242)]
    inner_ecc: Literal["reed-solomon-gf256"]
    inner_parity_bytes: Annotated[int, Field(ge=0, le=64)]
    crc: Literal["crc32"]
    scrambler: Literal["shake128"]
    frame_bytes: Annotated[int, Field(ge=14, le=255)]
    strand_nt: Annotated[int, Field(ge=1, le=2040)]


class Constraints(_Model):
    gc_min_percent: Annotated[int, Field(ge=0, le=100)]
    gc_max_percent: Annotated[int, Field(ge=0, le=100)]
    max_homopolymer: Annotated[int, Field(ge=0, le=2040)]
    gc_window_nt: Annotated[int, Field(ge=0, le=2040)]
    forbidden_motifs: list[Annotated[str, StringConstraints(pattern=r"^[ACGT]{1,64}$")]]
    check_reverse_complement: bool


class DnaManifest(_Model):
    enabled: bool
    scheme: Literal["cauchy-rs-gf256-8+8"]


class Seal(_Model):
    manifest_sha256: Hex64
    manifest_hmac_sha256: Hex64 | None


class Manifest(_Model):
    format: Literal["VNX-DNA"]
    format_version: Literal[4]
    encoder: Encoder
    required_features: list[str]
    archive_id: Hex32
    created_at: Annotated[str, StringConstraints(max_length=64)] | None
    chunk_size: Annotated[int, Field(ge=1, le=1 << 30)]
    compression: Compression
    encryption: Encryption
    stored_size: NonNeg
    stored_sha256: Hex64
    chunks: list[ChunkRecord]
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
        return int(self.archive_id[:6], 16)

    @property
    def stripe_bytes(self) -> int:
        return self.erasure_code.data_shards * self.strand.payload_bytes

    def sealed_bytes(self) -> bytes:
        if self.sealed_content is None:
            raise MetadataError("manifest has no sealed content")
        return base64.b64decode(self.sealed_content, validate=True)


# ---------------------------------------------------------------- canonical JSON
def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MetadataError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise MetadataError(f"non-finite number {name} is not allowed")


def _no_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise MetadataError(f"floating-point value at {path} is not allowed in a canonical manifest")
    if isinstance(value, dict):
        for k, v in value.items():
            _no_floats(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _no_floats(v, f"{path}[{i}]")


def canonical_bytes(value: Any) -> bytes:
    _no_floats(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def parse_json(data: bytes) -> Any:
    if len(data) > MAX_MANIFEST_BYTES:
        raise MetadataError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise MetadataError(f"manifest is not valid JSON: {error}") from error
    _no_floats(value)
    return value


def digest_payload(raw: dict) -> bytes:
    return canonical_bytes({k: v for k, v in raw.items() if k != "seal"})


def sniff(raw: Any) -> None:
    """Fail fast on non-format-4 inputs (before strict validation)."""
    if not isinstance(raw, dict):
        raise MetadataError("manifest must be a JSON object")
    if raw.get("format") != FORMAT_MAGIC:
        raise UnsupportedFormatError(f"not a VNX-DNA manifest (format={raw.get('format')!r})")
    if raw.get("format_version") != FORMAT_VERSION or isinstance(raw.get("format_version"), bool):
        raise UnsupportedFormatError(f"unsupported VNX-DNA format_version {raw.get('format_version')!r}; this decoder reads 4 (and legacy 1-3)")


def _format_validation_error(error: ValidationError) -> str:
    parts = []
    for item in error.errors()[:5]:
        location = ".".join(str(x) for x in item["loc"]) or "$"
        parts.append(f"{location}: {item['msg']}")
    more = f" (+{error.error_count() - 5} more)" if error.error_count() > 5 else ""
    return "; ".join(parts) + more


def validate(raw: Any) -> Manifest:
    """Schema + semantic validation. Does not check the seal."""
    sniff(raw)
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
        if m.content is not None or m.sealed_content is None or m.seal.manifest_hmac_sha256 is None:
            fail("encrypted archive must have sealed_content, no clear content, and a manifest HMAC")
    else:
        if enc.kdf is not None or enc.salt is not None or enc.key_check is not None:
            fail("unencrypted archive must not record key material")
        if m.content is None or m.sealed_content is not None or m.seal.manifest_hmac_sha256 is not None:
            fail("unencrypted archive must have clear content and no sealed content or HMAC")
    level_range = {"none": (0, 0), "zlib": (0, 9), "zstd": (1, 22)}[m.compression.algorithm]
    if not level_range[0] <= m.compression.level <= level_range[1]:
        fail("compression level out of range for algorithm")
    code = m.erasure_code
    if code.data_shards + code.parity_shards > 256:
        fail("data_shards + parity_shards exceeds 256")
    s = m.strand
    from ..dna.mapping import get_mapping
    frame = 13 + s.payload_bytes + s.inner_parity_bytes
    if s.frame_bytes != frame or frame > 255 or s.strand_nt != frame * get_mapping(s.mapping).nt_per_byte:
        fail("strand geometry fields are inconsistent")
    if s.inner_parity_bytes % 2:
        fail("inner_parity_bytes must be even")
    c = m.constraints
    if c.gc_min_percent > c.gc_max_percent:
        fail("gc_min_percent exceeds gc_max_percent")
    stripe_bytes = m.stripe_bytes
    next_stripe = 0
    total = 0
    for i, chunk in enumerate(m.chunks):
        if chunk.index != i:
            fail(f"chunk {i} has index {chunk.index}")
        if chunk.first_stripe != next_stripe:
            fail(f"chunk {i} first_stripe {chunk.first_stripe} != {next_stripe}")
        if chunk.stripe_count != -(-chunk.stored_size // stripe_bytes):
            fail(f"chunk {i} stripe_count does not match stored_size")
        next_stripe += chunk.stripe_count
        total += chunk.stored_size
    if not m.chunks:
        fail("an archive has at least one chunk")
    if next_stripe != code.stripe_count:
        fail("stripe_count does not equal the sum of chunk stripes")
    if total != m.stored_size:
        fail("stored_size does not equal the sum of chunk stored sizes")
    if m.content is not None:
        check_content(m, m.content)


def check_content(m: Manifest, content: Content) -> None:
    if len(content.chunks) != len(m.chunks):
        raise MetadataError("manifest inconsistency: content chunk count differs from chunk records")
    offset = 0
    for i, chunk in enumerate(content.chunks):
        expected = min(m.chunk_size, content.size - offset) if content.size else 0
        if chunk.index != i or chunk.offset != offset or chunk.size != expected:
            raise MetadataError(f"manifest inconsistency: content chunk {i} offset/size mismatch")
        offset += chunk.size
    if offset != content.size or len(content.chunks) != max(1, -(-content.size // m.chunk_size)):
        raise MetadataError("manifest inconsistency: content chunks do not cover the content size")
    if content.name is not None and (re.search(r"[/\\\x00-\x1f]", content.name) or content.name in (".", "..")):
        raise MetadataError("manifest inconsistency: content name must be a plain file name")


def parse_content(data: bytes) -> Content:
    raw = parse_json(data)
    try:
        return Content.model_validate(raw)
    except ValidationError as error:
        raise MetadataError("sealed content schema violation: " + _format_validation_error(error)) from None


def required_features(mapping: str, encrypted: bool) -> set[str]:
    features = {"chunked-v1", "outer-cauchy-rs-v1", "inner-rs-v1", "frame4-crc32-v1", "scrambler-shake128-v1", f"mapping-{mapping}"}
    if encrypted:
        features.add("aes-256-gcm-hkdf-v1")
    return features


def compute_digest(raw: dict) -> str:
    return hashlib.sha256(digest_payload(raw)).hexdigest()
