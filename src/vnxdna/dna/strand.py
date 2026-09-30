"""VNX-DNA format-4 strand frame: in-band identity, integrity, and inner ECC.

Byte layout of one strand before DNA mapping (big-endian integers)::

    offset  size  field
    0       1     variant v       screening/scrambler selector (not scrambled)
    1       1     kind/version    high nibble = 4 (frame format), low nibble = kind (0 data, 1 metadata)
    2       3     archive tag     first 3 bytes of the archive ID
    5       3     stripe index    0 .. 2^24-1
    8       1     shard index     0 .. K+M-1
    9       P     payload         one outer-code shard
    9+P     4     CRC-32          zlib/IEEE CRC over bytes 1 .. 8+P (unscrambled)
    13+P    r     inner RS parity RS over GF(256) of bytes 0 .. 12+P

Bytes 1 .. 12+P are XORed with keystream ``SHAKE128("VNX-DNA/4 scrambler" ‖ v)``.
The encoder tries v = 0, 1, … until the mapped sequence satisfies the
:class:`~vnxdna.dna.constraints.ConstraintSpec` ("screening"). The decoder
reads v from the frame, so the constraint settings are not needed to decode.

Everything a decoder needs to place a strand (archive, kind, stripe, shard)
is inside the DNA sequence and covered by the CRC and the inner code. FASTA
headers written by the encoder are labels only and are never trusted.

Checks and their scope:

* CRC-32 detects accidental corruption of a strand (undetected-error
  probability ≈ 2^-32 per corrupted strand). It is not authentication.
* The inner RS corrects ``e`` errors plus ``f`` erasures with ``2e+f ≤ r``.
"""
from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field

import numpy as np

from .constraints import ConstraintSpec, satisfied
from .mapping import get_mapping
from ..ecc.inner_rs import MAX_CODEWORD, InnerReedSolomon
from ..errors import ConfigurationError, ConstraintError

FRAME_FORMAT = 4
KIND_DATA = 0
KIND_META = 1
HEADER_BYTES = 9
CRC_BYTES = 4
MAX_STRIPES = 1 << 24
TAG_BYTES = 3


def _keystreams() -> np.ndarray:
    return np.stack([np.frombuffer(hashlib.shake_128(b"VNX-DNA/4 scrambler" + bytes([v])).digest(MAX_CODEWORD), dtype=np.uint8)
                     for v in range(256)])


KEYSTREAMS = _keystreams()


@dataclass(frozen=True)
class StrandGeometry:
    mapping: str
    payload_bytes: int
    inner_parity_bytes: int
    _inner: InnerReedSolomon = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        get_mapping(self.mapping)
        if not isinstance(self.payload_bytes, int) or isinstance(self.payload_bytes, bool) or self.payload_bytes < 1:
            raise ConfigurationError("payload_bytes must be a positive integer")
        object.__setattr__(self, "_inner", InnerReedSolomon(self.inner_parity_bytes))
        if self.frame_bytes > MAX_CODEWORD:
            raise ConfigurationError(f"frame of {self.frame_bytes} bytes exceeds the {MAX_CODEWORD}-byte RS codeword limit")

    @property
    def systematic_bytes(self) -> int:
        return HEADER_BYTES + self.payload_bytes + CRC_BYTES

    @property
    def frame_bytes(self) -> int:
        return self.systematic_bytes + self.inner_parity_bytes

    @property
    def strand_nt(self) -> int:
        return self.frame_bytes * get_mapping(self.mapping).nt_per_byte

    @property
    def inner(self) -> InnerReedSolomon:
        return self._inner

    def to_dict(self) -> dict:
        return {"mapping": self.mapping, "payload_bytes": self.payload_bytes, "inner_parity_bytes": self.inner_parity_bytes,
                "frame_bytes": self.frame_bytes, "strand_nt": self.strand_nt}


@dataclass(frozen=True)
class ParsedStrand:
    kind: int
    tag: int
    stripe: int
    shard: int
    payload: bytes
    corrected_symbols: int
    orientation: str  # "forward" | "reverse_complement"
    repair: str = "none"  # "none" | "indel" (experimental realignment)


