"""Fast-kernel variant of the V3 batch Reed–Solomon decoder (V4 performance work, docs/PERFORMANCE.md).

The algorithm is *identical* to :mod:`vnxdna.ecc.rs_batch` (V3, unchanged and
kept as the reference): syndromes, erasure locator, Berlekamp–Massey with
erasures, Chien search, Forney, re-check. Only two kernels differ:

* syndromes ``S_i = Σ_j c_j α^{i(n−1−j)}`` are computed as an XOR of one
  pre-computed (256 × nsym) table gather per codeword column instead of
  Horner's rule with a 2-D GF multiplication per step;
* polynomial evaluation at the n fixed points ``X_j⁻¹`` (Chien search and
  Forney) is an XOR of one (256 × n) table gather per coefficient.

Profiling showed these two kernels took ~75 % of inner-decoder time on noisy
reads. Tests compare this module with the V3 decoder word for word (corrected
codeword, success flag and errata count) on random codewords with random
errors and erasures, at and beyond the correction bound. Set
``VNX_RS_REFERENCE=1`` to use the V3 decoder instead.
"""
from __future__ import annotations

import numpy as np


from ..ecc import gf256
from ..ecc.rs_batch import MAX_BATCH, _INV, _MUL, _alpha_powers, _EXP

_TABLES: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}


def _tables(n: int, nsym: int) -> tuple[np.ndarray, np.ndarray]:
    key = (n, nsym)
    t = _TABLES.get(key)
    if t is None:
        j = np.arange(n)
        i = np.arange(nsym)
        pw = _EXP[(np.outer(n - 1 - j, i)) % 255]                    # (n, nsym): α^{i(n−1−j)}
        syn = np.ascontiguousarray(gf256.MUL[:, pw].transpose(1, 0, 2))  # (n, 256, nsym)
        x_inv = _INV[_alpha_powers(n)]                                # (n,)
        log_x = np.array(gf256.LOG, dtype=np.int64)[x_inv]
        k = np.arange(nsym + 1)
        powers = _EXP[(np.outer(k, log_x)) % 255]                     # (nsym+1, n): (X_j⁻¹)^k
        ev = np.ascontiguousarray(gf256.MUL[:, powers].transpose(1, 0, 2))  # (nsym+1, 256, n)
        t = _TABLES[key] = (syn, ev)
    return t


def _syndromes_fast(cw: np.ndarray, tabs) -> np.ndarray:
    syn = tabs[0]
    s = np.zeros((cw.shape[0], syn.shape[2]), dtype=np.uint8)
    for j in range(cw.shape[1]):
        s ^= syn[j][cw[:, j]]
    return s


def _eval_fixed(poly: np.ndarray, tabs) -> np.ndarray:
    ev = tabs[1]
    acc = np.zeros((poly.shape[0], ev.shape[2]), dtype=np.uint8)
    for k in range(poly.shape[1]):
        col = poly[:, k]
        if col.any():
            acc ^= ev[k][col]
    return acc


def decode_batch(codewords: np.ndarray, nsym: int, erasures: np.ndarray | None = None
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Same contract as :func:`vnxdna.ecc.rs_batch.decode_batch`."""
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
    tabs = _tables(n, nsym)
    s = _syndromes_fast(cw, tabs)
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
    values = _eval_fixed(lam, tabs)                       # (m, n)
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
    om_val = _eval_fixed(omega, tabs)
    de_val = _eval_fixed(deriv, tabs)
    feasible &= ~(roots & (de_val == 0)).any(axis=1)
    safe_de = np.where(de_val == 0, 1, de_val)
    magnitude = _MUL[_MUL[x_of[None, :], om_val], _INV[safe_de]]
    magnitude = np.where(roots, magnitude, 0).astype(np.uint8)

    candidate = cw[todo] ^ magnitude
    feasible &= ~_syndromes_fast(candidate, tabs).any(axis=1)
    rows = todo[feasible]
    result[rows] = candidate[feasible]
    ok[rows] = True
    errata[rows] = nroots[feasible]
    return result, ok, errata
