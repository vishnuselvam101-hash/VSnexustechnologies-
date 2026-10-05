"""Alignment path: the read position of every template base (V5 Phase 3, step 1).

``align_with_path`` returns the V4 :class:`~vnxdna.v4.sync.Projection` (bit-identical to ``TemplateAligner.project``)
plus ``readpos`` (n, T) int16: for every template position, the read index it was aligned to on the traceback path,
or −1 where the template base was deleted or the read did not align.

Two implementations, kept equivalent by tests:

* native: ``vnx_align_batch_path`` in ``v5/native/align.c`` (the same kernel as Phase 2, one extra output);
* reference: :class:`PathAligner`, a ``TemplateAligner`` subclass that reuses the V4 NumPy DP unchanged and only
  re-implements the traceback, line for line, adding the read index on each DIAG step.

The V4 aligner itself is not modified.
"""
from __future__ import annotations

import numpy as np

from ...v4.sync import DEL, DIAG, INS, Projection, TemplateAligner
from vnxdna.native import align as na


class PathAligner(TemplateAligner):
    """Reference aligner that also records the traceback path. Always runs the NumPy reference."""

    def __init__(self, layout, band: int = 6, costs=None):
        super().__init__(layout, band, costs, backend="reference")
        self._rpos: np.ndarray | None = None

    def _traceback(self, ptr, R, Q, lens, ok, final, min_quality):
        # Identical to TemplateAligner._traceback (V4.0.0) except for the ``rpos`` lines.
        lay = self.layout
        n = R.shape[0]
        B = self.band
        T = self.T
        tpl_bases = np.full((n, T), 4, dtype=np.uint8)
        tpl_bad = np.zeros((n, T), dtype=bool)
        rpos = np.full((n, T), -1, dtype=np.int16)
        seg_hit = np.zeros((n, self.n_segments), dtype=bool)
        ins_n = np.zeros(n, dtype=np.int64)
        del_n = np.zeros(n, dtype=np.int64)
        i = np.full(n, T, dtype=np.int64)
        d = lens - T
        active = ok.copy()
        rows = np.arange(n)
        guard = self.costs.guard_segments
        steps = 0
        while active.any():
            steps += 1
            if steps > 4 * (T + B) + 8:
                raise RuntimeError("traceback did not terminate")
            a = np.flatnonzero(active)
            ia, da = i[a], d[a]
            op = ptr[ia, da + B, rows[a]]
            m = op == DIAG
            if m.any():
                aa, ii, dd = a[m], ia[m], da[m]
                j = ii - 1 + dd
                tpl_bases[aa, ii - 1] = np.minimum(R[aa, j], 4).astype(np.uint8)
                tpl_bad[aa, ii - 1] = (R[aa, j] > 3) | (Q[aa, j] < min_quality)
                rpos[aa, ii - 1] = j
                i[aa] -= 1
            m = op == DEL
            if m.any():
                aa, ii = a[m], ia[m]
                tpl_bad[aa, ii - 1] = True
                del_n[aa] += 1
                s = self.seg_of[ii - 1]
                lo = np.where(s >= 0, s, self.prev_seg[ii - 1])
                hi = np.where(s >= 0, s, self.next_seg[ii - 1])
                self._mark(seg_hit, aa, lo, hi, guard)
                i[aa] -= 1
                d[aa] += 1
            m = op == INS
            if m.any():
                aa, ii = a[m], ia[m]
                ins_n[aa] += 1
                left = np.clip(ii - 1, 0, T - 1)
                right = np.clip(ii, 0, T - 1)
                sl = np.where(ii >= 1, self.seg_of[left], -1)
                sr = np.where(ii < T, self.seg_of[right], -1)
                lo = np.where(sl >= 0, sl, np.where(sr >= 0, sr, self.prev_seg[left]))
                hi = np.where(sr >= 0, sr, np.where(sl >= 0, sl, self.next_seg[right]))
                self._mark(seg_hit, aa, np.minimum(lo, hi), np.maximum(lo, hi), guard)
                d[aa] -= 1
            active = ok & ((i > 0) | (d > 0))
        bases = tpl_bases[:, self.frame_pos]
        bad = tpl_bad[:, self.frame_pos]
        seg_frame = np.arange(lay.frame_nt) // (lay.marker_period or lay.frame_nt)
        erased = bad | seg_hit[:, seg_frame]
        erased[~ok] = True
        marker_cols = self.tpl >= 0
        mm = ((tpl_bases[:, marker_cols] != self.tpl[marker_cols].astype(np.uint8)[None, :]) & ok[:, None]).sum(axis=1)
        self._rpos = rpos
        return bases, erased, ok, ins_n, del_n, mm, final


def align_with_path(aligner: TemplateAligner, reads: list, quals: list | None = None, min_quality: int = 0,
                    backend: str | None = None) -> tuple[Projection, np.ndarray]:
    """→ (Projection identical to ``aligner.project``, readpos (n, T) int16).

    ``backend``: None follows ``aligner.backend``; "reference" forces the NumPy path (tests and fallback)."""
    n = len(reads)
    T = aligner.T
    lay = aligner.layout
    out_bases = np.full((n, lay.frame_nt), 4, dtype=np.uint8)
    out_er = np.ones((n, lay.frame_nt), dtype=bool)
    ok = np.zeros(n, dtype=bool)
    ins_n = np.zeros(n, dtype=np.int64)
    del_n = np.zeros(n, dtype=np.int64)
    mm = np.zeros(n, dtype=np.int64)
    cost = np.full(n, 1 << 28, dtype=np.int64)
    rpos = np.full((n, T), -1, dtype=np.int16)
    if n == 0:
        return Projection(out_bases, out_er, ok, ins_n, del_n, mm, cost), rpos
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    idx = np.flatnonzero(np.abs(lengths - T) <= aligner.band)
    if not idx.size:
        return Projection(out_bases, out_er, ok, ins_n, del_n, mm, cost), rpos
    want = backend or aligner.backend
    sub_r = [reads[i] for i in idx]
    sub_q = None if quals is None else [quals[i] for i in idx]
    res = None
    if want == "native":
        res = na.align_usable(aligner, sub_r, sub_q, min_quality, readpos=True)
    if res is None:
        ref = _reference_twin(aligner)
        for start in range(0, idx.size, 2048):
            sel = idx[start:start + 2048]
            part = ref._align([reads[i] for i in sel], lengths[sel], None if quals is None else [quals[i] for i in sel],
                              min_quality)
            out_bases[sel], out_er[sel], ok[sel], ins_n[sel], del_n[sel], mm[sel], cost[sel] = part
            rpos[sel] = ref._rpos
    else:
        out_bases[idx], out_er[idx], ok[idx], ins_n[idx], del_n[idx], mm[idx], cost[idx], rpos[idx] = res
    return Projection(out_bases, out_er, ok, ins_n, del_n, mm, cost), rpos


def _reference_twin(aligner: TemplateAligner) -> PathAligner:
    twin = getattr(aligner, "_v5_path_twin", None)
    if twin is None:
        twin = PathAligner(aligner.layout, aligner.band, aligner.costs)
        aligner._v5_path_twin = twin
    return twin
