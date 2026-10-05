"""Per-reference sufficient statistics of read alignments (the tally the estimators of V7 plan 3.3 work from).

One reference gives one integer vector laid out by :class:`Layout`; vectors of all references form the matrix ``M`` (R x K).
Totals, bootstrap resamples over references (``W @ M``) and every estimator are linear in or functions of these counts, so
the result does not depend on how references are distributed over workers.

Event definition (one event class per reference site, as in the simulator's per-position draw): a site is *substituted*
(read base differs), *deleted* (reference base missing; a maximal run of deleted sites is one deletion event whose first
site is the *start*), or has an *insertion before it* (the read has extra bases between the previous and this site; a
maximal run of inserted bases is one insertion event). Insertions after the last reference base are counted separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from vnxdna.simulation.errormodels import context_index
from vnxdna.simulation.fit.align import align, dp_align_batch, left_normalise, right_normalise

LUT = np.full(256, 4, dtype=np.uint8)
for _i, _c in enumerate(b"ACGT"):
    LUT[_c] = _i
MINRUNS = (2, 3, 4, 5)
MAX_RUN = 16
EDIT_BINS = 301            # per-read edit distance histogram 0..300 (last bin = more)
DRIFT_OFF = 100            # read length - reference length histogram -100..+100
DRIFT_BINS = 2 * DRIFT_OFF + 1
EXCLUDE_FRAC = 0.30        # reads with more edits than this share of the reference are counted as mis-assigned, not tallied


@dataclass(frozen=True)
class Layout:
    """Offsets of the named count blocks inside a reference's vector."""

    L: int
    minruns: tuple = MINRUNS
    max_run: int = MAX_RUN
    quality: bool = False
    qbins: int = 94
    cycles: int = 0            # read cycles tracked for the quality slope (quality only)
    fields: dict = field(init=False, compare=False)
    size: int = field(init=False, compare=False)

    def __post_init__(self):
        L, M = self.L, len(self.minruns)
        spec = [("n_reads", 1), ("excluded", 1), ("pos_sub", L), ("pos_ins", L), ("pos_del", L), ("pos_delstart", L),
                ("sub_matrix", 16), ("ins_base", 4), ("ins_runs", self.max_run), ("del_runs", self.max_run),
                ("end_ins_events", 1), ("end_ins_bases", 1), ("corr", 4),
                ("ctx_sites", M * 128), ("ctx_sub", M * 128), ("ctx_ins", M * 128), ("ctx_del", M * 128),
                ("ed_n", 1), ("ed_sum", 1), ("ed_sq", 1), ("window_out", 1)]
        if self.quality:
            spec += [("q_correct", self.qbins), ("q_error", self.qbins), ("q_cycle_sum", self.cycles),
                     ("q_cycle_n", self.cycles)]
        off, fields = 0, {}
        for name, n in spec:
            fields[name] = (off, n)
            off += n
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "size", off)

    def get(self, vec: np.ndarray, name: str) -> np.ndarray:
        o, n = self.fields[name]
        return vec[..., o:o + n]

    def ctx(self, vec: np.ndarray, name: str) -> np.ndarray:
        """(..., M, 64, 2) view of a context block (min_run index, centred 3-mer, in-homopolymer flag)."""
        a = self.get(vec, name)
        return a.reshape(a.shape[:-1] + (len(self.minruns), 64, 2))


def ref_codes(ref: bytes) -> np.ndarray:
    return LUT[np.frombuffer(ref, dtype=np.uint8)]


def hp_masks(codes: np.ndarray, minruns=MINRUNS) -> np.ndarray:
    """(len(minruns), L) bool: site lies inside a run of >= min_run equal bases (the simulator's homopolymer mask)."""
    change = np.concatenate([[True], codes[1:] != codes[:-1]])
    rid = np.cumsum(change) - 1
    lens = np.bincount(rid)[rid]
    return np.stack([lens >= m for m in minruns])


def site_features(ref: bytes, minruns=MINRUNS):
    codes = ref_codes(ref)
    return codes, context_index(codes[None, :])[0], hp_masks(codes, minruns)


def read_events(ref: bytes, rcodes_read: bytes, runs, window_start: int = 0) -> dict:
    """Event lists of one normalised alignment (reference sites are 0-based)."""
    L = len(ref)
    sub_sites: list = []
    sub_pairs: list = []
    ins_sites: list = []
    ins_len: list = []
    ins_bases: list = []
    del_sites: list = []
    del_starts: list = []
    del_len: list = []
    ri = 0
    qi = window_start
    for op, n in runs:
        if op == "=":
            ri += n
            qi += n
        elif op == "X":
            for j in range(n):
                sub_sites.append(ri + j)
                to = int(LUT[rcodes_read[qi + j]])
                if to < 4 and LUT[ref[ri + j]] < 4:          # N calls are substitutions but have no matrix cell
                    sub_pairs.append(int(LUT[ref[ri + j]]) * 4 + to)
            ri += n
            qi += n
        elif op == "I":
            ins_sites.append(ri)
            ins_len.append(n)
            ins_bases.extend(int(LUT[b]) for b in rcodes_read[qi:qi + n])
            qi += n
        else:   # D
            del_starts.append(ri)
            del_len.append(n)
            del_sites.extend(range(ri, ri + n))
            ri += n
    return {"sub_sites": sub_sites, "sub_pairs": sub_pairs, "ins_sites": ins_sites, "ins_len": ins_len,
            "ins_bases": ins_bases, "del_sites": del_sites, "del_starts": del_starts, "del_len": del_len, "L": L}


