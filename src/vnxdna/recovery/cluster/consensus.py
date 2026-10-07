"""Per-cluster consensus with indel placement below segment level, then decode-and-peel (V7_ARCHITECTURE §5.3).

Every read of a cluster is aligned to a per-cluster *template* C (length strand_nt): marker bases (mismatch cost
``SyncCosts.marker_mismatch``), consensus bases at frame positions already decided (mismatch cost ``c_sub``) and
wildcards (cost 0) elsewhere; insertion and deletion cost ``c_indel``. A banded forward pass F and backward pass G give,
for each template position i, whether an optimal path matches i to read base j (F[i][j] + c(C_i, r_j) + G[i+1][j+1] ≤
opt + δ) or deletes it (F[i][j] + c_del + G[i+1][j] ≤ opt + δ). The read's call at i is **certain** iff no optimal path
deletes i and every optimal match at i reads the same base value; otherwise the call is erased for this read only. An
indel inside a run of equal bases stays certain (every placement gives the same bases); elsewhere only the positions
whose base depends on where the indel is placed are erased, not the whole marker segment.

Per frame position the consensus base needs a share ≥ ``vote_share`` of the certain calls and at least ``min_votes``
votes (one read never decides a base). Round 0 aligns to the marker-only template (layouts with markers; it also fixes
the cluster orientation by the summed optimal cost of both orientations) or starts from the cluster medoid (markerless
layouts: both orientations are tried, the forward one first). Rounds repeat until the template is stable or
``rounds`` is reached.

Decode: consensus bytes, undecided bases → byte erasures, the unchanged ``decode_frames`` (inner RS + CRC-32, I-5),
then a bounded GMD ladder that erases the lowest-margin bytes ``gmd_step`` at a time (at most ``max_trials`` RS + CRC
trials per cluster and peel, counted). The address is read from the verified frame only. Peel: the exact transmitted
strand of the verified frame is re-created, the members within ``peel_theta · strand_nt`` of it are removed, and a
residual of ≥ 2 reads is decoded again (at most ``max_peels`` per cluster).

:func:`fb_calls` runs the native forward-backward kernel (``vnxdna.native.cluster``) when it is available and selected
(``VNXDNA_CLUSTER_BACKEND``), on the same chunks of ``FB_CHUNK`` reads with the same shared band, else
:func:`fb_calls_reference`; both return identical calls and costs.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from vnxdna.codec.codecs import InnerRS
from vnxdna.dnaenc.frame4 import decode_frames, plain_rows
from vnxdna.dnaenc.layout import Layout
from vnxdna.dnaenc.mapping import bytes_to_nt, nt_to_bytes
from vnxdna.dnaenc.markers import insert_markers
from vnxdna.dnaenc.scrambler import VARIANTS, keystreams
from vnxdna.native import cluster as _nc
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster import polish as _pl
from vnxdna.recovery.cluster.editdist import banded_distance, revcomp
from vnxdna.sync.template import frame_erasures_to_bytes

INF = np.int32(1 << 28)
FB_CHUNK = 512


# ------------------------------------------------------------------------------------------------ forward-backward
def fb_calls(tpl: np.ndarray, mc: np.ndarray, reads: list, band: np.ndarray, c_indel: int, slack: int = 0,
             chunk: int = FB_CHUNK) -> tuple[np.ndarray, np.ndarray]:
    """Certain calls of reads against per-read templates.

    ``tpl``: (n, T) template codes (−1 = wildcard); ``mc``: (n, T) mismatch cost per position (ignored at wildcards);
    ``band``: (n,) maximal |j − i| per read. Returns ((n, T) uint8 calls, 4 = not certain; (n,) optimal cost, INF when
    the read does not fit its band). Native kernel or :func:`fb_calls_reference` (identical results)."""
    if _nc.resolve_backend() == "native":
        out = _fb_calls_native(tpl, mc, reads, band, c_indel, slack, chunk)
        if out is not None:
            return out
    return fb_calls_reference(tpl, mc, reads, band, c_indel, slack, chunk)


def _fb_calls_native(tpl, mc, reads, band, c_indel, slack, chunk):
    """The reference's chunks (shared band = the chunk's largest band) in the native kernel; None outside its domain."""
    n, T = tpl.shape
    band = np.asarray(band, dtype=np.int64)
    if n == 0 or band.size != n or (band.size and int(band.min()) < 0):
        return None
    tpl16 = np.ascontiguousarray(np.asarray(tpl).astype(np.int16))
    mc32 = np.ascontiguousarray(np.asarray(mc).astype(np.int32))
    buf, off, lens = _nc.pack(list(reads[:n]))
    calls = np.full((n, T), 4, dtype=np.uint8)
    opt = np.full(n, int(INF), dtype=np.int64)
    for c0 in range(0, n, chunk):
        c1 = min(n, c0 + chunk)
        B = int(band[c0:c1].max(initial=0))
        if not _nc.fb_in_domain(T, B, mc32[c0:c1], c_indel, slack, lens[c0:c1]):
            return None
    for c0 in range(0, n, chunk):
        c1 = min(n, c0 + chunk)
        B = int(band[c0:c1].max(initial=0))
        calls[c0:c1], opt[c0:c1] = _nc.fb(tpl16[c0:c1], mc32[c0:c1], buf, off[c0:c1], lens[c0:c1], band[c0:c1], B,
                                          c_indel, slack)
    return calls, opt


def fb_calls_reference(tpl: np.ndarray, mc: np.ndarray, reads: list, band: np.ndarray, c_indel: int, slack: int = 0,
                       chunk: int = FB_CHUNK) -> tuple[np.ndarray, np.ndarray]:
    """NumPy reference of :func:`fb_calls`."""
    n, T = tpl.shape
    calls = np.full((n, T), 4, dtype=np.uint8)
    opt = np.full(n, int(INF), dtype=np.int64)
    for c0 in range(0, n, chunk):
        sl = slice(c0, min(n, c0 + chunk))
        calls[sl], opt[sl] = _fb(tpl[sl], mc[sl], reads[sl], np.asarray(band[sl], dtype=np.int64), c_indel, slack)
    return calls, opt


def _fb(tpl, mc, reads, band, c_indel, slack):
    n, T = tpl.shape
    lens = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    B = int(band.max(initial=0))
    W = 2 * B + 1
    width = T + B + 2
    # read bases with B pad columns on the left: the bases at j = i + d, d = −B … B, are the contiguous slice
    # Rp[:, i : i + W] (5 = no base; it never equals a template code)
    Rp = np.full((n, width + 2 * B + 1), 5, dtype=np.int16)
    for k, r in enumerate(reads):
        Rp[k, B: B + min(r.size, width)] = r[:width]
    tpl = tpl.astype(np.int16)
    mc = mc.astype(np.int32)
    rows = np.arange(n)
    dvec = np.arange(-B, B + 1, dtype=np.int64)[:, None]
    bandok = np.abs(dvec) <= band[None, :]
    ramp = (np.arange(W, dtype=np.int32) * c_indel)[:, None]
    F = np.empty((T + 1, W, n), dtype=np.int32)
    F[0] = np.where((dvec >= 0) & (dvec <= lens[None, :]) & bandok, dvec * c_indel, INF)

    def sub_at(t: int, jd: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        okd = (jd >= 0) & (jd < lens[None, :])
        j0 = int(jd[0, 0]) + B
        rb = Rp[:, j0: j0 + W].T
        tc = tpl[:, t][None, :]
        cost = np.where((tc < 0) | (rb == tc), 0, mc[:, t][None, :]).astype(np.int32)
        return okd, rb, cost

    for i in range(1, T + 1):
        okd, _, sub = sub_at(i - 1, i - 1 + dvec)
        prev = F[i - 1]
        diag = np.where(okd, prev + sub, INF)
        dele = np.full_like(prev, INF)
        dele[:-1] = prev[1:] + c_indel
        new = np.minimum(diag, dele)
        new = np.minimum(new, np.minimum.accumulate(new - ramp, axis=0) + ramp)
        j = i + dvec
        F[i] = np.where((j >= 0) & (j <= lens[None, :]) & bandok, new, INF)
    fit = np.abs(lens - T) <= band
    w_end = np.clip(lens - T + B, 0, W - 1)
    opt = np.where(fit, F[T][w_end, rows], int(INF)).astype(np.int64)
    opt = np.minimum(opt, int(INF))
    thr = (opt + slack)[None, :]
    calls = np.full((n, T), 4, dtype=np.uint8)
    jT = T + dvec
    G = np.where((jT >= 0) & (jT <= lens[None, :]) & bandok, (lens[None, :] - jT) * c_indel, INF).astype(np.int32)
    live = opt < INF
    for i in range(T - 1, -1, -1):
        okd, rb, sub = sub_at(i, i + dvec)
        Fi = F[i]
        match = np.where(okd, Fi + sub + G, INF)
        delc = np.full_like(Fi, INF)
        delc[1:] = Fi[1:] + c_indel + G[:-1]
        matched = match <= thr
        deleted = (delc <= thr).any(axis=0)
        bmin = np.where(matched, rb, 99).min(axis=0)
        bmax = np.where(matched, rb, -1).max(axis=0)
        cert = live & ~deleted & matched.any(axis=0) & (bmin == bmax) & (bmin < 4)
        calls[:, i] = np.where(cert, bmin, 4)
        diag = np.where(okd, sub + G, INF)
        dele = np.full_like(G, INF)
        dele[1:] = G[:-1] + c_indel
        new = np.minimum(diag, dele)
        new = np.minimum(new, np.minimum.accumulate((new + ramp)[::-1], axis=0)[::-1] - ramp)
        j = i + dvec
        G = np.where((j >= 0) & (j <= lens[None, :]) & bandok, new, INF).astype(np.int32)
    return calls, opt


# ------------------------------------------------------------------------------------------------ vote
def vote(calls: np.ndarray, share: float, min_votes: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(m, L) certain calls (4 = none) → (best base, decided, margin = best − second count, certain-call total)."""
    counts = np.stack([(calls == b).sum(axis=0) for b in range(4)], axis=1)
    total = counts.sum(axis=1)
    best = counts.argmax(axis=1).astype(np.uint8)
    top = counts.max(axis=1)
    second = np.sort(counts, axis=1)[:, -2]
    decided = (top >= min_votes) & (top >= share * np.maximum(total, 1)) & (top > second)
    return best, decided, top - second, total


