"""Arithmetic in GF(2^8) with primitive polynomial x^8+x^4+x^3+x^2+1 (0x11D).

This matches the field used by ``reedsolo`` (prim=0x11D, generator=2), so the
inner Reed–Solomon code and the outer erasure code share one field definition.

Two implementations are provided:

* scalar reference functions (``mul``, ``inv`` ...) written directly from the
  log/antilog definition, used to validate everything else, and
* a 256×256 multiplication table ``MUL`` (numpy) for vectorized bulk work.
"""
from __future__ import annotations

import numpy as np

PRIMITIVE_POLY = 0x11D
GENERATOR = 2

EXP: list[int] = [0] * 512
LOG: list[int] = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= PRIMITIVE_POLY
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]
del _x, _i


def add(a: int, b: int) -> int:
    return a ^ b


def mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]


def inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("0 has no inverse in GF(256)")
    return EXP[255 - LOG[a]]


def div(a: int, b: int) -> int:
    return mul(a, inv(b))


def power(a: int, n: int) -> int:
    if n == 0:
        return 1
    if a == 0:
        return 0
    return EXP[(LOG[a] * n) % 255]


def _build_mul_table() -> np.ndarray:
    table = np.zeros((256, 256), dtype=np.uint8)
    log = np.array(LOG, dtype=np.int32)
    exp = np.array(EXP, dtype=np.uint8)
    values = np.arange(1, 256)
    for a in range(1, 256):
        table[a, 1:] = exp[log[a] + log[values]]
    return table


MUL: np.ndarray = _build_mul_table()


def matrix_invert(matrix: list[list[int]]) -> list[list[int]]:
    """Invert a square matrix over GF(256) by Gauss–Jordan elimination.

    Raises ``ValueError`` if the matrix is singular.
    """
    n = len(matrix)
    if any(len(row) != n for row in matrix):
        raise ValueError("matrix must be square")
    work = [list(row) + [int(i == j) for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = next((r for r in range(col, n) if work[r][col]), None)
        if pivot is None:
            raise ValueError("matrix is singular over GF(256)")
        work[col], work[pivot] = work[pivot], work[col]
        scale = inv(work[col][col])
        work[col] = [mul(v, scale) for v in work[col]]
        for r in range(n):
            factor = work[r][col]
            if r != col and factor:
                pivot_row = work[col]
                work[r] = [v ^ mul(factor, p) for v, p in zip(work[r], pivot_row)]
    return [row[n:] for row in work]


def matrix_invert_np(matrix: np.ndarray) -> np.ndarray:
    """Vectorized Gauss–Jordan inverse over GF(256) (validated against :func:`matrix_invert`)."""
    a = np.asarray(matrix, dtype=np.uint8)
    n = a.shape[0]
    if a.ndim != 2 or a.shape[1] != n:
        raise ValueError("matrix must be square")
    work = np.concatenate([a, np.eye(n, dtype=np.uint8)], axis=1)
    for col in range(n):
        nonzero = np.flatnonzero(work[col:, col])
        if nonzero.size == 0:
            raise ValueError("matrix is singular over GF(256)")
        pivot = col + int(nonzero[0])
        if pivot != col:
            work[[col, pivot]] = work[[pivot, col]]
        work[col] = MUL[inv(int(work[col, col]))][work[col]]
        factors = work[:, col].copy()
        factors[col] = 0
        work ^= MUL[factors][:, work[col]]
    return work[:, n:].copy()


_BROADCAST_LIMIT = 1 << 22


def matmul_rows(coefficients, rows: np.ndarray) -> np.ndarray:
    """Multiply a coefficient matrix (r×c) by symbol rows over GF(256).

    ``rows`` has shape ``(S, c, L)`` (S independent stripes of c rows of L
    bytes). Returns ``(S, r, L)``. Small products use one broadcast table
    lookup plus an XOR reduction; large ones loop over coefficients and
    vectorize over S and L. Both paths are exact (tested against scalar code).
    """
    coeff = np.asarray(coefficients, dtype=np.uint8)
    if coeff.ndim != 2:
        coeff = coeff.reshape(len(coefficients), -1)
    stripes, cols, length = rows.shape
    r = coeff.shape[0]
    if r and coeff.shape[1] != cols:
        raise ValueError("coefficient width does not match row count")
    if r == 0 or cols == 0:
        return np.zeros((stripes, r, length), dtype=np.uint8)
    if r * cols * length <= _BROADCAST_LIMIT:
        step = max(1, _BROADCAST_LIMIT // (r * cols * length))
        out = np.empty((stripes, r, length), dtype=np.uint8)
        table = MUL[coeff]  # (r, c, 256): table[i, j, x] = coeff[i, j] * x
        ri = np.arange(r)[None, :, None, None]
        ci = np.arange(cols)[None, None, :, None]
        for start in range(0, stripes, step):
            block = rows[start:start + step]  # (s, c, L)
            products = table[ri, ci, block[:, None, :, :]]  # (s, r, c, L)
            out[start:start + step] = np.bitwise_xor.reduce(products, axis=2)
        return out
    out = np.zeros((stripes, r, length), dtype=np.uint8)
    for i in range(r):
        acc = out[:, i, :]
        for c in range(cols):
            coefficient = int(coeff[i, c])
            if coefficient == 1:
                acc ^= rows[:, c, :]
            elif coefficient:
                acc ^= MUL[coefficient][rows[:, c, :]]
    return out
