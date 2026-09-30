"""Strand frame format 5: in-band identity, CRC-32 and inner Reed–Solomon.

Byte layout of one strand before DNA mapping (big-endian integers)::

    offset  size  field
    0       1     variant v        scrambler/screening selector (not scrambled)
    1       1     version/kind     high nibble 5 (frame format), low nibble kind (0 data, 1 metadata)
    2       4     archive tag      first 4 bytes of the 128-bit archive ID
    6       4     stripe index     0 .. 2^32-1 (ECC group; the chunk follows from the manifest index)
    10      1     shard index      0 .. K+M-1 (data shards first)
    11      P     payload          one outer-code shard
    11+P    4     CRC-32           zlib/IEEE over bytes 1 .. 10+P, before scrambling
    15+P    r     inner RS parity  RS over GF(2^8)/0x11D (fcr 0, generator 2) of bytes 0 .. 14+P

Bytes 1 .. 14+P are XORed with ``SHAKE128("VNX-DNA/5 scrambler" ‖ v)``. The
encoder tries v = 0, 1, … until the mapped sequence satisfies the constraints.

Differences from frame format 4 (V1): the stripe index grows from 24 to 32
bits (V1 was limited to 2^24 stripes, about 42 GB at the default geometry),
the archive tag from 24 to 32 bits, and CRC checks run vectorised over whole
batches of strands (:mod:`vnxdna.v2.crc`).

Everything needed to place a strand (archive, kind, stripe, shard) is inside
the DNA and covered by the CRC and the inner code, so recovery never depends on
FASTA/FASTQ headers. The chunk and the byte offset follow from the stripe
through the authenticated chunk index.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from ..dna.constraints import ConstraintSpec
from ..dna.mapping import get_mapping
from ..ecc.inner_rs import MAX_CODEWORD, InnerReedSolomon
from ..errors import ConfigurationError, ConstraintError
from .constraints import satisfied_v2
from .crc import crc32_bytes_be, crc32_rows

FRAME_FORMAT = 5
KIND_DATA = 0
KIND_META = 1
HEADER_BYTES = 11
CRC_BYTES = 4
TAG_BYTES = 4
MAX_STRIPES = 1 << 32


def _keystreams() -> np.ndarray:
    return np.stack([np.frombuffer(hashlib.shake_128(b"VNX-DNA/5 scrambler" + bytes([v])).digest(MAX_CODEWORD), dtype=np.uint8)
                     for v in range(256)])


KEYSTREAMS = _keystreams()


@dataclass(frozen=True)
class FrameGeometry:
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
    def nt_per_byte(self) -> int:
        return get_mapping(self.mapping).nt_per_byte

    @property
    def strand_nt(self) -> int:
        return self.frame_bytes * self.nt_per_byte

    @property
    def inner(self) -> InnerReedSolomon:
        return self._inner

    def to_dict(self) -> dict:
        return {"frame_format": FRAME_FORMAT, "mapping": self.mapping, "payload_bytes": self.payload_bytes,
                "inner_parity_bytes": self.inner_parity_bytes, "header_bytes": HEADER_BYTES,
                "frame_bytes": self.frame_bytes, "strand_nt": self.strand_nt}


def _plain_rows(tag: int, kinds: np.ndarray, stripes: np.ndarray, shards: np.ndarray, payloads: np.ndarray) -> np.ndarray:
    """Unscrambled bytes 1 .. 14+P of every frame: version/kind, tag, stripe, shard, payload, CRC."""
    n, p = payloads.shape
    body = np.empty((n, 10 + p), dtype=np.uint8)
    body[:, 0] = (FRAME_FORMAT << 4) | kinds.astype(np.uint8)
    body[:, 1:5] = np.frombuffer(int(tag).to_bytes(TAG_BYTES, "big"), dtype=np.uint8)
    body[:, 5:9] = stripes.astype(">u4").view(np.uint8).reshape(n, 4)
    body[:, 9] = shards.astype(np.uint8)
    body[:, 10:] = payloads
    return np.concatenate([body, crc32_bytes_be(crc32_rows(body))], axis=1)


def build_strands(geometry: FrameGeometry, spec: ConstraintSpec, tag: int, kinds: np.ndarray, stripes: np.ndarray,
                  shards: np.ndarray, payloads: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Build and screen strands. Returns (codes (N, strand_nt), variants (N,)).

    Raises :class:`ConstraintError` if some frame satisfies the constraints under
    none of the 256 scrambler variants. No strand that violates them is returned.
    """
    n = payloads.shape[0]
    p = geometry.payload_bytes
    if payloads.shape != (n, p):
        raise ValueError("payload array has wrong shape")
    if n and (int(stripes.max()) >= MAX_STRIPES or int(stripes.min()) < 0 or int(shards.max()) > 255):
        raise ConfigurationError("archive exceeds the frame's stripe/shard address space")
    plain = _plain_rows(tag, kinds, stripes, shards, payloads)
    if geometry.mapping == "2bit":
        codes, variants, pending = _screen_linear_2bit(geometry, spec, plain)
    else:
        codes, variants, pending = _screen_direct(geometry, spec, plain)
    if pending.size:
        raise ConstraintError(f"{pending.size} strand(s) cannot satisfy the DNA constraints with any of 256 scrambler variants",
                              details={"failed_strands": int(pending.size), "constraints": spec.to_dict()})
    return codes, variants


