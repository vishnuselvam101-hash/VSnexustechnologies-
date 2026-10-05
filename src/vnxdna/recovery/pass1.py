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
from vnxdna.sync.template import SyncCosts, TemplateAligner, frame_erasures_to_bytes, strip_markers_exact


_RC = np.array([3, 2, 1, 0, 4], dtype=np.uint8)
# ============================================================================ pass 1 worker
_P: dict = {}


def _p_init(layout: Layout, band: int, costs: SyncCosts, min_q: int, rc: bool, smart_cfg=None, soft_cfg=None,
            defer: bool = False, qw: bool = False, retry_band: int = 0, diag: bool = False) -> None:
    # defer: pass 1 of the deferred schedule — the cheap V4 paths only; smart/soft run later on the pending reads
    # qw: V6 Phase 4 quality-weighted consensus — keep the Phred quality of every projected frame base of pending reads
    # retry_band: V6 opt-in second, wider band for reads beyond ``band`` (0 = off, the V4 behaviour)
    # diag: V7 opt-in stage counters (recovery.stagecount); observability only, never changes a decision
    _P.update(lay=layout, al=TemplateAligner(layout, band, costs, retry_band=retry_band), min_q=min_q, rc=rc, smart=smart_cfg, soft=soft_cfg,
              defer=defer, qw=qw, diag=diag)
    if smart_cfg is not None or soft_cfg is not None:
        from vnxdna.sync.smart.recovery import Geometry
        _P["geom"] = Geometry(layout)


