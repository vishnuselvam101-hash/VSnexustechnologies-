"""Duplicate resolution and consensus of pending reads per address (hard vote, smart realignment, soft
evidence), and address snapping. Formerly in ``vnxdna.v4.decoder`` (V6 Phase 2, M4)."""
from __future__ import annotations

from collections import Counter

import numpy as np

from vnxdna.dnaenc.frame4 import decode_frames
from vnxdna.dnaenc.layout import Layout
from vnxdna.dnaenc.mapping import nt_to_bytes
from vnxdna.recovery.options import DecodeOptions
from vnxdna.sync.template import TemplateAligner, frame_erasures_to_bytes


# ============================================================================ consensus
def consensus_soft(bases: np.ndarray) -> np.ndarray:
    """(m, L) codes with 4 = erased → (L, 4) posterior base probabilities (add-½ smoothing).

    This is the soft-information interface: a future soft-decision inner decoder can
    consume these probabilities directly; the current decoder thresholds them into
    hard bases plus erasures.
    """
    onehot = np.zeros((bases.shape[1], 4), dtype=np.float64)
    for b in range(4):
        onehot[:, b] = (bases == b).sum(axis=0)
    return (onehot + 0.5) / (onehot.sum(axis=1, keepdims=True) + 2.0)


def consensus_hard(bases: np.ndarray, threshold: float) -> tuple[np.ndarray, np.ndarray]:
    votes = np.zeros((bases.shape[1], 4), dtype=np.int64)
    for b in range(4):
        votes[:, b] = (bases == b).sum(axis=0)
    total = votes.sum(axis=1)
    best = votes.argmax(axis=1).astype(np.uint8)
    share = np.where(total > 0, votes.max(axis=1) / np.maximum(total, 1), 0.0)
    erased = (total == 0) | (share < threshold)
    return best, erased


def consensus_quality_weighted(bases: np.ndarray, quals: np.ndarray, threshold: float) -> tuple[np.ndarray, np.ndarray]:
    """Quality-weighted vote (V6 Phase 4, opt-in): (m, L) codes (4 = erased, abstains) with their (m, L) Phred
    qualities → (best base, erased) per position.

    Each read base contributes log P(observed | true = x) under the Phred reading of its quality, ε = 10^(−Q/10)
    clipped to [1e-6, 0.75] (``soft.symbols.phred_error``): log(1 − ε) for x = observed, log(ε/3) otherwise. Reads are
    treated as independent. The summed log-likelihoods are normalised under a uniform prior; the winner is the argmax
    and the position is erased when the winner's normalised score is below ``threshold`` or no read has a base there.

    The score is a Phred-interpreted posterior, NOT a calibrated probability: it is only as good as the qualities, which
    must be measured per platform. Ties (equal score) are erased; with equal qualities a clear majority wins as in
    ``consensus_hard``. Deterministic (no randomness, fixed reduction order).
    """
    bases = np.asarray(bases)
    quals = np.asarray(quals)
    if bases.shape != quals.shape or bases.ndim != 2:
        raise ValueError("bases and qualities must be (reads, positions) arrays of the same shape")
    eps = np.clip(10.0 ** (-quals.astype(np.float64) / 10.0), 1e-6, 0.75)
    known = bases < 4
    l_mis = np.where(known, np.log(eps / 3.0), 0.0)                 # (m, L)
    gain = np.where(known, np.log1p(-eps) - np.log(eps / 3.0), 0.0)
    score = np.repeat(l_mis.sum(axis=0)[:, None], 4, axis=1)        # (L, 4)
    for b in range(4):
        score[:, b] += (gain * (bases == b)).sum(axis=0)
    top = score.max(axis=1, keepdims=True)
    post = np.exp(score - top)
    post /= post.sum(axis=1, keepdims=True)
    best = score.argmax(axis=1).astype(np.uint8)
    p_best = post.max(axis=1)
    tie = (np.isclose(score, top, rtol=0.0, atol=1e-9).sum(axis=1) > 1)
    erased = ~known.any(axis=0) | (p_best < threshold) | tie
    return best, erased