def _screen_direct(geometry: FrameGeometry, spec: ConstraintSpec, plain: np.ndarray):
    """Reference screening: build the full frame for each variant, map it and test the constraints."""
    n = plain.shape[0]
    mapping = get_mapping(geometry.mapping)
    codes = np.empty((n, geometry.strand_nt), dtype=np.uint8)
    variants = np.zeros(n, dtype=np.uint8)
    pending = np.arange(n)
    width = geometry.systematic_bytes - 1
    for v in range(256):
        if pending.size == 0:
            break
        system = np.empty((pending.size, geometry.systematic_bytes), dtype=np.uint8)
        system[:, 0] = v
        system[:, 1:] = plain[pending] ^ KEYSTREAMS[v, :width]
        frame = np.concatenate([system, rs_parity(geometry, system)], axis=1)
        candidate = mapping.encode(frame)
        ok = satisfied_v2(candidate, spec)
        done = pending[ok]
        codes[done] = candidate[ok]
        variants[done] = v
        pending = pending[~ok]
    return codes, variants, pending


_VARIANT_MASKS: dict[tuple[int, int], np.ndarray] = {}


def _variant_masks(geometry: FrameGeometry) -> np.ndarray:
    """(256, strand_nt) 2-bit masks: the codes of frame([v, keystream_v, parity([v, keystream_v])])."""
    key = (geometry.payload_bytes, geometry.inner_parity_bytes)
    if key not in _VARIANT_MASKS:
        width = geometry.systematic_bytes - 1
        system = np.empty((256, geometry.systematic_bytes), dtype=np.uint8)
        system[:, 0] = np.arange(256)
        system[:, 1:] = KEYSTREAMS[:, :width]
        frame = np.concatenate([system, rs_parity(geometry, system)], axis=1)
        _VARIANT_MASKS[key] = get_mapping("2bit").encode(frame)
    return _VARIANT_MASKS[key]


def _screen_linear_2bit(geometry: FrameGeometry, spec: ConstraintSpec, plain: np.ndarray):
    """Same result as :func:`_screen_direct` for the 2bit mapping, computing parity and mapping once per strand.

    The systematic part for variant v is ``[0, plain] XOR [v, keystream_v]``.
    RS parity is linear over GF(2^8) (addition is XOR), so
    ``parity(v) = parity([0, plain]) XOR parity([v, keystream_v])``. The 2bit
    mapping writes each byte as four 2-bit symbols, so it commutes with XOR as
    well. Hence ``codes(v) = codes(v=0 frame) XOR mask_v`` with a constant
    per-variant mask, and each extra variant costs one XOR plus the
    constraint check. Tests assert byte equality with the direct method.
    """
    n = plain.shape[0]
    system = np.empty((n, geometry.systematic_bytes), dtype=np.uint8)
    system[:, 0] = 0
    system[:, 1:] = plain
    base = get_mapping("2bit").encode(np.concatenate([system, rs_parity(geometry, system)], axis=1))
    masks = _variant_masks(geometry)
    codes = np.empty_like(base)
    variants = np.zeros(n, dtype=np.uint8)
    pending = np.arange(n)
    for v in range(256):
        if pending.size == 0:
            break
        candidate = base[pending] ^ masks[v]
        ok = satisfied_v2(candidate, spec)
        done = pending[ok]
        codes[done] = candidate[ok]
        variants[done] = v
        pending = pending[~ok]
    return codes, variants, pending


@dataclass
class ParsedBatch:
    """Vectorised parse result for N frames. Fields are valid where ``ok``."""

    ok: np.ndarray        # (N,) bool
    kind: np.ndarray      # (N,) uint8
    tag: np.ndarray       # (N,) uint32
    stripe: np.ndarray    # (N,) uint32
    shard: np.ndarray     # (N,) uint8
    payload: np.ndarray   # (N, P) uint8