# ------------------------------------------------------------------------------------------------ strand re-creation
def transmitted_strand(lay: Layout, kind: int, tag: int, group: int, symbol: int, payload: np.ndarray,
                       received: np.ndarray) -> np.ndarray:
    """The strand the verified frame was transmitted as: the variant byte is the one whose full frame (scrambled
    header/payload/CRC + inner parity) is nearest to the received consensus bytes."""
    plain = plain_rows(tag, kind, np.array([group]), np.array([symbol]), np.asarray(payload, dtype=np.uint8)[None, :])
    span = plain.shape[1]
    ks = keystreams(span)
    msgs = np.empty((VARIANTS, 1 + span), dtype=np.uint8)
    msgs[:, 0] = np.arange(VARIANTS, dtype=np.uint8)
    msgs[:, 1:] = plain ^ ks
    frames = np.concatenate([msgs, InnerRS(lay.inner_parity).parity(msgs)], axis=1)
    v = int((frames != received[None, :]).sum(axis=1).argmin())
    return insert_markers(lay, bytes_to_nt(frames[v:v + 1]))[0]


# ------------------------------------------------------------------------------------------------ the stage
class _Job:
    __slots__ = ("cid", "members", "peel", "active")

    def __init__(self, cid: int, members: list):
        self.cid = cid
        self.members = members          # reads oriented relative to the cluster reference
        self.peel = 0
        self.active = True


