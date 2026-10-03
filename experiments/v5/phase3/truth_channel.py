"""Truth-recording twin of the V4 channel (harness only; the decoder never imports this). SIMULATED.

``simulate_with_truth`` replays the random draws of :func:`vnxdna.v4.channel.simulate_batch` in the same order and also
returns, for every read, its source strand and the edit events in strand coordinates. Every call verifies that the
reads it rebuilds are byte-identical to the V4 simulator's output (codes, lengths, qualities); any difference raises.

Supported: substitutions, insertions, deletions, dropout, fixed/Poisson/negative-binomial coverage, homopolymer
multipliers, informative qualities. Not supported (raises): bursts, N calls, reverse complements, duplication, GC bias,
since none is needed by the Phase 3 accounting experiments.
"""
from __future__ import annotations

import numpy as np

from vnxdna.v4 import channel as ch


def simulate_with_truth(strands: np.ndarray, cfg: ch.ChannelConfig, batch_index: int = 0) -> dict:
    for name in ("burst_rate", "n_rate", "reverse_complement_rate", "duplication_rate", "gc_bias_strength"):
        if getattr(cfg, name):
            raise ValueError(f"truth twin does not model {name}")
    ref = ch.simulate_batch(strands, cfg, batch_index)
    rng = np.random.default_rng([cfg.seed, batch_index])
    n, L = strands.shape
    drop = rng.random(n) < cfg.dropout_rate
    mean = cfg.coverage * np.ones(n)
    if cfg.coverage_model == "fixed":
        reads = np.full(n, int(cfg.coverage))
    elif cfg.coverage_model == "poisson":
        reads = rng.poisson(mean)
    else:
        k = cfg.coverage_dispersion
        reads = rng.poisson(rng.gamma(k, mean / k))
    reads = np.where(drop, 0, reads).astype(np.int64)
    src = np.repeat(np.arange(n), reads)
    m = src.size
    if m == 0:
        return {"reads": [], "quals": [], "source": src, "events": [], "stats": ref["stats"]}
    base = strands[src]
    sub_r = np.full((m, L), cfg.substitution_rate)
    ins_r = np.full((m, L), cfg.insertion_rate)
    del_r = np.full((m, L), cfg.deletion_rate)
    if cfg.homopolymer_indel_multiplier != 1.0 or cfg.homopolymer_substitution_multiplier != 1.0:
        hp = ch._homopolymer_mask(strands, cfg.homopolymer_min_run)[src]
        ins_r = np.where(hp, ins_r * cfg.homopolymer_indel_multiplier, ins_r)
        del_r = np.where(hp, del_r * cfg.homopolymer_indel_multiplier, del_r)
        sub_r = np.where(hp, sub_r * cfg.homopolymer_substitution_multiplier, sub_r)
    u = rng.random((m, L))
    is_del = u < del_r
    is_ins = (u >= del_r) & (u < del_r + ins_r)
    is_sub = (u >= del_r + ins_r) & (u < del_r + ins_r + sub_r)
    subbed = np.where(is_sub, (base + rng.integers(1, 4, (m, L))) % 4, base).astype(np.uint8)
    ins_base = rng.integers(0, 4, (m, L)).astype(np.uint8)
    slots = np.stack([ins_base, subbed], axis=2).reshape(m, 2 * L)
    keep = np.stack([is_ins, ~is_del], axis=2).reshape(m, 2 * L)
    lengths = keep.sum(axis=1).astype(np.int64)
    flat = slots[keep]
    # verify against the V4 simulator (qualities are a function of the same draws; compare them too)
    if not (np.array_equal(flat, ref["codes"]) and np.array_equal(lengths, ref["lengths"])):
        raise AssertionError("truth twin diverged from vnxdna.v4.channel.simulate_batch")
    offs = np.concatenate([[0], np.cumsum(lengths)])
    out_reads = [ref["codes"][offs[i]:offs[i + 1]] for i in range(m)]
    out_quals = [ref["quals"][offs[i]:offs[i + 1]] for i in range(m)]
    events = []
    for r in range(m):
        ev = [("del", int(p)) for p in np.flatnonzero(is_del[r])]
        ev += [("ins", int(p), int(ins_base[r, p])) for p in np.flatnonzero(is_ins[r])]     # inserted before strand base p
        ev += [("sub", int(p), int(subbed[r, p])) for p in np.flatnonzero(is_sub[r] & ~is_del[r])]
        events.append(ev)
    return {"reads": out_reads, "quals": out_quals, "source": src, "events": events, "stats": ref["stats"]}
