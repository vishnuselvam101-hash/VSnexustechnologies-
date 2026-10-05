"""Bounded soft-information inner decoding (V5 Phase 4).

The inner code and its verification are unchanged. This module only decides *which received words* the existing
decoder sees, using per-base posteriors (:mod:`.symbols`):

  erasure  GMD (Forney's generalised minimum distance): the hard word with the s least reliable bytes erased,
           s = s0, s0 + 2, …, r, where s0 is the number of bytes that are already unknown. At most r/2 + 1 trials.
  chase    Chase-II: the t least reliable (non-erased) bytes each take one of their top-m values; every
           combination is a trial, m^t ≤ max_soft_trials, decoded with the existing erasures.
  auto     erasure first; chase only if no erasure trial verifies.

Every trial goes through ``decode_frames`` (inner RS errors+erasures, CRC-32, version/kind checks). Acceptance:

* at least one trial verifies, and **every verified trial of the deciding mode decodes to the same frame**. Two
  different verified frames → *ambiguous* → rejected (fail closed). Likelihood is used to order trials and to report
  the accepted trial's rank, never to choose between different verified frames;
* the per-read false-acceptance budget of Phase 3 applies (Σ over trials of P(wrong frame passes RS + CRC-32)).

A soft decoder therefore never accepts a word that the RS decoder did not correct to a codeword with a valid CRC, and
it never extends the algebraic guarantee: each accepted codeword is within the RS bound (2e + f ≤ r) of *one of the
trial words*, which the soft evidence chose. That is a bounded search, not a correction beyond the RS bound.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.layout import Layout
from vnxdna.dnaenc.frame4 import decode_frames
from vnxdna.sync.smart.recovery import _false_accept
from . import symbols as ss

MODES = ("off", "erasure", "chase", "auto")


@dataclass(frozen=True)
class SoftDecodeConfig:
    mode: str = "auto"
    chase_bytes: int = 4               # t: least reliable bytes perturbed by Chase (max_soft_symbols)
    chase_values: int = 2              # m: candidate values per perturbed byte (the hard value + m − 1 alternatives)
    max_soft_trials: int = 64          # per read, all modes together
    max_bytes_per_trial: int = 4       # Chase never changes more than this many bytes in one trial
    false_accept_budget: float = 1e-9  # per read, as Phase 3
    default_error: float = 0.005       # base error assumed for reads without qualities (not calibrated)
    unknown_reliability: float = 0.0   # bytes with an unknown base count as fully unreliable (erased first)

    def validate(self) -> "SoftDecodeConfig":
        checks = [
            (self.mode in MODES, f"mode must be one of {MODES}"),
            (0 <= self.chase_bytes <= 8, "chase_bytes must be in 0..8"),
            (2 <= self.chase_values <= 4, "chase_values must be in 2..4"),
            (1 <= self.max_soft_trials <= 4096, "max_soft_trials must be in 1..4096"),
            (1 <= self.max_bytes_per_trial <= 8, "max_bytes_per_trial must be in 1..8"),
            (0 <= self.false_accept_budget <= 1e-3, "false_accept_budget must be in [0, 1e-3]"),
            (0 < self.default_error < 0.75, "default_error must be in (0, 0.75)"),
        ]
        for ok, msg in checks:
            if not ok:
                raise VNXConfigurationError(msg)
        return self


@dataclass
class SoftOutcome:
    accepted: bool = False
    mode: str = "none"           # erasure | chase | ambiguous | invalid | none
    fields: tuple = ()
    payload: np.ndarray | None = None
    trials: int = 0
    verified_trials: int = 0
    distinct_verified: int = 0
    accepted_rank: int | None = None    # rank (0 = most likely) of the first verified trial in likelihood order
    erased_bytes: int = 0
    rs_errata: int = 0
    false_accept_bound: float = 0.0
    entropy_bits: float = 0.0           # mean per-base entropy of the input posterior
    candidates: int = 0                 # alternative byte values considered (Chase)
    error: str = ""
    verified_frames: list = field(default_factory=list)   # distinct (address, payload bytes) verified in the deciding mode


def _trials_erasure(hard: np.ndarray, rel: np.ndarray, unknown: np.ndarray, r: int) -> list[tuple[np.ndarray, np.ndarray]]:
    order = np.lexsort((np.arange(rel.size), rel))       # least reliable first, ties by position (deterministic)
    s0 = int(unknown.sum())
    out = []
    for s in range(s0, r + 1, 2):
        er = np.zeros(rel.size, dtype=bool)
        er[order[:s]] = True
        er |= unknown
        out.append((hard.copy(), er))
    if not out and s0 <= r:
        out.append((hard.copy(), unknown.copy()))
    return out


def _trials_chase(L: np.ndarray, hard: np.ndarray, rel: np.ndarray, unknown: np.ndarray, cfg: SoftDecodeConfig,
                  limit: int) -> tuple[list[tuple[np.ndarray, np.ndarray]], int]:
    cand_pos = np.flatnonzero(~unknown)
    order = cand_pos[np.lexsort((cand_pos, rel[cand_pos]))][: cfg.chase_bytes]
    alts = []
    n_cands = 0
    for j in order.tolist():
        top = ss.byte_topk(L[4 * j:4 * j + 4], cfg.chase_values)
        vals = [v for _, v in top]
        if hard[j] not in vals:                        # cannot happen (hard = argmax); defensive
            vals = [int(hard[j])] + vals[:-1]
        alts.append((j, vals))
        n_cands += len(vals) - 1
    out = []
    sizes = [len(v) for _, v in alts]
    for flat in range(int(np.prod(sizes)) if sizes else 1):
        idx = np.unravel_index(flat, sizes) if sizes else ()
        changed = sum(1 for i in idx if i != 0)
        if changed > cfg.max_bytes_per_trial:
            continue
        w = hard.copy()
        for (j, vals), i in zip(alts, idx):
            w[j] = vals[int(i)]
        out.append((w, unknown.copy()))
        if len(out) >= limit:
            break
    return out, n_cands


def soft_decode(layout: Layout, posteriors: list[np.ndarray], cfg: SoftDecodeConfig,
                unknown_bytes: list[np.ndarray] | None = None, expected: list | None = None) -> list[SoftOutcome]:
    """Soft-decode frames. ``posteriors[i]``: (frame_nt, 4) log-probabilities of frame i (validated; a malformed
    array makes that frame ``invalid``, never accepted). ``unknown_bytes[i]``: bytes known to be erased (optional).
    ``expected[i]``: an address (kind, tag, group, symbol) the frame must decode to, or None."""
    cfg.validate()
    nb, r = layout.frame_bytes, layout.inner_parity
    fa_cost = np.array([_false_accept(nb, r, f) for f in range(nb + 1)])
    outs: list[SoftOutcome] = []
    modes = {"erasure": ("erasure",), "chase": ("chase",), "auto": ("erasure", "chase"), "off": ()}[cfg.mode]
    per_read: list[dict] = []
    for i, L in enumerate(posteriors):
        o = SoftOutcome()
        outs.append(o)
        try:
            L = ss.validate_logp(np.asarray(L), layout.frame_nt)
        except ss.SoftInputError as error:
            o.mode, o.error = "invalid", str(error)
            per_read.append({})
            continue
        hard, hb = ss.byte_hard(L)
        rel = ss.byte_reliability(L)
        unk = np.zeros(nb, dtype=bool) if unknown_bytes is None or unknown_bytes[i] is None else \
            np.asarray(unknown_bytes[i], dtype=bool).copy()
        if unk.shape != (nb,):
            o.mode, o.error = "invalid", "unknown_bytes has the wrong shape"
            per_read.append({})
            continue
        # a byte with an unknown (uniform) base carries no hard value: treat it as erased
        unk |= (np.exp(L).max(axis=1) <= 0.25 + 1e-12).reshape(-1, 4).any(axis=1)
        o.entropy_bits = float(ss.entropy_bits(L).mean())
        per_read.append({"L": L, "hard": hard, "rel": rel, "unk": unk, "budget": cfg.false_accept_budget})
    for mode in modes:
        jobs = []
        for i, st in enumerate(per_read):
            o = outs[i]
            if not st or o.mode != "none" or o.trials >= cfg.max_soft_trials:
                continue
            room = cfg.max_soft_trials - o.trials
            if mode == "erasure":
                trials = _trials_erasure(st["hard"], st["rel"], st["unk"], r)
            else:
                trials, nc = _trials_chase(st["L"], st["hard"], st["rel"], st["unk"], cfg, room)
                o.candidates += nc
            for w, er in trials[:room]:
                f = int(er.sum())
                if f > r:
                    continue
                c = fa_cost[f]
                if c > st["budget"]:
                    continue
                st["budget"] -= c
                o.false_accept_bound += c
                o.trials += 1
                jobs.append((i, w, er))
        if not jobs:
            continue
        words = np.stack([w for _, w, _ in jobs])
        ers = np.stack([e for _, _, e in jobs])
        P = decode_frames(layout, words, ers, errors_only_retry=False)
        verified: dict[int, list] = {}
        for k in np.flatnonzero(P.ok).tolist():
            i = jobs[k][0]
            key = (int(P.kind[k]), int(P.tag[k]), int(P.group[k]), int(P.symbol[k]))
            if expected is not None and expected[i] is not None and key != tuple(expected[i]):
                continue                                   # decodes, but not to the address this frame must have
            ll = ss.word_loglik(per_read[i]["L"], jobs[k][1])
            verified.setdefault(i, []).append((ll, k, key, P.payload[k].copy(), int(P.errata[k]), int(ers[k].sum())))
        # rank of each read's trials by likelihood (for accounting)
        lls: dict[int, list] = {}
        for k, (i, w, _) in enumerate(jobs):
            lls.setdefault(i, []).append((ss.word_loglik(per_read[i]["L"], w), k))
        for i, hits in verified.items():
            o = outs[i]
            o.verified_trials += len(hits)
            frames = {(h[2], h[3].tobytes()) for h in hits}
            o.distinct_verified = len(frames)
            o.verified_frames = sorted(frames)
            if len(frames) > 1:
                o.mode = "ambiguous"                       # two verified frames disagree: never choose
                continue
            best = max(hits, key=lambda h: (h[0], -h[1]))
            order = [k for _, k in sorted(lls[i], key=lambda t: (-t[0], t[1]))]
            o.accepted, o.mode, o.fields, o.payload = True, mode, best[2], best[3]
            o.accepted_rank = order.index(best[1])
            o.rs_errata, o.erased_bytes = best[4], best[5]
    return outs


def summarize(outs: list[SoftOutcome]) -> dict:
    c: dict = {"attempted": len(outs)}
    for o in outs:
        c[f"mode_{o.mode}"] = c.get(f"mode_{o.mode}", 0) + 1
        c["trials"] = c.get("trials", 0) + o.trials
        c["candidates"] = c.get("candidates", 0) + o.candidates
        c["false_accept_bound"] = c.get("false_accept_bound", 0.0) + o.false_accept_bound
        c["entropy_bits_sum"] = c.get("entropy_bits_sum", 0.0) + o.entropy_bits
        if o.accepted:
            c["recovered"] = c.get("recovered", 0) + 1
            c["accepted_rank_sum"] = c.get("accepted_rank_sum", 0) + (o.accepted_rank or 0)
    if not math.isfinite(c.get("false_accept_bound", 0.0)):
        c["false_accept_bound"] = None
    return c