def cluster_consensus(lay: Layout, clusters: list, cfg: ClusterConfig, marker_mismatch: int = 4,
                      counts: Counter | None = None, trace: list | None = None) -> list[dict]:
    """``clusters``: list of (cluster ID, [reads oriented relative to the cluster's reference read]), in cluster-ID
    order. Returns the verified frames (dicts: kind, tag, group, symbol, payload, cluster, peel, reads, gmd_step) in
    cluster-ID then peel order. Clusters are independent; the result does not depend on how they are batched.

    ``trace`` (observability only, default None): when a list, one dict per consensus candidate is appended (cluster,
    peel, orientation, consensus bases, decided mask, margins, byte erasures, decode result); nothing else changes."""
    counts = Counter() if counts is None else counts
    T = lay.strand_nt
    tpl0, fpos = lay.template()
    tpl0 = tpl0.astype(np.int16)
    has_markers = bool((tpl0 >= 0).any())
    jobs = [_Job(cid, list(rs)) for cid, rs in clusters]
    out: list[dict] = []
    while True:
        active = [j for j in jobs if j.active]
        if not active:
            break
        units = []          # (job, orientation, reads, band)
        for job in active:
            usable, beyond, dups = _usable(job.members, T, cfg.consensus_band)
            counts["cluster_consensus_attempted"] += 1
            counts["cluster_reads_beyond_band"] += beyond
            counts["cluster_duplicate_reads"] += dups
            if len(usable) < 2:
                counts["cluster_insufficient_reads"] += 1
                job.active = False
                continue
            band = min(cfg.consensus_band, max(abs(r.size - T) for r in usable) + 4)
            units.append((job, usable, band))
        if not units:
            break
        results = _run_units(lay, units, tpl0, fpos, has_markers, cfg, marker_mismatch, counts, trace)
        # peel distances of every verified unit's members to its re-created strand, in one batch
        xs, ys = [], []
        for res in results:
            if res is not None:
                xs += res[1]
                ys += [res[2]] * len(res[1])
        dist = banded_distance(xs, ys, cfg.band_slack)
        at = 0
        for (job, usable, band), res in zip(units, results):
            if res is None:
                job.active = False
                continue
            frame, oriented, strand = res
            frame.update(cluster=int(job.cid), peel=job.peel, reads=len(usable))
            out.append(frame)
            # peel: remove the members that the verified strand explains
            d = dist[at: at + len(oriented)]
            at += len(oriented)
            near = d <= cfg.peel_theta * T
            if not near.any():
                near[:] = True
            counts["cluster_peeled_reads"] += int(near.sum())
            job.members = [r for r, x in zip(oriented, near.tolist()) if not x]
            job.peel += 1
            if len(job.members) >= 2 and job.peel < cfg.max_peels:
                counts["cluster_peels"] += 1
            else:
                job.active = False
    return out


