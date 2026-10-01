"""Vectorised Reed–Solomon errors-and-erasures decoding over GF(256) (V3).

The inner code of every strand is a systematic RS codeword with ``nsym``
parity bytes over GF(2^8)/0x11D, generator 2, first consecutive root 0
(identical to :mod:`vnxdna.ecc.inner_rs` and ``reedsolo``). V2 decoded each
damaged read separately with ``reedsolo`` in Python; this module decodes a
whole batch of equal-length codewords at once with NumPy, one GF(256) table
lookup per polynomial coefficient per step, vectorised over the batch:

1. syndromes ``S_i = c(α^i)``, i = 0 … nsym−1 (Horner over the codeword);
2. erasure locator ``Γ(x) = Π (1 + X_k x)`` for the flagged positions;
3. Berlekamp–Massey initialised with Γ (errors-and-erasures form, Blahut),
   giving the errata locator Λ of degree L;
4. Chien search: the roots of Λ among the codeword positions; the decoding
   fails unless Λ has exactly L distinct roots there;
5. Forney: ``e_k = X_k · Ω(X_k^-1) / Λ'(X_k^-1)`` with ``Ω = S·Λ mod x^nsym``;
6. the corrected codeword's syndromes are recomputed and must all be zero.

Guarantee (the standard RS bound): if ``2e + f ≤ nsym`` for ``e`` byte errors
and ``f`` flagged erasures, the transmitted codeword is returned. Beyond that
bound the decoder either reports failure or returns *some* codeword (a
miscorrection), exactly like any bounded-distance decoder; callers re-check
an independent code (the frame CRC-32). The tests compare this decoder with
``reedsolo`` on random codewords at and beyond the bound.

Positions are codeword indices (0 = first byte = highest-degree coefficient),
the same convention as ``reedsolo``'s ``erase_pos``.
"""
from __future__ import annotations

import numpy as np

from . import gf256

_EXP = np.array(gf256.EXP, dtype=np.uint8)          # 512 entries: EXP[i] = α^i (period 255)
_LOG = np.array(gf256.LOG, dtype=np.int64)          # LOG[0] unused
_MUL = gf256.MUL
_INV = np.zeros(256, dtype=np.uint8)
_INV[1:] = _EXP[(255 - _LOG[1:]) % 255]

MAX_BATCH = 4096  # codewords per vectorised block (bounds temporary memory to a few MB)


def _alpha_powers(n: int) -> np.ndarray:
    """X_j = α^(n-1-j): the locator of codeword position j (length n)."""
    return _EXP[(n - 1 - np.arange(n)) % 255]


def syndromes(codewords: np.ndarray, nsym: int) -> np.ndarray:
    """(N, nsym) syndromes S_i = c(α^i) by Horner's rule over the codeword bytes."""
    n_words, n = codewords.shape
    roots = _EXP[np.arange(nsym) % 255]               # α^i
    s = np.zeros((n_words, nsym), dtype=np.uint8)
    for j in range(n):
        s = _MUL[roots[None, :], s] ^ codewords[:, j:j + 1]
    return s


