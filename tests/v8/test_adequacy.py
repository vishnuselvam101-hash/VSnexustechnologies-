"""V8.5 adequacy harness (src/vnxdna/simulation/fit/adequacy.py): identifiable substitution product, stratified metric
table, and the M10-V8 round trip (replicate SD, sparse-bin rule). SIMULATED data only."""
from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("edlib")
from vnxdna.simulation import model as cm, model2                       # noqa: E402
from vnxdna.simulation.fit import adequacy as AQ, tally as T, validate as V   # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PRE = ROOT / "experiments/v7/fit-nano/d03-a7c/precheck/d03-hac-fwd.precheck.json"


def test_substitution_product_is_invariant_to_the_confounded_split():
    rnd = np.random.default_rng(1)
    ctx = rnd.uniform(0.5, 1.5, 64)
    mat = rnd.dirichlet(np.ones(4), 4)
    v = {"sequencing.substitution.rate": 0.02, "sequencing.context.substitution": ctx.tolist(),
         "sequencing.substitution.from_multipliers": [1.0] * 4, "sequencing.substitution.matrix": mat.tolist()}
    p = AQ.substitution_product(v)
    assert p.shape == (64, 4) and p.sum(axis=1) == pytest.approx(0.02 * ctx)
    w = dict(v, **{"sequencing.substitution.rate": 0.04, "sequencing.context.substitution": (ctx / 2).tolist()})
    assert AQ.substitution_product(w) == pytest.approx(p)                  # the identifiable part is unchanged
    assert AQ.substitution_product({k: x for k, x in v.items() if "context" not in k}) is None


def test_metric_table_has_one_row_per_stratum():
    rep = json.loads(PRE.read_text())["metrics_full"]
    rows = AQ.metric_table(rep, sample_counts={"reads": 1000, "bases": 100000})
    by = {}
    for r in rows:
        by.setdefault(r["metric"], []).append(r)
    assert len(by["M1"]) == 3 and len(by["M6r"]) == 6 and len(by["M3"]) == 3
    assert {r["stratum"] for r in by["M6r"]} == {f"run length {x}" for x in ("1", "2", "3", "4", "5", "6+")}
    m3 = {r["stratum"]: r for r in by["M3"]}
    assert m3["abs_drift_le_0"]["pass"] is False and m3["abs_drift_le_3"]["pass"] is True     # the stratum failure is visible
    for r in rows:
        assert set(r) >= {"observed", "model", "abs_error", "rel_error", "uncertainty", "n", "pass", "identifiability"}
        if r["observed"] is not None and r["model"] is not None:
            assert r["abs_error"] == pytest.approx(abs(r["model"] - r["observed"]))
    assert all(r["gating"] for r in rows if r["metric"] in ("M1", "M3", "M6r"))


def _model():
    pmf = [0.7, 0.2, 0.07, 0.025, 0.0045, 0.0003, 0.0001, 0.0001]
    s = sum(pmf)
    pmf = [x / s for x in pmf]
    seq = {"substitution": {"rate": 0.01}, "coverage": {"model": "fixed", "mean": 1}, "insertion": {"rate": 0.006},
           "deletion": {"rate": 0.008, "run_length": model2.empirical_run_length(pmf, 9.0)}}
    m = cm.from_doc({"schema": cm.SCHEMA_V2, "name": "t", "version": "1.0.0", "stages": {"sequencing": seq}})[0]
    params = {"sequencing.substitution.rate": {"value": 0.01, "ci95": None, "basis": "synthetic"},
              "sequencing.deletion.run_length.pmf": {"value": pmf, "ci95": None, "basis": "synthetic"},
              "sequencing.deletion.run_length.tail_mean": {"value": 9.0, "ci95": None, "basis": "synthetic"}}
    return cm.from_doc(dict(m.doc, parameters=params))[0]


def test_m10_v8_reports_replicates_and_does_not_gate_sparse_bins():
    rnd = random.Random(2)
    refs = [bytes(rnd.choice(b"ACGT") for _ in range(60)) for _ in range(250)]
    out = AQ.m10_v8(_model(), T.Layout(60, minruns=(2, 3, 4, 5, 6)), refs, coverage=4, seed=5, replicates=2, bootstrap=20)
    assert out["replicates"] == 2 and out["seeds"] == [5, 1005] and "SD_rep" in out["rule"]
    pmf = out["parameters"]["sequencing.deletion.run_length.pmf"]
    assert pmf["n"] == 8 and pmf["gated"] == 5 and "not gated" in pmf["note"]           # 3 bins below 1e-3
    tail = out["parameters"]["sequencing.deletion.run_length.tail_mean"]
    assert tail["gated"] == 0 and "fewer than" in tail["note"]                          # far fewer than 200 runs >= 8 here
    assert isinstance(out["parameters"]["sequencing.substitution.rate"]["pass"], bool)
    assert V.GATING_7B[-1] == "M10"