def resolve_duplicates(acc: np.ndarray) -> tuple[dict, int]:
    """(kind, tag, group, symbol) → payload by strict majority of identical verified copies; ties are dropped."""
    out: dict = {}
    conflicts = 0
    if not acc.size:
        return out, 0
    order = np.lexsort((acc["symbol"], acc["group"], acc["tag"], acc["kind"]))
    acc = acc[order]
    keys = np.stack([acc["kind"].astype(np.int64), acc["tag"].astype(np.int64), acc["group"].astype(np.int64),
                     acc["symbol"].astype(np.int64)], axis=1)
    change = np.flatnonzero((np.diff(keys, axis=0) != 0).any(axis=1)) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(acc)]])
    pay = acc["payload"]
    first = np.repeat(starts, ends - starts)
    same = (pay == pay[first]).all(axis=1)
    uniform = np.logical_and.reduceat(same, starts)
    klist = keys[starts].tolist()
    for gi in np.flatnonzero(uniform).tolist():
        out[tuple(klist[gi])] = pay[starts[gi]]
    for gi in np.flatnonzero(~uniform).tolist():
        s, e = int(starts[gi]), int(ends[gi])
        key = tuple(klist[gi])
        c = Counter(acc["payload"][i].tobytes() for i in range(s, e))
        (top, n1), *rest = c.most_common(2) + [(None, 0)]
        n2 = rest[0][1] if rest else 0
        if len(c) > 1:
            conflicts += 1
        if n1 > n2:
            out[key] = np.frombuffer(top, dtype=np.uint8)
    return out, conflicts


def _addr_bytes(key: tuple) -> bytes:
    kind, tag, group, symbol = key
    return bytes([(4 << 4) | (kind & 15)]) + int(tag).to_bytes(2, "big") + int(group).to_bytes(4, "big") + int(symbol).to_bytes(2, "big")


def snap_addresses(keys: np.ndarray, missing: set, alt: np.ndarray | None = None) -> tuple[np.ndarray, int]:
    """Map tentative addresses to the unique missing address within one header byte.

    Each read can carry two readings of its header (``keys`` from the indel-corrected projection, ``alt`` from the raw
    prefix). A candidate address matches a byte position if either reading has that byte; a read is snapped when
    exactly one missing address differs from it in at most one position. Reads whose address is already missing are
    unchanged; reads matching nothing (or several) are left as they are and later ignored. A wrong snap can only
    cost a consensus vote: every recovered frame is still RS- and CRC-verified, and the address it decodes to must
    equal the group's address.

    Pass 2 snaps per spill bucket (group mod B), so a snap across groups is limited to the groups of the same bucket:
    for pools above ~200,000 reads (B > 1) the consensus can depend on B (V6 Phase 2.8 finding; restricting snaps to the
    read's own group would remove that but loses recoveries at B = 1, so it is not done).
    """
    if not missing or not len(keys):
        return keys, 0
    index: dict = {}
    for m in missing:
        b = _addr_bytes(m)
        for pos in range(9):
            index.setdefault((pos, b[:pos] + b[pos + 1:]), []).append(m)
    out = keys.copy()
    snapped = 0
    alts = keys if alt is None else alt
    cache: dict = {}
    for i, (row, arow) in enumerate(zip(keys.tolist(), alts.tolist())):
        key, akey = tuple(row), tuple(arow)
        if key in missing:
            continue
        if akey in missing:
            out[i] = akey
            snapped += 1
            continue
        ck = (key, akey)
        hit = cache.get(ck)
        if hit is None:
            b1, b2 = _addr_bytes(key), _addr_bytes(akey)
            cands = set()
            for b in {b1, b2}:
                for pos in range(9):
                    cands.update(index.get((pos, b[:pos] + b[pos + 1:]), ()))
            good = [m for m in cands
                    if sum(1 for x, y, z in zip(_addr_bytes(m), b1, b2) if x != y and x != z) <= 1]
            hit = cache[ck] = good[0] if len(good) == 1 else False
        if hit:
            out[i] = hit
            snapped += 1
    return out, snapped


