"""Banded unit-cost edit distance of read pairs, vectorised over pairs (V7_ARCHITECTURE §5.2 step 3; NumPy reference).

Global (Levenshtein) distance of a (length La) and b (length Lb) restricted to the diagonals
d = j − i ∈ [min(0, Lb − La) − slack, max(0, Lb − La) + slack]. The band is symmetric under swapping a and b and under
reversing both, so dist(a, b) = dist(b, a) and dist(a, rc(b)) = dist(rc(a), b). A path that would leave the band is not
considered, so the banded value is ≥ the true distance (a pair can only be rejected, never accepted, by the band).
N (code 4) matches nothing, not even N. The architecture names the bit-parallel algorithm of Myers (1999) for the
native kernel; this reference computes the same banded values with a plain dynamic program.

:func:`banded_distance` runs the native kernel (``vnxdna.native.cluster``: Myers bit-vector distance, exact banded
program where the band can matter) when it is available and selected (``VNXDNA_CLUSTER_BACKEND``), else
:func:`banded_distance_reference`; both return identical values.
"""
from __future__ import annotations

import numpy as np

from vnxdna.native import cluster as _nc

INF = np.int32(1 << 28)
_RC = np.array([3, 2, 1, 0, 4, 5, 6, 7], dtype=np.uint8)


def revcomp(read: np.ndarray) -> np.ndarray:
    return _RC[np.asarray(read)[::-1]]


def banded_distance(a: list, b: list, slack: int = 32, chunk: int = 2048) -> np.ndarray:
    """(P,) int64 banded edit distances of the pairs (a[p], b[p]) (lists of uint8 code arrays); native or reference."""
    if len(a) and 0 <= slack <= _nc.MAX_SLACK and _nc.resolve_backend() == "native":
        lens = [x.size for x in a] + [x.size for x in b]
        if max(lens) <= _nc.MAX_READ:
            return _nc.banded_distance(a, b, slack)
    return banded_distance_reference(a, b, slack, chunk)


def banded_distance_reference(a: list, b: list, slack: int = 32, chunk: int = 2048) -> np.ndarray:
    """(P,) int64 banded edit distances of the pairs (a[p], b[p]) (lists of uint8 code arrays); NumPy reference."""
    p_all = len(a)
    out = np.zeros(p_all, dtype=np.int64)
    for c0 in range(0, p_all, chunk):
        out[c0:c0 + chunk] = _banded(a[c0:c0 + chunk], b[c0:c0 + chunk], slack)
    return out


def _banded(a: list, b: list, slack: int) -> np.ndarray:
    P = len(a)
    if P == 0:
        return np.zeros(0, dtype=np.int64)
    la = np.fromiter((x.size for x in a), dtype=np.int64, count=P)
    lb = np.fromiter((x.size for x in b), dtype=np.int64, count=P)
    delta = lb - la
    lo_p = np.minimum(0, delta) - slack            # per-pair band
    hi_p = np.maximum(0, delta) + slack
    dlo, dhi = int(lo_p.min()), int(hi_p.max())
    W = dhi - dlo + 1
    maxa = int(la.max(initial=0))
    widthb = int(lb.max(initial=0)) + 1
    A = np.full((P, max(1, maxa)), 6, dtype=np.uint8)
    # b with −dlo pad columns on the left: the bases at j = i − 1 + d, d = dlo … dhi, are the slice Bp[:, i−1 : i−1+W]
    pad = -dlo if dlo < 0 else 0
    Bp = np.full((P, pad + widthb + W + 1), 7, dtype=np.uint8)
    for k in range(P):
        A[k, : la[k]] = a[k]
        Bp[k, pad: pad + lb[k]] = b[k]
    A[A > 3] = 6                                   # N matches nothing
    Bp[Bp > 3] = 7
    dvec = np.arange(dlo, dhi + 1, dtype=np.int64)[:, None]       # (W, 1)
    in_band = (dvec >= lo_p[None, :]) & (dvec <= hi_p[None, :])   # (W, P)
    # row 0: j = d >= 0 insertions of b's prefix
    D = np.where((dvec >= 0) & (dvec <= lb[None, :]) & in_band, dvec, INF).astype(np.int32)
    final = np.where(la == 0, np.where(delta <= hi_p, lb, int(INF)), int(INF)).astype(np.int64)
    ramp = np.arange(W, dtype=np.int32)[:, None]
    for i in range(1, maxa + 1):
        jd = i - 1 + dvec                                          # b index for the diagonal move
        okd = (jd >= 0) & (jd < lb[None, :])
        j0 = i - 1 + dlo + pad
        bj = Bp[:, j0: j0 + W].T                                    # (W, P)
        sub = (bj != A[:, i - 1][None, :]).astype(np.int32)
        diag = np.where(okd, D + sub, INF)
        dele = np.full_like(D, INF)
        dele[:-1] = D[1:] + 1
        new = np.minimum(diag, dele)
        new = np.minimum(np.minimum.accumulate(new - ramp, axis=0) + ramp, new)
        j = i + dvec
        new = np.where((j >= 0) & (j <= lb[None, :]) & in_band, new, INF).astype(np.int32)
        D = new
        end = la == i
        if end.any():
            w = (delta[end] - dlo).astype(np.int64)
            inside = (w >= 0) & (w < W)
            v = np.full(int(end.sum()), int(INF), dtype=np.int64)
            v[inside] = D[w[inside], np.flatnonzero(end)[inside]]
            final[end] = v
    return np.minimum(final, int(INF))


def full_distance(a: np.ndarray, b: np.ndarray) -> int:
    """Unbanded Levenshtein distance (test oracle; N matches nothing)."""
    a = np.asarray(a)
    b = np.asarray(b)
    prev = np.arange(b.size + 1, dtype=np.int64)
    for i in range(1, a.size + 1):
        cur = np.empty_like(prev)
        cur[0] = i
        for j in range(1, b.size + 1):
            s = 0 if (a[i - 1] == b[j - 1] and a[i - 1] < 4) else 1
            cur[j] = min(prev[j - 1] + s, prev[j] + 1, cur[j - 1] + 1)
        prev = cur
    return int(prev[-1])
