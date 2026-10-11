"""V10 W6: the shared statistics module (vnxdna.benchmark.stats) against known values and the V8/V9 copies."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

from vnxdna.benchmark import stats as S

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str):
    spec = importlib.util.spec_from_file_location(rel.replace("/", "_"), ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wilson_known_values():
    # Wilson (1927) / Newcombe (1998) table 1: 81/263 -> [0.2553, 0.3662]; 15/148 -> [0.0624, 0.1605]; 0/20 -> [0, 0.1611]
    assert S.wilson(81, 263) == [0.2553, 0.3662]
    assert S.wilson(15, 148) == [0.0624, 0.1605]
    assert S.wilson(0, 20) == [0.0, 0.1611]
    assert S.wilson(20, 20) == [0.8389, 1.0]
    assert S.wilson(0, 0) is None
    lo, hi = S.wilson(5, 10, digits=None)
    assert math.isclose(lo + hi, 1.0) and 0.2365 < lo < 0.2366
    with pytest.raises(ValueError):
        S.wilson(3, 2)


def test_mcnemar_known_values():
    assert S.mcnemar_exact(0, 0) == 1.0
    assert S.mcnemar_exact(0, 5) == 0.0625              # 2 * (1/2)^5
    assert S.mcnemar_exact(5, 0) == 0.0625
    assert math.isclose(S.mcnemar_exact(1, 9), 2 * 11 / 1024)
    assert S.mcnemar_exact(10, 10) == 1.0
    assert math.isclose(S.mcnemar_exact(2, 12), 2 * (1 + 14 + 91) / 2 ** 14)


def test_holm_known_values():
    out = S.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert out == pytest.approx({"a": 0.03, "c": 0.06, "b": 0.06})
    assert S.holm({"x": 0.6, "y": 0.9}) == {"x": 1.0, "y": 1.0}
    assert S.holm({}) == {}


def test_paired_ratio():
    r = S.paired_ratio([2.0, 4.0, 8.0], [1.0, 2.0, 4.0], seed=1)
    assert r["geomean"] == 2.0 and r["ci95"] == [2.0, 2.0] and r["pairs"] == 3
    r = S.paired_ratio([1.0, 4.0], [1.0, 1.0], seed=3)
    assert r["geomean"] == 2.0 and 1.0 <= r["ci95"][0] <= r["ci95"][1] <= 4.0
    assert S.paired_ratio([], [], seed=1)["geomean"] is None
    with pytest.raises(ValueError):
        S.paired_ratio([1.0], [0.0], seed=1)
    with pytest.raises(ValueError):
        S.paired_ratio([1.0, 2.0], [1.0], seed=1)


def test_paired_ratio_reproduces_the_v10_w1_summary():
    import json
    rows = [json.loads(x) for x in (ROOT / "experiments/v10/w1/results/w1.jsonl").read_text().splitlines()]
    by = {}
    for r in rows:
        by.setdefault(r["seed"], {})[r["config"]] = r["decode_seconds"]
    seeds = sorted(by)
    got = S.paired_ratio([by[s]["v9"] for s in seeds], [by[s]["w1"] for s in seeds], seed=10_001)
    want = json.loads((ROOT / "experiments/v10/w1/results/summary.json").read_text())["speedup_v9_over_w1"]
    assert {k: got[k] for k in ("geomean", "ci95", "method")} == want


def test_bootstrap_median():
    r = S.bootstrap_median([3.0, 1.0, 2.0], seed=0)
    assert r["median"] == 2.0 and 1.0 <= r["ci95"][0] <= r["ci95"][1] <= 3.0
    assert S.bootstrap_median([], seed=0)["median"] is None


def test_load_rule_and_cache_key():
    assert S.load_ok([0.5, 3.9])["ok"] and not S.load_ok([0.5, 4.1])["ok"]
    assert not S.load_ok([float("nan")])["ok"] and not S.load_ok([])["ok"]
    assert S.load_ok([5.0], max_load1=6)["ok"]
    k = S.cache_key("abc", "d13-f1", 1, {"b": 1, "a": [1, 2]})
    assert k == S.cache_key("abc", "d13-f1", 1, {"a": [1, 2], "b": 1}) and len(k) == 64
    assert k != S.cache_key("abd", "d13-f1", 1, {"a": [1, 2], "b": 1})
    assert k != S.cache_key("abc", "d13-f1", 2, {"a": [1, 2], "b": 1})
    assert isinstance(S.host_load(), float)


@pytest.mark.parametrize("rel", ["experiments/v9/consensus/evaluate.py", "experiments/v9/oracle/run.py",
                                 "experiments/v8/matrix/run.py", "experiments/v8/coverage/envelope.py"])
def test_the_v8_v9_copies_agree(rel):
    """The frozen copies give the same 4-digit intervals (z differs only beyond the 6th digit)."""
    try:
        mod = _load(rel)
    except Exception as error:                     # a script whose imports need its experiment directory
        pytest.skip(f"cannot import {rel}: {error}")
    for n in (1, 2, 7, 30, 100, 600):
        for k in sorted({0, 1, n // 3, n // 2, n - 1, n}):
            got = mod.wilson(k, n)
            assert [round(x, 4) for x in got] == S.wilson(k, n), (rel, k, n)
    if hasattr(mod, "mcnemar_exact"):
        for b in range(0, 12):
            for c in range(0, 12):
                assert mod.mcnemar_exact(b, c) == S.mcnemar_exact(b, c)
        ps = {"a": 0.01, "b": 0.2, "c": 0.03, "d": 0.03}
        assert mod.holm(ps) == S.holm(ps)


def test_validate_wilson_copy_agrees_to_four_digits_at_its_z():
    from vnxdna.simulation.fit.validate import wilson as vw
    for n in (1, 5, 50, 500):
        for k in (0, 1, n // 2, n):
            assert [round(x, 4) for x in vw(k, n)] == S.wilson(k, n, z=1.96)
    assert np.isfinite(S.Z95)
