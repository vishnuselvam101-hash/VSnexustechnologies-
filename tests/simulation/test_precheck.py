"""FIT-only pre-check before DEV look 2 (V7 7.4; src/vnxdna/simulation/fit/precheck.py and run.py ``precheck``): halves,
per-run-length table with bootstrap intervals and sparse bins, stability classes, parameter stability, the decision rule, the
data firewall of the driver and the DEV-look-2 gate. SIMULATED data only."""
from __future__ import annotations

import ast
import inspect
import json
import random
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("edlib")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments/v7/fit"))
import run as R                                                                          # noqa: E402
from vnxdna.simulation import model as cm                                                # noqa: E402
from vnxdna.simulation.fit import pipeline as P, precheck as PC, simulate as S, tally as T, validate as V  # noqa: E402

LAY = T.Layout(60, minruns=(2, 3, 4, 5, 6))


def _model(by_length=None):
    hp = {"min_run": 3, "indel_multiplier": 1.0, "substitution_multiplier": 1.0}
    if by_length is not None:
        hp["indel_by_length"] = by_length
    seq = {"substitution": {"rate": 0.01}, "coverage": {"model": "fixed", "mean": 1}, "insertion": {"rate": 0.006},
           "deletion": {"rate": 0.009}, "homopolymer": hp}
    return cm.from_doc({"schema": cm.SCHEMA_V2, "name": "t", "version": "1.0.0", "stages": {"sequencing": seq}})[0]


def _refs(n, seed, runs=(1, 1, 2, 3, 4, 6)):
    """Runs of the given lengths, consecutive runs of different bases (so runs never merge into longer ones)."""
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        s, prev = b"", None
        while len(s) < LAY.L:
            b = rnd.choice([x for x in b"ACGT" if x != prev])
            s, prev = s + bytes([b]) * rnd.choice(runs), b
        out.append(s[:LAY.L])
    return out


@pytest.fixture(scope="module")
def tallied():
    refs = _refs(400, 3)
    M, _ = P.tally_matrix(zip(refs, S.simulate_clusters(_model([1.0, 1.2, 1.6, 2.0, 2.4, 2.8]), refs, 4, 5)), LAY)
    return M


def test_halves_are_disjoint_complete_and_deterministic():
    a, b = PC.halves(101, 7)
    assert len(a) + len(b) == 101 and abs(len(a) - len(b)) <= 1
    assert not set(a) & set(b) and set(a) | set(b) == set(range(101))
    assert all(np.array_equal(x, y) for x, y in zip((a, b), PC.halves(101, 7)))
    assert not np.array_equal(a, PC.halves(101, 8)[0])


def test_run_length_table_counts_rates_intervals_and_sparse_bins(tallied):
    rows = PC.run_length_table(LAY, tallied, seed=1, B=60)
    assert [r["run_length"] for r in rows] == list(PC.RUN_LENGTHS)
    T_ = tallied.sum(axis=0)
    assert sum(r["sites"] for r in rows) == int(LAY.ctx(T_, "ctx_sites")[0].sum())
    for r in rows:
        if r["sites"]:
            assert r["indel_rate"] == pytest.approx((r["insertion_events"] + r["deletion_events"]) / r["sites"])
            assert r["ci95"][0] <= r["indel_rate"] <= r["ci95"][1] and r["bootstrap_se"] > 0
        assert r["sparse"] == (r["sites"] < V.M6_MIN_SITES)
    assert rows[0]["sites"] > rows[4]["sites"] > 0                            # 5-runs are rarer than single bases here
    sparse = PC.run_length_table(LAY, tallied, seed=1, B=10, min_sites=rows[3]["sites"])
    assert [r["sparse"] for r in sparse] == [r["sites"] < rows[3]["sites"] for r in rows] and sparse[4]["sparse"]
    # agrees with the metric code: M6r's real-side rates are the same numbers
    m6 = V.m6r_homopolymer_runs(LAY, T_.astype(float), T_.astype(float), min_sites=1)
    assert [r["indel_rate"] for r in rows] == pytest.approx(m6["real"])


def test_run_length_table_flags_empty_classes():
    refs = _refs(60, 4, runs=(1, 2))                                  # no runs of 3 or longer
    M, _ = P.tally_matrix(zip(refs, S.simulate_clusters(_model(), refs, 2, 1)), LAY)
    rows = PC.run_length_table(LAY, M, seed=1, B=20)
    assert [r["zero_sites"] for r in rows] == [False, False, True, True, True, True]
    assert all(r["indel_rate"] is None and r["zero_events"] for r in rows[2:])


@pytest.mark.parametrize("full, halves, cls", [
    (True, [True, True], "PASS"), (True, [True, False], "PASS_UNSTABLE"), (False, [False, False], "FAIL_STABLE"),
    (False, [True, False], "FAIL_NOISE_PLAUSIBLE"), (False, [], "FAIL_NOISE_PLAUSIBLE"), (None, [True, True], "NOT_EVALUATED")])
def test_classify(full, halves, cls):
    assert PC.classify(full, halves) == cls


