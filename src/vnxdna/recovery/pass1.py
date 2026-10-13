"""Pass 1 worker (decode stages D2–D6): orientation, fast path, marker-template sync path, eager smart/soft
recovery and pending records, per batch of reads. Worker state lives in ``_P`` (one per process).
Formerly in ``vnxdna.v4.decoder`` (V6 Phase 2, M4)."""
from __future__ import annotations

import time
from collections import Counter

import numpy as np

from vnxdna.dnaenc.frame4 import decode_frames, tentative_address
from vnxdna.dnaenc.layout import HEADER_BYTES, Layout
from vnxdna.dnaenc.mapping import nt_to_bytes
from vnxdna.native import orient as native_orient
from vnxdna.sync.template import SyncCosts, TemplateAligner, frame_erasures_to_bytes, strip_markers_exact


_RC = np.array([3, 2, 1, 0, 4], dtype=np.uint8)
_ORIENT_SHIFT = 2       # orientation pre-pass: marker blocks also match up to this many nt left or right (V12)
_FLAT_PATH = True       # V13 C1/C2: the default configuration works on flat buffers; False = the V12 per-read view lists
#                         (the specification the flat path is pinned to, tests/v13/test_pass1_flat.py)
# ============================================================================ pass 1 worker
_P: dict = {}


def _p_init(layout: Layout, band: int, costs: SyncCosts, min_q: int, rc: bool, smart_cfg=None, soft_cfg=None,
            defer: bool = False, qw: bool = False, retry_band: int = 0, diag: bool = False, unplaced: bool = False,
            rc_retry: str = "bounded", spec: str = "markers") -> None:
    # defer: pass 1 of the deferred schedule — the cheap V4 paths only; smart/soft run later on the pending reads
    # qw: V6 Phase 4 quality-weighted consensus — keep the Phred quality of every projected frame base of pending reads
    # retry_band: V6 opt-in second, wider band for reads beyond ``band`` (0 = off, the V4 behaviour)
    # diag: V7 opt-in stage counters (recovery.stagecount); observability only, never changes a decision
    # unplaced: V7 opt-in read clustering — also return every unverified read, raw, for the unplaced-read store
    # rc_retry: V13 D1 — "bounded" skips the reverse-complement retry of a failed read when a lower bound on its
    # reverse-complement alignment cost already reaches the forward cost (the retry could then only be adopted
    # through a verified reverse-complement frame, see _process); "always" = the V12 retry of every failed read
    # spec: V13 B1 — "markers" projects the reads of the sync path from their marker-block shifts first and runs the
    # DP only for those whose speculative frame does not verify (_spec_project); "off" = the DP for every read
    _P.update(lay=layout, al=TemplateAligner(layout, band, costs, retry_band=retry_band), min_q=min_q, rc=rc, smart=smart_cfg, soft=soft_cfg,
              defer=defer, qw=qw, diag=diag, unpl=unplaced, rc_retry=rc_retry, spec=spec)
    if smart_cfg is not None or soft_cfg is not None:
        from vnxdna.sync.smart.recovery import Geometry
        _P["geom"] = Geometry(layout)


