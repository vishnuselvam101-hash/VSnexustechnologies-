"""Pre-registered verdicts for AB-EXP-01 (criteria C1-C7 and S1 of experiments/v6/align-band/PREREG.md). SIMULATED.

    PYTHONPATH=src python experiments/v6/align-band/verdict.py \
        --trials experiments/v6/align-band/AB-EXP-01-retry-band/trials.jsonl \
        --cost experiments/v6/align-band/AB-EXP-02-cost/results.json \
        --fuzz experiments/v6/align-band/AB-FUZZ/results.json \
        --out experiments/v6/align-band/AB-EXP-01-retry-band/verdict.json

Written and committed together with PREREG.md, before the comparison was run. Nothing here is tuned on its output.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase4"))
from phase4 import paired_difference  # noqa: E402
from vnxdna.v2.experiment import wilson  # noqa: E402

BASE, CAND, W4 = "default", "retry16", "retry16-w4"
TIME_RATIO_MAX = 1.5          # C4: per-cell median paired decode-time ratio, cells where the baseline already decodes
ALREADY_DECODES = 10          # ... "already decodes" = baseline exact in at least 10 of the cell's 20 seeds
RSS_RATIO_MAX = 1.15          # C5: fresh-process peak RSS ratio, every (model, seed) pair
MIN_FUZZ_READS = 100_000      # C7
HIGH_INDEL = ("deletion-heavy", "insertion-heavy", "mixed-harsh", "nanopore-like")


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


def model_of(cell_id: str) -> str:
    return cell_id.split("/")[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", nargs="+", required=True)
    ap.add_argument("--cost", required=True)
    ap.add_argument("--fuzz", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    trials = load(a.trials)
    by_cell = defaultdict(list)
    for t in trials:
        by_cell[t["cell"]].append(t)
    for ts in by_cell.values():
        ts.sort(key=lambda t: t["seed"])
    grid = {c: ts for c, ts in by_cell.items() if c.startswith("G/")}

    crit = {}
    # C1: no false SUCCESS anywhere (every arm, every panel)
    fs = sum(v["false_success"] for t in trials for v in t["arms"].values())
    decodes = sum(len(t["arms"]) for t in trials)
    crit["C1_no_false_success"] = {"false_success": fs, "decodes": decodes, "verdict": "ACCEPT" if fs == 0 else "REJECT"}

    cells = {}
    for cid, ts in sorted(grid.items()):
        b = [exact(t["arms"][BASE]) for t in ts]
        c = [exact(t["arms"][CAND]) for t in ts]
        pd = paired_difference(b, c)
        ratio = [t["arms"][CAND]["decode_seconds"] / t["arms"][BASE]["decode_seconds"] for t in ts
                 if t["arms"][BASE]["decode_seconds"] > 0]
        bl, bh = wilson(sum(b), len(b))
        cl, ch = wilson(sum(c), len(c))
        stages = {arm: dict(sorted(__import__("collections").Counter(t["arms"][arm]["classification"]["stage"]
                                                                     for t in ts).items())) for arm in (BASE, CAND)}
        cells[cid] = {"n": len(ts), "default_exact": sum(b), "retry16_exact": sum(c),
                      "default_wilson95": [round(bl, 4), round(bh, 4)], "retry16_wilson95": [round(cl, 4), round(ch, 4)],
                      "paired": pd, "median_time_ratio": round(float(np.median(ratio)), 3) if ratio else None,
                      "median_decode_seconds": {arm: float(np.median([t["arms"][arm]["decode_seconds"] for t in ts]))
                                                for arm in (BASE, CAND)},
                      "retry_band_reads_median": float(np.median([(t["arms"][CAND]["reads"] or {}).get("retry_band_reads", 0)
                                                                  for t in ts])),
                      "within_band_share_median": float(np.median([t["read_drift"].get("within_band", 0) for t in ts])),
                      "stages": stages}

    def pooled(pred):
        b, c = [], []
        for cid, ts in grid.items():
            if pred(cid):
                b += [exact(t["arms"][BASE]) for t in ts]
                c += [exact(t["arms"][CAND]) for t in ts]
        return paired_difference(b, c)

    # C2: nanopore-like efficacy, pooled over coverage 3/5/10
    p2 = pooled(lambda cid: model_of(cid) == "nanopore-like")
    crit["C2_nanopore_gain"] = {"pooled": p2, "verdict": "ACCEPT" if p2.get("ci95_low", -1) > 0 else "REJECT"}
    # C3: no harm in any grid cell (Newcombe upper bound >= 0), and pooled over every grid cell except nanopore-like
    harm = [cid for cid, c in cells.items() if c["paired"]["ci95_high"] < 0]
    worse = {cid: c["paired"]["only_baseline"] for cid, c in cells.items() if c["paired"]["only_baseline"] > 0}
    p3 = pooled(lambda cid: model_of(cid) != "nanopore-like")
    crit["C3_no_harm"] = {"cells_with_upper_bound_below_zero": harm, "pooled_non_nanopore": p3,
                          "cells_with_any_baseline_only_success": worse,
                          "verdict": "ACCEPT" if not harm and p3["ci95_high"] >= 0 else "REJECT"}
    # C4: decode-time budget on cells that already decode
    dec = {cid: c["median_time_ratio"] for cid, c in cells.items() if c["default_exact"] >= ALREADY_DECODES}
    over = {cid: r for cid, r in dec.items() if r is None or r > TIME_RATIO_MAX}
    crit["C4_time_budget"] = {"budget": TIME_RATIO_MAX, "cells": dec, "over_budget": over,
                              "max": max(dec.values()) if dec else None,
                              "median": float(np.median(list(dec.values()))) if dec else None,
                              "verdict": "ACCEPT" if dec and not over else "REJECT"}
    # C5: fresh-process peak RSS
    cost = json.loads(Path(a.cost).read_text())
    rss = [p["rss_ratio"] for p in cost["pairs"]]
    crit["C5_rss_budget"] = {"budget": RSS_RATIO_MAX, "max_rss_ratio": max(rss), "pairs": cost["pairs"],
                             "false_success": cost["false_success"],
                             "verdict": "ACCEPT" if max(rss) <= RSS_RATIO_MAX and cost["false_success"] == 0 else "REJECT"}
    # C6: determinism, 1 vs 4 decode workers
    det = [t for c, ts in by_cell.items() if c.startswith("D/") for t in ts]
    mism = [(t["cell"], t["seed"]) for t in det
            if (t["arms"][CAND]["status"], t["arms"][CAND]["container_sha256_decoded"], t["arms"][CAND]["reads"])
            != (t["arms"][W4]["status"], t["arms"][W4]["container_sha256_decoded"], t["arms"][W4]["reads"])]
    crit["C6_determinism"] = {"pairs": len(det), "mismatches": mism, "verdict": "ACCEPT" if det and not mism else "REJECT"}
    # C7: bit-exact native vs reference (differential fuzz)
    fz = json.loads(Path(a.fuzz).read_text())
    ok7 = fz["mismatches"] == 0 and fz["counts"]["reads"] >= MIN_FUZZ_READS
    crit["C7_bit_exact"] = {"reads": fz["counts"]["reads"], "counts": fz["counts"], "mismatches": fz["mismatches"],
                            "verdict": "ACCEPT" if ok7 else "REJECT"}
    # S1 (secondary efficacy): pooled over the high-indel models' grid cells
    s1 = pooled(lambda cid: model_of(cid) in HIGH_INDEL)
    crit["S1_high_indel_gain"] = {"models": HIGH_INDEL, "pooled": s1,
                                  "verdict": "ACCEPT" if s1.get("ci95_low", -1) > 0 else "REJECT"}

    others = all(crit[k]["verdict"] == "ACCEPT" for k in ("C1_no_false_success", "C3_no_harm", "C4_time_budget",
                                                         "C5_rss_budget", "C6_determinism", "C7_bit_exact"))
    if others and crit["C2_nanopore_gain"]["verdict"] == "ACCEPT":
        decision = "PROPOSE-DEFAULT (all of C1-C7 ACCEPT)"
    elif others and crit["S1_high_indel_gain"]["verdict"] == "ACCEPT":
        decision = "CONDITIONAL (C2 REJECT, S1 and C1/C3-C7 ACCEPT): lead decides; nanopore-like stays unsolved"
    else:
        decision = "KEEP OPT-IN"
    x = {c: ts for c, ts in by_cell.items() if c.startswith("X/")}
    expl = {cid: {arm: sum(exact(t["arms"][arm]) for t in ts) for arm in ts[0]["arms"]} | {"n": len(ts)}
            for cid, ts in sorted(x.items())}
    doc = {"experiment": "AB-EXP-01-retry-band", "classification": "SIMULATED", "criteria": crit, "decision": decision,
           "grid_cells": cells, "exploratory": expl, "trials": len(trials)}
    Path(a.out).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v["verdict"] for k, v in crit.items()} | {"decision": decision}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
