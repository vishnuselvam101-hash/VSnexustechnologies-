"""FIT-only pre-check of a fitted channel model before the last DEV look (V7 7.4; experiments/v7/fit-nano/d03/
CHANGE-HP-BY-LENGTH.md section 3).

The metrics of pre-registration 7B (+ amendment A1) are computed *in sample*: the real side is the FIT data the model was
fitted on, compared with reads simulated from the model by the same ``validate`` code. An in-sample pass is optimistic and
is never reported as validation; an in-sample failure is evidence that the model cannot pass on DEV. The decision rule:

* ``DEV_LOOK_2_JUSTIFIED`` only if every gating metric of 7B (``validate.GATING_7B``) passes on the full FIT data;
* else ``DEV_LOOK_2_BLOCKED — FIT PRE-CHECK INADEQUATE``.

To tell a stable failure from sampling noise, each metric is also evaluated against each of two disjoint halves of the FIT
references (``halves``) with the same model: ``FAIL_STABLE`` fails on the full data and on both halves, ``FAIL_NOISE_PLAUSIBLE``
fails on the full data but passes on at least one half (classification only; the decision uses the full data).

This module only computes on arrays it is given; the experiment driver (experiments/v7/fit/run.py ``precheck``) is the only
place that reads data and it requests the FIT split only."""
from __future__ import annotations

import numpy as np

from vnxdna.simulation.fit import estimate as est
from vnxdna.simulation.fit import fit as F
from vnxdna.simulation.fit import validate as V
from vnxdna.simulation.fit.tally import Layout

DECISION_JUSTIFIED = "DEV_LOOK_2_JUSTIFIED"
DECISION_BLOCKED = "DEV_LOOK_2_BLOCKED — FIT PRE-CHECK INADEQUATE"
RUN_LENGTHS = ("1", "2", "3", "4", "5", "6+")
#: the metrics the pre-check reports (7B gating set; M6 is reported as its 7B form M6r)
REPORTED = V.GATING_7B


def halves(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Two disjoint, sorted index sets covering 0..n-1 (sizes differ by at most 1); a deterministic function of the seed."""
    perm = np.random.default_rng([seed, 2]).permutation(n)
    return np.sort(perm[: n // 2]), np.sort(perm[n // 2:])


def run_length_table(layout: Layout, M: np.ndarray, *, seed: int, B: int = 200, min_sites: int = V.M6_MIN_SITES) -> list:
    """Per homopolymer run length 1..5, 6+: sites, insertion and deletion events, indel rate with its bootstrap SE and 95 %
    interval (references resampled), and whether the bin is sparse (< ``min_sites`` sites, not tested by M6r) or empty."""
    T = M.sum(axis=0).astype(float)

    def per_class(t):
        s = est.length_classes(layout, "ctx_sites", t).sum(axis=1)
        i = est.length_classes(layout, "ctx_ins", t).sum(axis=1)
        d = est.length_classes(layout, "ctx_del", t).sum(axis=1)
        return s, i, d
    s, i, d = per_class(T)
    tot = F.bootstrap_totals(M, F.bootstrap_weights(M.shape[0], B, seed))
    reps = np.array([(lambda x: (x[1] + x[2]) / np.maximum(x[0], 1))(per_class(tot[b])) for b in range(B)])
    rows = []
    for c, name in enumerate(RUN_LENGTHS):
        rate = float((i[c] + d[c]) / s[c]) if s[c] > 0 else None
        rows.append({"run_length": name, "sites": int(s[c]), "insertion_events": int(i[c]), "deletion_events": int(d[c]),
                     "indel_rate": rate, "bootstrap_se": float(reps[:, c].std(ddof=1)) if B > 1 else None,
                     "ci95": [float(np.percentile(reps[:, c], 2.5)), float(np.percentile(reps[:, c], 97.5))],
                     "sparse": bool(s[c] < min_sites), "zero_sites": bool(s[c] == 0),
                     "zero_events": bool(i[c] + d[c] == 0)})
    return rows


def classify(full: bool | None, half: list) -> str:
    """Stability class of one metric from its full-data pass flag and its pass flags on the two halves."""
    if full is None:
        return "NOT_EVALUATED"
    if full:
        return "PASS" if all(h is not False for h in half) else "PASS_UNSTABLE"
    return "FAIL_STABLE" if half and all(h is False for h in half) else "FAIL_NOISE_PLAUSIBLE"


def stability(full: dict, half_reports: list, metrics=REPORTED) -> dict:
    """Per metric: full-data pass, per-half pass and the stability class (``classify``)."""
    out = {}
    for m in metrics:
        f = full.get(m, {}).get("pass") if isinstance(full.get(m), dict) else None
        h = [r.get(m, {}).get("pass") if isinstance(r.get(m), dict) else None for r in half_reports]
        out[m] = {"full": f, "halves": h, "class": classify(f, [x for x in h if x is not None])}
    return out


def _vec(v) -> np.ndarray | None:
    if v is None or isinstance(v, (str, bool)):
        return None
    try:
        a = np.asarray(v, dtype=float).ravel()
    except (TypeError, ValueError):
        return None
    return a if a.size else None


def parameter_stability(full: dict, half_values: list, ci: dict) -> dict:
    """Per fitted sequencing parameter: the largest relative difference between the two half-fits (elementwise, over
    elements whose full value is not ~0), the share of half-fit elements inside the full fit's bootstrap 95 % interval, and the
    largest relative width of that interval."""
    out = {}
    for path, v in sorted(full.items()):
        if path.startswith("_") or not path.startswith("sequencing."):
            continue
        a = _vec(v)
        got = [_vec(h.get(path)) for h in half_values]
        hs = [h for h in got if h is not None and a is not None and h.size == a.size]
        if a is None or len(hs) != len(got):
            continue
        big = np.abs(a) > 1e-9
        row: dict = {"n": int(a.size)}
        if len(hs) == 2 and big.any():
            row["max_rel_diff_halves"] = float(np.max(np.abs(hs[0][big] - hs[1][big]) / np.abs(a[big])))
        c = ci.get(path)
        if c is not None:
            lo, hi = (_vec(c["lo"]), _vec(c["hi"])) if isinstance(c, dict) else (_vec(c[0]), _vec(c[1]))
            if lo is not None and hi is not None and lo.size == a.size:
                row["ci95_max_rel_width"] = float(np.max((hi[big] - lo[big]) / np.abs(a[big]))) if big.any() else 0.0
                row["halves_inside_full_ci95"] = float(np.mean([np.mean((h >= lo - 1e-12) & (h <= hi + 1e-12)) for h in hs]))
        out[path] = row
    return out


def decide(full: dict, gating=V.GATING_7B) -> dict:
    """The pre-check decision from the full-data (in-sample) report: justified only if every gating metric passes."""
    verdict, failed = V.adequacy(full, gating)
    ok = verdict == "ADEQUATE"
    return {"decision": DECISION_JUSTIFIED if ok else DECISION_BLOCKED, "in_sample_verdict": verdict,
            "failed_gating_metrics": failed,
            "missing_gating_metrics": [m for m in gating if not isinstance(full.get(m), dict) or full[m].get("pass") is None],
            "rule": "DEV look 2 only if every 7B gating metric passes in sample on FIT (an in-sample pass is not validation)"}
