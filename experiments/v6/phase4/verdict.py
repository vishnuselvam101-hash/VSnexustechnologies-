"""Pre-registered verdicts for P4-EXP-02 (criteria C1-C7 of experiments/v6/phase4/PREREG.md). SIMULATED.

    python experiments/v6/phase4/verdict.py --trials experiments/v6/phase4/P4-EXP-02-qw-consensus/trials.jsonl \
        --cost experiments/v6/phase4/P4-EXP-04-cost/results.json --out experiments/v6/phase4/P4-EXP-02-qw-consensus/verdict.json

Written and committed together with PREREG.md, before the comparison was run. Nothing here is tuned on its output.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase4 import mean_ci, paired_difference  # noqa: E402

BASE, CAND, W4 = "count", "quality", "quality-w4"
TIME_RATIO_MAX = 1.30
RSS_RATIO_MAX = 1.15


def load(paths):
    out = []
    for p in paths:
        for line in open(p):
            r = json.loads(line)
            if r.get("record") == "trial":
                out.append(r)
    return out


def exact(arm: dict) -> bool:
    return arm["outcome"] == "exact"


def panel_of(cell_id: str) -> str:
    return {"P": "primary", "C": "control", "R": "regression", "D": "determinism"}[cell_id.split("/")[0]]


def fits(arm: dict, vote: str) -> int:
    return int(((arm.get("consensus_quality") or {}).get(vote) or {}).get("multi", {}).get("fits", 0))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trials", nargs="+", required=True)
    ap.add_argument("--cost", help="P4-EXP-04 results.json (fresh-process peak RSS)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    trials = load(a.trials)
    by_cell = defaultdict(list)
    for t in trials:
        by_cell[t["cell"]].append(t)
    for ts in by_cell.values():
        ts.sort(key=lambda t: t["seed"])

    v: dict = {"classification": "SIMULATED", "trials": len(trials), "criteria": {}, "cells": {}}
    # C1 safety
    fs = sum(arm["false_success"] for t in trials for arm in t["arms"].values())
    v["criteria"]["C1_false_success"] = {"false_success": fs, "decodes": sum(len(t["arms"]) for t in trials),
                                         "verdict": "ACCEPT" if fs == 0 else "REJECT"}
    # per cell
    pooled = defaultdict(lambda: ([], []))
    harm = []
    for cid, ts in sorted(by_cell.items()):
        panel = panel_of(cid)
        a_ = [exact(t["arms"][BASE]) for t in ts]
        b_ = [exact(t["arms"][CAND]) for t in ts]
        pd = paired_difference(a_, b_)
        pooled[panel][0].extend(a_)
        pooled[panel][1].extend(b_)
        gain = pd["ci95_low"] > 0
        loss = pd["ci95_high"] < 0
        if loss:
            harm.append(cid)
        v["cells"][cid] = {"panel": panel, **pd, "gain_claimed": gain, "loss": loss}
    for panel, (a_, b_) in pooled.items():
        v.setdefault("pooled", {})[panel] = paired_difference(a_, b_)
    prim = v["pooled"].get("primary", {"ci95_low": 0})
    v["criteria"]["C2_pooled_primary_gain"] = {**prim, "verdict": "ACCEPT" if prim.get("ci95_low", 0) > 0 else "REJECT"}
    gains = [c for c, x in v["cells"].items() if x["gain_claimed"]]
    v["criteria"]["C3_cells_with_gain"] = {"cells": gains, "count": len(gains),
                                           "note": "per-cell 95 % intervals, not corrected for multiplicity"}
    ctrl = v["pooled"].get("control", {"ci95_high": 0})
    v["criteria"]["C4_no_harm"] = {"cells_with_loss": harm, "pooled_control": ctrl,
                                   "pooled_regression": v["pooled"].get("regression"),
                                   "verdict": "ACCEPT" if not harm and ctrl.get("ci95_high", 0) >= 0
                                   and v["pooled"].get("regression", {"ci95_high": 0}).get("ci95_high", 0) >= 0 else "REJECT"}
    # C5 mechanism: per primary trial, (multi-read attempts fitting 2e+f <= r under the weighted vote) - (under the count
    # vote), both computed by the observer on the quality arm's own pending reads
    diffs = [fits(t["arms"][CAND], "qw") - fits(t["arms"][CAND], "hard") for c, ts in by_cell.items()
             if panel_of(c) == "primary" for t in ts]
    m = mean_ci(diffs)
    v["criteria"]["C5_mechanism_fits"] = {**m, "verdict": "ACCEPT" if m.get("ci95_low", 0) > 0 else "REJECT"}
    # C6 cost
    ratios = [t["arms"][CAND]["decode_seconds"] / t["arms"][BASE]["decode_seconds"] for t in trials
              if t["arms"][BASE]["decode_seconds"] > 0]
    med = float(np.median(ratios)) if ratios else None
    c6 = {"median_decode_time_ratio": None if med is None else round(med, 3), "time_ratio_max": TIME_RATIO_MAX}
    ok = med is not None and med <= TIME_RATIO_MAX
    if a.cost:
        cost = json.loads(Path(a.cost).read_text())
        c6["fresh_process_peak_rss_ratio_max"] = cost["max_rss_ratio"]
        c6["rss_ratio_max"] = RSS_RATIO_MAX
        ok = ok and cost["max_rss_ratio"] <= RSS_RATIO_MAX
    else:
        c6["note"] = "no fresh-process memory measurement given"
        ok = False
    c6["verdict"] = "ACCEPT" if ok else "REJECT"
    v["criteria"]["C6_cost"] = c6
    # C7 determinism (workers 1 vs 4)
    det = []
    for c, ts in by_cell.items():
        for t in ts:
            if W4 in t["arms"]:
                x, y = t["arms"][CAND], t["arms"][W4]
                same = (x["status"] == y["status"] and x["container_sha256_decoded"] == y["container_sha256_decoded"]
                        and x["reads"] == y["reads"])
                det.append({"cell": c, "seed": t["seed"], "identical": same})
    n_same = sum(d["identical"] for d in det)
    v["criteria"]["C7_determinism"] = {"pairs": len(det), "identical": n_same,
                                       "verdict": "ACCEPT" if det and n_same == len(det) else "REJECT"}
    need = ("C1_false_success", "C2_pooled_primary_gain", "C4_no_harm", "C6_cost", "C7_determinism")
    v["default_change_rule"] = {"requires": list(need),
                                "satisfied": all(v["criteria"][k]["verdict"] == "ACCEPT" for k in need)}
    Path(a.out).write_text(json.dumps(v, indent=1, sort_keys=True) + "\n")
    for k, x in v["criteria"].items():
        print(k, x.get("verdict", ""), {kk: x[kk] for kk in x if kk in ("difference", "ci95_low", "ci95_high", "count",
                                                                          "mean", "median_decode_time_ratio",
                                                                          "identical", "false_success")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
