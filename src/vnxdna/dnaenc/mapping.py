"""2-bit base mapping (A=00 C=01 G=10 T=11, most significant first) and the ASCII ↔ code tables.

``bytes_to_nt``/``nt_to_bytes`` are split from ``vnxdna.v4.frame``; the ASCII tables moved verbatim from the V1 module
``vnxdna.dna.mapping`` (which re-imports them), so the strand file I/O no longer depends on V1 code (V6 Phase 2, M3).
"""
from __future__ import annotations

import numpy as np

BASES = "ACGT"
INVALID = 4
_ASCII_TO_CODE = np.full(256, 255, dtype=np.uint8)
for _code, _base in enumerate(BASES):
    _ASCII_TO_CODE[ord(_base)] = _code
    _ASCII_TO_CODE[ord(_base.lower())] = _code
_ASCII_TO_CODE[ord("N")] = INVALID
_ASCII_TO_CODE[ord("n")] = INVALID
_CODE_TO_ASCII = np.frombuffer(b"ACGTN", dtype=np.uint8)
_COMPLEMENT = np.array([3, 2, 1, 0, INVALID], dtype=np.uint8)
del _code, _base


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
