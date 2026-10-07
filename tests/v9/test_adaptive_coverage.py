"""V9 adaptive computational coverage: the row-margin counter and the pre-registered stopping/tuning rule."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from vnxdna.recovery.stagecount import StageCounters

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("v9cov", ROOT / "experiments" / "v9" / "coverage" / "run.py")
cov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cov)


def test_margins_are_the_smallest_row_margin_per_level():
    sc = StageCounters()
    assert sc.margins() is None and sc.block(status="SUCCESS")["outer_ecc_margins"] is None
    sc.row(0, {"k": 10, "verified_pass1": 8, "from_consensus": 1, "from_cluster": 3})
    sc.row(1, {"k": 10, "verified_pass1": 11, "from_consensus": 0})
    assert sc.margins() == {"rows": 2, "pass1": -2, "consensus": -1, "all": 1}


def _traj(*steps):
    return {"trajectory": [{"batch": i + 1, "reads": 100 * (i + 1), "outcome": o, "margins": m} for i, (o, m) in enumerate(steps)]}


def test_rule_and_tuning():
    m = lambda a: {"pass1": a - 2, "consensus": a - 1, "all": a, "rows": 1}  # noqa: E731
    assert not cov.fires(None, "all", 0) and cov.fires(m(0), "all", 0) and not cov.fires(m(0), "consensus", 0)
    rows = [_traj(("EXPLICIT_FAILURE", None), ("EXPLICIT_FAILURE", m(0)), ("EXACT", m(1)), ("EXACT", m(3))),
            _traj(("EXPLICIT_FAILURE", m(-3)), ("EXACT", m(0)), ("EXACT", m(2)), ("EXACT", m(4)))]
    res = cov.tune(rows)
    t = {(x["level"], x["m"]): x for x in res["table"]}
    assert t[("all", 0)]["false_terminations"] == 1           # row 1 stops at batch 2 (not EXACT) although the pool is
    assert t[("all", 1)]["false_terminations"] == 0
    # (all,1), (consensus,0) and (consensus,1) all stop both rows at batch 3; ties go to the larger m, then to the more
    # conservative level (table order pass1 < consensus < all)
    assert res["chosen"] == {"level": "consensus", "m": 1, "false_terminations": 0, "mean_reads": 300.0}