def build_strands(geometry: StrandGeometry, spec: ConstraintSpec, tag: int, kinds: np.ndarray, stripes: np.ndarray,
                  shards: np.ndarray, payloads: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Build and screen strands. Returns (codes (N, strand_nt), variants (N,))."""
    n = payloads.shape[0]
    p = geometry.payload_bytes
    if payloads.shape != (n, p):
        raise ValueError("payload array has wrong shape")
    if n and (int(stripes.max()) >= MAX_STRIPES or int(shards.max()) > 255):
        raise ConfigurationError("archive exceeds the frame's stripe/shard address space")
    body = np.empty((n, 8 + p), dtype=np.uint8)
    body[:, 0] = (FRAME_FORMAT << 4) | kinds.astype(np.uint8)
    body[:, 1:4] = np.frombuffer(tag.to_bytes(TAG_BYTES, "big"), dtype=np.uint8)
    stripes = stripes.astype(np.uint32)
    body[:, 4] = (stripes >> 16) & 0xFF
    body[:, 5] = (stripes >> 8) & 0xFF
    body[:, 6] = stripes & 0xFF
    body[:, 7] = shards.astype(np.uint8)
    body[:, 8:] = payloads
    crc = np.array([zlib.crc32(row.tobytes()) for row in body], dtype=np.uint32)
    plain = np.empty((n, 12 + p), dtype=np.uint8)
    plain[:, :8 + p] = body
    for i, shift in enumerate((24, 16, 8, 0)):
        plain[:, 8 + p + i] = (crc >> shift) & 0xFF
    mapping = get_mapping(geometry.mapping)
    codes = np.empty((n, geometry.strand_nt), dtype=np.uint8)
    variants = np.zeros(n, dtype=np.uint8)
    pending = np.arange(n)
    for v in range(256):
        if pending.size == 0:
            break
        system = np.empty((pending.size, geometry.systematic_bytes), dtype=np.uint8)
        system[:, 0] = v
        system[:, 1:] = plain[pending] ^ KEYSTREAMS[v, :12 + p]
        frame = np.concatenate([system, geometry.inner.encode_batch(system)], axis=1)
        candidate = mapping.encode(frame)
        ok = satisfied(candidate, spec)
        done = pending[ok]
        codes[done] = candidate[ok]
        variants[done] = v
        pending = pending[~ok]
    if pending.size:
        raise ConstraintError(f"{pending.size} strand(s) cannot satisfy the DNA constraints with any of 256 scrambler variants",
                              details={"failed_strands": int(pending.size), "constraints": spec.to_dict()})
    return codes, variants


def _parse_plain(geometry: StrandGeometry, system: bytes) -> tuple[int, int, int, int, bytes] | None:
    p = geometry.payload_bytes
    v = system[0]
    plain = (np.frombuffer(system[1:], dtype=np.uint8) ^ KEYSTREAMS[v, :12 + p]).tobytes()
    body, crc = plain[:8 + p], plain[8 + p:]
    if zlib.crc32(body) != int.from_bytes(crc, "big"):
        return None
    if body[0] >> 4 != FRAME_FORMAT or (body[0] & 0x0F) not in (KIND_DATA, KIND_META):
        return None
    return body[0] & 0x0F, int.from_bytes(body[1:4], "big"), int.from_bytes(body[4:7], "big"), body[7], body[8:]


def parse_frame(geometry: StrandGeometry, frame: np.ndarray, erasures: np.ndarray) -> tuple[tuple, int] | None:
    """Validate one demapped frame; use the inner code if the CRC fails."""
    s = geometry.systematic_bytes
    raw = frame.tobytes()
    if not erasures.any():
        parsed = _parse_plain(geometry, raw[:s])
        if parsed is not None:
            return parsed, 0
    flagged = np.flatnonzero(erasures).tolist()
    attempts = [flagged] if len(flagged) <= geometry.inner_parity_bytes else []
    if flagged:
        attempts.append([])  # flags can be wrong (e.g. rotation code); also try errors-only decoding
    for erase in attempts or [[]]:
        corrected = geometry.inner.correct(raw, erase)
        if corrected is None:
            continue
        message, count = corrected
        parsed = _parse_plain(geometry, message[:s])
        if parsed is not None:
            return parsed, max(count, 1)
    return None
