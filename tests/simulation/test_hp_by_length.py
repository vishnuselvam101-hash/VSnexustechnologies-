"""``vnx.channel-model/2`` per-run-length homopolymer indel multiplier ``homopolymer.indel_by_length`` (V7 7.4,
experiments/v7/fit-nano/d03/CHANGE-HP-BY-LENGTH.md): validation and typed refusals, canonical form and SHA-256 stability,
/1 refusal, unchanged draws, engine rates by run length, fitter estimate + calibration recovery and the M6r metric
(SIMULATED, software tests)."""
from __future__ import annotations

import copy
import dataclasses
import json
import random

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.simulation import model as cm, model2

TRUTH = [1.0, 1.3, 1.9, 2.6, 3.2, 3.6]
HP = {"min_run": 3, "indel_multiplier": 1.0, "substitution_multiplier": 1.0}


def _model(seq_stage):
    return cm.from_doc({"schema": cm.SCHEMA_V2, "name": "t", "version": "1.0.0", "stages": {"sequencing": seq_stage}})[0]


def _seq(by_length=None, **hp):
    h = {**HP, **hp}
    if by_length is not None:
        h["indel_by_length"] = by_length
    return {"substitution": {"rate": 0.015}, "coverage": {"model": "fixed", "mean": 1}, "insertion": {"rate": 0.008},
            "deletion": {"rate": 0.012}, "homopolymer": h}


def hp_refs(n, L, seed=1):
    """References rich in homopolymers (run lengths 1..7), so every run-length class has sites."""
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        s = b""
        while len(s) < L:
            s += bytes([rnd.choice(b"ACGT")]) * rnd.choice([1, 1, 1, 2, 2, 3, 4, 5, 6, 7])
        out.append(s[:L])
    return out


# -- schema -------------------------------------------------------------------------------------------------------------
def test_indel_by_length_loads_canonicalises_and_round_trips():
    m = _model(_seq(TRUTH))
    assert m.stages["sequencing"]["homopolymer"]["indel_by_length"] == TRUTH
    again, _ = cm.from_doc(json.loads(m.dumps()))
    assert again.doc == m.doc and again.sha256 == m.sha256
    assert "homopolymer.indel_by_length" in model2.active_effects(m.stages)
    assert "homopolymer.indel_by_length" in model2.HONOURED
    with pytest.raises(VNXConfigurationError, match="cannot express"):
        m.to_v1()


def test_models_without_the_field_keep_their_canonical_form():
    m = _model(_seq())
    assert "indel_by_length" not in m.stages["sequencing"]["homopolymer"]
    assert "homopolymer.indel_by_length" not in model2.active_effects(m.stages)
    # SHA-256 of this model computed with the code before the field existed (build/v7-sprint d3b57fe)
    assert m.sha256 == "01d96f21a49903e64d901413a335e8a91e24fd9930b9ad028fef1e17263f03d8"
    assert m.to_v1()["schema"] == cm.SCHEMA_V1


@pytest.mark.parametrize("by_length, hp, why", [
    (TRUTH[:5], {}, "6 multipliers"),
    (TRUTH + [4.0], {}, "6 multipliers"),
    ("x", {}, "6 multipliers"),
    ([1.0, -0.1] + TRUTH[2:], {}, r"indel_by_length\[1\]"),
    ([1.0, 101.0] + TRUTH[2:], {}, r"indel_by_length\[1\]"),
    ([1.0, float("nan")] + TRUTH[2:], {}, r"indel_by_length\[1\]"),
    (TRUTH, {"indel_multiplier": 1.5}, "must then be 1"),
])
def test_malformed_indel_by_length_is_refused_with_a_typed_error(by_length, hp, why):
    with pytest.raises(VNXConfigurationError, match=why):
        _model(_seq(copy.deepcopy(by_length), **hp))


def test_indel_by_length_is_refused_outside_the_v2_sequencing_stage():
    with pytest.raises(VNXConfigurationError):          # a /1 document cannot carry it
        cm.from_doc({"schema": cm.SCHEMA_V1, "name": "t", "version": "1.0.0", "stages": {"sequencing": _seq(TRUTH)}})


# -- engine -------------------------------------------------------------------------------------------------------------
def test_run_lengths_and_mask_agree():
    from vnxdna.simulation.channel import _homopolymer_mask, _run_lengths
    codes = np.array([[0, 0, 1, 2, 2, 2, 3, 0, 0, 0, 0, 1]], dtype=np.uint8)
    assert _run_lengths(codes).tolist() == [[2, 2, 1, 3, 3, 3, 1, 4, 4, 4, 4, 1]]
    for k in (2, 3, 4):
        assert np.array_equal(_homopolymer_mask(codes, k), _run_lengths(codes) >= k)


def test_unit_multipliers_give_the_same_reads_as_no_field():
    """Only the rates change: multipliers of 1 draw exactly the reads of the model without the field."""
    from vnxdna.simulation.fit import simulate as S
    refs = hp_refs(80, 60, seed=4)
    a = S.simulate_clusters(_model(_seq()), refs, 3, seed=5)
    b = S.simulate_clusters(_model(_seq([1.0] * 6)), refs, 3, seed=5)
    c = S.simulate_clusters(_model(_seq(TRUTH)), refs, 3, seed=5)
    assert a == b and a != c


pytest.importorskip("edlib")
from vnxdna.simulation.fit import calibrate as C, estimate as est, fit as F, model_out as MO, pipeline as P  # noqa: E402
from vnxdna.simulation.fit import simulate as S, tally as T, validate as V                                  # noqa: E402

LAY = T.Layout(100, minruns=(2, 3, 4, 5, 6))


