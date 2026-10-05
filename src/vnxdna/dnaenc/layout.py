"""Strand layout of frame 4: header/CRC sizes, frame kinds, :class:`Layout` (payload, inner parity, marker geometry)
and the named strand profiles ``PROFILES`` (VNX4 §8, §11). Split from ``vnxdna.v4.frame`` (V6 Phase 2, M3)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.constraints import to_codes
from vnxdna.dnaenc.markers import MARKER_TABLES

HEADER_BYTES = 10
CRC_BYTES = 4
KIND_DATA = 0
KIND_SUPER = 1
VARIANTS = 256


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
