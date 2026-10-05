"""Phase 3 → Phase 4 interface: per-read and consensus soft posteriors over the frame (V5 Phase 4).

``read_evidence`` returns, for one aligned read, an (frame_nt, 4) **log-likelihood** of the read's own evidence about
each frame base (any constant per row is irrelevant). Its normalisation is the read's posterior under a uniform prior.

* frame bases outside indel windows: the aligned read base and its quality (``symbols.observation_loglik``);
  the read index comes from the alignment path (``readpos``), so qualities stay attached to the right base;
* N calls and V4-erased positions outside windows: flat (no evidence);
* inside a Phase 3 window with candidates: the window's per-position posterior π, softened by the substitution model
  of the read itself: P'(b) = π(b)(1 − ε) + (1 − π(b)) ε/3. A Phase 3 RECOVERED base is therefore *never* a
  probability-1 certainty, and an unverified placement stays a distribution over its candidates;
* UNRECOVERABLE windows: flat over every frame base of their segments (the V4 whole-segment rule, as information);
* a read that did not align: no evidence at all (the caller skips it).

Verified information is kept separate: a frame decoded by the code (Phase 3 ``CODE``) is a result, not evidence, and
never enters a posterior here.

``consensus_evidence`` sums the evidence of several reads of one address. Each read's term contains only its own
evidence, so no read can confirm itself and no evidence is counted twice (the leave-one-out rule of Phase 3 is then
automatic). Independence between reads is a modelling assumption.
"""
from __future__ import annotations

import numpy as np

from vnxdna.sync.smart import recovery as rv
from vnxdna.recovery.soft import symbols as ss


def read_evidence(geom: rv.Geometry, read: np.ndarray, qual: np.ndarray | None, proj_bases: np.ndarray,
                  proj_erased: np.ndarray, rpos: np.ndarray, plan: rv.ReadPlan | None, default_error: float) -> np.ndarray:
    lay = geom.layout
    fnt = lay.frame_nt
    out = np.zeros((fnt, 4), dtype=np.float64)
    fpos = geom.frame_pos
    ri = rpos[fpos].astype(np.int64)                         # read index of each frame base (−1: deleted)
    on_path = ri >= 0
    bases = np.where(on_path, proj_bases, 4).astype(np.uint8)
    q = None
    if qual is not None:
        q = np.full(fnt, 0, dtype=np.int64)
        q[on_path] = np.asarray(qual)[ri[on_path]]
    out[:] = ss.observation_loglik(bases, None if q is None else q, default_error)
    if plan is None:
        # V4 projection: its erasures include whole indel segments, so they carry no evidence
        out[proj_erased] = 0.0
        return out
    # with a Phase 3 plan, erasures outside windows are N calls (already flat) or low-quality calls: their quality
    # is used directly instead of an erasure; indel segments are handled window by window below
    for w in plan.windows:
        if not w.active:
            continue
        fr = geom.tmap[w.ta:w.tb]
        sel = fr >= 0
        fidx = fr[sel]
        if w.status == rv.CONF_UNRECOVERABLE or w.post is None:
            segs = np.unique(geom.seg_of_frame[fidx])
            out[np.isin(geom.seg_of_frame, segs)] = 0.0
            continue
        pi = w.post[sel]
        pi = pi / np.maximum(pi.sum(axis=1, keepdims=True), 1e-300)
        e = default_error
        soft = pi * (1.0 - e) + (1.0 - pi) * (e / 3.0)
        with np.errstate(divide="ignore"):
            out[fidx] = np.log(soft)
    return out


def read_posterior(*args, **kw) -> np.ndarray:
    return ss.normalise(read_evidence(*args, **kw))


def consensus_evidence(evidences: list[np.ndarray]) -> np.ndarray:
    if not evidences:
        raise ss.SoftInputError("no reads")
    acc = np.zeros_like(evidences[0])
    for e in evidences:
        if e.shape != acc.shape or not np.isfinite(e).all():
            raise ss.SoftInputError("malformed read evidence")
        acc = acc + e
    return acc
