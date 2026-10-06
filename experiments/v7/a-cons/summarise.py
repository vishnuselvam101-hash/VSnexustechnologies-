"""Summarise A-CONS results per set, coverage and arm, and apply the pre-registered decision rule (SIMULATED).

    python experiments/v7/a-cons/summarise.py        # writes results/summary.json, prints one line per row"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIELDS = ("two_reads", "clustered", "candidate", "consensus_ok", "data_frames", "false_frames")


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
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
    out: dict = {}
    for (cov, arm), rs in sorted(by.items()):
        n = len(rs)
        e = sum(r["sha256_match"] for r in rs)
        out.setdefault(str(int(cov)), {})[arm] = {
            "cases": n, **{f"mean_{f}": round(sum(r[f] for r in rs) / n, 1) for f in FIELDS},
            "data_total": round(sum(r["data_total"] for r in rs) / n, 1), "exact": e, "exact_wilson95": wilson(e, n),
            "false_success": sum(r["false_success"] for r in rs), "false_frames": sum(r["false_frames"] for r in rs),
            "mean_decode_seconds": round(sum(r["decode_seconds"] for r in rs) / n, 2),
            "max_peak_rss_mib": max(r["peak_rss_bytes"] for r in rs) >> 20}
    for cov, arms in out.items():
        if {"wildcard", "full"} <= arms.keys():
            old = {r["seed"]: r for r in rows if str(int(r["coverage"])) == cov and r["arm"] == "wildcard"}
            new = {r["seed"]: r for r in rows if str(int(r["coverage"])) == cov and r["arm"] == "full"}
            seeds = sorted(old.keys() & new.keys())
            ge = sum(new[s]["data_frames"] >= old[s]["data_frames"] for s in seeds)
            a, b = arms["wildcard"], arms["full"]
            arms["decision"] = {
                "paired_seeds": len(seeds), "new_ge_old_seeds": ge,
                "no_false": a["false_success"] + b["false_success"] + a["false_frames"] + b["false_frames"] == 0,
                "mean_frames_better": b["mean_data_frames"] > a["mean_data_frames"],
                "ge_on_90pct": ge >= 0.9 * len(seeds), "exact_not_worse": b["exact"] >= a["exact"]}
            arms["decision"]["improvement"] = all(arms["decision"][k] for k in
                                                  ("no_false", "mean_frames_better", "ge_on_90pct", "exact_not_worse"))
    return out


def main() -> int:
    res = {}
    for name in ("frozen", "heldout"):
        p = HERE / "results" / f"{name}.jsonl"
        if p.exists():
            res[name] = summarise([json.loads(x) for x in p.read_text().splitlines()])
    (HERE / "results" / "summary.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    for name, covs in res.items():
        for cov, arms in sorted(covs.items(), key=lambda x: int(x[0])):
            for arm in ("wildcard", "full"):
                if arm in arms:
                    a = arms[arm]
                    print(f"{name} cov{cov} {arm:8s} n={a['cases']} 2r={a['mean_two_reads']} cl={a['mean_clustered']} "
                          f"cons={a['mean_consensus_ok']} frames={a['mean_data_frames']}/{a['data_total']} "
                          f"EXACT={a['exact']} {a['exact_wilson95']} FS={a['false_success']} ff={a['false_frames']} "
                          f"t={a['mean_decode_seconds']}s rss={a['max_peak_rss_mib']}MiB")
            if "decision" in arms:
                print(f"{name} cov{cov} decision {arms['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
