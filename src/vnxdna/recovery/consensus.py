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


def snap_addresses(keys: np.ndarray, missing: set, alt: np.ndarray | None = None, *,
                   keep_group: bool = False) -> tuple[np.ndarray, int]:
    """Map tentative addresses to the unique missing address within one header byte.

    Each read can carry two readings of its header (``keys`` from the indel-corrected projection, ``alt`` from the raw
    prefix). A candidate address matches a byte position if either reading has that byte; a read is snapped when
    exactly one missing address differs from it in at most one position. Reads whose address is already missing are
    unchanged; reads matching nothing (or several) are left as they are and later ignored. A wrong snap can only
    cost a consensus vote: every recovered frame is still RS- and CRC-verified, and the address it decodes to must
    equal the group's address.

    ``keep_group``: only addresses of the read's own (tentative) group qualify. Pass 2 spills pending reads by that
    group into ``group mod B`` buckets and snaps per bucket, so without this a snap's candidates (and hence the
    consensus) would depend on the bucket count B, i.e. on RLIMIT_NOFILE (V6 Phase 2.8). With it the candidates are the
    same for every B.
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
        if akey in missing and (not keep_group or akey[2] == key[2]):
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
            good = [m for m in cands if (not keep_group or m[2] == key[2])
                    and sum(1 for x, y, z in zip(_addr_bytes(m), b1, b2) if x != y and x != z) <= 1]
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
    if not pend.size:
        return out
    keys = np.stack([pend["kind"].astype(np.int64), pend["tag"].astype(np.int64), pend["group"].astype(np.int64),
                     pend["symbol"].astype(np.int64)], axis=1)
    if missing is not None:
        alt = np.asarray(pend["alt"], dtype=np.int64) if "alt" in pend.dtype.names else None
        keys, snapped = snap_addresses(keys, missing if snap_to is None else snap_to, alt, keep_group=True)
        stats["addresses_snapped"] += snapped
        wanted = np.fromiter((tuple(k) in missing for k in keys.tolist()), dtype=bool, count=len(keys))
        keys, pend = keys[wanted], pend[wanted]
        if not len(keys):
            return out
    order = np.lexsort((keys[:, 3], keys[:, 2], keys[:, 1], keys[:, 0]))
    keys = keys[order]
    bases = pend["bases"][order]
    change = np.flatnonzero((np.diff(keys, axis=0) != 0).any(axis=1)) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(keys)]])
    cand_keys, frames, ers = [], [], []
    for s, e in zip(starts.tolist(), ends.tolist()):
        key = tuple(int(x) for x in keys[s])
        if key in known:
            continue
        group = bases[s:min(e, s + opt.max_pending_per_address)]
        best, erased = consensus_hard(group, opt.consensus_threshold)
        cand_keys.append(key)
        frames.append(best)
        ers.append(erased)
    stats["consensus_attempted"] += len(cand_keys)
    if not cand_keys:
        return out
    fr = nt_to_bytes(np.stack(frames))
    er = frame_erasures_to_bytes(np.stack(ers))
    P = decode_frames(lay, fr, er)
    for i, key in enumerate(cand_keys):
        if P.ok[i] and (int(P.kind[i]), int(P.tag[i]), int(P.group[i]), int(P.symbol[i])) == key:
            out[key] = P.payload[i]
    stats["consensus_recovered"] += len(out)
    if opt.indel_recovery == "smart" and "raw" in pend.dtype.names:
        out.update(_smart_consensus(cand_keys, out, starts, ends, keys, pend[order], lay, opt, stats))
    if opt.soft_decoding != "off" and "raw" in pend.dtype.names:
        out.update(_soft_consensus(cand_keys, out, starts, ends, keys, pend[order], lay, opt, stats))
    return out


def _soft_consensus(cand_keys, done, starts, ends, keys, pend, lay, opt, stats) -> dict:
    """V5 Phase 4: sum the reads' own soft evidence per still-missing address (≥ 2 reads) and soft-decode it."""
    from vnxdna.sync.smart import recovery as rv
    from vnxdna.sync.smart.path import align_with_path
    from vnxdna.recovery.soft import decoder as sd
    from vnxdna.recovery.soft import frames as sf
    from vnxdna.recovery.soft import symbols as ss
    geom = rv.Geometry(lay)
    al = TemplateAligner(lay, opt.band, opt.sync_costs)
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
    al = TemplateAligner(lay, opt.band, opt.sync_costs)
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