def _tally(model, refs, cov, seed):
    M, _ = P.tally_matrix(zip(refs, S.simulate_clusters(model, refs, cov, seed)), LAY)
    return M, M.sum(axis=0).astype(float)


def _class_rates(Tt):
    sites = est.length_classes(LAY, "ctx_sites", Tt).sum(axis=1)
    ev = (est.length_classes(LAY, "ctx_ins", Tt) + est.length_classes(LAY, "ctx_del", Tt)).sum(axis=1)
    return ev / sites


@pytest.fixture(scope="module")
def tallies():
    refs = hp_refs(1500, 100, seed=9)
    flat_M, flat_T = _tally(_model(_seq()), refs, 6, 2)
    ramp_M, ramp_T = _tally(_model(_seq(TRUTH)), refs, 6, 2)
    _M2, ramp_T2 = _tally(_model(_seq(TRUTH)), refs, 6, 3)
    return refs, (flat_M, flat_T), (ramp_M, ramp_T), ramp_T2


def test_length_classes_partition_the_sites(tallies):
    _refs, (_fM, flat_T), _r, _t2 = tallies
    sites = est.length_classes(LAY, "ctx_sites", flat_T)
    assert sites.shape == (6, 64) and sites.min() >= 0
    assert sites.sum() == pytest.approx(LAY.ctx(flat_T, "ctx_sites")[0].sum())        # every site in exactly one class
    with pytest.raises(ValueError, match="min_runs"):
        est.length_classes(T.Layout(100), "ctx_sites", flat_T)


@pytest.mark.parametrize("run, cls", [(1, 0), (2, 1), (3, 2), (5, 4), (6, 5), (9, 5)])
def test_engine_deletion_rate_is_the_multiplier_of_the_run_length(run, cls):
    """References made only of runs of one length; deletions of single bases only, so the bases missing from the reads
    count the deletion events exactly (no alignment): mean deleted per read = L * rate * multiplier."""
    rate, L, n = 0.004, 90, 3000
    rnd = random.Random(run)
    refs = []
    for _ in range(n):
        s, prev = b"", None
        while len(s) < L:
            b = rnd.choice([x for x in b"ACGT" if x != prev])
            s, prev = s + bytes([b]) * run, b
        refs.append(s[:L - L % run] if L % run else s[:L])
    seq = {"coverage": {"model": "fixed", "mean": 1}, "deletion": {"rate": rate},
           "homopolymer": {**HP, "indel_by_length": TRUTH}}
    reads = S.simulate_clusters(_model(seq), refs, 1, seed=run)
    lost = np.array([len(r) - len(c[0]) for r, c in zip(refs, reads)], dtype=float)
    expect = np.array([len(r) for r in refs], dtype=float) * rate * TRUTH[cls]
    assert abs(lost.sum() - expect.sum()) < 4 * expect.sum() ** 0.5, (lost.sum(), expect.sum())


def test_m6r_tells_a_ramp_from_a_flat_step(tallies):
    _refs, (_fM, flat_T), (ramp_M, ramp_T), ramp_T2 = tallies
    assert V.m6r_homopolymer_runs(LAY, ramp_T, ramp_T2, real_M=ramp_M, seed=1, B=100)["pass"]
    assert not V.m6r_homopolymer_runs(LAY, ramp_T, flat_T, real_M=ramp_M, seed=1, B=100)["pass"]


@pytest.fixture(scope="module")
def calibrated(tallies):
    refs, _f, (ramp_M, ramp_T), _t2 = tallies
    d = dataclasses.replace(est.choose_design(LAY, ramp_T), hp_by_length=True)
    return F.fit_tallies(ramp_M, LAY, seed=3, bootstrap=20, with_coverage=False, design=d,
                         calibration=dict(refs=refs[:1200], coverage=6, iterations=6))


def test_ipf_by_length_recovers_exact_poisson_means():
    S_ = np.full((6, 64), 1000.0)
    O = {"insertion": 0.01 * S_ * np.asarray(TRUTH)[:, None], "deletion": 0.02 * S_ * np.asarray(TRUTH)[:, None]}
    out = est._ipf_by_length(S_, O, set())
    assert np.allclose(out["m"], TRUTH) and out["r"]["insertion"] == pytest.approx(0.01) and out["r"]["deletion"] == pytest.approx(0.02)


def test_calibration_recovers_the_run_length_multipliers(calibrated):
    v, raw = calibrated["values"], calibrated["calibration"]["raw_values"]
    got, start = np.asarray(v["sequencing.homopolymer.indel_by_length"]), np.asarray(raw["sequencing.homopolymer.indel_by_length"])
    # alignment hides events inside long runs, so the raw estimate is far below the truth; calibration must close the gap
    assert got[0] == 1.0 and np.all(np.abs(got / TRUTH - 1.0) <= 0.10), got
    assert np.max(np.abs(start / TRUTH - 1.0)) > 0.2, start
    assert v["sequencing.homopolymer.indel_multiplier"] == 1.0
    assert "hp_len" in C.calibrated_keys(calibrated["design"]) and "hp_indel" not in C.calibrated_keys(calibrated["design"])
    assert isinstance(calibrated["ci95"]["sequencing.homopolymer.indel_by_length"], dict)


def test_fitted_model_carries_the_field_and_implies_the_same_design(calibrated):
    seq = MO.sequencing_stage(calibrated)
    assert seq["homopolymer"]["indel_by_length"] == calibrated["values"]["sequencing.homopolymer.indel_by_length"]
    m = _model(seq)
    assert V.design_from_model(m).hp_by_length and not V.design_from_model(_model(_seq())).hp_by_length
    assert "sequencing.homopolymer.indel_by_length" in MO.BASIS