def _try(reads: list[np.ndarray], quals: list | None, with_quality: bool = False, diag: dict | None = None) -> tuple:
    """Fast and sync paths (plus eager smart/soft) for one list of reads. Returns (acc, fields, payload, projected
    bases, cost, path, hard bases, stats), and with ``with_quality`` also the projected qualities (None unless the
    worker keeps them for quality-weighted consensus). ``diag`` (V7 stage counters, observability only): a dict that
    receives per-read arrays of the last attempt (erased frame bytes, inner RS and CRC outcome, marker mismatches,
    RS errata); it never changes a result."""
    lay: Layout = _P["lay"]
    n = len(reads)
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
    path = np.zeros(n, dtype=np.int8)          # 1 fast, 2 sync
    # quality of each projected frame base (V6 Phase 4 weighted consensus; 0 where no read base or no qualities)
    proj_q = np.zeros((n, lay.frame_nt), dtype=np.uint8) if _P.get("qw") else None
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    exact = np.flatnonzero(lengths == lay.strand_nt)
    if exact.size:
        mat = np.stack([reads[i] for i in exact])
        fb, mism = strip_markers_exact(lay, mat)
        er = fb > 3
        if quals is not None and (_P["min_q"] or proj_q is not None):
            qm = np.stack([quals[i] for i in exact])
            tpl, pos = lay.template()
            if _P["min_q"]:
                er |= qm[:, pos] < _P["min_q"]
            if proj_q is not None:
                proj_q[exact] = qm[:, pos]
        frames = nt_to_bytes(np.minimum(fb, 3))
        P = decode_frames(lay, frames, frame_erasures_to_bytes(er))
        ok = P.ok
        idx = exact[ok]
        acc[idx] = True
        path[idx] = 1
        parsed_fields[idx] = np.stack([P.kind, P.tag, P.group, P.symbol], axis=1)[ok]
        payload[idx] = P.payload[ok]
        cost[exact] = mism * _P["al"].costs.marker_mismatch
        if diag is not None:
            diag["erased_bytes"][exact] = frame_erasures_to_bytes(er).sum(axis=1)
            diag["rs_ok"][exact], diag["crc_ok"][exact] = P.rs_ok, P.crc_ok
            diag["marker_mm"][exact], diag["errata"][exact] = mism, P.errata
        hard_bases[exact] = np.minimum(fb, 3)
        pb = fb.copy()
        pb[er] = 4
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
    if rest.size:
        rreads = [reads[i] for i in rest]
        rquals = None if quals is None else [quals[i] for i in rest]
        if smart is None and soft is None and not (proj_q is not None and rquals is not None):
            pr = _P["al"].project(rreads, rquals, _P["min_q"])
        else:
            from vnxdna.sync.smart.path import align_with_path
            pr, rpos = align_with_path(_P["al"], rreads, rquals, _P["min_q"])   # identical projection + the path
        frames = nt_to_bytes(np.minimum(pr.bases, 3))
        er = frame_erasures_to_bytes(pr.erased)
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
        hard_bases[rest] = np.minimum(pr.bases, 3)
        pb = pr.bases.copy()
        pb[pr.erased] = 4
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

    Vectorised over the flat batch: marker positions are gathered from each read's start (forward) and from its end
    (reverse complement) without per-read Python work.
    """
    lay: Layout = _P["lay"]
    n = lengths.size
    tpl, _ = lay.template()
    mpos = np.flatnonzero(tpl >= 0)
    if n == 0 or mpos.size == 0:
        return np.zeros(n, dtype=bool)
    offs = np.concatenate([[0], np.cumsum(lengths)])[:-1]
    want = tpl[mpos].astype(np.uint8)
    inside = mpos[None, :] < lengths[:, None]
    fidx = np.minimum(offs[:, None] + mpos[None, :], max(0, codes.size - 1))
    sf = ((codes[fidx] == want[None, :]) & inside).sum(axis=1)
    ridx = np.clip(offs[:, None] + lengths[:, None] - 1 - mpos[None, :], 0, max(0, codes.size - 1))
    sr = ((_RC[codes[ridx]] == want[None, :]) & inside).sum(axis=1)
    return sr > sf + max(2, mpos.size // 8)


def _process(batch_codes: np.ndarray, lengths: np.ndarray, quals: np.ndarray | None) -> dict:
    cpu0 = time.process_time()
    lay: Layout = _P["lay"]
    offs = np.concatenate([[0], np.cumsum(lengths)])
    reads = [batch_codes[offs[i]:offs[i + 1]] for i in range(lengths.size)]
    ql = None if quals is None else [quals[offs[i]:offs[i + 1]] for i in range(lengths.size)]
    rc_used = np.zeros(lengths.size, dtype=bool)
    d = {} if _P.get("diag") else None           # V7 stage counters (observability only)
    flip = np.zeros(lengths.size, dtype=bool)
    if _P["rc"]:
        # orientation pre-pass: marker agreement at the expected (unshifted) positions, forward vs reverse complement
        flip = _orientation(batch_codes, lengths)
        for i in np.flatnonzero(flip).tolist():
            reads[i] = _RC[reads[i][::-1]]
            if ql is not None:
                ql[i] = ql[i][::-1]
        rc_used |= flip
    acc, fields, payload, proj, cost, path, hard, sstats, pq = _try(reads, ql, with_quality=True, diag=d)
    if d is not None:
        d["flip"] = flip.copy()
        d["rc_tried"] = np.zeros(lengths.size, dtype=bool)
        d["rc_adopted"] = np.zeros(lengths.size, dtype=bool)
    oriented = reads                       # the orientation each read's projection refers to (pending raw reads)
    oriented_q = ql
    if _P["rc"]:
        bad = np.flatnonzero(~acc & (cost > 2 * _P["al"].costs.insertion))
        if bad.size:
            rreads = [_RC[reads[i][::-1]] for i in bad]
            rq = None if ql is None else [ql[i][::-1] for i in bad]
            d2 = {} if d is not None else None
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
            if _P.get("smart") is not None or _P.get("soft") is not None:
                oriented = list(reads)
                oriented_q = None if ql is None else list(ql)
                for j in np.flatnonzero(better).tolist():
                    oriented[bad[j]] = rreads[j]
                    if oriented_q is not None:
                        oriented_q[bad[j]] = rq[j]
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
        for j, i in enumerate(pend.tolist()):
            r = reads[i]
            take = hpos[hpos < r.size]
            raw[j, : take.size] = np.minimum(r[take], 3)
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
           "stats": {"reads": int(lengths.size), "fast": int((path == 1).sum()), "sync": int((path == 2).sum()),
                     "reverse_complement": int((rc_used & acc).sum()), "pending": int(pend_fields.shape[0]), "orphans": orphans,
                     "unaligned": int((~acc).sum()) - int(pend.size)}}
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
            raw = np.zeros((sel.size, width), dtype=np.uint8)
            rawq = np.zeros((sel.size, width), dtype=np.uint8)
            rawlen = np.zeros(sel.size, dtype=np.int64)
            hasq = np.zeros(sel.size, dtype=np.uint8)
            for j, i in enumerate(sel.tolist()):
                r = oriented[i][:width]
                raw[j, : r.size] = r
                rawlen[j] = r.size
                if oriented_q is not None and oriented_q[i] is not None:
                    rawq[j, : r.size] = oriented_q[i][:width]
                    hasq[j] = 1
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
    out["cpu_seconds"] = time.process_time() - cpu0          # observability only (never in the report)
    return out