def _consensus_symbols(pend: np.ndarray, known: dict, lay: Layout, opt: DecodeOptions, stats: Counter,
                       missing: set | None = None, snap_to: set | None = None) -> dict:
    """Consensus over pending reads per (snapped) tentative address; addresses already known are skipped.

    ``snap_to``: the missing addresses that reads may be snapped to (default ``missing``). Random access attempts
    consensus only for the groups it needs (``missing``) but snaps against every missing address of the bucket, as a
    full decode does, so that a read of a group it does not need is not snapped onto a needed address (job #56)."""
    out = {}
    sc = stats.get("_stage")              # V7 stage counters (opt-in; observability only)
    diag = None
    if sc is not None:
        diag = {"n_records": int(pend.size), "direct": 0, "snapped": 0, "unplaced": 0}
    if not pend.size:
        _count_consensus(sc, diag, missing, None, [], [], [], None, out, lay, opt)
        return out
    keys = np.stack([pend["kind"].astype(np.int64), pend["tag"].astype(np.int64), pend["group"].astype(np.int64),
                     pend["symbol"].astype(np.int64)], axis=1)
    if missing is not None:
        alt = np.asarray(pend["alt"], dtype=np.int64) if "alt" in pend.dtype.names else None
        if diag is not None:
            diag["direct"] = sum(1 for k in keys.tolist() if tuple(k) in missing)
        keys, snapped = snap_addresses(keys, missing if snap_to is None else snap_to, alt)
        stats["addresses_snapped"] += snapped
        wanted = np.fromiter((tuple(k) in missing for k in keys.tolist()), dtype=bool, count=len(keys))
        keys, pend = keys[wanted], pend[wanted]
        if diag is not None:
            diag["unplaced"] = int((~wanted).sum())
            diag["snapped"] = int(wanted.sum()) - diag["direct"]
        if not len(keys):
            _count_consensus(sc, diag, missing, keys, [], [], [], None, out, lay, opt)
            return out
    order = np.lexsort((keys[:, 3], keys[:, 2], keys[:, 1], keys[:, 0]))
    keys = keys[order]
    bases = pend["bases"][order]
    weighted = opt.consensus_weighting == "quality" and "pq" in pend.dtype.names
    if weighted:
        pq = pend["pq"][order]
        pqok = pend["pqok"][order].astype(bool)
    change = np.flatnonzero((np.diff(keys, axis=0) != 0).any(axis=1)) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(keys)]])
    cand_keys, frames, ers = [], [], []
    for s, e in zip(starts.tolist(), ends.tolist()):
        key = tuple(int(x) for x in keys[s])
        if key in known:
            continue
        stop = min(e, s + opt.max_pending_per_address)
        group = bases[s:stop]
        if weighted and pqok[s:stop].all():
            best, erased = consensus_quality_weighted(group, pq[s:stop], opt.consensus_threshold)
            stats["consensus_weighted"] += 1
        else:
            if weighted:
                stats["consensus_weighting_fallback"] += 1        # some read has no qualities: the V4 count vote
            best, erased = consensus_hard(group, opt.consensus_threshold)
        cand_keys.append(key)
        frames.append(best)
        ers.append(erased)
    stats["consensus_attempted"] += len(cand_keys)
    if not cand_keys:
        _count_consensus(sc, diag, missing, keys, [], [], [], None, out, lay, opt)
        return out
    fr = nt_to_bytes(np.stack(frames))
    er = frame_erasures_to_bytes(np.stack(ers))
    P = decode_frames(lay, fr, er)
    for i, key in enumerate(cand_keys):
        if P.ok[i] and (int(P.kind[i]), int(P.tag[i]), int(P.group[i]), int(P.symbol[i])) == key:
            out[key] = P.payload[i]
    stats["consensus_recovered"] += len(out)
    if sc is not None:
        vote_in = [bases[s:min(e, s + opt.max_pending_per_address)] for s, e in zip(starts.tolist(), ends.tolist())
                   if tuple(int(x) for x in keys[s]) not in known]
        _count_consensus(sc, diag, missing, keys, cand_keys, vote_in, list(er), P, out, lay, opt)
    if opt.indel_recovery == "smart" and "raw" in pend.dtype.names:
        out.update(_smart_consensus(cand_keys, out, starts, ends, keys, pend[order], lay, opt, stats))
    if opt.soft_decoding != "off" and "raw" in pend.dtype.names:
        out.update(_soft_consensus(cand_keys, out, starts, ends, keys, pend[order], lay, opt, stats))
    return out


def _count_consensus(sc, diag, missing, keys, cand_keys, vote_in, erased, P, out, lay, opt) -> None:
    """V7 stage counters of one consensus call (observability only; see vnxdna.recovery.stagecount)."""
    if sc is None:
        return
    from vnxdna.recovery.stagecount import consensus_counts
    placed: Counter = Counter()
    if keys is not None and len(keys):
        placed.update(tuple(int(x) for x in k) for k in keys.tolist())
    consensus_counts(sc, superblock=missing is None, n_records=diag["n_records"], direct=diag["direct"],
                     snapped=diag["snapped"], unplaced=diag["unplaced"], missing=missing, placed=placed, groups=vote_in,
                     erased=erased, P=P, cand_keys=cand_keys, recovered=out, threshold=opt.consensus_threshold,
                     cap=opt.max_pending_per_address, r=lay.inner_parity)


