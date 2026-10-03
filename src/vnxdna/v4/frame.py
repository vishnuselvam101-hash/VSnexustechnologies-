"""Strand frame v4: explicit address + payload + CRC + inner RS, mapped to DNA with periodic sync markers.

Frame bytes (before DNA mapping; normative spec docs/VNX4_FORMAT.md §8)::

    offset  size  field
    0       1     scrambler variant v (not scrambled)
    1       1     (4 << 4) | kind          kind 0 = data symbol, 1 = superblock symbol
    2       2     archive tag (first 2 bytes of the archive ID)
    4       4     group (outer-code block) index
    8       2     symbol index within the group
    10      P     payload (one outer-code symbol)
    10+P    4     CRC-32 (IEEE) of bytes 1 … 9+P (unscrambled)
    14+P    r     inner RS parity over bytes 0 … 13+P as transmitted (scrambled)

Bytes 1 … 13+P are XORed with SHAKE-128("VNX4 scrambler" ‖ v). The encoder
chooses the smallest v whose final strand satisfies the constraints.

DNA: 2 bits per base (A=00 C=01 G=10 T=11, most significant first). After
every ``marker_period`` nt of frame bases a marker of ``marker_len`` nt is
inserted (markers rotate through a fixed table). Markers carry no data; they
let the decoder re-synchronise after insertions/deletions and turn an indel
into a bounded run of *erasures* for the inner code (vnxdna.v4.sync).
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass

import numpy as np

from ..v2.crc import crc32_bytes_be, crc32_rows
from .codecs import InnerRS
from .constraints import ConstraintConfig, satisfied_batch, to_codes
from .errors import VNXConfigurationError, VNXConstraintError
from .version import FRAME_VERSION

HEADER_BYTES = 10
CRC_BYTES = 4
KIND_DATA = 0
KIND_SUPER = 1
VARIANTS = 256

# Marker tables: no internal homopolymer, balanced GC; rotating identities make a one-period slip detectable.
MARKER_TABLES = {
    0: [],
    1: ["A", "C", "G", "T"],
    2: ["AC", "GT", "CA", "TG"],
    3: ["ACG", "TGC", "CAT", "GTA"],
    4: ["ACGT", "TGCA", "CATG", "GTAC"],
    5: ["ACGTC", "TGCAG", "CATGA", "GTACT"],
    6: ["ACGTCA", "TGCAGT", "CATGAC", "GTACTG"],
}


@dataclass(frozen=True)
class Layout:
    payload_bytes: int = 40
    inner_parity: int = 16
    marker_period: int = 32   # nt of frame bases between markers (multiple of 4; 0 = no markers)
    marker_len: int = 2

    def validate(self) -> "Layout":
        if not 1 <= self.payload_bytes <= 200:
            raise VNXConfigurationError("payload_bytes must be in 1..200")
        if not 0 <= self.inner_parity <= 64 or self.inner_parity % 2:
            raise VNXConfigurationError("inner_parity must be an even number in 0..64")
        if self.frame_bytes > 255:
            raise VNXConfigurationError(f"frame of {self.frame_bytes} bytes exceeds one RS codeword (255)")
        if self.marker_period:
            if self.marker_period % 4 or not 8 <= self.marker_period <= 256:
                raise VNXConfigurationError("marker_period must be a multiple of 4 in 8..256 (or 0 to disable markers)")
            if self.marker_len not in MARKER_TABLES or self.marker_len == 0:
                raise VNXConfigurationError(f"marker_len must be one of {sorted(k for k in MARKER_TABLES if k)}")
        elif self.marker_len:
            raise VNXConfigurationError("marker_len must be 0 when markers are disabled")
        return self

    @property
    def frame_bytes(self) -> int:
        return HEADER_BYTES + self.payload_bytes + CRC_BYTES + self.inner_parity

    @property
    def frame_nt(self) -> int:
        return 4 * self.frame_bytes

    @property
    def markers(self) -> int:
        if not self.marker_period:
            return 0
        return max(0, -(-self.frame_nt // self.marker_period) - 1)

    @property
    def strand_nt(self) -> int:
        return self.frame_nt + self.markers * self.marker_len

    @property
    def segment_bytes(self) -> int:
        return self.marker_period // 4 if self.marker_period else self.frame_bytes

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(frame_bytes=self.frame_bytes, strand_nt=self.strand_nt, markers=self.markers)
        return d

    # ---------------------------------------------------------------- template geometry
    def template(self) -> tuple[np.ndarray, np.ndarray]:
        """(strand_nt,) template: marker base code at marker positions, -1 at frame positions; and frame→strand index map."""
        tpl = np.full(self.strand_nt, -1, dtype=np.int8)
        frame_pos = np.empty(self.frame_nt, dtype=np.int64)
        table = MARKER_TABLES[self.marker_len]
        s = 0
        f = 0
        k = 0
        while f < self.frame_nt:
            take = min(self.marker_period or self.frame_nt, self.frame_nt - f)
            frame_pos[f:f + take] = np.arange(s, s + take)
            s += take
            f += take
            if f < self.frame_nt and self.marker_period:
                m = to_codes(table[k % len(table)])
                tpl[s:s + self.marker_len] = m
                s += self.marker_len
                k += 1
        return tpl, frame_pos


PROFILES = {
    # name: (layout, outer K, outer M) — redundancy budgets are explicit; see docs/V4_ARCHITECTURE.md
    # v4-balanced uses 3-nt markers every 24 nt since round 2 of the experiments (EXP-0009: 10/10 vs 2/10 recoveries at
    # 0.2 % insertions + 0.2 % deletions, coverage 1, for +5.8 % nucleotides); round 1 used Layout(40, 16, 32, 2)
    "v4-balanced": (Layout(40, 16, 24, 3), 64, 16),
    "v4-dense": (Layout(44, 12, 0, 0), 64, 16),
    "v4-indel": (Layout(36, 20, 24, 3), 48, 16),
    "v4-archival": (Layout(36, 20, 24, 3), 32, 32),
}


# ============================================================================ helpers
_KEYSTREAM: dict[int, np.ndarray] = {}


def keystreams(span: int) -> np.ndarray:
    ks = _KEYSTREAM.get(span)
    if ks is None:
        ks = np.stack([np.frombuffer(hashlib.shake_128(b"VNX4 scrambler" + bytes([v])).digest(span), dtype=np.uint8)
                       for v in range(VARIANTS)])
        _KEYSTREAM[span] = ks
    return ks


def bytes_to_nt(frames: np.ndarray) -> np.ndarray:
    """(N, B) bytes → (N, 4B) base codes, most significant bits first."""
    f = frames.astype(np.uint8)
    out = np.empty((f.shape[0], f.shape[1], 4), dtype=np.uint8)
    out[..., 0] = f >> 6
    out[..., 1] = (f >> 4) & 3
    out[..., 2] = (f >> 2) & 3
    out[..., 3] = f & 3
    return out.reshape(f.shape[0], -1)


def nt_to_bytes(nt: np.ndarray) -> np.ndarray:
    """(N, 4B) base codes (0..3) → (N, B) bytes."""
    q = nt.reshape(nt.shape[0], -1, 4).astype(np.uint8) & 3
    return (q[..., 0] << 6) | (q[..., 1] << 4) | (q[..., 2] << 2) | q[..., 3]


def insert_markers(layout: Layout, frame_nt: np.ndarray) -> np.ndarray:
    tpl, pos = layout.template()
    out = np.empty((frame_nt.shape[0], layout.strand_nt), dtype=np.uint8)
    marker_pos = tpl >= 0
    out[:, marker_pos] = tpl[marker_pos].astype(np.uint8)
    out[:, pos] = frame_nt
    return out


def plain_rows(tag: int, kind: int | np.ndarray, groups: np.ndarray, symbols: np.ndarray, payloads: np.ndarray) -> np.ndarray:
    """(N, 9 + P + 4) unscrambled bytes 1 … 13+P (header without variant, payload, CRC)."""
    n, p = payloads.shape
    rows = np.empty((n, HEADER_BYTES - 1 + p + CRC_BYTES), dtype=np.uint8)
    rows[:, 0] = (FRAME_VERSION << 4) | np.asarray(kind, dtype=np.uint8)
    rows[:, 1] = (tag >> 8) & 0xFF
    rows[:, 2] = tag & 0xFF
    rows[:, 3:7] = np.asarray(groups, dtype=">u4").view(np.uint8).reshape(n, 4)
    rows[:, 7:9] = np.asarray(symbols, dtype=">u2").view(np.uint8).reshape(n, 2)
    rows[:, 9:9 + p] = payloads
    rows[:, 9 + p:] = crc32_bytes_be(crc32_rows(rows[:, : 9 + p]))
    return rows


def build_strands(layout: Layout, cfg: ConstraintConfig, tag: int, kind: int | np.ndarray, groups: np.ndarray,
                  symbols: np.ndarray, payloads: np.ndarray, *, max_variants: int = VARIANTS) -> tuple[np.ndarray, np.ndarray]:
    """Frames → constraint-screened strands. Returns ((N, strand_nt) codes, (N,) chosen variant)."""
    plain = plain_rows(tag, kind, groups, symbols, payloads)
    n, span = plain.shape
    ks = keystreams(span)
    inner = InnerRS(layout.inner_parity)
    out = np.empty((n, layout.strand_nt), dtype=np.uint8)
    chosen = np.full(n, -1, dtype=np.int16)
    todo = np.arange(n)
    for v in range(max_variants):
        if todo.size == 0:
            break
        msg = np.empty((todo.size, 1 + span), dtype=np.uint8)
        msg[:, 0] = v
        msg[:, 1:] = plain[todo] ^ ks[v]
        frames = np.concatenate([msg, inner.parity(msg)], axis=1)
        strands = insert_markers(layout, bytes_to_nt(frames))
        ok = satisfied_batch(strands, cfg)
        good = todo[ok]
        out[good] = strands[ok]
        chosen[good] = v
        todo = todo[~ok]
    if todo.size:
        raise VNXConstraintError(f"{todo.size} strand(s) violate the constraints for all {max_variants} scrambler variants",
                                 details={"first_groups": np.asarray(groups)[todo[:10]].tolist(),
                                          "first_symbols": np.asarray(symbols)[todo[:10]].tolist()},
                                 hint="relax the constraints (GC range, homopolymer limit, motifs) or change the layout")
    return out, chosen


# ============================================================================ decoding helpers
@dataclass
class Parsed:
    ok: np.ndarray        # (N,) bool: RS-decoded (or clean) and CRC verified
    kind: np.ndarray
    tag: np.ndarray
    group: np.ndarray
    symbol: np.ndarray
    payload: np.ndarray   # (N, P)
    errata: np.ndarray    # symbols corrected by the inner code


def decode_frames(layout: Layout, frames: np.ndarray, erasures: np.ndarray | None = None, *,
                  errors_only_retry: bool = True) -> Parsed:
    """Inner RS decoding (errors + erasures) and CRC check for (N, frame_bytes) received frames."""
    n = frames.shape[0]
    p = layout.payload_bytes
    inner = InnerRS(layout.inner_parity)
    frames = np.ascontiguousarray(frames, dtype=np.uint8)
    span = HEADER_BYTES - 1 + p + CRC_BYTES
    ks = keystreams(span)
    # 1) CRC-first: a frame without flagged erasures whose CRC verifies is accepted without RS decoding (a corrupt frame
    #    passes the CRC with probability ~2^-32, the same check that guards every RS correction below)
    plain0 = frames[:, 1:1 + span] ^ ks[frames[:, 0]]
    clean = (crc32_bytes_be(crc32_rows(plain0[:, : 9 + p])) == plain0[:, 9 + p:]).all(axis=1)
    if erasures is not None:
        clean &= ~erasures.any(axis=1)
    fixed = frames.copy()
    ok = clean.copy()
    errata = np.zeros(n, dtype=np.int64)
    todo = np.flatnonzero(~clean)
    if todo.size:
        sub = frames[todo]
        if erasures is not None and erasures[todo].any():
            er = erasures[todo].copy()
            er[er.sum(axis=1) > layout.inner_parity] = False
        else:
            er = None
        f1, ok1, e1 = inner.decode(sub, er)
        if errors_only_retry and er is not None:
            retry = ~ok1
            if retry.any():
                f2, ok2, e2 = inner.decode(sub[retry])
                ridx = np.flatnonzero(retry)
                f1[ridx[ok2]] = f2[ok2]
                ok1[ridx[ok2]] = True
                e1[ridx[ok2]] = e2[ok2]
        fixed[todo], ok[todo], errata[todo] = f1, ok1, e1
    span = HEADER_BYTES - 1 + p + CRC_BYTES
    ks = keystreams(span)
    plain = fixed[:, 1:1 + span] ^ ks[fixed[:, 0]]
    crc = crc32_bytes_be(crc32_rows(plain[:, : 9 + p]))
    good = ok & (crc == plain[:, 9 + p:]).all(axis=1) & ((plain[:, 0] >> 4) == FRAME_VERSION) & ((plain[:, 0] & 15) <= KIND_SUPER)
    return Parsed(good, plain[:, 0] & 15, (plain[:, 1].astype(np.int64) << 8) | plain[:, 2],
                  plain[:, 3:7].copy().view(">u4").reshape(n).astype(np.int64),
                  plain[:, 7:9].copy().view(">u2").reshape(n).astype(np.int64), plain[:, 9:9 + p], errata)


def tentative_address(layout: Layout, frames: np.ndarray) -> np.ndarray:
    """Header fields read without correction: (N, 4) [kind, tag, group, symbol] (used to group failed reads)."""
    p = layout.payload_bytes
    span = HEADER_BYTES - 1 + p + CRC_BYTES
    ks = keystreams(span)
    hdr = frames[:, 1:HEADER_BYTES] ^ ks[frames[:, 0], : HEADER_BYTES - 1]
    n = frames.shape[0]
    out = np.empty((n, 4), dtype=np.int64)
    out[:, 0] = hdr[:, 0] & 15
    out[:, 1] = (hdr[:, 1].astype(np.int64) << 8) | hdr[:, 2]
    out[:, 2] = hdr[:, 3:7].copy().view(">u4").reshape(n)
    out[:, 3] = hdr[:, 7:9].copy().view(">u2").reshape(n)
    out[(hdr[:, 0] >> 4) != FRAME_VERSION] = -1
    return out
