"""Summarise A-CONF and apply the pre-registered criteria C1-C5 (SIMULATED).

    python experiments/v7/a-conf/summarise.py        # writes results/summary.json, prints one line per cell"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent

FIELDS = ("two_reads", "clustered", "candidate", "consensus_ok", "data_frames", "strands_total")


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    """Wilson 95 % interval, the same formula as ``experiments/v7/a-cons/summarise.py``."""
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def summarise(rows: list[dict]) -> dict:
    by = defaultdict(list)
    for r in rows:
        by[(r["coverage"], r["arm"])].append(r)
    cells: dict = {}
    for (cov, arm), rs in sorted(by.items()):
        n = len(rs)
        e = sum(r["outcome"] == "EXACT" for r in rs)
        secs = [r["decode_seconds"] for r in rs]
        cells.setdefault(str(cov), {})[arm] = {
            "cases": n, "exact": e, "exact_wilson95": wilson(e, n),
            "explicit_failure": sum(r["outcome"] == "EXPLICIT_FAILURE" for r in rs),
            "other_outcomes": sorted({r["outcome"] for r in rs} - {"EXACT", "EXPLICIT_FAILURE"}),
            "false_success": sum(r["false_success"] for r in rs), "false_frames": sum(r["false_frames"] for r in rs),
            **{f"mean_{f}": round(sum(r[f] for r in rs) / n, 1) for f in FIELDS},
            "data_total": rs[0]["data_total"], "median_decode_seconds": round(statistics.median(secs), 2),
            "max_decode_seconds": round(max(secs), 2), "max_peak_rss_mib": max(r["peak_rss_bytes"] for r in rs) >> 20,
            "load1_range": [min(r["load1"] for r in rs), max(r["load1"] for r in rs)]}
    crit: dict = {"C1_no_false": all(r["false_success"] is False and r["false_frames"] == 0 for r in rows),
                  "decodes": len(rows), "C2": {}, "C5_harm_seeds": []}
    for cov in cells:
        old = {r["seed"]: r for r in rows if str(r["coverage"]) == cov and r["arm"] == "old"}
        new = {r["seed"]: r for r in rows if str(r["coverage"]) == cov and r["arm"] == "phase1"}
        seeds = sorted(old.keys() & new.keys())
        ge = sum(new[s]["data_frames"] >= old[s]["data_frames"] for s in seeds)
        a, b = cells[cov]["old"], cells[cov]["phase1"]
        c2 = {"paired_seeds": len(seeds), "phase1_ge_old_seeds": ge,
              "mean_frames_better": b["mean_data_frames"] > a["mean_data_frames"],
              "ge_on_90pct": ge >= 0.9 * len(seeds), "exact_not_worse": b["exact"] >= a["exact"]}
        c2["pass"] = c2["mean_frames_better"] and c2["ge_on_90pct"] and c2["exact_not_worse"]
        crit["C2"][cov] = c2
        crit["C5_harm_seeds"] += [[int(cov), s] for s in seeds
                                  if old[s]["outcome"] == "EXACT" and new[s]["outcome"] != "EXACT"]
    crit["C3_cov10_phase1_ge_36"] = cells["10"]["phase1"]["exact"] >= 36
    crit["C4_lowcov"] = cells["5"]["lowcov"]["exact"] >= 36 and cells["10"]["lowcov"]["exact"] == 40
    crit["C5_no_harm"] = not crit["C5_harm_seeds"]
    crit["strand_overhead_lowcov_vs_balanced"] = round(
        cells["10"]["lowcov"]["mean_strands_total"] / cells["10"]["phase1"]["mean_strands_total"] - 1, 4)
    p1 = crit["C1_no_false"] and crit["C2"]["10"]["pass"] and crit["C3_cov10_phase1_ge_36"] and crit["C5_no_harm"]
    crit["decision_phase1"] = "REJECT" if not crit["C1_no_false"] else ("CONFIRMED" if p1 else "NOT CONFIRMED")
    crit["decision_lowcov"] = "CONFIRMED" if crit["C1_no_false"] and crit["C4_lowcov"] else "NOT REPLICATED"
    return {"label": "EXPERIMENTAL, SIMULATED (nanopore-like stress model, unfitted)", "cells": cells,
            "criteria": crit}


def main() -> int:
    rows = [json.loads(x) for x in (HERE / "results" / "confirmatory.jsonl").read_text().splitlines()]
    doc = summarise(rows)
    (HERE / "results" / "summary.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    for cov, arms in doc["cells"].items():
        for arm, a in arms.items():
            print(f"cov{cov} {arm:7s} EXACT={a['exact']}/{a['cases']} {a['exact_wilson95']} FS={a['false_success']} "
                  f"ff={a['false_frames']} frames={a['mean_data_frames']}/{a['data_total']} "
                  f"strands={a['mean_strands_total']} t50={a['median_decode_seconds']}s rss={a['max_peak_rss_mib']}MiB")
    print(json.dumps(doc["criteria"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
