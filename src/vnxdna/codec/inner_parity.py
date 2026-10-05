"""Parity matrix of the systematic inner Reed-Solomon code, computed without ``reedsolo``.

Row ``i`` of ``parity_matrix(k, nsym)`` is the parity of the unit message ``e_i``: the remainder of ``e_i · x^nsym``
modulo the generator ``g(x) = Π_{j<nsym} (x - α^j)`` over GF(256) (primitive polynomial 0x11D, α = 2, first
consecutive root α^0). This is exactly the code of ``reedsolo.RSCodec(nsym, fcr=0, prim=0x11D, generator=2)`` used by
V1-V5 (``vnxdna.ecc.inner_rs._parity_matrix``); a test compares both for every parity size the formats allow. Moving
it here keeps ``reedsolo`` (a V1/V2 dependency) off the ``vnx`` import path (V6_ARCHITECTURE §6).
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from . import gf256

MAX_CODEWORD = 255


def generator_poly(nsym: int) -> np.ndarray:
    """Coefficients of g(x), highest degree first (monic), length nsym + 1."""
    g = [1]
    for j in range(nsym):
        root = gf256.EXP[j]
        out = [0] * (len(g) + 1)
        for i, c in enumerate(g):           # g · (x + root)
            out[i] ^= c
            out[i + 1] ^= gf256.mul(c, root)
        g = out
    return np.asarray(g, dtype=np.uint8)


@lru_cache(maxsize=64)
def parity_matrix(k: int, nsym: int) -> np.ndarray:
    """(k, nsym) uint8: parity bytes of each unit message (read-only, cached)."""
    if not 0 < k or k + nsym > MAX_CODEWORD or nsym < 0:
        raise ValueError(f"no RS code with k={k}, nsym={nsym} over GF(256)")
    g = generator_poly(nsym)
    buf = np.zeros((k, k + nsym), dtype=np.uint8)
    buf[np.arange(k), np.arange(k)] = 1
    mul = gf256.MUL
    for j in range(k):                     # synthetic division, one leading coefficient column at a time
        coef = buf[:, j]
        if nsym and coef.any():
            buf[:, j + 1:j + 1 + nsym] ^= mul[coef[:, None], g[None, 1:]]
    out = np.ascontiguousarray(buf[:, k:])
    out.setflags(write=False)
    return out