def _poly_eval(poly: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Evaluate low-degree-first polynomials (N, D) at points (N, P) or (P,): returns (N, P)."""
    n_words, degree = poly.shape
    pts = np.broadcast_to(points, (n_words, points.shape[-1]))
    acc = np.zeros(pts.shape, dtype=np.uint8)
    for k in range(degree - 1, -1, -1):
        acc = _MUL[acc, pts] ^ poly[:, k:k + 1]
    return acc


def decode_batch(codewords: np.ndarray, nsym: int, erasures: np.ndarray | None = None
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Errors-and-erasures decoding of N codewords of equal length n ≤ 255.

    ``erasures`` is an optional (N, n) boolean mask. Returns
    ``(corrected (N, n) uint8, ok (N,) bool, errata (N,) int)`` where ``errata``
    counts the symbols located (errors plus erasures). Rows with ``ok`` False
    are returned unchanged.
    """
    codewords = np.ascontiguousarray(codewords, dtype=np.uint8)
    if codewords.ndim != 2:
        raise ValueError("codewords must be a 2-D array")
    n_words, n = codewords.shape
    if n > 255 or nsym < 0 or nsym >= n and n_words:
        raise ValueError("invalid RS code length")
    if erasures is None:
        erasures = np.zeros((n_words, n), dtype=bool)
    else:
        erasures = np.asarray(erasures, dtype=bool)
        if erasures.shape != (n_words, n):
            raise ValueError("erasure mask has the wrong shape")
    out = codewords.copy()
    ok = np.zeros(n_words, dtype=bool)
    errata = np.zeros(n_words, dtype=np.int64)
    if nsym == 0 or n_words == 0:
        ok[:] = nsym == 0
        return out, ok, errata
    for start in range(0, n_words, MAX_BATCH):
        stop = min(n_words, start + MAX_BATCH)
        c, k, e = _decode_block(codewords[start:stop], nsym, erasures[start:stop])
        out[start:stop], ok[start:stop], errata[start:stop] = c, k, e
    return out, ok, errata


def _decode_block(cw: np.ndarray, nsym: int, erase: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n_words, n = cw.shape
    width = nsym + 1                                    # polynomial coefficient slots (degree ≤ nsym)
    x_of = _alpha_powers(n)                             # locator per position
    s = syndromes(cw, nsym)
    clean = ~s.any(axis=1)
    f = erase.sum(axis=1).astype(np.int64)
    result = cw.copy()
    ok = clean & (f <= nsym)
    errata = np.zeros(n_words, dtype=np.int64)
    todo = np.flatnonzero(~clean & (f <= nsym))
    if todo.size == 0:
        return result, ok, errata
    s = s[todo]
    er = erase[todo]
    f = f[todo]
    m = todo.size

    # Erasure locator Γ(x) = Π_{flagged j} (1 + X_j x), low degree first.
    gamma = np.zeros((m, width), dtype=np.uint8)
    gamma[:, 0] = 1
    for j in np.flatnonzero(er.any(axis=0)).tolist():
        rows = np.flatnonzero(er[:, j])
        g = gamma[rows]
        shifted = np.zeros_like(g)
        shifted[:, 1:] = _MUL[x_of[j], g[:, :-1]]
        gamma[rows] = g ^ shifted

    # Berlekamp–Massey with erasures: Λ = B = Γ, L = f; steps r = f+1 … nsym.
    lam = gamma.copy()
    b = gamma.copy()
    big_l = f.copy()
    for r in range(1, nsym + 1):
        active = r > f
        if not active.any():
            continue
        # Δ = Σ_j Λ_j S_{r-1-j}
        idx = r - 1 - np.arange(width)
        valid = idx >= 0
        s_sel = np.zeros((m, width), dtype=np.uint8)
        s_sel[:, valid] = s[:, idx[valid]]
        delta = np.bitwise_xor.reduce(_MUL[lam, s_sel], axis=1)
        xb = np.zeros_like(b)
        xb[:, 1:] = b[:, :-1]                            # x·B
        nonzero = active & (delta != 0)
        t = lam ^ _MUL[delta[:, None], xb]               # Λ − Δ·x·B
        grow = nonzero & (2 * big_l <= r - 1 + f)
        # B ← Δ⁻¹·Λ where L grows, else x·B (for active rows)
        b_new = np.where(grow[:, None], _MUL[_INV[delta][:, None], lam], xb)
        b = np.where(active[:, None], b_new, b)
        big_l = np.where(grow, r + f - big_l, big_l)
        lam = np.where(nonzero[:, None], t, lam)

    degree = np.where(lam.any(axis=1), width - 1 - np.argmax(lam[:, ::-1] != 0, axis=1), 0)
    feasible = (degree == big_l) & (2 * (big_l - f) + f <= nsym) & (big_l <= n)

    # Chien search over the codeword positions: Λ(X_j^-1) == 0.
    x_inv = _INV[x_of]
    values = _poly_eval(lam, x_inv)                      # (m, n)
    roots = values == 0
    nroots = roots.sum(axis=1)
    feasible &= nroots == big_l
    # Every flagged erasure must be a root (Γ divides Λ by construction; a failure means inconsistency).
    feasible &= ~(er & ~roots).any(axis=1)

    # Forney: Ω = S·Λ mod x^nsym ; e_j = X_j · Ω(X_j^-1) / Λ'(X_j^-1).
    omega = np.zeros((m, nsym), dtype=np.uint8)
    for k in range(width):
        coeff = lam[:, k]
        if not coeff.any():
            continue
        span = nsym - k
        if span <= 0:
            break
        omega[:, k:] ^= _MUL[coeff[:, None], s[:, :span]]
    deriv = np.zeros((m, width), dtype=np.uint8)
    deriv[:, 0:width - 1:2] = lam[:, 1::2]               # formal derivative in characteristic 2: odd terms
    om_val = _poly_eval(omega, x_inv)
    de_val = _poly_eval(deriv, x_inv)
    feasible &= ~(roots & (de_val == 0)).any(axis=1)
    safe_de = np.where(de_val == 0, 1, de_val)
    magnitude = _MUL[_MUL[x_of[None, :], om_val], _INV[safe_de]]
    magnitude = np.where(roots, magnitude, 0).astype(np.uint8)

    candidate = cw[todo] ^ magnitude
    feasible &= ~syndromes(candidate, nsym).any(axis=1)
    rows = todo[feasible]
    result[rows] = candidate[feasible]
    ok[rows] = True
    errata[rows] = nroots[feasible]
    return result, ok, errata
