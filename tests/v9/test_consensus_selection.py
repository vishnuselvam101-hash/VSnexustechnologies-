"""V9 consensus selection statistics: exact McNemar and Holm (the pre-registered selection rule's tests)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("v9sel", ROOT / "experiments" / "v9" / "consensus" / "evaluate.py")
sel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sel)


def test_mcnemar_exact_matches_the_binomial_test():
    assert sel.mcnemar_exact(0, 0) == 1.0
    assert abs(sel.mcnemar_exact(10, 2) - 0.03857) < 1e-4          # 2 * P(Bin(12, 1/2) <= 2)
    assert sel.mcnemar_exact(5, 5) == 1.0
    assert sel.mcnemar_exact(2, 10) == sel.mcnemar_exact(10, 2)


def test_holm_is_monotone_and_step_down():
    adj = sel.holm({"A": 0.01, "E": 0.04})
    assert adj == {"A": 0.02, "E": 0.04}
    adj = sel.holm({"A": 0.03, "E": 0.02})
    assert adj["E"] == 0.04 and adj["A"] == 0.04                    # monotone: never below the previous adjusted value


def test_wilson():
    assert sel.wilson(0, 0) is None
    lo, hi = sel.wilson(10, 10)
    assert hi == 1.0 and 0.69 < lo < 0.73
