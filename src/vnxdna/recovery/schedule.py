"""The recovery schedule: pass-1 planning and the deferred smart/soft rounds S, A and B (V5/V6). Formerly in
``vnxdna.v4.decoder`` (V6 Phase 2, M4)."""
from __future__ import annotations

import time
from collections import Counter, deque
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from vnxdna.codec.codecs import CauchyRSCodec, make_outer
from vnxdna.core.errors import VNXAddressError, VNXDecodeError
from vnxdna.dnaenc.layout import KIND_DATA, KIND_SUPER, Layout
from vnxdna.recovery.consensus import _consensus_symbols, resolve_duplicates, snap_addresses
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.pass1 import _p_init, _process
from vnxdna.recovery.spill import Spill
from vnxdna.recovery.superblock import _decode_superblock
from vnxdna.dnaenc.superblock import Superblock, group_k


def _plan_pass1(planner, opt: DecodeOptions, stats: Counter, deferred: bool) -> None:
    """Record pass 1 (FAST, SYNC and, under the eager schedule, SMART/SOFT inline) in the recovery plan."""
    reads = stats["reads"]
    planner.decide("FAST", True, "every read: exact-length frame, inner RS + CRC", {"reads": reads})
    planner.outcome("FAST", reads_verified=stats["fast"])
    planner.decide("SYNC", True, "reads FAST did not verify: marker-template alignment, indels as erasures",
                   {"reads_not_fast": reads - stats["fast"]})
    planner.outcome("SYNC", reads_verified=stats["sync"], reverse_complement=stats["reverse_complement"],
                    pending_after_confidence_check=stats["pending"], unaligned=stats["unaligned"])
    smart, soft = opt.indel_recovery == "smart", opt.soft_decoding != "off"
    if not (smart or soft):
        planner.decide("SMART", False, "indel_recovery is 'segment' (V4 erasure rule only)")
        planner.decide("SOFT", False, "soft_decoding is off")
    elif not deferred:
        why = "eager schedule: inside pass 1 for every read the hard paths failed (no budget admission)"
        if smart:
            planner.decide("SMART", True, why)
            planner.outcome("SMART", reads_verified=stats["smart"])
        if soft:
            planner.decide("SOFT", True, why)
            planner.outcome("SOFT", reads_verified=stats["soft"])


# ============================================================================ deferred per-read recovery (V5)
_DEFER_CHUNK = 256          # pending reads per recovery task
_ORPHAN_SLICE = 65536       # unaddressed reads materialised at once in round B


def _group_decodable(codec, syms: dict, k: int, P: int, g: int) -> bool:
    """The predicate of pass 2's outer decode: would group g decode from these verified symbols?"""
    if isinstance(codec, CauchyRSCodec):     # MDS erasure code: any k distinct symbols (the codec's own check)
        n = codec.symbols_for(k)
        return sum(1 for s in syms if 0 <= s < n) >= k
    try:
        if hasattr(codec, "decode_block"):
            codec.decode_block(syms, k, P, g)
        else:
            codec.decode(syms, k, P)
    except VNXDecodeError:
        return False
    return True


