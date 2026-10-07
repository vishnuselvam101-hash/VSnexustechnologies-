"""V7 step 7.3: fitter support for empirical insertion/deletion run-length pmfs (estimate, FIT calibration, model output,
design round trip) and the pre-registration 7B metrics M1a, M1c, M2b, M5 share rule, M5i, M6r, RL and the 7B verdict
rule (experiments/v7/fit-nano/PREREGISTRATION.md). SIMULATED data only."""
from __future__ import annotations

import dataclasses
import random

import numpy as np
import pytest

pytest.importorskip("edlib")
from vnxdna.simulation import model as cm, model2                                                     # noqa: E402
from vnxdna.simulation.fit import estimate as est, fit as F, model_out as MO, pipeline as P, simulate as S, tally as T, validate as V  # noqa: E402

DEL_PMF = [0.62, 0.25, 0.07, 0.03, 0.015, 0.008, 0.004, 0.003]
INS_PMF = [0.70, 0.18, 0.06, 0.03, 0.015, 0.008, 0.004, 0.003]


def refs_of(n, L, seed=1):
    rnd = random.Random(seed)
    return [bytes(rnd.choice(b"ACGT") for _ in range(L)) for _ in range(n)]


def truth_model():
    seq = {"substitution": {"rate": 0.015}, "coverage": {"model": "fixed", "mean": 1},
           "insertion": {"rate": 0.008, "run_length": model2.empirical_run_length(INS_PMF, 9.0)},
           "deletion": {"rate": 0.012, "run_length": model2.empirical_run_length(DEL_PMF, 9.0)}}
    return cm.from_doc({"schema": cm.SCHEMA_V2, "name": "truth", "version": "1.0.0", "stages": {"sequencing": seq}})[0]


def tv(a, b):
    return 0.5 * float(np.abs(np.asarray(a) - np.asarray(b)).sum())


# -- estimator helpers --------------------------------------------------------------------------------------------------
def test_empirical_from_hist_folds_the_tail_and_measures_its_mean():
    h = np.zeros(16)
    h[0], h[1], h[7], h[9], h[15] = 60, 30, 5, 3, 2        # runs 1, 2, 8, 10, 16+
    pmf, tail = est.empirical_from_hist(h)
    assert pmf == pytest.approx([0.6, 0.3, 0, 0, 0, 0, 0, 0.1]) and sum(pmf) == pytest.approx(1.0)
    assert tail == pytest.approx((5 * 8 + 3 * 10 + 2 * 16) / 10)
    assert est.empirical_from_hist(np.zeros(16)) == ([1.0] + [0.0] * 7, 8.0)
    values: dict = {}
    est.set_empirical(values, "deletion", pmf, tail)
    assert values["sequencing.deletion.run_length.mean"] == model2.empirical_mean(pmf, tail)


def test_design_refuses_geometric_and_empirical_together():
    with pytest.raises(ValueError):
        est.Design(min_run=3, del_geometric=True, del_empirical=True)
    with pytest.raises(ValueError):
        est.Design(min_run=3, ins_geometric=True, ins_empirical=True)


# -- tally field --------------------------------------------------------------------------------------------------------
def test_read_rate_field_is_opt_in_and_counts_edits_per_aligned_column():
    assert "read_rate" not in T.Layout(50).fields and T.Layout(50).size == T.Layout(50, read_rate=False).size
    lay = T.Layout(50, read_rate=True)
    ref = refs_of(1, 50, seed=3)[0]
    read = ref[:10] + bytes([b"ACGT"[(b"ACGT".index(ref[10]) + 1) % 4]]) + ref[11:30] + ref[31:]   # 1 substitution, 1 deletion
    vec, _ = T.tally_reference(ref, [read, ref], lay, "NW")
    h = lay.get(vec, "read_rate")
    assert h.sum() == 2 and h[0] == 1 and h[int(T.RATE_BINS * 2 / 50)] == 1