def _usable(members: list, T: int, cap: int) -> tuple[list, int, int]:
    seen: set = set()
    out = []
    beyond = dups = 0
    for r in members:
        if abs(r.size - T) > cap:
            beyond += 1
            continue
        key = r.tobytes()
        if key in seen:
            dups += 1           # exact duplicates vote once
            continue
        seen.add(key)
        out.append(r)
    return out, beyond, dups


def _run_units(lay, units, tpl0, fpos, has_markers, cfg, marker_mismatch, counts, trace=None) -> list:
    """Seed, rounds and decode for every unit (one per active cluster); returns per unit (frame, oriented reads, strand)
    or None."""
    T = lay.strand_nt
    base_mc = np.where(tpl0 >= 0, marker_mismatch, 0).astype(np.int32)
    # candidate orientations per unit: markers decide by cost; markerless try both (forward first)
    cands = []                                   # (unit index, reads oriented, band, seed template or None)
    flips: list = []                             # orientation of each candidate (trace only)
    if has_markers:
        reads_all, bands, owner = [], [], []
        for u, (_, usable, band) in enumerate(units):
            for flip in (0, 1):
                for r in usable:
                    reads_all.append(revcomp(r) if flip else r)
                    bands.append(band)
                    owner.append((u, flip))
        n = len(reads_all)
        calls, opt = fb_calls(np.repeat(tpl0[None, :], n, axis=0), np.repeat(base_mc[None, :], n, axis=0), reads_all,
                              np.asarray(bands), cfg.c_indel, cfg.slack)
        pen = 2 * cfg.c_indel * T
        cost = np.minimum(opt, pen)
        own = np.asarray(owner, dtype=np.int64).reshape(-1, 2)
        for u, (_, usable, band) in enumerate(units):
            c = [int(cost[(own[:, 0] == u) & (own[:, 1] == f)].sum()) for f in (0, 1)]
            flip = 1 if c[1] < c[0] else 0
            counts["cluster_orientation_flipped"] += flip
            sel = np.flatnonzero((own[:, 0] == u) & (own[:, 1] == flip))
            best, dec, _, _ = vote(calls[sel][:, fpos], cfg.vote_share, cfg.min_votes)
            tpl = tpl0.copy()
            tpl[fpos[dec]] = best[dec]
            cands.append((u, [reads_all[i] for i in sel.tolist()], band, tpl))
            flips.append(flip)
    else:
        for u, (_, usable, band) in enumerate(units):
            for flip in (0, 1):
                rs = [revcomp(r) for r in usable] if flip else list(usable)
                exact = [r for r in rs if r.size == T]
                if not exact:
                    counts["cluster_no_seed"] += 1
                    continue
                pick = exact[: cfg.medoid_members]
                d = banded_distance([a for a in pick for _ in pick], [b for _ in pick for b in pick], cfg.band_slack)
                med = pick[int(d.reshape(len(pick), len(pick)).sum(axis=1).argmin())]
                cands.append((u, rs, band, np.minimum(med, 3).astype(np.int16)))
                flips.append(flip)
    # rounds: align to the consensus template, vote, until stable
    state = [{"tpl": tpl, "dec": None, "best": None, "margin": None, "stable": False} for (_, _, _, tpl) in cands]
    for _ in range(max(1, cfg.rounds)):
        live = [k for k, s in enumerate(state) if not s["stable"]]
        if not live:
            break
        reads_all, tpls, mcs, bands, owner = [], [], [], [], []
        for k in live:
            _, rs, band, _ = cands[k]
            tpl = state[k]["tpl"]
            mc = np.where(tpl0 >= 0, marker_mismatch, np.where(tpl >= 0, cfg.c_sub, 0)).astype(np.int32)
            for r in rs:
                reads_all.append(r)
                tpls.append(tpl)
                mcs.append(mc)
                bands.append(band)
                owner.append(k)
        counts["cluster_rounds"] += len(live)
        calls, _ = fb_calls(np.stack(tpls), np.stack(mcs), reads_all, np.asarray(bands), cfg.c_indel, cfg.slack)
        own = np.asarray(owner)
        for k in live:
            sel = own == k
            best, dec, margin, total = vote(calls[sel][:, fpos], cfg.vote_share, cfg.min_votes)
            tpl = tpl0.copy()
            tpl[fpos[dec]] = best[dec]
            s = state[k]
            s["stable"] = s["dec"] is not None and np.array_equal(tpl, s["tpl"])
            s.update(tpl=tpl, dec=dec, best=best, margin=margin, total=total)
    if cfg.consensus_template == "full" and has_markers and state:
        _polish(cands, state, tpl0, fpos, cfg, marker_mismatch, counts)
    # decode every candidate; the GMD ladder for those that fail
    results: list = [None] * len(units)
    done_unit: set = set()
    frames = np.stack([nt_to_bytes(np.minimum(s["best"], 3)[None, :])[0] for s in state]) if state else None
    if not state:
        return results
    er = frame_erasures_to_bytes(~np.stack([s["dec"] for s in state]))
    for k, s in enumerate(state):
        ex = int(er[k].sum())
        counts["cluster_erased_bytes"] += ex
        counts["cluster_ambiguous_columns"] += int(((s["total"] > 0) & ~s["dec"]).sum())
        counts["cluster_empty_columns"] += int((s["total"] == 0).sum())
        if ex > lay.inner_parity:
            counts["cluster_erasures_exceed_parity"] += 1
    P = decode_frames(lay, frames, er)
    trials = np.full(len(state), 2, dtype=np.int64)       # the first call may retry errors-only
    ok = P.ok.copy()
    fields = np.stack([P.kind, P.tag, P.group, P.symbol], axis=1)
    payload = P.payload.copy()
    gmd = np.zeros(len(state), dtype=np.int64)
    bmargin = np.stack([np.where(s["dec"], s["margin"], -1).reshape(-1, 4).min(axis=1) for s in state])
    for step in range(1, cfg.gmd_steps + 1):
        todo = np.flatnonzero(~ok & (trials < cfg.max_trials))
        if not todo.size:
            break
        er2 = er[todo].copy()
        for t, k in enumerate(todo.tolist()):
            cand = np.flatnonzero(~er[k])
            order = cand[np.lexsort((cand, bmargin[k][cand]))]
            er2[t, order[: step * cfg.gmd_step]] = True
        fit = er2.sum(axis=1) <= lay.inner_parity
        todo, er2 = todo[fit], er2[fit]
        if not todo.size:
            break
        P2 = decode_frames(lay, frames[todo], er2, errors_only_retry=False)
        trials[todo] += 1
        hit = P2.ok
        ok[todo[hit]] = True
        fields[todo[hit]] = np.stack([P2.kind, P2.tag, P2.group, P2.symbol], axis=1)[hit]
        payload[todo[hit]] = P2.payload[hit]
        gmd[todo[hit]] = step
    if cfg.fill == "template" and cfg.consensus_template == "full" and has_markers:
        _fill_ladder(lay, state, fpos, cfg, frames, er, ok, fields, payload, gmd, trials, counts)
    counts["cluster_rs_crc_trials"] += int(trials.sum())
    if trace is not None:
        for k, (u, rs, band, _) in enumerate(cands):
            s = state[k]
            trace.append({"cluster": int(units[u][0].cid), "peel": int(units[u][0].peel), "flip": int(flips[k]),
                          "reads": len(rs), "band": int(band), "best": s["best"].copy(), "decided": s["dec"].copy(),
                          "margin": s["margin"].copy(), "total": s["total"].copy(), "erased": er[k].copy(),
                          "bytes": frames[k].copy(), "ok": bool(ok[k]), "gmd": int(gmd[k]), "trials": int(trials[k]),
                          "fields": tuple(int(x) for x in fields[k]) if ok[k] else None})
    for k, (u, rs, _, _) in enumerate(cands):
        if u in done_unit:
            continue
        if not ok[k]:
            counts["cluster_decode_failed"] += 1
            continue
        done_unit.add(u)
        kind, tag, group, symbol = (int(x) for x in fields[k])
        counts["cluster_frames_verified"] += 1
        counts["cluster_frames_verified_gmd"] += int(gmd[k] > 0)
        strand = transmitted_strand(lay, kind, tag, group, symbol, payload[k], frames[k])
        results[u] = ({"kind": kind, "tag": tag, "group": group, "symbol": symbol,
                       "payload": np.array(payload[k], dtype=np.uint8), "gmd_step": int(gmd[k])}, rs, strand)
    return results



