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


_NT_FIELDS = np.uint32(0x03030303)      # the four 2-bit codes of one output byte inside its little-endian uint32
_NT_MUL = np.uint32(0x40100401)         # 1 + 2^10 + 2^20 + 2^30: lands code k of the uint32 at bits 30 - 2k .. 31 - 2k


def nt_to_bytes(nt: np.ndarray) -> np.ndarray:
    """(N, 4B) base codes (0..3) → (N, B) bytes."""
    if (nt.dtype == np.uint8 and nt.ndim == 2 and nt.shape[1] % 4 == 0 and nt.shape[1] and nt.flags.c_contiguous
            and nt.flags.aligned):
        # V13 C3: one uint32 per output byte. With the codes masked to 2 bits, x * _NT_MUL puts code 0 at bits 30-31,
        # code 1 at 28-29, code 2 at 26-27 and code 3 at 24-25 (the other partial products occupy disjoint 2-bit
        # slots below bit 24, so nothing carries); the top byte is the result. Same bytes as the reference form below
        # on every input (tests/v13/test_nt_to_bytes.py).
        x = nt.view("<u4") & _NT_FIELDS
        x *= _NT_MUL
        x >>= 24
        return x.astype(np.uint8)
    return nt_to_bytes_reference(nt)


def nt_to_bytes_reference(nt: np.ndarray) -> np.ndarray:
    """The shift-and-or form of :func:`nt_to_bytes` (the specification; any shape, dtype or memory layout)."""
    q = nt.reshape(nt.shape[0], -1, 4).astype(np.uint8) & 3
    return (q[..., 0] << 6) | (q[..., 1] << 4) | (q[..., 2] << 2) | q[..., 3]