def _try(reads: list[np.ndarray], quals: list | None, with_quality: bool = False, diag: dict | None = None,
         flat: tuple | None = None) -> tuple:
    """Fast and sync paths (plus eager smart/soft) for one list of reads. Returns (acc, fields, payload, projected
    bases, cost, path, hard bases, stats), and with ``with_quality`` also the projected qualities (None unless the
    worker keeps them for quality-weighted consensus). ``diag`` (V7 stage counters, observability only): a dict that
    receives per-read arrays of the last attempt (erased frame bytes, inner RS and CRC outcome, marker mismatches,
    RS errata); it never changes a result. ``flat`` = (codes, quals or None, offsets, lengths) when ``reads`` (and
    ``quals``) are consecutive views into flat buffers: the exact-length reads are then gathered from the buffers
    instead of stacked view by view (V12 pass-1 bookkeeping; same values)."""
    lay: Layout = _P["lay"]
    if flat is not None:
        lengths = flat[3]
        n = int(lengths.size)
    else:
        n = len(reads)
        lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    qsrc = flat[1] if flat is not None else quals          # the batch's qualities, flat or as views
    if reads is None and (_P.get("smart") is not None or _P.get("soft") is not None or _P.get("qw")):
        raise ValueError("flat-only reads need the default recovery configuration (no smart/soft recovery, no "
                         "quality-weighted consensus)")
    if diag is not None:
        diag.update(erased_bytes=np.full(n, lay.frame_bytes, dtype=np.int64), rs_ok=np.zeros(n, dtype=bool),
                    crc_ok=np.zeros(n, dtype=bool), marker_mm=np.zeros(n, dtype=np.int64),
                    errata=np.zeros(n, dtype=np.int64))
    acc = np.zeros(n, dtype=bool)
    proj_bases = np.full((n, lay.frame_nt), 4, dtype=np.uint8)
    hard_bases = np.zeros((n, lay.frame_nt), dtype=np.uint8)
    cost = np.full(n, 1 << 28, dtype=np.int64)
    parsed_fields = np.zeros((n, 4), dtype=np.int64)
    payload = np.zeros((n, lay.payload_bytes), dtype=np.uint8)
    path = np.zeros(n, dtype=np.int8)          # 1 fast, 2 sync, 3 smart, 4 soft, 5 speculative sync (V13 B1)
    # quality of each projected frame base (V6 Phase 4 weighted consensus; 0 where no read base or no qualities)
    proj_q = np.zeros((n, lay.frame_nt), dtype=np.uint8) if _P.get("qw") else None
    exact = np.flatnonzero(lengths == lay.strand_nt)
    if exact.size:
        if flat is not None and proj_q is None:
            # V13 S3: gather, marker check, quality erasures and byte packing in one native pass over the reads
            hard, pb, frames, erb, mism = _pack_exact(flat[0], flat[2][exact], qsrc, _P["min_q"])
        else:
            if flat is not None:
                estart = flat[2][exact]
                mat = _gather_exact(flat[0], estart, lay.strand_nt)
            else:
                mat = np.stack([reads[i] for i in exact])
            fb, mism = strip_markers_exact(lay, mat)
            er = fb > 3
            if qsrc is not None and (_P["min_q"] or proj_q is not None):
                qm = _gather_exact(flat[1], estart, lay.strand_nt) if flat is not None else np.stack([quals[i] for i in exact])
                tpl, pos = lay.template()
                if _P["min_q"]:
                    er |= qm[:, pos] < _P["min_q"]
                if proj_q is not None:
                    proj_q[exact] = qm[:, pos]
            hard, pb, frames, erb = _pack_projection(fb, er)
        P = decode_frames(lay, frames, erb)
        ok = P.ok
        idx = exact[ok]
        acc[idx] = True
        path[idx] = 1
        parsed_fields[idx] = np.stack([P.kind, P.tag, P.group, P.symbol], axis=1)[ok]
        payload[idx] = P.payload[ok]
        cost[exact] = mism * _P["al"].costs.marker_mismatch
        if diag is not None:
            diag["erased_bytes"][exact] = erb.sum(axis=1)
            diag["rs_ok"][exact], diag["crc_ok"][exact] = P.rs_ok, P.crc_ok
            diag["marker_mm"][exact], diag["errata"][exact] = mism, P.errata
        hard_bases[exact] = hard
        proj_bases[exact] = pb
    # exact-length reads whose markers all match gain nothing from re-alignment (same frame, same RS failure)
    hopeless = np.zeros(n, dtype=bool)
    if exact.size and lay.markers:
        hopeless[exact[mism == 0]] = True
    rest = np.flatnonzero(~acc & ~hopeless)
    defer = _P.get("defer", False)
    smart = None if defer else _P.get("smart")
    soft = None if defer else _P.get("soft")
    sstats: Counter = Counter()
    soft_jobs: list = []                   # (read index, evidence) for the V5 Phase 4 soft stage
    if rest.size and reads is None and _P.get("spec", "markers") == "markers" and lay.markers:
        # V13 B1: marker-anchored speculative projection. A read whose frame verifies (inner RS + CRC) from the
        # projection implied by its marker-block shifts is accepted without the DP; every other read of ``rest``
        # takes the DP below, unchanged. Acceptances can only be added by this step, never removed.
        sok, sbases, serased, smm, sind = _spec_project(flat[0], flat[2][rest], lengths[rest], flat[1], _P["min_q"])
        sidx = np.flatnonzero(sok)
        if sidx.size:
            hard_s, pb_s, frames_s, er_s = _pack_projection(sbases[sidx], serased[sidx])
            Ps = decode_frames(lay, frames_s, er_s, errors_only_retry=False)
            hit = Ps.ok
            if hit.any():
                ridx = rest[sidx[hit]]
                c = _P["al"].costs
                acc[ridx] = True
                path[ridx] = 5
                parsed_fields[ridx] = np.stack([Ps.kind, Ps.tag, Ps.group, Ps.symbol], axis=1)[hit]
                payload[ridx] = Ps.payload[hit]
                # nominal cost of the speculative alignment (marker mismatches + one indel per unit of shift change);
                # no decision depends on the cost of an accepted read
                cost[ridx] = smm[sidx[hit]] * c.marker_mismatch + sind[sidx[hit]] * min(c.insertion, c.deletion)
                hard_bases[ridx] = hard_s[hit]
                proj_bases[ridx] = pb_s[hit]
                if diag is not None:
                    diag["erased_bytes"][ridx] = er_s[hit].sum(axis=1)
                    diag["rs_ok"][ridx], diag["crc_ok"][ridx] = Ps.rs_ok[hit], Ps.crc_ok[hit]
                    diag["marker_mm"][ridx], diag["errata"][ridx] = smm[sidx[hit]], Ps.errata[hit]
                rest = rest[~acc[rest]]
    if rest.size:
        if reads is None:
            # V13 C1: the sync path on the flat batch, no per-read views (same Projection as the list form)
            rreads = rquals = None
            pr = _P["al"].project_flat(flat[0], flat[2], lengths, rest, flat[1], _P["min_q"])
        else:
            rreads = [reads[i] for i in rest]
            rquals = None if quals is None else [quals[i] for i in rest]
        if reads is None:
            pass
        elif smart is None and soft is None and not (proj_q is not None and rquals is not None):
            pr = _P["al"].project(rreads, rquals, _P["min_q"])
        else:
            from vnxdna.sync.smart.path import align_with_path
            pr, rpos = align_with_path(_P["al"], rreads, rquals, _P["min_q"])   # identical projection + the path
        hard, pb, frames, er = _pack_projection(pr.bases, pr.erased)
        P = decode_frames(lay, frames, er, errors_only_retry=False)   # sync erasures come from detected indels
        ok = P.ok & pr.ok
        if diag is not None:
            diag["erased_bytes"][rest] = np.where(pr.ok, er.sum(axis=1), lay.frame_bytes)
            diag["rs_ok"][rest], diag["crc_ok"][rest] = P.rs_ok & pr.ok, P.crc_ok & pr.ok
            diag["marker_mm"][rest], diag["errata"][rest] = np.where(pr.ok, pr.marker_mismatches, 0), P.errata
        idx = rest[ok]
        acc[idx] = True
        path[idx] = 2
        parsed_fields[idx] = np.stack([P.kind, P.tag, P.group, P.symbol], axis=1)[ok]
        payload[idx] = P.payload[ok]
        cand_all = np.flatnonzero(~ok & pr.ok)
        if smart is not None:
            # V5 smart indel recovery, only for aligned reads the V4 erasure rule could not decode
            from vnxdna.sync.smart import recovery as rv
            for c0 in range(0, cand_all.size, rv.PLAN_CHUNK):     # bounded memory: plans for ≤ PLAN_CHUNK reads at a time
                cand = cand_all[c0:c0 + rv.PLAN_CHUNK]
                plans = rv.plan_reads(_P["geom"], rreads, rquals, pr, rpos, cand, smart)
                outs = rv.recover_batch(_P["geom"], plans, smart)
                sstats.update(rv.summarize_outcomes(plans, outs, pr.erased[cand]))
                for j, o, pl in zip(cand.tolist(), outs, plans):
                    if o.accepted:
                        i = rest[j]
                        acc[i] = True
                        path[i] = 3
                        parsed_fields[i] = o.fields
                        payload[i] = o.payload
                    elif soft is not None:
                        soft_jobs.append((int(rest[j]), _soft_evidence(rreads[j], None if rquals is None else rquals[j],
                                                                       pr.bases[j], pr.erased[j], rpos[j], pl)))
        elif soft is not None:
            for j in cand_all.tolist():
                soft_jobs.append((int(rest[j]), _soft_evidence(rreads[j], None if rquals is None else rquals[j], pr.bases[j],
                                                               pr.erased[j], rpos[j], None)))
        if proj_q is not None and rquals is not None:
            # quality of the read base each frame base was aligned to (the alignment path; −1 = deleted → 0)
            fpos = lay.template()[1]
            qlen = np.fromiter((q.size for q in rquals), dtype=np.int64, count=len(rquals))
            qmat = np.zeros((len(rquals), max(1, int(qlen.max(initial=0)))), dtype=np.uint8)
            for j, q in enumerate(rquals):
                qmat[j, : q.size] = q
            ri = np.asarray(rpos)[:, fpos].astype(np.int64)
            proj_q[rest] = np.where(ri >= 0, np.take_along_axis(qmat, np.maximum(ri, 0), axis=1), 0)
        cost[rest] = np.where(pr.ok, pr.cost, 1 << 28)
        hard_bases[rest] = hard
        proj_bases[rest] = pb
    if soft is not None:
        # exact-length reads whose markers all match skipped re-alignment: their frame is the read itself
        T = lay.strand_nt
        ident = np.arange(T, dtype=np.int16)
        for i in np.flatnonzero(hopeless & ~acc).tolist():
            fb = reads[i][_P["geom"].frame_pos]
            soft_jobs.append((i, _soft_evidence(reads[i], None if quals is None else quals[i], np.minimum(fb, 4),
                                                fb > 3, ident, None)))
        if soft_jobs:
            from vnxdna.recovery.soft import decoder as sd
            from vnxdna.recovery.soft import symbols as ss
            post = []
            for _, ev in soft_jobs:
                try:
                    post.append(ss.normalise(ev))
                except ss.SoftInputError:
                    post.append(np.full((lay.frame_nt, 4), np.nan))    # validated (and rejected) by soft_decode
            for c0 in range(0, len(soft_jobs), 512):
                outs = sd.soft_decode(lay, post[c0:c0 + 512], soft)
                sstats.update({f"soft_{k}": v for k, v in sd.summarize(outs).items() if v is not None})
                for (i, _), o in zip(soft_jobs[c0:c0 + 512], outs):
                    if o.accepted and not acc[i]:
                        acc[i] = True
                        path[i] = 4
                        parsed_fields[i] = o.fields
                        payload[i] = o.payload
    if with_quality:
        return acc, parsed_fields, payload, proj_bases, cost, path, hard_bases, sstats, proj_q
    return acc, parsed_fields, payload, proj_bases, cost, path, hard_bases, sstats


