"""V7 review (substitution parameter identifiability, calibration checks): with a substitution 3-mer context and
homopolymer min_run 2 the homopolymer substitution multiplier is fixed at 1; the context is kept at site-weighted mean 1
with the factor folded into the rate; calibration convergence and its verification cover the run-length means
(SIMULATED, software tests)."""
from __future__ import annotations

import random

import numpy as np
import pytest

pytest.importorskip("edlib")

from vnxdna.simulation import model as cm  # noqa: E402
from vnxdna.simulation.fit import calibrate as C, estimate as est, fit as F, model_out as MO, pipeline as P  # noqa: E402
from vnxdna.simulation.fit import simulate as S, tally as T  # noqa: E402
from vnxdna.simulation.model2 import dataset_digest  # noqa: E402

FILES = [{"name": "a.txt", "sha256": "1" * 64}]


def refs_of(n, L, seed=1):
    rnd = random.Random(seed)
    return [bytes(rnd.choice(b"ACGT") for _ in range(L)) for _ in range(n)]


def _context(seed=3):
    rng = np.random.default_rng(seed)
    return np.exp(rng.normal(0.0, 0.6, 64))


def _model(min_run=2, hp_sub=0.3, ctx=None):
    seq = {"substitution": {"rate": 0.02}, "insertion": {"rate": 0.004}, "deletion": {"rate": 0.004},
           "homopolymer": {"min_run": min_run, "indel_multiplier": 1.0, "substitution_multiplier": hp_sub}}
    if ctx is not None:
        seq["context"] = {"k": 3, "substitution": [float(x) for x in ctx]}
    doc = {"schema": cm.SCHEMA_V2, "name": "unit", "version": "1.0.0", "model_id": "unit-F", "data_source": "LABORATORY",
           "evidence_class": "PUBLIC-DATA-DERIVED",
           "provenance": {"datasets": [{"id": "D04", "accession": "t", "url": "https://example.org", "files": FILES,
                                        "sha256": dataset_digest(FILES)}],
                          "split": {"name": "FIT", "manifest_sha256": "0" * 64},
                          "fitting": {"method": "unit", "version": "1", "commit": "a" * 40, "dirty": False, "seed": 1,
                                      "timestamp_utc": "2026-10-06T00:00:00Z", "software": {"numpy": "2"}}},
           "stages": {"sequencing": seq}}
    return cm.from_doc(doc)[0]


def _tallies(model, n=1500, L=100, cov=6, seed=4):
    refs = refs_of(n, L, seed=seed)
    lay = T.Layout(L)
    M, _ = P.tally_matrix(zip(refs, S.simulate_clusters(model, refs, cov, seed)), lay)
    return lay, M, refs


# -- the rule --------------------------------------------------------------------------------------------------------
def test_hp_flag_is_a_function_of_the_centred_3mer_at_min_run_2_but_not_at_3():
    rnd = random.Random(2)
    seq = bytes(rnd.choice(b"ACGT") for _ in range(4000))
    _codes, kmer, hp = T.site_features(seq, (2, 3))
    inner = slice(1, len(seq) - 1)                      # edges use the base itself as the missing neighbour
    for mi, unique in ((0, True), (1, False)):
        flags = {}
        for k, h in zip(kmer[inner].tolist(), hp[mi][inner].tolist()):
            flags.setdefault(k, set()).add(h)
        assert all(len(v) == 1 for v in flags.values()) is unique


def test_hp_sub_fixed_only_with_a_substitution_context_and_min_run_2():
    assert est.hp_sub_fixed(est.Design(min_run=2, context=("substitution",)))
    assert not est.hp_sub_fixed(est.Design(min_run=3, context=("substitution",)))
    assert not est.hp_sub_fixed(est.Design(min_run=2, context=()))


