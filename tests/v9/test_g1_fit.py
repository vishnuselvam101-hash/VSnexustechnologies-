"""V9 G1 fitter: the Poisson-mixture EM recovers simulated classes, BIC prefers the true K, multipliers keep the mean."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def g1():
    pytest.importorskip("edlib")
    sys.path.insert(0, str(ROOT / "experiments" / "v8" / "d13"))
    spec = importlib.util.spec_from_file_location("v9g1", ROOT / "experiments" / "v9" / "d13" / "g1.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_em_recovers_two_classes(g1):
    rng = np.random.default_rng(5)
    n = 20000
    z = rng.random(n) < 0.7
    lam = np.where(z[:, None], [[2.0, 1.5, 2.5]], [[8.0, 3.0, 9.0]])
    X = rng.poisson(lam)
    sols = {k: g1.em(X, k) for k in (1, 2, 3)}
    assert min(sols, key=lambda k: sols[k]["bic"]) == 2
    s = sols[2]
    assert abs(s["weights"][0] - 0.7) < 0.03
    assert np.allclose(s["rates"], [[2.0, 1.5, 2.5], [8.0, 3.0, 9.0]], rtol=0.06)
    assert g1.em(X, 2) == s                                   # deterministic
    st = g1.multipliers(s)
    for t in ("sub", "ins", "del"):
        assert abs(sum(x["weight"] * x[t] for x in st) - 1.0) < 1e-9


def test_per_read_counts(g1):
    ref = b"ACGTACGTAC" * 10
    read = ref[:20] + b"T" + ref[21:50] + ref[52:]           # one substitution (or none if equal) + a 2-base deletion
    X = g1.per_read_counts([(ref, [ref, read, b"A" * 100], None)], len(ref))
    assert X[0].tolist() == [0, 0, 0, 0]
    assert X[1, 2] == 1 and X[1, 3] >= 2
    assert X.shape[0] == 2                                     # the junk read exceeds 0.30 L edits and is dropped
