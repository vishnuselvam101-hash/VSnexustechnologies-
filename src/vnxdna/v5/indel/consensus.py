"""Consensus-assisted realignment for reads of one address (V5 Phase 3 §6).

Input: the raw reads that pass 2 grouped under one (snapped) address, after each failed individually. Method:

1. align every read (marker path) and find its indel windows (as in :mod:`.recovery`);
2. votes: every read contributes weight 1 per frame base it *knows* (outside its indel windows, not N/low quality);
3. for each read and each window, the candidates are re-scored with a **leave-one-out** consensus prior, the
   votes of the *other* reads only, so a read can never confirm its own reconstruction;
4. a window's bases become votes only where the window posterior is HIGH (≥ 1 − high_confidence), with weight
   equal to that posterior; two rounds, so windows that overlap in different reads can help each other;
5. consensus base = weighted majority; a position is erased when it has no vote or its share is below the V4
   ``consensus_threshold`` (0.6), so one read can never outvote two that agree;
6. the consensus frame goes to the unchanged inner RS + CRC, and pass 2 still requires that the decoded address
   equal the group's address. If that fails, each read is retried alone with the consensus prior through the
   normal trial phases (T0, T1, T2), and accepted only if every read that verifies agrees (unanimity).

Bounds: at most ``max_reads`` reads per address, two rounds, the per-read bounds of IndelRecoveryConfig.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ...v4.frame import decode_frames, nt_to_bytes
from ...v4.sync import TemplateAligner
from . import recovery as rv
from .path import align_with_path


@dataclass
class ConsensusOutcome:
    accepted: bool = False
    route: str = "none"          # consensus | read | ambiguous | none
    fields: tuple = ()
    payload: np.ndarray | None = None
    reads_used: int = 0
    windows_recovered: int = 0   # windows that contributed HIGH-confidence votes
    windows_total: int = 0
    erased_nt: int = 0           # erasures in the consensus frame that was decoded
    stats: dict = field(default_factory=dict)


def _votes_from_plan(plan: rv.ReadPlan) -> np.ndarray:
    """(frame_nt, 4) weight-1 votes from the bases a read knows outside its indel windows."""
    sym = plan.symbols
    v = np.zeros((sym.size, 4), dtype=np.float64)
    known = (sym["status"] == rv.STATUS_KNOWN) & (sym["base"] < 4)
    v[np.flatnonzero(known), sym["base"][known]] = 1.0
    return v


def _window_prior(geom: rv.Geometry, w: rv.Window, votes: np.ndarray) -> np.ndarray:
    fr = geom.tmap[w.ta:w.tb]
    pr = np.zeros((w.tb - w.ta, 4), dtype=np.float64)
    sel = fr >= 0
    pr[sel] = votes[fr[sel]]
    return pr


def consensus_recover(geom: rv.Geometry, aligner: TemplateAligner, reads: list, quals: list | None,
                      cfg: rv.IndelRecoveryConfig, threshold: float = 0.6, max_reads: int = 64,
                      expected: tuple | None = None) -> ConsensusOutcome:
    out = ConsensusOutcome()
    reads = [np.asarray(r) for r in reads[:max_reads]]
    if quals is not None:
        quals = quals[:max_reads]
    m = len(reads)
    out.reads_used = m
    if m == 0:
        return out
    proj, rpos = align_with_path(aligner, reads, quals)
    usable = np.flatnonzero(proj.ok)
    if usable.size == 0:
        return out
    plans: dict[int, rv.ReadPlan] = {}
    for i in usable.tolist():
        plans[i] = rv.plan_read(geom, reads[i], None if quals is None else quals[i], proj.bases[i], proj.erased[i], rpos[i], cfg)
    own = {i: _votes_from_plan(p) for i, p in plans.items()}
    base_votes = sum(own.values())
    extra = {i: np.zeros_like(base_votes) for i in plans}          # HIGH window votes per read
    out.windows_total = sum(len(p.searchable) for p in plans.values())
    for _round in range(2):
        total = base_votes + sum(extra.values())
        for i, p in plans.items():
            loo = total - own[i] - extra[i]
            ev = np.zeros_like(base_votes)
            for wi in p.searchable:
                w = p.windows[wi]
                prior = _window_prior(geom, w, loo)
                if prior.sum() == 0:
                    continue
                trial = rv.Window(w.ta, w.tb, w.ra, w.rb, w.active, w.n_segments)
                rv.score_window(geom, trial, reads[i], None if quals is None else quals[i], cfg, prior)
                if trial.status != rv.CONF_HIGH:
                    continue
                fr = geom.tmap[w.ta:w.tb]
                sel = fr >= 0
                conf = trial.post.max(axis=1)[sel]
                best = trial.post.argmax(axis=1)[sel]
                good = conf >= 1.0 - cfg.high_confidence
                ev[fr[sel][good], best[good]] += conf[good]
            extra[i] = ev
    out.windows_recovered = int(sum((e.sum(axis=1) > 0).any() for e in extra.values()))
    votes = base_votes + sum(extra.values())
    tot = votes.sum(axis=1)
    best = votes.argmax(axis=1).astype(np.uint8)
    share = np.where(tot > 0, votes.max(axis=1) / np.maximum(tot, 1e-300), 0.0)
    erased = (tot <= 0) | (share < threshold)
    out.erased_nt = int(erased.sum())
    P = decode_frames(geom.layout, nt_to_bytes(best[None, :]), erased.reshape(1, -1, 4).any(axis=2))
    key = (int(P.kind[0]), int(P.tag[0]), int(P.group[0]), int(P.symbol[0]))
    if P.ok[0] and (expected is None or key == tuple(expected)):
        out.accepted, out.route, out.fields, out.payload = True, "consensus", key, P.payload[0].copy()
        return out
    # each read alone, its windows scored with the leave-one-out consensus of the others
    total = base_votes + sum(extra.values())
    rplans = []
    for i, p in plans.items():
        loo = total - own[i] - extra[i]
        priors = {p.windows[wi].ta: _window_prior(geom, p.windows[wi], loo) for wi in p.searchable}
        rplans.append(rv.plan_read(geom, reads[i], None if quals is None else quals[i], proj.bases[i], proj.erased[i], rpos[i],
                                   cfg, priors))
    res = rv.recover_batch(geom, rplans, cfg)
    hits = [o for o in res if o.accepted and (expected is None or o.fields == tuple(expected))]
    if not hits:
        if any(o.phase == "ambiguous" for o in res):
            out.route = "ambiguous"
        return out
    if all(h.fields == hits[0].fields and np.array_equal(h.payload, hits[0].payload) for h in hits):
        out.accepted, out.route, out.fields, out.payload = True, "read", hits[0].fields, hits[0].payload
    else:
        out.route = "ambiguous"
    return out