def _soft_consensus(cand_keys, done, starts, ends, keys, pend, lay, opt, stats) -> dict:
    """V5 Phase 4: sum the reads' own soft evidence per still-missing address (≥ 2 reads) and soft-decode it."""
    from vnxdna.sync.smart import recovery as rv
    from vnxdna.sync.smart.path import align_with_path
    from vnxdna.recovery.soft import decoder as sd
    from vnxdna.recovery.soft import frames as sf
    from vnxdna.recovery.soft import symbols as ss
    geom = rv.Geometry(lay)
    al = TemplateAligner(lay, opt.band, opt.sync_costs, retry_band=opt.retry_band)
    icfg = opt.indel_config if opt.indel_recovery == "smart" else None
    wanted = set(cand_keys)
    ist = stats["_indel"] if isinstance(stats.get("_indel"), Counter) else Counter()
    keys_out, posts = [], []
    for s, e in zip(starts.tolist(), ends.tolist()):
        key = tuple(int(x) for x in keys[s])
        if key in done or key not in wanted or e - s < 2:
            continue
        grp = pend[s:min(e, s + opt.max_pending_per_address)]
        reads = [grp["raw"][j, : int(grp["rawlen"][j])] for j in range(len(grp))]
        quals = [grp["rawq"][j, : int(grp["rawlen"][j])] if grp["hasq"][j] else None for j in range(len(grp))]
        proj, rpos = align_with_path(al, reads, None if any(q is None for q in quals) else quals)
        evs = []
        for j in np.flatnonzero(proj.ok).tolist():
            plan = None if icfg is None else rv.plan_read(geom, reads[j], quals[j], proj.bases[j], proj.erased[j], rpos[j], icfg)
            evs.append(sf.read_evidence(geom, reads[j], quals[j], proj.bases[j], proj.erased[j], rpos[j], plan,
                                        opt.soft_config.default_error))
        if len(evs) < 2:
            continue
        try:
            posts.append(ss.normalise(sf.consensus_evidence(evs)))
        except ss.SoftInputError:
            continue
        keys_out.append(key)
    out = {}
    if keys_out:
        outs = sd.soft_decode(lay, posts, opt.soft_config, expected=keys_out)
        for key, o in zip(keys_out, outs):
            ist[f"soft_consensus_{o.mode}"] += 1
            if o.accepted:
                out[key] = o.payload
    stats["_indel"] = ist
    stats["consensus_recovered_soft"] += len(out)
    return out


def _smart_consensus(cand_keys, done, starts, ends, keys, pend, lay, opt, stats) -> dict:
    """V5 consensus realignment for addresses the V4 vote could not decode (groups of ≥ 2 pending reads)."""
    from vnxdna.sync.smart.consensus import consensus_recover
    from vnxdna.sync.smart.recovery import Geometry
    geom = Geometry(lay)
    al = TemplateAligner(lay, opt.band, opt.sync_costs, retry_band=opt.retry_band)
    out = {}
    ist = stats["_indel"] if isinstance(stats.get("_indel"), Counter) else Counter()
    wanted = set(cand_keys)
    for s, e in zip(starts.tolist(), ends.tolist()):
        key = tuple(int(x) for x in keys[s])
        if key in done or key not in wanted or e - s < 2:
            continue
        grp = pend[s:min(e, s + opt.max_pending_per_address)]
        reads = [grp["raw"][j, : int(grp["rawlen"][j])] for j in range(len(grp))]
        quals = None
        if grp["hasq"].all():
            quals = [grp["rawq"][j, : int(grp["rawlen"][j])] for j in range(len(grp))]
        res = consensus_recover(geom, al, reads, quals, opt.indel_config, threshold=opt.consensus_threshold,
                                max_reads=opt.max_pending_per_address, expected=key)
        ist["consensus_groups"] += 1
        ist[f"consensus_route_{res.route}"] += 1
        if res.accepted:
            out[key] = res.payload
    stats["_indel"] = ist
    stats["consensus_recovered_smart"] += len(out)
    return out