def test_stability_and_decision():
    full = {m: {"pass": True} for m in V.GATING_7B}
    assert PC.decide(full)["decision"] == PC.DECISION_JUSTIFIED
    bad = {**full, "M3": {"pass": False}}
    d = PC.decide(bad)
    assert d["decision"] == PC.DECISION_BLOCKED == "DEV_LOOK_2_BLOCKED — FIT PRE-CHECK INADEQUATE" and d["failed_gating_metrics"] == ["M3"]
    assert PC.decide({k: v for k, v in full.items() if k != "M10"})["decision"] == PC.DECISION_BLOCKED     # missing = not justified
    assert PC.decide({**full, "M6r": {"pass": None}})["missing_gating_metrics"] == ["M6r"]
    st = PC.stability(bad, [{**full, "M3": {"pass": False}}, {**full, "M3": {"pass": True}}])
    assert st["M3"]["class"] == "FAIL_NOISE_PLAUSIBLE" and st["M1"]["class"] == "PASS" and set(st) == set(PC.REPORTED)


def test_parameter_stability():
    full = {"sequencing.a": 0.02, "sequencing.v": [1.0, 2.0], "sequencing.s": "x", "_obs": 1.0, "other": 3.0}
    halves = [{"sequencing.a": 0.021, "sequencing.v": [1.0, 2.2]}, {"sequencing.a": 0.019, "sequencing.v": [1.1, 2.0]}]
    ci = {"sequencing.a": [0.0195, 0.0205], "sequencing.v": {"lo": [0.9, 1.9], "hi": [1.05, 2.1]}}
    out = PC.parameter_stability(full, halves, ci)
    assert set(out) == {"sequencing.a", "sequencing.v"}
    assert out["sequencing.a"]["max_rel_diff_halves"] == pytest.approx(0.1)
    assert out["sequencing.a"]["halves_inside_full_ci95"] == 0.0 and out["sequencing.v"]["halves_inside_full_ci95"] == 0.5
    assert out["sequencing.v"]["max_rel_diff_halves"] == pytest.approx(0.1)


# -- the driver -----------------------------------------------------------------------------------------------------------
def _strings(fn) -> set:
    return {n.value for n in ast.walk(ast.parse(inspect.getsource(fn))) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def test_precheck_driver_reads_the_fit_split_only():
    """Data firewall: the pre-check's only data request is tally_split(..., PRECHECK_SPLIT, ...) with PRECHECK_SPLIT == FIT,
    and its code names no other split and no validation artefact."""
    assert R.PRECHECK_SPLIT == "FIT"
    tree = ast.parse(inspect.getsource(R.do_precheck))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "tally_split"]
    assert len(calls) == 1 and isinstance(calls[0].args[2], ast.Name) and calls[0].args[2].id == "PRECHECK_SPLIT"
    for fn in (R.do_precheck, R._in_sample, R._brief):
        lits = _strings(fn)
        assert not lits & {"DEV", "HELDOUT", "HELD_OUT"}, fn
        assert not any(".validation.json" in x or "ACCESS_LOG" in x or "PREREG" in x for x in lits), fn
    assert "iter_d03" not in inspect.getsource(R.do_precheck) and "Guard(" in inspect.getsource(R.do_precheck)


def test_dev_look_2_gate_needs_a_justifying_precheck_and_a_freeze(tmp_path):
    m = _model([1.0] * 6)
    with pytest.raises(SystemExit, match="no FIT pre-check"):
        R.dev_look_2_gate("d03-hac-fwd", tmp_path, m)
    (tmp_path / "precheck").mkdir()
    pc = tmp_path / "precheck" / "d03-hac-fwd.precheck.json"
    pc.write_text(json.dumps({"decision": {"decision": PC.DECISION_BLOCKED, "model_sha256": m.sha256}}))
    with pytest.raises(SystemExit, match="no FIT pre-check"):
        R.dev_look_2_gate("d03-hac-fwd", tmp_path, m)
    pc.write_text(json.dumps({"decision": {"decision": PC.DECISION_JUSTIFIED, "model_sha256": "0" * 64}}))
    with pytest.raises(SystemExit, match="no FIT pre-check"):
        R.dev_look_2_gate("d03-hac-fwd", tmp_path, m)
    pc.write_text(json.dumps({"decision": {"decision": PC.DECISION_JUSTIFIED, "model_sha256": m.sha256}}))
    with pytest.raises(SystemExit, match="not frozen"):
        R.dev_look_2_gate("d03-hac-fwd", tmp_path, m)
    (tmp_path / "FREEZE.json").write_text(json.dumps({"models": {"d03-hac-fwd": {"model_sha256": m.sha256}}}))
    R.dev_look_2_gate("d03-hac-fwd", tmp_path, m)


def test_round_a7c_fits_the_per_length_multiplier_into_its_own_folder(monkeypatch):
    monkeypatch.setattr(R, "ROUND", "a7c")
    job = R.JOBS["d03-hac-fwd"]
    assert R.is_7b() and R.out_dir(job).name == "d03-a7c" and R.ROUNDS["a7c"]["version"] == "3.1.0"
    assert R.layout_for(job).minruns == (2, 3, 4, 5, 6)
    assert 'hp_by_length=ROUND == "a7c"' in inspect.getsource(R.do_fit)
    monkeypatch.setattr(R, "ROUND", "a7b")
    assert R.out_dir(job).name == "d03"
