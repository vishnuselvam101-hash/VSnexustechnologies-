"""Shared statistics for VNX-DNA experiments and benchmarks (V10 W6).

One implementation of the methods the experiment runners report, so every V10 table cites the same method:

* :func:`wilson` -- Wilson score interval for a binomial proportion;
* :func:`mcnemar_exact` -- two-sided exact McNemar test (binomial test of the discordant pairs at p = 1/2);
* :func:`holm` -- Holm step-down adjustment of a family of p-values;
* :func:`paired_ratio` -- paired speed-up: geometric mean of per-pair time ratios with a percentile-bootstrap CI;
* :func:`bootstrap_median` -- percentile-bootstrap CI of a median;
* :func:`host_load` / :func:`load_ok` -- the host load recorded with every timing, and the rule that refuses a timing
  comparison when the host was busier than ``MAX_LOAD1`` during any of its runs;
* :func:`cache_key` -- the key results are cached under: commit + corpus + seed + parameters.

The V8/V9 experiment scripts keep their own copies (their committed results were produced with them);
``tests/v10/test_stats.py`` checks that they agree with this module.
"""
from __future__ import annotations

import hashlib
import json
import math
import os

import numpy as np

Z95 = 1.959963984540054          # standard normal 0.975 quantile
MAX_LOAD1 = 4.0                   # lab host 4C/8T: refuse timing comparisons when the 1-min load exceeded the core count
BOOTSTRAP = 10_000


def wilson(k: int, n: int, z: float = Z95, digits: int | None = 4) -> list[float] | None:
    """Wilson score interval [lo, hi] for k successes in n trials (None when n == 0), rounded to ``digits``."""
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n, got k={k}, n={n}")
    if n == 0:
        return None
    p, d = k / n, 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    lo, hi = max(0.0, c - h), min(1.0, c + h)
    return [round(lo, digits), round(hi, digits)] if digits is not None else [lo, hi]


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for b and c discordant pairs (1.0 when there are none)."""
    if b < 0 or c < 0:
        raise ValueError("discordant counts must be >= 0")
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def holm(ps: dict) -> dict:
    """Holm-adjusted p-values (same keys); monotone in the sorted order, capped at 1. Ties keep insertion order."""
    order = sorted(ps, key=lambda k: ps[k])
    m, out, run = len(order), {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (m - i) * ps[k]))
        out[k] = run
    return out


def paired_ratio(base_seconds, new_seconds, *, seed: int, resamples: int = BOOTSTRAP, digits: int = 3) -> dict:
    """Speed-up base/new over paired runs: geometric mean of the per-pair ratios and its 95 % percentile-bootstrap CI
    (resampling pairs with ``numpy.random.default_rng(seed)``)."""
    a, b = np.asarray(base_seconds, dtype=float), np.asarray(new_seconds, dtype=float)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError("base and new must be paired 1-D sequences")
    if a.size == 0:
        return {"pairs": 0, "geomean": None, "ci95": None, "method": _ratio_method(seed, resamples)}
    if np.any(a <= 0) or np.any(b <= 0):
        raise ValueError("times must be > 0")
    logr = np.log(a / b)
    rng = np.random.default_rng(seed)
    boot = np.exp(rng.choice(logr, size=(resamples, logr.size), replace=True).mean(axis=1))
    return {"pairs": int(a.size), "geomean": round(float(np.exp(logr.mean())), digits),
            "ci95": [round(float(np.percentile(boot, 2.5)), digits), round(float(np.percentile(boot, 97.5)), digits)],
            "method": _ratio_method(seed, resamples)}


def _ratio_method(seed: int, resamples: int) -> str:
    return f"paired per-seed ratio, geometric mean, percentile bootstrap {resamples}, rng {seed}"


def bootstrap_median(values, *, seed: int, resamples: int = BOOTSTRAP, digits: int = 3) -> dict:
    """Median and its 95 % percentile-bootstrap CI (``numpy.random.default_rng(seed)``)."""
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return {"n": 0, "median": None, "ci95": None}
    rng = np.random.default_rng(seed)
    boot = np.median(rng.choice(v, size=(resamples, v.size), replace=True), axis=1)
    return {"n": int(v.size), "median": round(float(np.median(v)), digits),
            "ci95": [round(float(np.percentile(boot, 2.5)), digits), round(float(np.percentile(boot, 97.5)), digits)],
            "method": f"percentile bootstrap {resamples}, rng {seed}"}


def host_load() -> float:
    """The 1-minute load average (recorded with every timing)."""
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return float("nan")


def load_ok(loads, max_load1: float = MAX_LOAD1) -> dict:
    """Whether a timing comparison may be reported: every run's recorded load is known and at most ``max_load1``."""
    vals = [float(x) for x in loads]
    bad = [x for x in vals if not (x <= max_load1)]       # NaN counts as not known
    return {"ok": not bad and bool(vals), "max_load1": max_load1, "observed": [min(vals), max(vals)] if vals else None,
            "runs_over": len(bad)}


def cache_key(commit: str, corpus: str, seed: int, params: dict) -> str:
    """Result cache key: commit + corpus + seed + parameters (canonical JSON), sha256 hex."""
    blob = json.dumps({"commit": commit, "corpus": corpus, "seed": int(seed), "params": params}, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()