# -- estimator --------------------------------------------------------------------------------------------------------
def test_estimate_fixes_the_multiplier_and_the_rate_is_the_effective_per_base_rate():
    lay, M, _refs = _tallies(_model(min_run=2, hp_sub=0.3, ctx=_context()))
    Tt = M.sum(axis=0).astype(float)
    d = est.Design(min_run=2, bins={"substitution": None, "insertion": None, "deletion": None}, context=("substitution",))
    out = est.estimate(lay, Tt, d)
    assert out["sequencing.homopolymer.substitution_multiplier"] == 1.0
    ctx = np.asarray(out["sequencing.context.substitution"])
    mi = lay.minruns.index(2)
    sites = lay.ctx(Tt, "ctx_sites")[mi].sum(axis=1)
    assert float((sites * ctx).sum() / sites.sum()) == pytest.approx(1.0, rel=1e-9)
    # identifiable now: the baseline at the site-weighted mean context is the observed per-base substitution rate
    obs = out["_observed"]["substitution"]
    assert out["sequencing.substitution.rate"] == pytest.approx(obs, rel=1e-6)
    # the fitted per-3-mer rates reproduce the observed ones (up to the prior shrinkage)
    ev = lay.ctx(Tt, "ctx_sub")[mi].sum(axis=1)
    ok = ev >= 200
    assert np.allclose(out["sequencing.substitution.rate"] * ctx[ok], ev[ok] / sites[ok], rtol=0.06)


def test_ipf_with_the_multiplier_fixed_still_reproduces_the_per_3mer_totals():
    lay, M, _refs = _tallies(_model(min_run=2, hp_sub=0.3, ctx=_context()), n=800)
    Tt = M.sum(axis=0).astype(float)
    mi = lay.minruns.index(2)
    Sx, O = est._tables(lay, Tt, mi)
    fm = est._fm(np.ones(4), True)
    fixed = est._ipf(Sx, O, fm, {"substitution"}, True, fixed_hp=frozenset({"substitution"}))
    assert fixed["hp"]["substitution"] == 1.0
    # the context absorbs the homopolymer effect: every 3-mer's fitted total matches its observed total up to the
    # shrinkage of CONTEXT_PRIOR pseudo-events (which lifts sparse 3-mers and, the grand total being exact, lowers the
    # densest ones by a few per cent)
    mu, obs = fixed["mu"]["substitution"].sum(axis=1), O["substitution"].sum(axis=1)
    assert np.all(np.abs(mu - obs) <= 0.1 * obs + 2 * est.CONTEXT_PRIOR)
    assert fixed["mu"]["substitution"].sum() == pytest.approx(O["substitution"].sum(), rel=1e-6)


def test_min_run_3_keeps_the_multiplier_free():
    lay, M, _refs = _tallies(_model(min_run=3, hp_sub=0.5, ctx=_context()), n=800)
    d = est.Design(min_run=3, bins={"substitution": None, "insertion": None, "deletion": None}, context=("substitution",))
    out = est.estimate(lay, M.sum(axis=0).astype(float), d)
    assert out["sequencing.homopolymer.substitution_multiplier"] != 1.0


# -- calibration --------------------------------------------------------------------------------------------------------
def test_context_renormalisation_is_site_weighted_and_keeps_every_site_rate():
    rng = np.random.default_rng(1)
    ctx = rng.uniform(0.2, 3.0, 64)
    so, si = rng.integers(0, 500, 64).astype(float), rng.integers(0, 200, 64).astype(float)
    values = {"sequencing.context.substitution": ctx.tolist(), "sequencing.substitution.rate": 0.01,
              "sequencing.homopolymer.substitution_multiplier": 0.7}
    c = C.renormalise_context(values, so, si)
    new = np.asarray(values["sequencing.context.substitution"])
    w = so + 0.7 * si
    assert float((w * new).sum() / w.sum()) == pytest.approx(1.0, rel=1e-12)
    assert np.allclose(values["sequencing.substitution.rate"] * new, 0.01 * ctx, rtol=1e-12)
    assert c == pytest.approx(float((w * ctx).sum() / w.sum()))
    assert not np.isclose(np.mean(new), 1.0)          # not the unweighted mean


