"""Batched banded global alignment of reads to per-read references (edit distance).

Used by consensus (to project reads with insertions/deletions onto strand
coordinates) and by clustering (to compare unaddressed reads).

For read ``r`` (length ``m``) and reference ``ref`` (length ``L``) the DP is
the standard global edit distance with costs, in half-units: match 0, a
position involving ``N`` 1, mismatch 2, insertion 2, deletion 2. Only cells
with ``|j − i| ≤ band`` are computed. The DP runs **for many reads at once**:
arrays are (reads, 2·band + 1) and one numpy step computes one DP row for
every read, so the per-read Python overhead disappears.

Within a row, the "left" (deletion) dependency is sequential. With a constant
deletion cost ``c`` it has a closed form: ``D[d] = min over d' ≤ d of X[d'] +
c·(d − d')``, where ``X`` is the best of the diagonal and up moves. That is
``minimum.accumulate(X − c·d) + c·d``, one vectorised pass.

The traceback walks all reads back simultaneously and returns, for every
reference position, the read's base or a gap (``GAP``). Inserted read bases,
which have no reference position, are dropped and counted. Consensus only
needs per-position evidence of the designed strand.
"""
from __future__ import annotations

import numpy as np

GAP = 5
PAD = 6
_INF = np.int32(1 << 28)


def align_batch(reads: list[np.ndarray], refs: np.ndarray, band: int = 12) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Align each ``reads[i]`` to ``refs[i]`` (refs: (n, L) codes).

    Returns ``(projection (n, L) codes with GAP, cost (n,) in half-units,
    inserted bases (n,), ok (n,))``. ``ok`` is False for reads whose length
    differs from L by more than ``band`` (they are not aligned).
    """
    n = len(reads)
    if n == 0:
        return np.zeros((0, refs.shape[1]), np.uint8), np.zeros(0, np.int32), np.zeros(0, np.int32), np.zeros(0, bool)
    length = refs.shape[1]
    lens = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    ok = np.abs(lens - length) <= band
    width = 2 * band + 1
    max_m = int(lens[ok].max()) if ok.any() else 0
    seq = np.full((n, max_m + 1), PAD, dtype=np.uint8)
    for i, r in enumerate(reads):
        if ok[i]:
            seq[i, 1:1 + r.size] = r
    ref_pad = np.full((n, length + band + 2), PAD, dtype=np.uint8)
    ref_pad[:, 1:1 + length] = refs
    offs = np.arange(-band, band + 1, dtype=np.int64)
    rows = np.arange(n)
    ptr = np.zeros((max_m + 1, n, width), dtype=np.uint8)  # 0 diag, 1 up, 2 left
    d_prev = np.where(offs[None, :] >= 0, 2 * offs[None, :], _INF).astype(np.int32).repeat(n, axis=0)
    ptr[0] = np.where(offs >= 0, 2, 0)[None, :]
    step = 2 * np.arange(width, dtype=np.int32)
    finals = np.full(n, _INF, dtype=np.int32)
    for i in range(1, max_m + 1):
        j = i + offs  # (width,) reference column of each band cell
        valid = (j >= 0) & (j <= length)
        base = seq[:, i][:, None]
        ref_base = ref_pad[:, np.clip(j, 0, length + band + 1)]
        is_n = (base == 4) | (ref_base == 4)
        cost = np.where(base == ref_base, 0, 2).astype(np.int32)
        cost = np.where(is_n & (base != PAD) & (ref_base != PAD), 1, cost)
        diag = np.where((j >= 1)[None, :], d_prev + cost, _INF)
        up = np.full((n, width), _INF, dtype=np.int32)
        up[:, :-1] = d_prev[:, 1:] + 2
        x = np.minimum(diag, up)
        x = np.where(valid[None, :], x, _INF)
        x[:, np.flatnonzero(j == 0)] = 2 * i  # column 0: all read bases so far are insertions
        left_best = np.minimum.accumulate(x - step[None, :], axis=1) + step[None, :]
        cur = np.minimum(x, left_best)
        choice = np.where(diag <= up, 0, 1).astype(np.uint8)
        choice = np.where(left_best < x, 2, choice)
        choice[:, np.flatnonzero(j == 0)] = 1
        cur = np.where(valid[None, :], cur, _INF)
        ptr[i] = choice
        d_prev = cur
        at_end = (lens == i) & ok
        d_end = length - i + band  # band column of the final cell (i, L)
        if at_end.any() and 0 <= d_end < width:
            finals[at_end] = cur[at_end, d_end]
    # traceback, all reads in lock-step
    proj = np.full((n, length), GAP, dtype=np.uint8)
    inserted = np.zeros(n, dtype=np.int32)
    i_pos = np.where(ok, lens, 0)
    j_pos = np.where(ok, length, 0)
    active = ok & (finals < _INF)
    for _ in range(max_m + length + 2):
        live = active & ((i_pos > 0) | (j_pos > 0))
        if not live.any():
            break
        idx = np.flatnonzero(live)
        ii, jj = i_pos[idx], j_pos[idx]
        d = jj - ii + band
        move = np.where(ii == 0, 2, ptr[ii, idx, np.clip(d, 0, width - 1)])
        move = np.where((jj == 0) & (ii > 0), 1, move)
        diag_m = move == 0
        proj[idx[diag_m], jj[diag_m] - 1] = seq[idx[diag_m], ii[diag_m]]
        up_m = move == 1
        inserted[idx[up_m]] += 1
        i_pos[idx] = ii - (move != 2)
        j_pos[idx] = jj - (move != 1)
    ok &= finals < _INF
    return proj, np.where(ok, finals, -1).astype(np.int32), inserted, ok


def edit_distance_one(read: np.ndarray, ref: np.ndarray, band: int = 12) -> int:
    """Banded edit distance in whole units (``1 << 20`` if the lengths differ by more than ``band``)."""
    _, cost, _, ok = align_batch([read], ref[None, :], band)
    return int(cost[0] // 2) if ok[0] else 1 << 20