def _fill_ladder(lay, state, fpos, cfg, frames, er, ok, fields, payload, gmd, trials, counts) -> None:
    """V9 candidate E (``fill="template"``): for every candidate still failing, decode the polished template's base at
    each undecided position (the decided bases unchanged) and erase the ``f`` lowest-confidence bytes, f stepping from 0
    to the inner parity in ``fill_trials`` trials (each an inner RS + CRC-32 trial, counted). Confidence of a base: its
    vote margin when decided; when undecided, below every decided base, ordered by the margin of the template base
    (positive if it is the plurality base, negative otherwise). A byte's confidence is its weakest base. Successful
    frames are marked ``gmd_step`` = -1 (fill) in the trace. Only the frame's own RS + CRC-32 accept a result."""
    todo = np.flatnonzero(~ok)
    if not todo.size:
        return
    P = lay.inner_parity
    levels = sorted({int(round(P * j / max(1, cfg.fill_trials - 1))) for j in range(cfg.fill_trials)})
    fr, ers, keys = [], [], []
    for k in todo.tolist():
        s = state[k]
        tb = np.minimum(s["tpl"][fpos], 3).astype(np.uint8)
        base = np.where(s["dec"], s["best"], tb)
        conf = np.where(s["dec"], s["margin"].astype(np.int64),
                        -(1 << 20) + np.where(tb == s["best"], s["margin"], -s["margin"]).astype(np.int64))
        bconf = conf.reshape(-1, 4).min(axis=1)
        order = np.lexsort((np.arange(bconf.size), bconf))
        fbytes = nt_to_bytes(base[None, :])[0]
        for f in levels:
            e = np.zeros(bconf.size, dtype=bool)
            e[order[:f]] = True
            fr.append(fbytes)
            ers.append(e)
            keys.append(k)
    Pd = decode_frames(lay, np.stack(fr), np.stack(ers), errors_only_retry=False)
    counts["cluster_fill_trials"] += len(keys)
    seen: set = set()
    for t, k in enumerate(keys):
        trials[k] += 1
        if k in seen or not Pd.ok[t]:
            continue
        seen.add(k)
        ok[k] = True
        fields[k] = (Pd.kind[t], Pd.tag[t], Pd.group[t], Pd.symbol[t])
        payload[k] = Pd.payload[t]
        frames[k] = fr[t]
        gmd[k] = -1
        counts["cluster_frames_filled"] += 1


