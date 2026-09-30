"""Batched banded global alignment of reads to per-read references (edit distance).

Used by consensus (to project reads with insertions and deletions onto the
coordinates of a draft) and by the tests.

For read ``r`` (length ``m``) and reference ``ref`` (length ``n``) the DP is
the standard global edit distance with costs, in half-units: match 0, a
position involving ``N`` 1, mismatch 2, insertion 2, deletion 2. Only cells
with ``|j − i| ≤ band`` are computed. The DP runs **for many reads at once**:
arrays are (reads, 2·band + 1) and one numpy step computes one DP row for
every read, each against its own reference of its own length.

Within a row, the "left" (deletion) dependency is sequential. With a constant
deletion cost ``c`` it has a closed form: ``D[d] = min over d' ≤ d of X[d'] +
c·(d − d')``, where ``X`` is the best of the diagonal and up moves. That is
``minimum.accumulate(X − c·d) + c·d``, one vectorised pass.

The traceback walks all reads back simultaneously and returns, per reference
position, the read's base (and its quality) or a gap, and per insertion slot
(before reference position j, and after the last position) the first
inserted base and how many bases were inserted there.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

GAP = 5
PAD = 6
NONE = 255
_INF = np.int32(1 << 28)


@dataclass
class Alignment:
    projection: np.ndarray   # (n, W) read base per reference position, GAP where the read has a deletion
    proj_quality: np.ndarray  # (n, W) quality of the projected base (0 for gaps)
    ins_base: np.ndarray     # (n, W + 1) first base inserted before reference position j (NONE if none)
    ins_count: np.ndarray    # (n, W + 1) number of bases inserted there
    cost: np.ndarray         # (n,) edit cost in half-units (-1 if not aligned)
    ok: np.ndarray           # (n,) aligned within the band


def align_reads(reads: list[np.ndarray], refs: list[np.ndarray], band: int = 12,
                quals: list[np.ndarray] | None = None) -> Alignment:
    """Align each ``reads[i]`` to ``refs[i]`` (both code arrays; references may differ in length)."""
    n = len(reads)
    ref_lens = np.fromiter((r.size for r in refs), dtype=np.int64, count=n)
    width_ref = int(ref_lens.max()) if n else 0
    if n == 0:
        z = np.zeros((0, width_ref), np.uint8)
        return Alignment(z, z, np.zeros((0, width_ref + 1), np.uint8), np.zeros((0, width_ref + 1), np.int32),
                         np.zeros(0, np.int32), np.zeros(0, bool))
    lens = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    ok = np.abs(lens - ref_lens) <= band
    width = 2 * band + 1
    max_m = int(lens[ok].max()) if ok.any() else 0
    seq = np.full((n, max_m + 1), PAD, dtype=np.uint8)
    qseq = np.zeros((n, max_m + 1), dtype=np.uint8)
    for i, r in enumerate(reads):
        if ok[i]:
            seq[i, 1:1 + r.size] = r
            if quals is not None:
                qseq[i, 1:1 + r.size] = quals[i]
    ref_pad = np.full((n, width_ref + band + 2), PAD, dtype=np.uint8)
    for i, r in enumerate(refs):
        ref_pad[i, 1:1 + r.size] = r
    offs = np.arange(-band, band + 1, dtype=np.int64)
    ptr = np.zeros((max_m + 1, n, width), dtype=np.uint8)  # 0 diag, 1 up (insertion), 2 left (deletion)
    d_prev = np.where(offs[None, :] >= 0, 2 * offs[None, :], _INF).astype(np.int32).repeat(n, axis=0)
    d_prev = np.where(offs[None, :] <= ref_lens[:, None], d_prev, _INF)
    ptr[0] = np.where(offs >= 0, 2, 0)[None, :]
    step = 2 * np.arange(width, dtype=np.int32)
    finals = np.full(n, _INF, dtype=np.int32)
    finals[ok & (lens == 0)] = (2 * ref_lens[ok & (lens == 0)]).astype(np.int32)
    rows = np.arange(n)
    for i in range(1, max_m + 1):
        j = i + offs  # reference column of each band cell
        valid = (j[None, :] >= 0) & (j[None, :] <= ref_lens[:, None])
        base = seq[:, i][:, None]
        ref_base = ref_pad[:, np.clip(j, 0, width_ref + band + 1)]
        cost = np.where(base == ref_base, 0, 2).astype(np.int32)
        cost = np.where(((base == 4) | (ref_base == 4)) & (base != PAD) & (ref_base != PAD), 1, cost)
        diag = np.where((j >= 1)[None, :], d_prev + cost, _INF)
        up = np.full((n, width), _INF, dtype=np.int32)
        up[:, :-1] = d_prev[:, 1:] + 2
        x = np.minimum(diag, up)
        x = np.where(valid, x, _INF)
        col0 = np.flatnonzero(j == 0)
        x[:, col0] = 2 * i  # reference column 0: every read base so far is an insertion
        left_best = np.minimum.accumulate(x - step[None, :], axis=1) + step[None, :]
        cur = np.where(valid, np.minimum(x, left_best), _INF)
        choice = np.where(diag <= up, 0, 1).astype(np.uint8)
        choice = np.where(left_best < x, 2, choice)
        choice[:, col0] = 1
        ptr[i] = choice
        d_prev = cur
        at_end = np.flatnonzero((lens == i) & ok)
        if at_end.size:
            d_end = ref_lens[at_end] - i + band
            inside = (d_end >= 0) & (d_end < width)
            finals[at_end[inside]] = cur[at_end[inside], d_end[inside]]
    proj = np.full((n, width_ref), GAP, dtype=np.uint8)
    proj_q = np.zeros((n, width_ref), dtype=np.uint8)
    ins_base = np.full((n, width_ref + 1), NONE, dtype=np.uint8)
    ins_count = np.zeros((n, width_ref + 1), dtype=np.int32)
    i_pos = np.where(ok, lens, 0)
    j_pos = np.where(ok, ref_lens, 0)
    active = ok & (finals < _INF)
    for _ in range(max_m + width_ref + 2):
        live = active & ((i_pos > 0) | (j_pos > 0))
        if not live.any():
            break
        idx = np.flatnonzero(live)
        ii, jj = i_pos[idx], j_pos[idx]
        d = jj - ii + band
        move = np.where(ii == 0, 2, ptr[ii, idx, np.clip(d, 0, width - 1)])
        move = np.where((jj == 0) & (ii > 0), 1, move)
        dm = move == 0
        proj[idx[dm], jj[dm] - 1] = seq[idx[dm], ii[dm]]
        proj_q[idx[dm], jj[dm] - 1] = qseq[idx[dm], ii[dm]]
        um = move == 1  # read base ii inserted before reference position jj (0-based slot jj)
        ins_base[idx[um], jj[um]] = seq[idx[um], ii[um]]  # walking backwards: the last write is the first inserted base
        ins_count[idx[um], jj[um]] += 1
        i_pos[idx] = ii - (move != 2)
        j_pos[idx] = jj - (move != 1)
    ok &= finals < _INF
    return Alignment(proj, proj_q, ins_base, ins_count, np.where(ok, finals, -1).astype(np.int32), ok)


def align_batch(reads: list[np.ndarray], refs: np.ndarray, band: int = 12) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Equal-length references (n, L). Returns (projection, cost, inserted bases per read, ok)."""
    a = align_reads(reads, [row for row in refs], band)
    return a.projection, a.cost, a.ins_count.sum(axis=1).astype(np.int32), a.ok


def edit_distance_one(read: np.ndarray, ref: np.ndarray, band: int = 12) -> int:
    """Banded edit distance in whole units (``1 << 20`` if the lengths differ by more than ``band``)."""
    a = align_reads([read], [ref], band)
    return int(a.cost[0] // 2) if a.ok[0] else 1 << 20