# -- metrics ------------------------------------------------------------------------------------------------------------
def _sim_T(model, refs, lay, cov=4, seed=1):
    M, rs = P.tally_matrix(zip(refs, S.simulate_clusters(model, refs, cov, seed)), lay)
    return M, M.sum(axis=0).astype(float), rs


@pytest.fixture(scope="module")
def two_channels():
    """Tallies of the truth model (two seeds) and of a geometric model with the same means (one seed)."""
    lay = T.Layout(80, minruns=(2, 3, 4, 5, 6), read_rate=True)
    refs = refs_of(600, 80, seed=5)
    m = truth_model()
    s = m.stages["sequencing"]
    geo = m.with_parameters({"sequencing.deletion.run_length": {"distribution": "geometric", "mean": s["deletion"]["run_length"]["mean"]},
                             "sequencing.insertion.run_length": {"distribution": "geometric", "mean": s["insertion"]["run_length"]["mean"]},
                             "sequencing.substitution.rate": 0.03})
    return lay, _sim_T(m, refs, lay, seed=1), _sim_T(m, refs, lay, seed=2), _sim_T(geo, refs, lay, seed=3)


def test_7b_metrics_accept_the_same_channel(two_channels):
    lay, (M1, T1, rs1), (_M2, T2, rs2), _ = two_channels
    assert V.m1a_total(lay, M1, T2, seed=1, B=50)["pass"]
    assert V.m1c_spectrum(lay, T1, T2)["pass"]
    assert V.m2b_read_rate(lay, T1, T2)["pass"]
    assert V.m5_delruns(lay, T1, T2, V.SHARE_PP_7B)["pass"] and V.m5i_insruns(lay, T1, T2)["pass"]
    assert V.m_read_length(rs1, rs2)["pass"]
    r6 = V.m6r_homopolymer_runs(lay, T1, T2)                                   # prereg floor: 10,000 sites
    assert r6["pass"] and any(r6["tested"])


def test_7b_metrics_reject_a_different_channel(two_channels):
    lay, (M1, T1, rs1), _, (_Mg, Tg, rsg) = two_channels
    assert not V.m1a_total(lay, M1, Tg, seed=1, B=50)["pass"]               # substitution rate doubled
    assert not V.m2b_read_rate(lay, T1, Tg)["pass"]
    d = V.m5_delruns(lay, T1, Tg, V.SHARE_PP_7B)
    assert d["share_ge2"]["real"] != d["share_ge2"]["sim"]
    shifted = rs1.copy()
    shifted[T.EDIT_BINS:] = np.roll(shifted[T.EDIT_BINS:], 3)
    assert not V.m_read_length(rs1, shifted)["pass"]
    spectrum = T1.copy()
    o, _n = lay.fields["sub_matrix"]
    spectrum[o + 1] *= 4                                                      # A>C four times as often
    assert not V.m1c_spectrum(lay, T1, spectrum)["pass"]


def test_m5_share_rule_is_opt_in_and_m5i_uses_it():
    lay = T.Layout(20)
    a, b = np.zeros(lay.size), np.zeros(lay.size)
    for name in ("del_runs", "ins_runs"):
        o, _n = lay.fields[name]
        a[o:o + 2] = (960, 40)                                                 # share >= 2: 4 %
        b[o:o + 2] = (1000 - 40 - 45, 85)                                      # 8.5 %: TV 0.045 passes, share 4.5 pp fails
    assert V.m5_delruns(lay, a, b)["pass"] and not V.m5_delruns(lay, a, b, 3.0)["pass"]
    assert not V.m5i_insruns(lay, a, b)["pass"]


def test_metrics_needing_absent_layout_fields_are_not_evaluated():
    lay = T.Layout(20)
    z = np.ones(lay.size)
    assert V.m2b_read_rate(lay, z, z)["pass"] is None
    assert V.m6r_homopolymer_runs(lay, z, z)["pass"] is None


