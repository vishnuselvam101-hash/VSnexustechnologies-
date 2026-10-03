"""Marker-based synchronization: align reads to the strand template and convert indels into erasures.

The decoder knows the strand *template* (markers at fixed positions, unknown
frame bases elsewhere) but not the frame content. Each read is aligned to the
template by a global banded dynamic program:

    D[i][d]  minimal cost of aligning template[:i] with read[:i + d],  |d| ≤ band
    diag     D[i−1][d]   + c(template[i−1], read[i−1+d])     (match / substitution)
    del      D[i−1][d+1] + c_del                             (template base missing in the read)
    ins      D[i][d−1]   + c_ins                             (extra read base)

with c = 0 at frame positions (wildcards: the content is unknown) and
c = mismatch cost at marker positions. Markers are the only anchors, so the
DP can place an indel anywhere inside the segment between two markers at
equal cost. The decoder therefore never trusts the bases of a segment that
contains an indel: the whole segment (marker_period / 4 bytes) becomes an
*erasure* for the inner RS code, which costs 1 unit of its budget per byte
instead of 2 for an unknown error (2e + f ≤ r). Substitutions are not
detected here; they are left to the inner code.

Complexity: O(template_length × (2·band + 1)) per read, vectorised over a
batch of reads with NumPy (the Python loop runs over template positions and
band offsets, not over reads). Memory: one int8 traceback pointer per cell.
See docs/INDEL_ENGINE.md for assumptions and failure modes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .frame import Layout

DIAG, DEL, INS = 0, 1, 2
INF = np.int32(1 << 28)


@dataclass
class SyncCosts:
    marker_mismatch: int = 4
    insertion: int = 6
    deletion: int = 6
    marker_deletion_extra: int = 1   # tie-break: prefer explaining a shift by a deleted frame base (blames 1 segment)
    guard_segments: int = 0          # also erase this many neighbouring segments around each indel


@dataclass
class Projection:
    """Reads projected onto frame coordinates."""

    bases: np.ndarray        # (n, frame_nt) uint8 codes 0..3 (4 = unknown)
    erased: np.ndarray       # (n, frame_nt) bool
    ok: np.ndarray           # (n,) bool: alignment found within the band
    insertions: np.ndarray   # (n,) int
    deletions: np.ndarray    # (n,) int
    marker_mismatches: np.ndarray
    cost: np.ndarray


class TemplateAligner:
    def __init__(self, layout: Layout, band: int = 6, costs: SyncCosts | None = None):
        self.layout = layout
        self.band = band
        self.costs = costs or SyncCosts()
        tpl, frame_pos = layout.template()
        self.tpl = tpl.astype(np.int16)
        self.frame_pos = frame_pos
        self.T = tpl.size
        # segment id of every template position (frame positions → their segment; markers → -1)
        seg = np.full(self.T, -1, dtype=np.int64)
        seg[frame_pos] = np.arange(layout.frame_nt) // (layout.marker_period or layout.frame_nt)
        self.seg_of = seg
        self.n_segments = int(seg.max()) + 1
        # for markers / boundaries: previous and next segment
        prev = np.maximum.accumulate(np.where(seg >= 0, seg, -1))
        nxt = seg.copy()
        last = self.n_segments - 1
        for i in range(self.T - 1, -1, -1):
            if nxt[i] < 0:
                nxt[i] = nxt[i + 1] if i + 1 < self.T else last
        self.prev_seg = np.where(prev < 0, 0, prev)
        self.next_seg = nxt

    # ------------------------------------------------------------------ alignment
    def project(self, reads: list[np.ndarray], quals: list[np.ndarray] | None = None, min_quality: int = 0) -> Projection:
        n = len(reads)
        lay = self.layout
        out_bases = np.full((n, lay.frame_nt), 4, dtype=np.uint8)
        out_er = np.ones((n, lay.frame_nt), dtype=bool)
        ok = np.zeros(n, dtype=bool)
        ins_n = np.zeros(n, dtype=np.int64)
        del_n = np.zeros(n, dtype=np.int64)
        mm = np.zeros(n, dtype=np.int64)
        cost = np.full(n, int(INF), dtype=np.int64)
        if n == 0:
            return Projection(out_bases, out_er, ok, ins_n, del_n, mm, cost)
        lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
        usable = np.abs(lengths - self.T) <= self.band
        idx = np.flatnonzero(usable)
        for start in range(0, idx.size, 2048):
            sel = idx[start:start + 2048]
            res = self._align([reads[i] for i in sel], lengths[sel],
                              None if quals is None else [quals[i] for i in sel], min_quality)
            out_bases[sel], out_er[sel], ok[sel], ins_n[sel], del_n[sel], mm[sel], cost[sel] = res
        return Projection(out_bases, out_er, ok, ins_n, del_n, mm, cost)

    def _align(self, reads, lengths, quals, min_quality):
        n = len(reads)
        B = self.band
        W = 2 * B + 1
        T = self.T
        width = T + B + 2
        R = np.full((n, width), 5, dtype=np.int16)          # 5 never matches a marker base
        Q = np.full((n, width), 99, dtype=np.int16)
        for k, r in enumerate(reads):
            R[k, : r.size] = r
            if quals is not None and quals[k] is not None:
                Q[k, : r.size] = quals[k]
        c = self.costs
        dvec = np.arange(-B, B + 1)
        D = np.full((W, n), INF, dtype=np.int32)
        ptr = np.zeros((T + 1, W, n), dtype=np.int8)
        # row 0: leading insertions (j = d ≥ 0)
        for w, d in enumerate(dvec):
            if d >= 0:
                D[w] = d * c.insertion
                ptr[0, w] = INS
        lens = lengths.astype(np.int64)
        for i in range(1, T + 1):
            t = int(self.tpl[i - 1])
            cols = i - 1 + dvec                               # read index for the diagonal move
            valid_cols = (cols >= 0) & (cols < width)
            rb = np.full((W, n), 5, dtype=np.int16)
            rb[valid_cols] = R[:, cols[valid_cols]].T
            if t >= 0:
                sub = np.where(rb == t, 0, c.marker_mismatch).astype(np.int32)
            else:
                sub = np.zeros((W, n), dtype=np.int32)
            diag = D + sub
            diag[~valid_cols] = INF
            dele = np.full((W, n), INF, dtype=np.int32)
            dele[:-1] = D[1:] + (c.deletion + (c.marker_deletion_extra if t >= 0 else 0))
            new = np.minimum(diag, dele)
            p = np.where(dele < diag, DEL, DIAG).astype(np.int8)
            # insertions within the row, all at once: new'[w] = w·c + min_{k ≤ w}(new[k] − k·c)
            ramp = (np.arange(W, dtype=np.int32) * c.insertion)[:, None]
            best = np.minimum.accumulate(new - ramp, axis=0) + ramp
            better = best < new
            new = np.where(better, best, new)
            p = np.where(better, INS, p).astype(np.int8)
            # read index j = i + d must lie in [0, len]
            j = i + dvec[:, None]
            new[(j < 0) | (j > lens[None, :])] = INF
            D = new
            ptr[i] = p
        d_end = lens - T
        w_end = d_end + B
        inside = (w_end >= 0) & (w_end < W)
        final = np.full(n, int(INF), dtype=np.int64)
        final[inside] = D[w_end[inside], np.flatnonzero(inside)]
        ok = inside & (final < INF)
        return self._traceback(ptr, R, Q, lens, ok, final, min_quality)

    def _traceback(self, ptr, R, Q, lens, ok, final, min_quality):
        lay = self.layout
        n = R.shape[0]
        B = self.band
        T = self.T
        tpl_bases = np.full((n, T), 4, dtype=np.uint8)
        tpl_bad = np.zeros((n, T), dtype=bool)        # template position received with low quality / N
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
                raise RuntimeError("traceback did not terminate")  # cannot happen for a valid DP; defensive
            a = np.flatnonzero(active)
            ia, da = i[a], d[a]
            op = ptr[ia, da + B, rows[a]]
            # DIAG
            m = op == DIAG
            if m.any():
                aa, ii, dd = a[m], ia[m], da[m]
                j = ii - 1 + dd
                tpl_bases[aa, ii - 1] = np.minimum(R[aa, j], 4).astype(np.uint8)
                tpl_bad[aa, ii - 1] = (R[aa, j] > 3) | (Q[aa, j] < min_quality)
                i[aa] -= 1
            m = op == DEL
            if m.any():
                aa, ii = a[m], ia[m]
                tpl_bad[aa, ii - 1] = True
                del_n[aa] += 1
                # a deleted frame base blames its segment; a deleted marker base blames both neighbours
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
                # an extra base between template positions ii−1 and ii blames the adjacent *frame* segment(s):
                # a marker aligned without a shift shows that the shift starts on its frame side
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
        return bases, erased, ok, ins_n, del_n, mm, final

    def _mark(self, seg_hit, rows, lo, hi, guard):
        lo = np.clip(lo - guard, 0, self.n_segments - 1)
        hi = np.clip(hi + guard, 0, self.n_segments - 1)
        span = int((hi - lo).max()) + 1 if rows.size else 0
        for k in range(span):
            s = lo + k
            sel = s <= hi
            seg_hit[rows[sel], s[sel]] = True


def strip_markers_exact(layout: Layout, reads: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fast path for reads of exactly strand_nt bases: (frame bases, marker mismatch count)."""
    tpl, pos = layout.template()
    mcols = tpl >= 0
    mism = (reads[:, mcols] != tpl[mcols].astype(np.uint8)[None, :]).sum(axis=1) if mcols.any() else np.zeros(reads.shape[0], int)
    return reads[:, pos], mism


def frame_erasures_to_bytes(erased_nt: np.ndarray) -> np.ndarray:
    return erased_nt.reshape(erased_nt.shape[0], -1, 4).any(axis=2)
