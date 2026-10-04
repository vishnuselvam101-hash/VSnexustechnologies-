"""Phase 3 smart indel recovery: windows, candidates, partial-segment masks, code arbitration, bounds, properties.

Truth (the injected edits, the original strand) is used only in assertions; the functions under test see reads only.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from vnxdna.v4.errors import VNXConfigurationError
from vnxdna.v5.indel import recovery as rv

from .indel_support import LAY, inject, is_correct, recover, strands

S = strands(64, 9301)
GEOM = rv.Geometry(LAY)


# ---------------------------------------------------------------- properties
def test_no_indel_changes_nothing():
    """Property: a read without an indel has no active window, and its plan is exactly the V4 projection."""
    rng = np.random.default_rng(1)
    reads = [inject(S["strands"][i], [("sub", int(rng.integers(0, 313)), int(rng.integers(0, 4)))])[0] for i in range(20)]
    reads += [S["strands"][i].copy() for i in range(20, 30)]
    proj, rows, plans, outs = recover(reads)
    for k, p in zip(rows.tolist(), plans):
        assert not any(w.active for w in p.windows)
        assert np.array_equal(p.base_frame, proj.bases[k]) and np.array_equal(p.base_erased, proj.erased[k])
        assert (p.symbols["status"] != rv.STATUS_RECOVERED).all()
    for k, o in zip(rows.tolist(), outs):
        assert is_correct(o, S, k) and o.phase == "T0"


@pytest.mark.parametrize("seed", range(4))
def test_uncertain_recovery_never_keeps_an_unverified_symbol(seed):
    """Property (strict default): every byte the plan keeps inside an indel window has P(correct) ≥ 1 − keep_byte_error,
    i.e. local evidence alone never introduces a symbol that is not near-certain; everything else is erased or is an
    explicit CANDIDATE that only the inner code can confirm."""
    rng = np.random.default_rng(seed)
    cfg = rv.IndelRecoveryConfig()
    reads = []
    for i in range(40):
        p = int(rng.integers(0, 313))
        ev = [("del", p)] if rng.random() < 0.5 else [("ins", p, int(rng.integers(0, 4)))]
        reads.append(inject(S["strands"][i], ev)[0])
    _, rows, plans, _ = recover(reads, cfg=cfg)
    for p in plans:
        rec = p.symbols["status"] == rv.STATUS_RECOVERED
        kept_bytes = ~p.base_erased.reshape(-1, 4).any(axis=1)
        conf = p.symbols["conf"].reshape(-1, 4).astype(np.float64)
        in_win = rec.reshape(-1, 4).any(axis=1)
        for b in np.flatnonzero(kept_bytes & in_win).tolist():
            assert 1.0 - np.prod(conf[b]) < cfg.keep_byte_error + 1e-9


# ---------------------------------------------------------------- single indel (step 3)
@pytest.mark.parametrize("kind", ["del", "ins"])
def test_single_indel_every_position_class(kind):
    """Beginning, middle, end, marker bases and marker neighbours: T1 recovers the correct frame; a deletion costs one
    erased byte (the missing base) and an insertion none."""
    cfg = rv.IndelRecoveryConfig(phases=("T1",))
    positions = [0, 1, 11, 23, 24, 25, 26, 27, 28, 150, 286, 300, 312]
    reads = []
    for j, p in enumerate(positions):
        ev = [("del", p)] if kind == "del" else [("ins", p, j % 4)]
        reads.append(inject(S["strands"][j], ev)[0])
    _, rows, plans, outs = recover(reads, cfg=cfg)
    assert rows.size == len(positions)
    for k, o in zip(rows.tolist(), outs):
        assert is_correct(o, S, k), (kind, positions[k], o.phase)
        assert o.erased_nt <= (4 if kind == "del" else 0)


def test_candidates_contain_the_truth():
    rng = np.random.default_rng(7)
    for i in range(30):
        p = int(rng.integers(0, 313))
        kind = "del" if i % 2 else "ins"
        ev = [("del", p)] if kind == "del" else [("ins", p, int(rng.integers(0, 4)))]
        read = inject(S["strands"][i], ev)[0]
        _, _, plans, _ = recover([read], cfg=rv.IndelRecoveryConfig(phases=("T0",)))
        act = [w for w in plans[0].windows if w.active]
        assert len(act) == 1
        w = act[0]
        if not (w.ta <= p <= w.tb) or w.cands is None:
            continue                                   # a chance marker match moved the window (measured in EXP-01)
        truth = S["strands"][i][w.ta:w.tb]
        assert any(np.array_equal(c[c != rv.UNK], truth[c != rv.UNK]) for c in w.cands)


def test_quality_concentrates_insertion_candidates():
    """An informative low quality on the inserted base makes it the top candidate (MEDIUM or better)."""
    read, q = inject(S["strands"][0], [("ins", 100, 2)], q_err=12)
    _, _, plans, _ = recover([read], [q])
    w = [w for w in plans[0].windows if w.active][0]
    assert w.source == rv.SOURCE_QUALITY and w.status in (rv.CONF_MEDIUM, rv.CONF_HIGH)
    assert np.exp(w.logw[0]) > 0.5


# ---------------------------------------------------------------- multi-indel and bounds
def test_three_separated_indels_beyond_v4():
    from vnxdna.v4.frame import decode_frames, nt_to_bytes
    from vnxdna.v4.sync import frame_erasures_to_bytes
    # late in segments 0, 4 and 8 (segment s starts at template 27·s): many shifted bytes, so V4's errors-only retry
    # cannot rescue the read either
    rng = np.random.default_rng(2)
    reads = [inject(S["strands"][i], [("del", int(rng.integers(16, 23))), ("ins", 108 + int(rng.integers(16, 23)), 1),
                                      ("del", 216 + int(rng.integers(16, 23)))])[0] for i in range(16)]
    proj, rows, plans, outs = recover(reads)
    v4 = decode_frames(LAY, nt_to_bytes(np.minimum(proj.bases, 3)), frame_erasures_to_bytes(proj.erased), errors_only_retry=False)
    assert sum(is_correct(o, S, k) for k, o in zip(rows.tolist(), outs)) > int(v4.ok.sum())
    assert not any(o.accepted and not is_correct(o, S, k) for k, o in zip(rows.tolist(), outs))


def test_two_indel_window_is_unrecoverable_by_default_and_bounded_otherwise():
    read = inject(S["strands"][3], [("del", 100), ("del", 101)])[0]
    _, _, plans, _ = recover([read])
    w = [w for w in plans[0].windows if w.active][0]
    assert w.shift == -2 and w.status == rv.CONF_UNRECOVERABLE
    # C(24, 2) = 276 placements: refused when above 16 × max_candidates, otherwise cut to max_candidates
    _, _, plans2, _ = recover([read], cfg=rv.IndelRecoveryConfig(max_shift=2, max_candidates=16))
    w2 = [w for w in plans2[0].windows if w.active][0]
    assert w2.status == rv.CONF_UNRECOVERABLE and w2.reason == "too many two-indel placements"
    _, _, plans3, _ = recover([read], cfg=rv.IndelRecoveryConfig(max_shift=2, max_candidates=64))
    w3 = [w for w in plans3[0].windows if w.active][0]
    assert w3.cands is not None and w3.cands.shape[0] <= 64


@pytest.mark.parametrize("max_trials", [0, 1, 7, 50])
def test_trial_and_budget_limits(max_trials):
    reads = [inject(S["strands"][i], [("del", 10), ("ins", 110, 1), ("del", 230), ("ins", 290, 2)])[0] for i in range(6)]
    cfg = rv.IndelRecoveryConfig(max_trials=max_trials)
    _, _, _, outs = recover(reads, cfg=cfg)
    for o in outs:
        assert o.trials <= max_trials
        assert o.false_accept_bound <= cfg.false_accept_budget * (1 + 1e-9)


def test_zero_budget_runs_no_trial():
    reads = [inject(S["strands"][0], [("del", 50)])[0]]
    _, _, _, outs = recover(reads, cfg=rv.IndelRecoveryConfig(false_accept_budget=0.0))
    assert outs[0].trials == 0 and not outs[0].accepted


def test_batching_and_composition_do_not_change_results():
    rng = np.random.default_rng(3)
    reads = []
    for i in range(24):
        ev = [("del", int(rng.integers(0, 313))), ("ins", int(rng.integers(0, 313)), 1), ("del", int(rng.integers(0, 313)))]
        reads.append(inject(S["strands"][i], ev)[0])
    _, rows_a, _, outs_a = recover(reads, cfg=rv.IndelRecoveryConfig(trial_batch=8192))
    _, rows_b, _, outs_b = recover(reads, cfg=rv.IndelRecoveryConfig(trial_batch=7))
    assert np.array_equal(rows_a, rows_b)
    for a, b in zip(outs_a, outs_b):
        assert (a.accepted, a.phase, a.trials, a.erased_nt) == (b.accepted, b.phase, b.trials, b.erased_nt)
    for k in (0, 5, 17):                                              # alone vs in the batch
        _, rows_c, _, outs_c = recover([reads[k]])
        j = int(np.flatnonzero(rows_a == k)[0]) if k in rows_a else None
        if j is not None and rows_c.size:
            assert (outs_c[0].accepted, outs_c[0].phase) == (outs_a[j].accepted, outs_a[j].phase)


def test_worst_case_read_is_bounded_in_time():
    """Ten indels in ten segments plus substitutions: the search stops at its bounds."""
    rng = np.random.default_rng(11)
    reads = []
    for i in range(20):
        segs = rng.choice(11, 10, replace=False)
        ev = [("del", int(24 * s + 27 * (s > 0) + rng.integers(0, 20))) if rng.random() < 0.5 else
              ("ins", int(24 * s + rng.integers(0, 20)), 1) for s in segs]
        reads.append(inject(S["strands"][i], ev)[0])
    cfg = rv.IndelRecoveryConfig()
    t = time.perf_counter()
    _, _, _, outs = recover(reads, cfg=cfg)
    assert time.perf_counter() - t < 30
    assert all(o.trials <= cfg.max_trials for o in outs)


# ---------------------------------------------------------------- malformed input and configuration
def test_malformed_reads_do_not_crash():
    T = GEOM.T
    rng = np.random.default_rng(5)
    reads = [np.zeros(0, np.uint8), np.zeros(T, np.uint8), np.full(T, 3, np.uint8), rng.integers(0, 4, T).astype(np.uint8),
             np.full(T, 4, np.uint8), rng.integers(0, 256, T).astype(np.uint8), rng.integers(0, 4, T + 6).astype(np.uint8),
             rng.integers(0, 4, T - 6).astype(np.uint8), rng.integers(0, 4, 20000).astype(np.uint8)]
    quals = [rng.integers(0, 94, r.size).astype(np.uint8) for r in reads]
    _, _, _, outs = recover(reads, quals)
    assert not any(o.accepted for o in outs)          # none of these is a valid frame


def test_plan_read_with_inconsistent_path_is_safe():
    read = S["strands"][0].copy()
    bad = np.full(GEOM.T, -1, np.int16)              # an unaligned path: nothing is solid, one window, no shift
    from vnxdna.v4.sync import TemplateAligner
    proj = TemplateAligner(LAY, 6).project([read])
    p = rv.plan_read(GEOM, read, None, proj.bases[0], proj.erased[0], bad, rv.IndelRecoveryConfig())
    assert all(w.status in ("", rv.CONF_UNRECOVERABLE) for w in p.windows)


@pytest.mark.parametrize("kw", [{"max_shift": 3}, {"max_candidates": 0}, {"max_trials": -1}, {"max_joint_windows": 3},
                                {"trial_batch": 0}, {"substitution_prior": 0.3}, {"keep_byte_error": 0.0},
                                {"high_confidence": 0.6}, {"false_accept_budget": 0.1}, {"phases": ("T9",)},
                                {"max_window_segments": 0}])
def test_config_validation(kw):
    with pytest.raises(VNXConfigurationError):
        rv.IndelRecoveryConfig(**kw).validate()


def test_false_accept_bound_formula():
    # f = r: no redundancy left, every word "decodes"; CRC-32 alone guards it
    assert rv._false_accept(70, 16, 16) == pytest.approx(2.0 ** -32)
    assert rv._false_accept(70, 16, 17) == 0.0
    # f = 14 (t' = 1): (1 + 56·255) / 256^2 · 2^-32
    assert rv._false_accept(70, 16, 14) == pytest.approx((1 + 56 * 255) / 256 ** 2 * 2.0 ** -32)
    assert rv._false_accept(70, 16, 0) < 1e-18           # V(70, 8) / 256^16 · 2^-32 ≈ 1.2e-19


def test_temporary_memory_is_bounded_by_trial_batch():
    """Many reads with many trials each: frames are decoded in chunks of trial_batch, never all at once.
    Unbounded, 240 reads × up to 1024 trials × 350 B would need ~86 MB of trial frames."""
    import tracemalloc
    rng = np.random.default_rng(21)
    reads = []
    for i in range(240):
        segs = rng.choice(11, 4, replace=False)
        ev = [("del", int(27 * s + rng.integers(14, 22))) if rng.random() < 0.5 else ("ins", int(27 * s + rng.integers(14, 22)), 1)
              for s in segs]
        reads.append(inject(S["strands"][i % 64], ev)[0])
    cfg = rv.IndelRecoveryConfig(trial_batch=512)
    tracemalloc.start()
    _, _, _, outs = recover(reads, cfg=cfg)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert sum(o.trials for o in outs) > 20 * cfg.trial_batch            # the workload really is large
    assert peak < 40 * 2**20, peak / 2**20