def parse_batch(geometry: FrameGeometry, frames: np.ndarray, erasures: np.ndarray | None = None) -> ParsedBatch:
    """Check every frame's CRC and version without error correction."""
    s = geometry.systematic_bytes
    p = geometry.payload_bytes
    n = frames.shape[0]
    plain = frames[:, 1:s] ^ KEYSTREAMS[frames[:, 0], : s - 1]
    body = plain[:, : 10 + p]
    stored = plain[:, 10 + p: 14 + p].astype(np.uint32)
    crc_stored = (stored[:, 0] << 24) | (stored[:, 1] << 16) | (stored[:, 2] << 8) | stored[:, 3]
    ok = crc32_rows(body) == crc_stored
    ok &= (body[:, 0] >> 4) == FRAME_FORMAT
    kind = body[:, 0] & 0x0F
    ok &= kind <= KIND_META
    if erasures is not None:
        ok &= ~erasures.any(axis=1)
    tag = body[:, 1:5].copy().view(">u4").reshape(n).astype(np.uint32)
    stripe = body[:, 5:9].copy().view(">u4").reshape(n).astype(np.uint32)
    return ParsedBatch(ok, kind.astype(np.uint8), tag, stripe, body[:, 9].copy(), body[:, 10:].copy())


def parse_one_corrected(geometry: FrameGeometry, frame: np.ndarray, erasures: np.ndarray) -> tuple[tuple, int] | None:
    """Slow path for one frame whose CRC failed: inner RS errors-and-erasures decoding, then the CRC again.

    Returns ((kind, tag, stripe, shard, payload bytes), symbols corrected) or None.
    """
    raw = frame.tobytes()
    flagged = np.flatnonzero(erasures).tolist()
    attempts = [flagged] if len(flagged) <= geometry.inner_parity_bytes else []
    if flagged:
        attempts.append([])  # erasure flags can be wrong (e.g. the rotation code); also try errors-only decoding
    for erase in attempts or [[]]:
        corrected = geometry.inner.correct(raw, erase)
        if corrected is None:
            continue
        message, count = corrected
        row = np.frombuffer(message[: geometry.systematic_bytes], dtype=np.uint8)[None, :]
        parsed = parse_batch(geometry, row)
        if parsed.ok[0]:
            return ((int(parsed.kind[0]), int(parsed.tag[0]), int(parsed.stripe[0]), int(parsed.shard[0]),
                     parsed.payload[0].tobytes()), max(count, 1))
    return None


def tentative_address(geometry: FrameGeometry, frame: np.ndarray) -> tuple[int, int, int, int] | None:
    """Descramble the header of an unverified frame: (kind, tag, stripe, shard), or None if implausible.

    Used only to *propose* a cluster for reads that fail validation (for
    example because of an indel after the header). Consensus and the CRC decide
    afterwards; a wrong proposal can only cost a failed cluster, never wrong data.
    """
    ks = KEYSTREAMS[int(frame[0]), :HEADER_BYTES - 1]
    head = frame[1:HEADER_BYTES] ^ ks
    if head[0] >> 4 != FRAME_FORMAT or (head[0] & 0x0F) > KIND_META:
        return None
    return (int(head[0] & 0x0F), int.from_bytes(head[1:5].tobytes(), "big"), int.from_bytes(head[5:9].tobytes(), "big"),
            int(head[9]))


_PARITY_TABLES: dict[tuple[int, int], np.ndarray] = {}


def _parity_tables(k: int, nsym: int) -> np.ndarray:
    """(k, 256, nsym) table: row i, value x → x · (parity row i of the systematic RS generator)."""
    key = (k, nsym)
    if key not in _PARITY_TABLES:
        from ..ecc import gf256
        from ..ecc.inner_rs import _parity_matrix
        matrix = _parity_matrix(k, nsym)  # (k, nsym), bit-identical to reedsolo's encoder
        _PARITY_TABLES[key] = np.ascontiguousarray(gf256.MUL[:, matrix].transpose(1, 0, 2))  # MUL[x, m[i, j]] → [i, x, j]
    return _PARITY_TABLES[key]


def rs_parity(geometry: FrameGeometry, messages: np.ndarray) -> np.ndarray:
    """Inner RS parity (N, r) for systematic parts (N, k); equal to ``InnerReedSolomon.encode_batch``.

    RS parity is linear over GF(256), so it is the XOR over message columns of a
    per-column table lookup: k gathers of shape (N, r) instead of k·r scalar passes.
    """
    n, k = messages.shape
    r = geometry.inner_parity_bytes
    parity = np.zeros((n, r), dtype=np.uint8)
    if r == 0 or n == 0:
        return parity
    tables = _parity_tables(k, r)
    for i in range(k):
        parity ^= tables[i][messages[:, i]]
    return parity