def _polish(cands, state, tpl0, fpos, cfg, marker_mismatch, counts) -> None:
    """``consensus_template="full"`` (EXPERIMENTAL): polish a full template per candidate (:mod:`.polish`), then
    replace the candidate's vote by the vote of its reads' certain calls against the polished template."""
    T = tpl0.size
    seg = _pl.segments(tpl0)
    frame = tpl0 < 0
    gap_seg = np.where(frame, seg, np.concatenate([[-1], seg[:-1]]))      # segment of the gap before position i
    mc = np.where(tpl0 >= 0, marker_mismatch, cfg.c_sub).astype(np.int32)
    pen = 2 * cfg.c_indel * T
    tpls = []
    for s in state:
        t = tpl0.copy()
        t[fpos] = np.minimum(s["best"], 3)
        tpls.append(t)
    undecided = [np.unique(seg[fpos[~s["dec"]]]) for s in state]
    reads_all = [r for (_, rs, _, _) in cands for r in rs]
    owner = np.repeat(np.arange(len(cands)), [len(rs) for (_, rs, _, _) in cands])
    bands = np.repeat(np.asarray([b for (_, _, b, _) in cands]), [len(rs) for (_, rs, _, _) in cands])
    for _ in range(max(1, cfg.rounds)):
        counts["cluster_polish_rounds"] += 1
        n = len(reads_all)
        tt = np.stack([tpls[k] for k in owner.tolist()])
        mm = np.repeat(mc[None, :], n, axis=0)
        dsub = np.zeros((len(cands), T, 4), dtype=np.int64)
        ddel = np.zeros((len(cands), T), dtype=np.int64)
        dins = np.zeros((len(cands), T, 4), dtype=np.int64)
        for a in range(0, n, FB_CHUNK):
            sl = slice(a, a + FB_CHUNK)
            opt, sub, dele, ins = _pl.edit_costs(tt[sl], mm[sl], reads_all[sl], bands[sl], cfg.c_indel, cfg.c_sub)
            live = opt < INF
            own = owner[sl][live]
            np.add.at(dsub, own, np.minimum(sub[live], pen) - opt[live][:, None, None])
            np.add.at(ddel, own, np.minimum(dele[live], pen) - opt[live][:, None])
            np.add.at(dins, own, np.minimum(ins[live], pen) - opt[live][:, None, None])
        # candidate moves per (candidate, hot segment): the improving substitutions together, and shift pairs
        trial_tpl, trial_key = [], []
        for k, t in enumerate(tpls):
            ds = dsub[k].copy()
            ds[np.arange(T), np.minimum(t, 3)] = 0
            ds[~frame] = 0
            de = np.where(frame, ddel[k], pen)
            di = np.where(gap_seg >= 0, dins[k].min(axis=1), pen)
            dix = dins[k].argmin(axis=1)
            improving = np.flatnonzero(frame & (ds.min(axis=1) < 0))
            hot = set(undecided[k].tolist()) | set(seg[improving].tolist()) | set(seg[frame & (de < 0)].tolist()) \
                | set(gap_seg[(gap_seg >= 0) & (di < 0)].tolist())
            for g in sorted(hot):
                sp = improving[seg[improving] == g]
                if sp.size:
                    u = t.copy()
                    u[sp] = ds[sp].argmin(axis=1)
                    trial_tpl.append(u)
                    trial_key.append((k, g))
                ps = np.flatnonzero(seg == g)
                qs = np.flatnonzero(gap_seg == g)
                dels = ps[np.argsort(de[ps], kind="stable")[:_pl.PAIRS_PER_SIDE]]
                inss = qs[np.argsort(di[qs], kind="stable")[:_pl.PAIRS_PER_SIDE]]
                for p in dels.tolist():
                    for q in inss.tolist():
                        if q in (p, p + 1):
                            continue
                        trial_tpl.append(_pl.shift(t, p, q, int(dix[q])))
                        trial_key.append((k, g))
        if not trial_tpl:
            break
        # exact summed cost of every trial template (all reads of its candidate)
        rows, towner = [], []
        for x, (k, _) in enumerate(trial_key):
            idx = np.flatnonzero(owner == k)
            rows.append(idx)
            towner.append(np.full(idx.size, x))
        rows_a, tow = np.concatenate(rows), np.concatenate(towner)
        _, opt = fb_calls(np.stack([trial_tpl[x] for x in tow.tolist()]), np.repeat(mc[None, :], rows_a.size, axis=0),
                          [reads_all[i] for i in rows_a.tolist()], bands[rows_a], cfg.c_indel, 0)
        cost = np.zeros(len(trial_tpl), dtype=np.int64)
        np.add.at(cost, tow, np.minimum(opt, pen))
        basec = np.zeros(len(cands), dtype=np.int64)
        best_move: dict = {}
        for x, key in enumerate(trial_key):
            if key not in best_move or cost[x] < cost[best_move[key]]:
                best_move[key] = x
        # the base cost with the same clipping as the trials
        _, bopt = fb_calls(tt, mm, reads_all, bands, cfg.c_indel, 0)
        np.add.at(basec, owner, np.minimum(bopt, pen))
        moved = 0
        new = [t.copy() for t in tpls]
        for (k, g), x in sorted(best_move.items()):
            if cost[x] < basec[k]:
                m = seg == g
                new[k][m] = trial_tpl[x][m]
                moved += 1
        counts["cluster_polish_moves"] += moved
        tpls = new
        if not moved:
            break
    # final: certain calls against the polished template, the unchanged vote
    tt = np.stack([tpls[k] for k in owner.tolist()])
    calls, _ = fb_calls(tt, np.repeat(mc[None, :], len(reads_all), axis=0), reads_all, bands, cfg.c_indel, cfg.slack)
    for k, s in enumerate(state):
        best, dec, margin, total = vote(calls[owner == k][:, fpos], cfg.vote_share, cfg.min_votes)
        s.update(tpl=tpls[k], dec=dec, best=best, margin=margin, total=total)