def tally_reference(ref: bytes, reads: list, layout: Layout, mode: str = "NW", quals: list | None = None,
                    exclude_frac: float = EXCLUDE_FRAC, aligner: str = "edlib", shift: str = "left",
                    length_window: tuple | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(count vector of this reference, read-level histogram vector). ``reads`` are upper-case ACGT bytes; for ``HW`` they may
    carry flanks. A read with more than ``exclude_frac * len(ref)`` edits is counted in ``excluded`` and not tallied.
    ``length_window`` = (lo, hi): only reads with lo <= len(read) - len(ref) <= hi are used (protocol 5.5, A2.2: the
    dataset's read selection applied to simulated reads too); the others are counted in ``window_out`` and nothing else.
    ``ed_n``/``ed_sum``/``ed_sq`` hold the count, sum and sum of squares of the edit distance of the tallied reads."""
    L = layout.L
    if len(ref) != L:
        raise ValueError(f"reference length {len(ref)} differs from the layout length {L}")
    vec = np.zeros(layout.size, dtype=np.int64)
    rs = np.zeros(EDIT_BINS + DRIFT_BINS, dtype=np.int64)
    codes, kmer, hp = site_features(ref, layout.minruns)
    lists: dict[str, list] = {k: [] for k in ("sub_sites", "sub_pairs", "ins_sites", "ins_bases", "del_sites", "del_starts")}
    ins_len: list = []
    del_len: list = []
    corr = np.zeros(4, dtype=np.int64)
    n_ok = 0
    qc: np.ndarray | None = None
    qe: np.ndarray | None = None
    if layout.quality:
        qc = np.zeros(layout.qbins, dtype=np.int64)
        qe = np.zeros(layout.qbins, dtype=np.int64)
        qsum = np.zeros(layout.cycles, dtype=np.int64)
        qn = np.zeros(layout.cycles, dtype=np.int64)
    if length_window is not None:
        lo, hi = length_window
        keep = [i for i, r in enumerate(reads) if lo <= len(r) - L <= hi]
        vec[layout.fields["window_out"][0]] = len(reads) - len(keep)
        if len(keep) < len(reads):
            reads = [reads[i] for i in keep]
            quals = None if quals is None else [quals[i] for i in keep]
    ed = np.zeros(3, dtype=np.int64)
    dp = dp_align_batch(ref, reads) if aligner == "dp-diag" and mode == "NW" else None
    for idx, read in enumerate(reads):
        if dp is not None:
            d0, r0, w0 = dp[idx]
            if any(op in "ID" for op, _ in r0):
                r0 = left_normalise(ref, read, r0) if shift == "left" else right_normalise(ref, read, r0)
            res = (d0, r0, w0)
        else:
            res = align(read, ref, mode, shift=shift)
        if res is None:
            vec[layout.fields["excluded"][0]] += 1
            continue
        dist, runs, (ws, we) = res
        rs[min(dist, EDIT_BINS - 1)] += 1
        rs[EDIT_BINS + max(0, min(DRIFT_BINS - 1, (we - ws) - L + DRIFT_OFF))] += 1
        if dist > exclude_frac * L:
            vec[layout.fields["excluded"][0]] += 1
            continue
        n_ok += 1
        ed += (1, dist, dist * dist)
        ev = read_events(ref, read, runs, ws)
        lists["sub_sites"] += ev["sub_sites"]
        lists["sub_pairs"] += ev["sub_pairs"]
        lists["ins_bases"] += ev["ins_bases"]
        lists["del_sites"] += ev["del_sites"]
        lists["del_starts"] += ev["del_starts"]
        ins_len += ev["ins_len"]
        del_len += ev["del_len"]
        lists["ins_sites"] += ev["ins_sites"]
        sites = set(ev["sub_sites"]) | set(ev["del_sites"]) | {i for i in ev["ins_sites"] if i < L}
        if L > 1:
            a = sum(1 for i in sites if i <= L - 2)
            b = sum(1 for i in sites if i >= 1)
            n11 = sum(1 for i in sites if i + 1 in sites)
            corr += (L - 1 - a - b + n11, b - n11, a - n11, n11)
        if layout.quality and quals is not None:
            _tally_quality(qc, qe, qsum, qn, runs, ws, quals[idx], layout)
    f = layout.fields
    vec[f["n_reads"][0]] = n_ok
    vec[f["ed_n"][0]], vec[f["ed_sum"][0]], vec[f["ed_sq"][0]] = ed
    if n_ok:
        for name, key in (("pos_sub", "sub_sites"), ("pos_del", "del_sites"), ("pos_delstart", "del_starts")):
            vec[f[name][0]:f[name][0] + L] = (np.bincount(np.asarray(lists[key], dtype=np.int64), minlength=L)[:L]
                                              if lists[key] else np.zeros(L, dtype=np.int64))
        ins = np.asarray(lists["ins_sites"], dtype=np.int64)
        ins_in = ins[ins < L]
        vec[f["pos_ins"][0]:f["pos_ins"][0] + L] = np.bincount(ins_in, minlength=L)[:L] if ins_in.size else np.zeros(L, dtype=np.int64)
        vec[f["end_ins_events"][0]] = int((ins >= L).sum())
        pairs = np.asarray(lists["sub_pairs"], dtype=np.int64)
        if pairs.size:
            vec[f["sub_matrix"][0]:f["sub_matrix"][0] + 16] = np.bincount(pairs, minlength=16)
        ib = np.asarray(lists["ins_bases"], dtype=np.int64)
        if ib.size:
            vec[f["ins_base"][0]:f["ins_base"][0] + 4] = np.bincount(np.minimum(ib, 3), minlength=4)[:4]
        if ins_len:
            h = np.bincount(np.minimum(np.asarray(ins_len), layout.max_run), minlength=layout.max_run + 1)[1:]
            vec[f["ins_runs"][0]:f["ins_runs"][0] + layout.max_run] = h
        # inserted bases after the last site: sum of run lengths of events at site L
        if (ins >= L).any():
            il = np.asarray(ins_len)
            vec[f["end_ins_bases"][0]] = int(il[ins >= L].sum())
        if del_len:
            h = np.bincount(np.minimum(np.asarray(del_len), layout.max_run), minlength=layout.max_run + 1)[1:]
            vec[f["del_runs"][0]:f["del_runs"][0] + layout.max_run] = h
        vec[f["corr"][0]:f["corr"][0] + 4] = corr
        key = kmer * 2
        for blk, sites_list in (("ctx_sub", lists["sub_sites"]), ("ctx_ins", [i for i in lists["ins_sites"] if i < L]),
                                ("ctx_del", lists["del_starts"])):
            if not sites_list:
                continue
            s = np.asarray(sites_list, dtype=np.int64)
            tab = vec[f[blk][0]:f[blk][0] + len(layout.minruns) * 128].reshape(len(layout.minruns), 128)
            for mi in range(len(layout.minruns)):
                tab[mi] = np.bincount(key[s] + hp[mi][s], minlength=128)
        tab = vec[f["ctx_sites"][0]:f["ctx_sites"][0] + len(layout.minruns) * 128].reshape(len(layout.minruns), 128)
        for mi in range(len(layout.minruns)):
            tab[mi] = np.bincount(key + hp[mi], minlength=128) * n_ok
        if layout.quality and qc is not None and qe is not None:
            vec[f["q_correct"][0]:f["q_correct"][0] + layout.qbins] = qc
            vec[f["q_error"][0]:f["q_error"][0] + layout.qbins] = qe
            vec[f["q_cycle_sum"][0]:f["q_cycle_sum"][0] + layout.cycles] = qsum
            vec[f["q_cycle_n"][0]:f["q_cycle_n"][0] + layout.cycles] = qn
    return vec, rs


def _tally_quality(qc, qe, qsum, qn, runs, ws, quality, layout: Layout) -> None:
    """Phred+33 quality bookkeeping of one alignment: bases at '=' are correct, at 'X' and 'I' erroneous. ``quality`` is
    ``(qual, rev)``: the quality string in the read's original (sequenced) orientation and whether the read was reverse
    complemented before alignment; cycles are counted in the sequenced orientation."""
    qual, rev = quality if isinstance(quality, tuple) else (quality, False)
    q = np.frombuffer(qual, dtype=np.uint8).astype(np.int64) - 33
    if rev:
        q = q[::-1]                                  # aligned orientation
    flag = np.zeros(q.size, dtype=np.int8)         # 0 outside the window / flank, 1 correct, 2 error
    pos = ws
    for op, n in runs:
        if op == "=":
            flag[pos:pos + n] = 1
            pos += n
        elif op in "XI":
            flag[pos:pos + n] = 2
            pos += n
    if rev:
        q, flag = q[::-1], flag[::-1]                # back to the sequenced orientation
    qq = np.clip(q, 0, layout.qbins - 1)
    qc += np.bincount(qq[flag == 1], minlength=layout.qbins)
    qe += np.bincount(qq[flag == 2], minlength=layout.qbins)
    cyc = np.arange(q.size)
    ok = (flag == 1) & (cyc < layout.cycles)
    qsum[:] += np.bincount(cyc[ok], weights=q[ok], minlength=layout.cycles).astype(np.int64)[:layout.cycles]
    qn[:] += np.bincount(cyc[ok], minlength=layout.cycles)[:layout.cycles]