def _fake_stats(**over):
    st = {"sub": 0.01, "ins": 0.01, "start": 0.01, "f_del": 0.015, "del_mean": 1.5, "ins_mean": 1.3, "hp_indel": 1.2,
          "hp_sub": 1.1, "ctx_sub": np.full(64, 0.01), "ctx_sub_events": np.full(64, 100.0), "ctx_sites_out": np.full(64, 1e4),
          "ctx_sites_in": np.full(64, 1e3), "profile": {"substitution": None, "insertion": None, "deletion": None},
          "ed_mean": 3.0, "ed_var": 5.0}
    st.update(over)
    return st


def _run_calibration(monkeypatch, design, sim_stats, iterations=4):
    target = _fake_stats()
    real_T = np.zeros(4)
    monkeypatch.setattr(C, "_sim_model", lambda *a, **k: None)
    monkeypatch.setattr(C, "simulate_clusters", lambda model, refs, cov, seed: [[] for _ in refs])
    monkeypatch.setattr(C, "tally_matrix", lambda pairs, layout, **k: (np.zeros((1, 4)), None))
    monkeypatch.setattr(C, "summary", lambda layout, Tm, d: target if Tm is real_T else sim_stats)
    values = {"sequencing.substitution.rate": 0.01, "sequencing.insertion.rate": 0.01, "sequencing.deletion.rate": 0.01,
              "sequencing.deletion.run_length.mean": 1.5, "sequencing.insertion.run_length.mean": 1.3,
              "sequencing.homopolymer.indel_multiplier": 1.0, "sequencing.homopolymer.substitution_multiplier": 1.0,
              "sequencing.context.substitution": None}
    fit = {"design": design, "values": values, "ci95": {}}
    return C.calibrate(fit, None, ["r"], real_T=real_T, seed=1, iterations=iterations)


def test_calibration_does_not_stop_while_a_run_length_mean_is_off(monkeypatch):
    d = est.Design(min_run=3, del_geometric=True, ins_geometric=True)
    for key, off in (("del_mean", 1.2), ("ins_mean", 1.15)):
        cal = _run_calibration(monkeypatch, d, _fake_stats(**{key: off}))
        assert len(cal["trace"]) == 4, key                 # the other ratios are 1: only the run length is off
        assert key in cal["final_residual_ratios"]
        assert cal["final_residual_ratios"][key] == pytest.approx(_fake_stats()[key] / off)
    cal = _run_calibration(monkeypatch, d, _fake_stats())
    assert len(cal["trace"]) == 1
    assert cal["final_residual_ratios"]["del_mean"] == 1.0 and cal["final_residual_ratios"]["ins_mean"] == 1.0


def test_calibration_convergence_ignores_quantities_it_does_not_update(monkeypatch):
    d = est.Design(min_run=2, context=("substitution",), del_geometric=False, ins_geometric=False)
    cal = _run_calibration(monkeypatch, d, _fake_stats(del_mean=1.2, ins_mean=1.15, hp_sub=0.5))
    assert len(cal["trace"]) == 1                          # run lengths not geometric, hp_sub fixed: nothing to update
    assert cal["values"]["sequencing.homopolymer.substitution_multiplier"] == 1.0
    assert {"del_mean", "ins_mean", "hp_sub"} <= set(cal["final_residual_ratios"])
    assert C.calibrated_keys(d) == ["sub", "ins", "start", "hp_indel"]