def test_7b_verdict_needs_every_gating_metric():
    full = {m: {"pass": True} for m in V.GATING_7B}
    assert V.adequacy(full, V.GATING_7B) == ("ADEQUATE", [])
    assert V.adequacy({k: v for k, v in full.items() if k != "M10"}, V.GATING_7B)[0] == "UNVALIDATED"
    assert V.adequacy({**full, "M5i": {"pass": False}}, V.GATING_7B) == ("INADEQUATE", ["M5i"])
    assert V.adequacy({"M2": {"pass": True}, "M3": {"pass": True}, "M8": {"pass": True}}) == ("ADEQUATE", [])   # 5.4 unchanged


# -- estimate + calibration on SIMULATED truth --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def calibrated():
    lay = T.Layout(100)
    refs = refs_of(1500, 100, seed=9)
    M, _ = P.tally_matrix(zip(refs, S.simulate_clusters(truth_model(), refs, 6, seed=2)), lay)
    d = est.choose_design(lay, M.sum(axis=0).astype(float))
    d = dataclasses.replace(d, del_geometric=False, ins_geometric=False, del_empirical=True, ins_empirical=True)
    fit = F.fit_tallies(M, lay, seed=3, bootstrap=20, with_coverage=False, design=d,
                        calibration=dict(refs=refs[:1200], coverage=6, iterations=5))
    return fit


def test_calibration_recovers_the_empirical_run_lengths(calibrated):
    v, raw = calibrated["values"], calibrated["calibration"]["raw_values"]
    for kind, truth in (("deletion", DEL_PMF), ("insertion", INS_PMF)):
        got, start = v[f"sequencing.{kind}.run_length.pmf"], raw[f"sequencing.{kind}.run_length.pmf"]
        # the raw observed histogram is ~0.03 TV and ~3 pp (share of runs >= 2) off the truth (alignment merges and
        # splits); calibration must close most of that gap, so these bounds fail without it
        assert sum(got) == pytest.approx(1.0) and tv(got, truth) <= 0.015, (kind, got)
        assert tv(got, truth) < 0.6 * tv(start, truth), kind
        assert abs(sum(got[1:]) - sum(truth[1:])) <= 0.015, kind
        assert v[f"sequencing.{kind}.run_length.mean"] == model2.empirical_mean(got, v[f"sequencing.{kind}.run_length.tail_mean"])
    res = calibrated["calibration"]["final_residual_ratios"]
    assert res["del_pmf_tv"] < 0.05 and res["ins_pmf_tv"] < 0.05
    assert isinstance(calibrated["ci95"]["sequencing.deletion.run_length.pmf"], dict)    # per-bin bootstrap interval


def test_fitted_empirical_model_loads_and_implies_the_same_design(calibrated):
    seq = MO.sequencing_stage(calibrated)
    assert seq["deletion"]["run_length"]["distribution"] == "empirical"
    m = cm.from_doc({"schema": cm.SCHEMA_V2, "name": "fit", "version": "0.0.1", "stages": {"sequencing": seq}})[0]
    d = V.design_from_model(m)
    assert d.del_empirical and d.ins_empirical and not d.del_geometric and not d.ins_geometric


def test_m6r_amendment_a1_tolerates_sampling_noise_but_not_a_real_difference(two_channels):
    lay, (M1, T1, _), (_M2, T2, _), _ = two_channels
    assert not V.m6r_homopolymer_runs(lay, T1, T2, min_sites=2000)["pass"]            # plain 10 %: noise in sparse bins fails
    a1 = V.m6r_homopolymer_runs(lay, T1, T2, min_sites=2000, real_M=M1, seed=1, B=100)
    assert a1["pass"] and "amendment A1" in a1["rule"]
    worse = T2.copy()
    for name in ("ctx_ins", "ctx_del"):                                            # indel events x 1.5 everywhere
        o, n = lay.fields[name]
        worse[o:o + n] *= 1.5
    assert not V.m6r_homopolymer_runs(lay, T1, worse, min_sites=2000, real_M=M1, seed=1, B=100)["pass"]