def _soft_evidence(read, qual, proj_bases, proj_erased, rpos, plan):
    from vnxdna.recovery.soft.frames import read_evidence
    return read_evidence(_P["geom"], np.asarray(read), None if qual is None else np.asarray(qual), proj_bases, proj_erased,
                         rpos, plan, _P["soft"].default_error)


def _orientation(codes: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    """True where the reverse complement agrees better with the marker template (layouts with markers only).

    V12: each marker block scores its best agreement over shifts of up to ``_ORIENT_SHIFT`` nt. One indel shifts every
    later marker; V11 compared unshifted positions only, so 4.5 % of forward reads with indels (SIMULATED, 1 %/0.2 %/0.2 %
    channel) were flipped, failed, and were only rescued by the reverse-complement retry. The retry is unchanged: a wrong
    orientation still costs only time.

    The scan runs in the native kernel ``vnxdna.native.orient`` when it loads (``VNXDNA_ORIENT_BACKEND``), else in
    :func:`_orientation_reference`, the NumPy specification the kernel is checked against bit for bit.
    """
    lay: Layout = _P["lay"]
    n = lengths.size
    tpl, _ = lay.template()
    mpos = np.flatnonzero(tpl >= 0)
    if n == 0 or mpos.size == 0:
        return np.zeros(n, dtype=bool)
    want = tpl[mpos].astype(np.uint8)
    starts = np.flatnonzero(np.diff(mpos, prepend=-2) > 1)          # first marker position of each marker block
    margin = max(2, mpos.size // 8)
    if native_orient.resolve_backend() == "native":
        return native_orient.scan(codes, lengths, mpos, want, starts, margin, _ORIENT_SHIFT)
    return _orientation_reference(codes, lengths, mpos, want, starts, margin)


def _orientation_reference(codes: np.ndarray, lengths: np.ndarray, mpos: np.ndarray, want: np.ndarray,
                           starts: np.ndarray, margin: int) -> np.ndarray:
    """NumPy reference of the orientation scan (the specification of native/c/orient.c).

    Vectorised over the flat batch: marker positions are gathered from each read's start (forward) and from its end
    (reverse complement) without per-read Python work.
    """
    n = lengths.size
    offs = np.concatenate([[0], np.cumsum(lengths)])[:-1]
    top = max(0, codes.size - 1)

    def score(rows: np.ndarray, s: int, rc: bool) -> np.ndarray:
        """Per-block marker agreement of reads ``rows`` at shift ``s``, forward or as reverse complement."""
        p = mpos[None, :] + s
        ln = lengths[rows][:, None]
        inside = (p >= 0) & (p < ln)
        if rc:
            idx = np.clip(offs[rows][:, None] + ln - 1 - p, 0, top)
            hit = (_RC[codes[idx]] == want[None, :]) & inside
        else:
            idx = np.clip(offs[rows][:, None] + p, 0, top)
            hit = (codes[idx] == want[None, :]) & inside
        return np.add.reduceat(hit, starts, axis=1, dtype=np.int64)

    # Best block agreement over the shifts, forward (sf) and reverse complement (sr); a read is flipped when
    # sum(sr) > sum(sf) + margin. Since sr never exceeds the marker count, a read whose forward score already reaches
    # marker count - margin cannot be flipped whatever the remaining shifts give: it leaves the scan (decisions are
    # identical to scoring every shift for every read, with far fewer gathers: most reads are decided at shift 0 or +-1).
    sf = score(np.arange(n), 0, False)
    sr = np.zeros_like(sf)
    rows = np.flatnonzero(sf.sum(axis=1) + margin < mpos.size)
    if rows.size:
        sr[rows] = score(rows, 0, True)
    for s in [x for k in range(1, _ORIENT_SHIFT + 1) for x in (-k, k)]:
        if not rows.size:
            break
        sf_rows = np.maximum(sf[rows], score(rows, s, False))
        sf[rows] = sf_rows
        sr[rows] = np.maximum(sr[rows], score(rows, s, True))
        rows = rows[sf_rows.sum(axis=1) + margin < mpos.size]
    return sr.sum(axis=1) > sf.sum(axis=1) + margin


_RC256 = np.full(256, 255, dtype=np.uint8)      # complement table over every byte value: codes above 4 never match
_RC256[:5] = _RC


def _rc_bound_reference(codes: np.ndarray, lengths: np.ndarray, offs: np.ndarray, mpos: np.ndarray, want: np.ndarray,
                        starts: np.ndarray, band: int, c_mm: int, c_ins: int, c_del: int) -> np.ndarray:
    """Lower bound on the band-``band`` marker-template alignment cost of each selected read as a reverse complement
    (the specification of ``native.orient.rc_bound``, V13 D1).

    Any alignment within the band either keeps one shift ``s`` (|s| <= band) for all the markers of a block, paying
    ``c_mm`` per mismatching marker, or places an insertion or deletion inside that block, paying at least
    ``min(c_ins, c_del)``; blocks and the operations inside them are disjoint, so the sum over blocks of
    ``min(c_mm * (block size - best hits over the shifts), c_ins, c_del)`` is at most the cost of every alignment the
    DP can return (tests/v13/test_rc_retry_bound.py checks it against the DP on random reads)."""
    n = lengths.size
    out = np.zeros(n, dtype=np.int64)
    if n == 0 or mpos.size == 0:
        return out
    offs = offs.astype(np.int64, copy=False)
    lengths = lengths.astype(np.int64, copy=False)
    sizes = np.diff(np.append(starts, mpos.size)).astype(np.int64)
    best = np.zeros((n, starts.size), dtype=np.int64)
    top = max(0, codes.size - 1)
    ln = lengths[:, None]
    for s in range(-int(band), int(band) + 1):
        p = mpos[None, :] + s
        inside = (p >= 0) & (p < ln)
        idx = np.clip(offs[:, None] + ln - 1 - p, 0, top)
        hit = (_RC256[codes[idx]] == want[None, :]) & inside
        np.maximum(best, np.add.reduceat(hit, starts, axis=1, dtype=np.int64), out=best)
    return np.minimum(int(c_mm) * (sizes[None, :] - best), min(int(c_ins), int(c_del))).sum(axis=1)


def _rc_bound(codes: np.ndarray, offs: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    """The reverse-complement cost bound of the selected reads with the worker's layout, band and costs (native kernel
    when it loads, else the reference)."""
    lay: Layout = _P["lay"]
    al = _P["al"]
    tpl, _ = lay.template()
    mpos = np.flatnonzero(tpl >= 0)
    want = tpl[mpos].astype(np.uint8)
    starts = np.flatnonzero(np.diff(mpos, prepend=-2) > 1)
    band = max(al.band, al.retry_band)
    c = al.costs
    if native_orient.resolve_backend() == "native":
        return native_orient.rc_bound(codes, lengths, offs, mpos, want, starts, band, c.marker_mismatch, c.insertion,
                                      c.deletion)
    return _rc_bound_reference(codes, lengths, offs, mpos, want, starts, band, c.marker_mismatch, c.insertion, c.deletion)


def _spec_project_reference(codes: np.ndarray, lengths: np.ndarray, offs: np.ndarray, qflat: np.ndarray | None,
                            min_q: int, T: int, mpos: np.ndarray, want: np.ndarray, starts: np.ndarray,
                            frame_pos: np.ndarray, band: int) -> tuple:
    """Marker-anchored speculative projection of the selected reads onto frame coordinates, without the DP (the
    specification of ``native.orient.spec_project``, V13 B1). Returns (ok, bases, erased, mm, indels).

    For a read of length ``L`` (``delta = L - T``, ``|delta| <= band`` or the read is not projected) the marker blocks
    (``starts`` indexes the first marker of each block in ``mpos``) are visited left to right with a running shift
    ``s_prev`` (0 at the start). A block takes the first shift of the sequence ``s_prev, s_prev - 1, s_prev + 1,
    s_prev - 2, ...`` (``|s| <= band``, the whole block inside the read) at which every marker matches; if none does,
    it keeps ``s_prev`` when at most one marker mismatches there (counted in ``mm``), otherwise the read is not
    projected. ``indels`` is the sum of the shift changes, including the final change to ``delta``. A template position
    ``p`` lies in region ``r`` = the number of blocks whose first marker is ``<= p``; its left shift is that of block
    ``r - 1`` (0 for the first region) and its right shift that of block ``r`` (``delta`` after the last block). Its
    base is the read base at ``p + left shift`` (4 outside the read) and it is erased when that base is not A/C/G/T,
    when its quality is below ``min_q`` (``qflat`` given) or when the two shifts differ (the region holds an indel).
    Only frame positions are returned. A read that is not projected has bases 4, every position erased, mm and indels 0.

    Unlike the DP (``TemplateAligner``) this projection does not minimise a cost: it is only ever used behind the inner
    RS + CRC verification of the frame it yields."""
    n = int(lengths.size)
    F = int(frame_pos.size)
    L = lengths.astype(np.int64, copy=False)
    offs = offs.astype(np.int64, copy=False)
    bases = np.full((n, F), 4, dtype=np.uint8)
    erased = np.ones((n, F), dtype=bool)
    mm = np.zeros(n, dtype=np.int64)
    indels = np.zeros(n, dtype=np.int64)
    alive = np.abs(L - T) <= band
    if n == 0:
        return alive, bases, erased, mm, indels
    nb = int(starts.size) if mpos.size else 0
    blocks = [mpos[a:b].astype(np.int64) for a, b in zip(starts, np.append(starts[1:], mpos.size))] if nb else []
    s_prev = np.zeros(n, dtype=np.int64)
    shifts = np.zeros((n, nb), dtype=np.int64)
    for b, bl in enumerate(blocks):
        wb = want[starts[b]:starts[b] + bl.size]
        found = np.zeros(n, dtype=bool)
        for d in range(2 * band + 1):
            k = (d + 1) // 2
            s = s_prev - k if d % 2 else s_prev + k
            rows = np.flatnonzero(alive & ~found & (s >= -band) & (s <= band) & (bl[0] + s >= 0) & (bl[-1] + s < L))
            if rows.size == 0:
                continue
            hit = (codes[offs[rows, None] + bl[None, :] + s[rows, None]] == wb[None, :]).all(axis=1)
            r2 = rows[hit]
            indels[r2] += np.abs(s[r2] - s_prev[r2])
            s_prev[r2] = s[r2]
            found[r2] = True
        rows = np.flatnonzero(alive & ~found)
        if rows.size:
            s = s_prev[rows]
            inside = (bl[0] + s >= 0) & (bl[-1] + s < L[rows])
            bad = np.full(rows.size, bl.size + 1, dtype=np.int64)
            r_in = rows[inside]
            if r_in.size:
                bad[inside] = (codes[offs[r_in, None] + bl[None, :] + s_prev[r_in, None]] != wb[None, :]).sum(axis=1)
            keep = bad <= 1
            mm[rows[keep]] += bad[keep]
            alive[rows[~keep]] = False
        shifts[:, b] = s_prev
    ok = alive
    delta = L - T
    indels += np.abs(delta - s_prev)
    mm[~ok] = 0
    indels[~ok] = 0
    if F == 0 or not ok.any():
        return ok, bases, erased, mm, indels
    rows = np.flatnonzero(ok)
    if nb:
        region = np.searchsorted(mpos[starts], frame_pos, side="right")
        sl = np.where(region[None, :] == 0, 0, shifts[rows][:, np.maximum(region - 1, 0)])
        sr = np.where(region[None, :] == nb, delta[rows, None], shifts[rows][:, np.minimum(region, nb - 1)])
    else:
        sl = np.zeros((rows.size, F), dtype=np.int64)
        sr = np.broadcast_to(delta[rows, None], (rows.size, F))
    j = frame_pos[None, :].astype(np.int64) + sl
    inside = (j >= 0) & (j < L[rows, None])
    idx = np.clip(offs[rows, None] + j, 0, max(0, codes.size - 1))
    base = np.where(inside, np.minimum(codes[idx], 4), 4).astype(np.uint8)
    er = (sl != sr) | (base > 3)
    if qflat is not None:
        er |= inside & (qflat[idx] < min_q)
    bases[rows] = base
    erased[rows] = er
    return ok, bases, erased, mm, indels


def _spec_project(codes: np.ndarray, offs: np.ndarray, lengths: np.ndarray, qflat: np.ndarray | None,
                  min_q: int) -> tuple:
    """The speculative projection of the selected reads with the worker's layout and band (native kernel when it
    loads, else the reference)."""
    lay: Layout = _P["lay"]
    al = _P["al"]
    tpl, fpos = lay.template()
    mpos = np.flatnonzero(tpl >= 0)
    want = tpl[mpos].astype(np.uint8)
    starts = np.flatnonzero(np.diff(mpos, prepend=-2) > 1)
    band = al.band          # reads of the opt-in retry window (band < |len - T| <= retry_band) take the DP as before
    if native_orient.resolve_backend() == "native":
        return native_orient.spec_project(codes, lengths, offs, qflat, min_q, tpl.size, mpos, want, starts, fpos, band)
    return _spec_project_reference(codes, lengths, offs, qflat, min_q, tpl.size, mpos, want, starts, fpos, band)


def _pack_projection_reference(bases: np.ndarray, erased: np.ndarray) -> tuple:
    """NumPy form of the frame packing of (n, F) projected bases and erasure flags (the specification of
    ``vnx_or_pack_projection``): (hard bases, projection with erased bases = 4, frame bytes, erased bytes)."""
    hard = np.minimum(bases, 3).astype(np.uint8, copy=False)
    proj = np.array(bases, dtype=np.uint8, copy=True)
    proj[erased.astype(bool, copy=False)] = 4
    return hard, proj, nt_to_bytes(hard), frame_erasures_to_bytes(np.ascontiguousarray(erased, dtype=np.uint8))


def _pack_exact_reference(lay: Layout, codes: np.ndarray, offs: np.ndarray, qflat: np.ndarray | None, min_q: int) -> tuple:
    """NumPy form of the exact-length path's frame packing (the specification of ``vnx_or_pack_exact``): the reads of
    ``lay.strand_nt`` bases at ``offs`` → (hard, proj, frames, erased bytes, marker mismatches); a base is erased when
    it is not A/C/G/T or (with qualities and ``min_q`` > 0) its quality is below ``min_q``."""
    mat = _gather_exact(codes, offs, lay.strand_nt)
    fb, mism = strip_markers_exact(lay, mat)
    er = fb > 3
    if qflat is not None and min_q:
        er |= _gather_exact(qflat, offs, lay.strand_nt)[:, lay.template()[1]] < min_q
    return (*_pack_projection_reference(fb, er), mism)


def _pack_projection(bases: np.ndarray, erased: np.ndarray) -> tuple:
    """Frame packing of projected bases (native kernel when it loads and the frame has 4k bases, else the reference)."""
    if bases.shape[1] % 4 == 0 and native_orient.resolve_backend() == "native":
        return native_orient.pack_projection(bases, erased)
    return _pack_projection_reference(bases, erased)


def _pack_exact(codes: np.ndarray, offs: np.ndarray, qflat: np.ndarray | None, min_q: int) -> tuple:
    """Frame packing of the exact-length reads at ``offs`` with the worker's layout (native kernel or reference)."""
    lay: Layout = _P["lay"]
    if lay.frame_nt % 4 == 0 and native_orient.resolve_backend() == "native":
        tpl, fpos = lay.template()
        mpos = np.flatnonzero(tpl >= 0)
        return native_orient.pack_exact(codes, offs, qflat if min_q else None, min_q, tpl.size, mpos,
                                        tpl[mpos].astype(np.uint8), fpos)
    return _pack_exact_reference(lay, codes, offs, qflat, min_q)


_GATHER_ROWS = 512      # rows per gather chunk (V12 session 5): the int64 index matrix of one chunk is 512 x strand_nt
#                         x 8 B (1.3 MiB at 313 nt); one index for the whole 8192-read batch was 20 MiB per worker, and
#                         the freed arena stayed resident, raising the peak RSS of every pass-1 worker by that much


def _gather_exact(flat: np.ndarray, starts: np.ndarray, width: int) -> np.ndarray:
    """(len(starts), width) matrix of the ``width`` consecutive entries of ``flat`` from each start (reads of exactly
    ``width`` entries, as views into the flat batch), gathered in row chunks of ``_GATHER_ROWS`` so the temporary index
    matrix stays small. Same values as ``np.stack`` of the views (tests/v12/test_pass1_gather.py)."""
    out = np.empty((starts.size, width), dtype=flat.dtype)
    cols = np.arange(width, dtype=np.int64)[None, :]
    for a in range(0, starts.size, _GATHER_ROWS):
        out[a:a + _GATHER_ROWS] = flat[starts[a:a + _GATHER_ROWS][:, None] + cols]
    return out


def _gather_rows(flat: np.ndarray, offs: np.ndarray, lengths: np.ndarray, sel: np.ndarray, cols: np.ndarray,
                 clip: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Rows ``sel`` of a flat read buffer as a (len(sel), len(cols)) matrix plus the valid mask: entry (j, c) is read
    ``sel[j]``'s base at position ``cols[c]`` (clipped to ``clip`` when given) and 0 where the read is shorter than
    ``cols[c]``. One gather per row chunk instead of a Python loop over reads (V12 pass-1 bookkeeping); the loop form
    it replaces is in tests/v12/test_pass1_gather.py."""
    valid = cols[None, :] < lengths[sel][:, None]
    out = np.zeros((sel.size, cols.size), dtype=np.uint8)
    if sel.size and flat.size:
        starts = offs[sel]
        for a in range(0, sel.size, _GATHER_ROWS):
            v = valid[a:a + _GATHER_ROWS]
            got = flat[np.where(v, starts[a:a + _GATHER_ROWS][:, None] + cols[None, :], 0)]
            if clip is not None:
                np.minimum(got, clip, out=got)
            o = out[a:a + _GATHER_ROWS]
            o[v] = got[v]
    return out, valid


def _rc_flat(codes: np.ndarray, qflat: np.ndarray | None, offs: np.ndarray, lengths: np.ndarray,
             sel: np.ndarray) -> tuple:
    """Reads ``sel`` of a flat batch, each reversed and complemented, as a new flat batch (codes, quals or None,
    offsets, lengths) in the order of ``sel``: one gather instead of a per-read loop (V13 C2; the loop form it
    replaces is in tests/v13/test_pass1_flat.py). Code 4 (N) stays 4."""
    L = lengths[sel].astype(np.int64, copy=False)
    roffs = np.zeros(L.size + 1, dtype=np.int64)
    np.cumsum(L, out=roffs[1:])
    total = int(roffs[-1])
    # output position p of read j reads source position offs[sel[j]] + L[j] - 1 - (p - roffs[j])
    src = np.repeat(offs[sel].astype(np.int64, copy=False) + L - 1 + roffs[:-1], L) - np.arange(total, dtype=np.int64)
    rcodes = _RC[codes[src]]
    rq = None if qflat is None else qflat[src]
    return rcodes, rq, roffs, L


def _process(batch_codes: np.ndarray, lengths: np.ndarray, quals: np.ndarray | None) -> dict:
    cpu0 = time.process_time()
    lay: Layout = _P["lay"]
    offs = np.concatenate([[0], np.cumsum(lengths)])
    rc_used = np.zeros(lengths.size, dtype=bool)
    d = {} if _P.get("diag") else None           # V7 stage counters (observability only)
    flip = np.zeros(lengths.size, dtype=bool)
    # flat buffers of the reads in the orientation pass 1 works on (the batch, or a copy with the flipped reads
    # written in place); the read lists below are views into them
    codes, qflat = batch_codes, quals
    if _P["rc"]:
        # orientation pre-pass: marker agreement at the expected (unshifted) positions, forward vs reverse complement
        flip = _orientation(batch_codes, lengths)
        if flip.any():
            codes = batch_codes.copy()
            qflat = None if quals is None else quals.copy()
            for i in np.flatnonzero(flip).tolist():
                a, b = int(offs[i]), int(offs[i + 1])
                codes[a:b] = _RC[batch_codes[a:b][::-1]]
                if qflat is not None:
                    qflat[a:b] = quals[a:b][::-1]
        rc_used |= flip
    if _FLAT_PATH and _P.get("smart") is None and _P.get("soft") is None and not _P.get("qw"):
        reads = ql = None       # V13 C1: the default configuration works on the flat buffers only (no view lists)
    else:
        reads = [codes[offs[i]:offs[i + 1]] for i in range(lengths.size)]
        ql = None if qflat is None else [qflat[offs[i]:offs[i + 1]] for i in range(lengths.size)]
    acc, fields, payload, proj, cost, path, hard, sstats, pq = _try(reads, ql, with_quality=True, diag=d,
                                                                   flat=(codes, qflat, offs, lengths))
    if d is not None:
        d["flip"] = flip.copy()
        d["rc_tried"] = np.zeros(lengths.size, dtype=bool)
        d["rc_adopted"] = np.zeros(lengths.size, dtype=bool)
    ocodes, oq = codes, qflat              # the orientation each read's projection refers to (pending raw reads)
    rc_skipped = 0
    if _P["rc"]:
        bad = np.flatnonzero(~acc & (cost > 2 * _P["al"].costs.insertion))
        if bad.size and reads is None and _P.get("rc_retry", "bounded") == "bounded":
            # V13 D1: the retry is adopted when its frame verifies or its alignment cost is below the forward cost.
            # Where the bound on the reverse-complement cost already reaches the forward cost, only a verified
            # reverse-complement frame could still adopt it; such reads are not retried (counted in the stats).
            keep = _rc_bound(codes, offs[bad], lengths[bad]) < cost[bad]
            rc_skipped = int(bad.size - keep.sum())
            bad = bad[keep]
        if bad.size:
            d2 = {} if d is not None else None
            if reads is None:
                # V13 C2: the retried reads reversed and complemented by one gather into a flat buffer
                a2, f2, p2, pr2, c2, path2, h2, s2, q2 = _try(None, None, with_quality=True, diag=d2,
                                                             flat=_rc_flat(codes, qflat, offs, lengths, bad))
            else:
                rreads = [_RC[reads[i][::-1]] for i in bad]
                rq = None if ql is None else [ql[i][::-1] for i in bad]
                a2, f2, p2, pr2, c2, path2, h2, s2, q2 = _try(rreads, rq, with_quality=True, diag=d2)
            sstats.update(s2)
            better = a2 | (c2 < cost[bad])
            sel = bad[better]
            if d is not None:
                d["rc_tried"][bad] = True
                d["rc_adopted"][sel] = True
                for k_, v_ in d2.items():
                    d[k_][sel] = v_[better]
            acc[sel], fields[sel], payload[sel], proj[sel], cost[sel], path[sel], hard[sel] = (
                a2[better], f2[better], p2[better], pr2[better], c2[better], path2[better], h2[better])
            rc_used[sel] = ~rc_used[sel]
            if pq is not None:
                pq[sel] = q2[better]
            if (_P.get("smart") is not None or _P.get("soft") is not None) and sel.size:
                ocodes = codes.copy()
                oq = None if qflat is None else qflat.copy()
                for j in np.flatnonzero(better).tolist():
                    a, b = int(offs[bad[j]]), int(offs[bad[j] + 1])
                    ocodes[a:b] = rreads[j]
                    if oq is not None:
                        oq[a:b] = rq[j]
    # pending: every failed but aligned read. Its tentative address comes from the hard header bases (even where the
    # header was erased by an indel); pass 2 snaps addresses that are not expected to the nearest missing address.
    pend = np.flatnonzero(~acc & (cost < (1 << 28)))
    pend_fields = np.zeros((0, 4), dtype=np.int64)
    pend_bases = np.zeros((0, lay.frame_nt), dtype=np.uint8)
    orphans = 0
    pend_alt = np.zeros((0, 4), dtype=np.int64)
    good = np.zeros(pend.size, dtype=bool)
    if pend.size:
        header_erased = (proj[pend][:, : 4 * HEADER_BYTES] > 3).any(axis=1)
        orphans = int(header_erased.sum())            # header not trusted (kept, addressed by hard bases)
        ta = tentative_address(lay, nt_to_bytes(hard[pend]))
        # second reading of the header: the raw read prefix without any indel correction. The DP puts an indel at the
        # left edge of its segment, so where the projected header is shifted, the unshifted prefix is right.
        _, fpos = lay.template()
        hpos = fpos[: 4 * HEADER_BYTES]
        raw = np.zeros((pend.size, lay.frame_nt), dtype=np.uint8)
        # header positions are ascending, so the positions inside a read are a prefix: a masked gather
        raw[:, : hpos.size] = _gather_rows(codes, offs, lengths, pend, hpos, clip=3)[0]
        alt = tentative_address(lay, nt_to_bytes(raw))
        good = (ta[:, 0] >= 0) | (alt[:, 0] >= 0)
        if d is not None:
            d["header_erased"] = header_erased
            d["reading"] = np.where(ta[:, 0] >= 0, 1, np.where(alt[:, 0] >= 0, 2, 0))
        pend_fields = np.where((ta[:, 0] >= 0)[:, None], ta, alt)[good]
        pend_alt = np.where((alt[:, 0] >= 0)[:, None], alt, ta)[good]
        pend_bases = proj[pend][good]
    if pq is not None:
        out_pq = pq[pend][good] if pend.size else np.zeros((0, lay.frame_nt), dtype=np.uint8)
    out = {"acc_fields": fields[acc], "acc_payload": payload[acc], "acc_index": np.flatnonzero(acc),
           "pend_fields": pend_fields, "pend_bases": pend_bases, "pend_alt": pend_alt,
           **({} if pq is None else {"pend_pq": out_pq,
                                     "pend_pqok": np.full(out_pq.shape[0], int(ql is not None), dtype=np.uint8)}),
           "stats": {"reads": int(lengths.size), "fast": int((path == 1).sum()),
                     "sync": int(((path == 2) | (path == 5)).sum()), "speculative": int((path == 5).sum()),
                     "reverse_complement": int((rc_used & acc).sum()), "pending": int(pend_fields.shape[0]), "orphans": orphans,
                     "unaligned": int((~acc).sum()) - int(pend.size), "rc_retry_skipped": rc_skipped}}
    al = _P["al"]
    if al.retry_band:
        # V6 opt-in retry band: reads beyond the band that the wider band aligned (stat absent when the option is off)
        drift = np.abs(lengths - lay.strand_nt)
        out["stats"]["retry_band_reads"] = int(((drift > al.band) & (drift <= al.retry_band)).sum())
    if _P.get("smart") is not None or _P.get("soft") is not None:
        if _P.get("smart") is not None:
            out["stats"]["smart"] = int((path == 3).sum())
        if _P.get("soft") is not None:
            out["stats"]["soft"] = int((path == 4).sum())
        out["indel"] = dict(sstats)
        # raw (oriented) reads of pending records, for consensus realignment in pass 2 (and the deferred stage)
        width = lay.strand_nt + max(_P["al"].band, _P["al"].retry_band)

        def pack(sel):
            cols = np.arange(width, dtype=np.int64)
            raw, _ = _gather_rows(ocodes, offs, lengths, sel, cols)
            rawq = np.zeros((sel.size, width), dtype=np.uint8) if oq is None else _gather_rows(oq, offs, lengths, sel, cols)[0]
            rawlen = np.minimum(lengths[sel], width).astype(np.int64)
            hasq = np.full(sel.size, 0 if oq is None else 1, dtype=np.uint8)
            return raw, rawq, rawlen, hasq

        out["pend_raw"], out["pend_rawq"], out["pend_rawlen"], out["pend_hasq"] = pack(pend[good])
        if _P.get("defer"):
            # aligned reads without a readable header: the eager schedule tried them in pass 1, so the deferred stage
            # keeps them (address unknown) for its last round
            out["orph_raw"], out["orph_rawq"], out["orph_rawlen"], out["orph_hasq"] = pack(pend[~good])
    if d is not None:
        from vnxdna.recovery.stagecount import pass1_counts
        pm = np.zeros(lengths.size, dtype=bool)
        pm[pend] = True
        pg = np.zeros(lengths.size, dtype=bool)
        pg[pend[good]] = True
        d.setdefault("header_erased", np.zeros(0, dtype=bool))
        d.setdefault("reading", np.zeros(0, dtype=np.int64))
        d.update(pend=pm, pend_good=pg, rc_used=rc_used)
        counts, fate = pass1_counts(lay, lengths, batch_codes, d, acc, path, cost, al.band, al.retry_band, 1 << 28)
        # per-read arrays (batch order) for ground-truth harnesses; the decoder itself only adds up ``counts``
        out["diag"] = {"counts": counts, "fate": fate, "rc_used": rc_used.copy(),
                       "pend_index": pend[good].astype(np.int64)}
    if _P.get("unpl"):
        # V7 read clustering (opt-in): every read without a verified frame, as read (not re-oriented); decisions above
        # are unchanged
        from vnxdna.recovery.cluster.store import select_unplaced
        out["unpl"] = select_unplaced(batch_codes, lengths, quals, acc, lay.strand_nt)
    out["cpu_seconds"] = time.process_time() - cpu0          # observability only (never in the report)
    return out