def test_calibrated_fit_with_context_and_min_run_2_keeps_the_multiplier_and_labels_it(monkeypatch):
    lay, M, refs = _tallies(_model(min_run=2, hp_sub=0.3, ctx=_context()), n=1200)
    d = est.Design(min_run=2, bins={"substitution": None, "insertion": None, "deletion": None}, context=("substitution",))
    fit = F.fit_tallies(M, lay, seed=3, bootstrap=10, with_coverage=False, design=d,
                        calibration=dict(refs=refs[:800], coverage=6, iterations=2))
    v = fit["values"]
    assert v["sequencing.homopolymer.substitution_multiplier"] == 1.0
    Tt = M.sum(axis=0).astype(float)
    sites = T.Layout.ctx(lay, Tt, "ctx_sites")[lay.minruns.index(2)].sum(axis=1)
    ctx = np.asarray(v["sequencing.context.substitution"])
    assert float((sites * ctx).sum() / sites.sum()) == pytest.approx(1.0, rel=1e-9)
    assert "del_mean" in fit["calibration"]["final_residual_ratios"] and "ins_mean" in fit["calibration"]["final_residual_ratios"]
    doc = MO.build(fit, name="unit-fit", version="1.0.0", model_id="unit-F", description="unit", note="SIMULATED input",
                   datasets=[{"id": "X", "accession": "unit:x", "url": "https://example.org", "files": [{"name": "f", "sha256": "1" * 64}]}],
                   split={"name": "FIT", "manifest_sha256": "2" * 64},
                   fitting={"method": "unit", "version": "1", "commit": "a" * 40, "dirty": False, "seed": 3,
                            "timestamp_utc": "2026-10-06T00:00:00Z", "software": {"numpy": np.__version__}})
    p = doc["parameters"]
    assert p["sequencing.homopolymer.substitution_multiplier"]["basis"] == "assumed"
    assert p["sequencing.substitution.rate"]["basis"] == "estimated"


# -- committed models (relabelled, not refit) ----------------------------------------------------------------------------
def test_committed_models_with_a_confounded_rate_are_labelled_estimated_with_the_effective_rate():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    # every recorded relabel (fit-d03 2026-10-06: the a2 models; fit-nano/d03 2026-10-07: the a7b DEV-look-1 models)
    files = sorted(root.glob("experiments/v7/fit-*/**/RELABEL-*.json"))
    assert len(files) == 2, files
    relabel: dict = {"models": {}}
    for f in files:
        relabel["models"].update(json.loads(f.read_text())["models"])
    seen = []
    for p in sorted(root.glob("experiments/v7/fit-*/**/models/*.json")):
        m = cm.from_doc(json.loads(p.read_text()))[0]
        seq = m.stages["sequencing"]
        if not ((seq.get("context") or {}).get("substitution") is not None and seq["homopolymer"]["min_run"] <= 2):
            continue
        rel = str(p.relative_to(root))
        seen.append(rel)
        assert m.doc["parameters"]["sequencing.substitution.rate"]["basis"] == "estimated", rel
        ms = m.doc["fit_report"]["measured_statistics"]
        eff = ms["substitution_rate_effective_per_base"]
        assert eff["value"] == ms["observed_per_base_rates"]["substitution"] < seq["substitution"]["rate"]
        assert relabel["models"][rel]["sha256_after"] == m.sha256
    assert sorted(seen) == sorted(relabel["models"]) and len(seen) == 6


def test_fit_driver_records_the_effective_rate_of_a_confounded_design():
    """model_out.confounded_substitution: the measured per-base rate for designs with an unidentifiable rate, else None."""
    fit = {"values": {"_observed": {"substitution": 0.02}}, "ci95": {"_observed": {"substitution": [0.019, 0.021]}}}
    assert MO.confounded_substitution({**fit, "design": est.Design(min_run=2, context=("substitution",))}) == \
        {"value": 0.02, "ci95": [0.019, 0.021]}
    assert MO.confounded_substitution({**fit, "design": est.Design(min_run=3, context=("substitution",))}) is None
    assert MO.confounded_substitution({**fit, "design": est.Design(min_run=2)}) is None
    assert "substitution_rate_effective_per_base" in MO.CONFOUNDED_SUBSTITUTION_NOTE