def _pending_keys(pend: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    keys = np.stack([pend["kind"].astype(np.int64), pend["tag"].astype(np.int64), pend["group"].astype(np.int64),
                     pend["symbol"].astype(np.int64)], axis=1) if len(pend) else np.zeros((0, 4), dtype=np.int64)
    alt = np.asarray(pend["alt"], dtype=np.int64) if len(pend) else np.zeros((0, 4), dtype=np.int64)
    return keys, alt


def _targeted(pend: np.ndarray, needed: set) -> np.ndarray:
    """Pending reads whose header reading — either reading, or its unique one-byte snap — is a needed address."""
    if not len(pend) or not needed:
        return np.zeros(len(pend), dtype=bool)
    keys, alt = _pending_keys(pend)
    snapped, _ = snap_addresses(keys, needed, alt)
    return np.fromiter((tuple(k) in needed for k in snapped.tolist()), dtype=bool, count=len(pend))


def _group_state(spill: Spill, sb: Superblock, codec, lay: Layout, opt: DecodeOptions, consumed: list,
                 vote: bool, groups: set | None = None) -> tuple[set, int, int]:
    """(needed addresses, groups, decodable groups) from the verified frames in the spill.

    vote: also count what pass 2's V4 consensus vote will recover — computed exactly as pass 2 computes it (same known
    symbols, same missing set, same pending reads), so a group called decodable here is decodable in pass 2, which only
    adds smart/soft consensus on top. Without it (random access, whose pass 2 uses other missing sets) only single
    verified reads count, which pass 2 can only extend. ``groups``: only these data groups are considered (random
    access); the group count returned is then the number of those groups.
    """
    from dataclasses import replace
    tag = int.from_bytes(sb.archive_id[:2], "big")
    K, P = sb.K, lay.payload_bytes
    v4 = replace(opt, indel_recovery="segment", soft_decoding="off")
    needed: set = set()
    decodable = 0
    for b in range(spill.B):
        acc, pend = spill.load(b)
        acc = acc[(acc["kind"] == KIND_DATA) & (acc["tag"] == tag) & (acc["group"] < sb.group_count)]
        symbols, _ = resolve_duplicates(acc)
        groups_b = [g for g in range(b, sb.group_count, spill.B) if groups is None or g in groups]
        if not groups_b:
            continue
        if vote and len(pend):
            missing = set()
            for g in groups_b:
                n_sym = codec.symbols_for(group_k(sb.container_size, K, P, g))
                missing.update((KIND_DATA, tag, g, s) for s in range(n_sym) if (KIND_DATA, tag, g, s) not in symbols)
            symbols.update(_consensus_symbols(pend[~consumed[b]], symbols, lay, v4, Counter(), missing))
        by_group: dict[int, dict[int, np.ndarray]] = {}
        for (kd, tg, g, s), v in symbols.items():
            if kd == KIND_DATA and tg == tag:
                by_group.setdefault(g, {})[s] = v
        for g in groups_b:
            k = group_k(sb.container_size, K, P, g)
            have = by_group.get(g, {})
            if _group_decodable(codec, have, k, P, g):
                decodable += 1
            else:
                needed.update((KIND_DATA, tag, g, s) for s in range(codec.symbols_for(k)) if s not in have)
    total = sb.group_count if groups is None else sum(1 for g in groups if 0 <= g < sb.group_count)
    return needed, total, decodable


def _deferred_recovery(spill: Spill, lay: Layout, opt: DecodeOptions, stats: Counter, indel_stats: Counter,
                       initargs: tuple, vote: bool = True, planner=None,
                       groups: set | Callable[[Superblock], set] | None = None, state: dict | None = None) -> dict:
    """Per-read smart/soft recovery after the cheap pass, only where it can still change the result.

    State after the cheap pass (fast + sync paths, both orientations), all from verified frames:

      address RECOVERED   at least one verified copy that survives duplicate resolution (strict majority)
      address MISSING     expected by the superblock, not recovered
      group   DECODABLE   its recovered symbols, plus what pass 2's V4 consensus vote will recover from the pending
                          reads (``_group_state``), already satisfy pass 2's outer decode (``_group_decodable``)
      group   INCOMPLETE  not decodable yet
      address NEEDED      MISSING and in an INCOMPLETE group
      read    TARGETED    pending, and a header reading (or its unique one-byte snap) is a NEEDED address
      read    UNADDRESSED aligned, failed, no readable header (kept only for round B)

    Rounds, each running the unchanged eager per-read recovery (``_process`` with smart/soft on) on the read's stored
    orientation:

      S  every pending read whose header says superblock (the superblock defines what is expected)
      A  TARGETED reads
      B  only if the superblock is still undecodable or a group is still INCOMPLETE: every read not yet tried,
         including UNADDRESSED reads and reads whose header points at recovered addresses. A header can be wrong, so
         while data is missing nothing the eager schedule would have tried is left untried.

    A verified frame is stored under its *verified* address and its pending record is removed, as if pass 1 had
    accepted it. Reads never tried stay pending for pass 2 exactly like any failed read. Pass 2 is unchanged.

    Random access (V6) calls this once per stage with ``groups`` (first the index groups, then the stripes holding the
    selected files) and a shared ``state``, so reads tried in one stage are not tried again and later stages only
    consider what is still untried; groups outside ``groups`` never make round A or B run. ``groups`` may be a function
    of the superblock: random access cannot know its first stage's groups (the index) before the superblock is decoded,
    and the superblock must be decoded after round S, exactly as in a full decode (job #56).
    """
    B = spill.B
    sizes = [(spill.dir / f"pend{b}.bin").stat().st_size // spill.pend_dtype.itemsize for b in range(B)]
    st = state if state is not None else {}
    if "tried" not in st:
        st.update(tried=[np.zeros(n, dtype=bool) for n in sizes], orph_done=0, calls=0,
                  info={"mode": "deferred", "pending_reads": int(sum(sizes)), "rounds": {}})
    st["calls"] += 1
    tried = st["tried"]
    consumed = [np.zeros(n, dtype=bool) for n in sizes]
    orph = spill.load_orphans()
    info: dict = st["info"]
    info["unaddressed_reads"] = int(len(orph))
    first = st["calls"] == 1
    pool = None
    if planner is None:
        from vnxdna.recovery.planner import RecoveryPlanner
        planner = RecoveryPlanner()
    modes = "+".join(m for m, on in (("SMART", opt.indel_recovery == "smart"), ("SOFT", opt.soft_decoding != "off")) if on)

    def recover(recs: np.ndarray, rnd: str) -> tuple[np.ndarray, int]:
        """Eager per-read recovery on stored records; returns the accepted mask and the number of records examined
        (fewer than ``len(recs)`` only when the wall-time budget cut the round short), and spills the verified
        frames."""
        t = time.perf_counter()
        ok = np.zeros(len(recs), dtype=bool)
        examined = len(recs)
        r = info["rounds"].setdefault(rnd, {"reads": 0, "recovered": 0, "unique_addresses": 0, "seconds": 0.0})
        if len(recs):
            nonlocal pool
            # about four tasks per worker (≤ _DEFER_CHUNK reads each), so small rounds still use every worker. Per-read
            # results do not depend on the chunking.
            size = _DEFER_CHUNK if opt.workers == 1 else max(8, min(_DEFER_CHUNK, -(-len(recs) // (4 * opt.workers))))
            jobs = []
            for c0 in range(0, len(recs), size):
                c = recs[c0:c0 + size]
                lens = c["rawlen"].astype(np.int64)
                codes = np.concatenate([c["raw"][j, : lens[j]] for j in range(len(c))])
                quals = np.concatenate([c["rawq"][j, : lens[j]] for j in range(len(c))]) if c["hasq"].all() else None
                jobs.append((codes, lens, quals))
            timed = planner.budget.max_wall_seconds is not None
            if opt.workers == 1:
                _p_init(*initargs)
                outs = []
                for j in jobs:
                    if timed and planner.out_of_time():
                        break
                    outs.append(_process(*j))
            else:
                if pool is None:
                    pool = ProcessPoolExecutor(max_workers=opt.workers, initializer=_p_init, initargs=initargs)
                if not timed:
                    outs = list(pool.map(_process, *zip(*jobs)))
                else:
                    # bounded window in job order, so a wall-time cut leaves a deterministic prefix examined
                    outs, window, it = [], deque(), iter(jobs)
                    for j in it:
                        window.append(pool.submit(_process, *j))
                        if len(window) >= 2 * opt.workers:
                            outs.append(window.popleft().result())
                            if planner.out_of_time():
                                break
                    for f in window:
                        f.cancel()
                    while window and not planner.out_of_time():
                        outs.append(window.popleft().result())
            examined = min(len(recs), len(outs) * size)
            fields, payloads = [], []
            for c0, out in zip(range(0, len(recs), size), outs):
                ok[c0 + out["acc_index"]] = True
                fields.append(out["acc_fields"])
                payloads.append(out["acc_payload"])
                indel_stats.update(out.get("indel", {}))
                for key in ("smart", "soft"):
                    if key in out["stats"]:
                        stats[key] += out["stats"][key]
            if fields:
                fields_a = np.concatenate(fields)
                spill.append_acc(fields_a, np.concatenate(payloads))
                r["recovered_addresses"] = r.get("recovered_addresses", 0) + len({tuple(f) for f in fields_a.tolist()})
            r["recovered"] += int(ok.sum())
            if examined < len(recs):
                planner.unexamined(rnd, len(recs) - examined)
                planner.exhausted.append({"limit": "max_wall_seconds", "at": f"round {rnd}", "requested": int(len(recs)),
                                          "admitted": int(examined), "effect": "round cut short; the rest stay pending"})
        r["reads"] += int(examined)
        r["seconds"] = round(r["seconds"] + time.perf_counter() - t, 3)
        return ok, examined

    def round_over(rnd: str, select) -> None:
        addrs: set = set()
        for b in range(B):
            if not sizes[b]:
                continue
            _, pend = spill.load(b)
            sel = np.flatnonzero(select(b, pend) & ~tried[b])
            if not sel.size:
                continue
            sel = sel[: planner.admit_reads(rnd, int(sel.size))]
            if not sel.size:
                continue
            ok, examined = recover(pend[sel], rnd)
            sel, ok = sel[:examined], ok[:examined]
            tried[b][sel] = True
            keys, _ = _pending_keys(pend[sel])
            addrs.update(map(tuple, keys.tolist()))
            consumed[b][sel[ok]] = True
        info["rounds"].setdefault(rnd, {"reads": 0, "recovered": 0, "unique_addresses": 0, "seconds": 0.0})
        info["rounds"][rnd]["unique_addresses"] += len(addrs)

    try:
        def is_super(b, pend):
            keys, alt = _pending_keys(pend)
            return (keys[:, 0] == KIND_SUPER) | (alt[:, 0] == KIND_SUPER)

        if first:
            planner.decide(modes, True, "round S: pending reads whose header says superblock (the superblock defines "
                           "what is expected)", {"pending_reads": info["pending_reads"],
                                                 "unaddressed_reads": info["unaddressed_reads"]})
            round_over("S", is_super)
        try:
            sb, _ = _decode_superblock(spill, lay, opt, Counter())
        except (VNXDecodeError, VNXAddressError):
            sb = None
        info["superblock_decoded"] = sb is not None
        info["v4_vote_counted"] = vote
        if callable(groups):
            groups = set(groups(sb)) if sb is not None else None
        complete = False
        if sb is not None:
            codec = make_outer(sb.outer_code, sb.K, sb.M, sb.lt_seed, sb.lt_distribution)
            needed, n_groups, dec = _group_state(spill, sb, codec, lay, opt, consumed, vote, groups)
            info.update(groups=n_groups, groups_decodable_after_cheap_pass=dec, needed_addresses=len(needed))
            sig = {"groups": n_groups, "groups_decodable": dec, "needed_addresses": len(needed)}
            if groups is not None:
                sig["random_access_stage"] = st["calls"]
            if planner.decide(modes, bool(needed), "round A: reads targeting addresses needed by incomplete groups"
                              if needed else "round A skipped: no address is needed", sig):
                round_over("A", lambda b, pend: _targeted(pend, needed))
                needed, _, dec = _group_state(spill, sb, codec, lay, opt, consumed, vote, groups)
            info["groups_decodable_after_round_a"] = dec
            complete = dec == n_groups
        info["round_b"] = not complete
        why = ("round B: superblock undecodable after round S" if sb is None else
               f"round B: {info['groups'] - info['groups_decodable_after_round_a']} group(s) still incomplete" if not complete
               else "round B skipped: every group decodable")
        planner.decide("EXPENSIVE", not complete, why, {"superblock_decoded": sb is not None,
                                                        "groups_incomplete": None if sb is None else
                                                        info["groups"] - info["groups_decodable_after_round_a"]})
        if not complete:
            planner.checkpoint("round B")
            round_over("B", lambda b, pend: np.ones(len(pend), dtype=bool))
            o0 = st["orph_done"]                           # unaddressed reads already tried by an earlier stage
            n = planner.admit_reads("B", int(len(orph)) - o0)
            examined, found = 0, 0
            for c0 in range(o0, o0 + n, _ORPHAN_SLICE):    # bounded memory: one slice of unaddressed reads at a time
                part = np.array(orph[c0:min(o0 + n, c0 + _ORPHAN_SLICE)])
                ok, done_ = recover(part, "B")
                examined += done_
                found += int(ok.sum())
                if done_ < len(part):
                    planner.unexamined("B", o0 + n - c0 - len(part))
                    break
            st["orph_done"] = o0 + examined
            info["rounds"]["B"]["unaddressed_recovered"] = info["rounds"]["B"].get("unaddressed_recovered", 0) + found
    finally:
        if pool is not None:
            pool.shutdown()
    skipped_keys: set = set()
    skipped = 0
    for b in range(B):
        if sizes[b] and not tried[b].all():          # before the rewrite: indices refer to the pass-1 file
            _, pend = spill.load(b)
            keys, _ = _pending_keys(pend[~tried[b]])
            skipped += len(keys)
            skipped_keys.update(map(tuple, keys.tolist()))
        if consumed[b].any():
            spill.rewrite_pend(b, ~consumed[b])
            st["tried_consumed"] = st.get("tried_consumed", 0) + int(consumed[b].sum())
            tried[b] = tried[b][~consumed[b]]                # keep indices aligned with the rewritten file
    info["reads_skipped"] = skipped
    info["addresses_skipped"] = len(skipped_keys)
    info["reads_tried"] = int(sum(int(t.sum()) for t in tried)) + st.get("tried_consumed", 0) + st["orph_done"]
    rounds = info["rounds"]
    planner.outcome(modes, reads_examined=sum(rounds.get(k, {}).get("reads", 0) for k in "SA"),
                    reads_verified=sum(rounds.get(k, {}).get("recovered", 0) for k in "SA"))
    if info["round_b"]:
        planner.outcome("EXPENSIVE", reads_examined=rounds.get("B", {}).get("reads", 0),
                        reads_verified=rounds.get("B", {}).get("recovered", 0))
    planner.checkpoint("deferred recovery end")
    return info
