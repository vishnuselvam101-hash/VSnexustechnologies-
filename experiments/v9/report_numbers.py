"""Derived numbers for docs/V9_COMPLETION_REPORT.md, computed only from committed V9 results files (SIMULATED).

- H1: native/reference speed-up on the 60 benchmark decodes (median, bootstrap 95 % interval, seed 94000, 10,000
  resamples) and the byte-identity count (consensus/results/benchmark-d13f1.jsonl).
- H4: adaptive-coverage EVAL per profile (coverage/results/eval.jsonl): stopping outcome, reads and coverage used, false
  terminations with the Wilson 95 % upper bound, the fixed-coverage EXACT counts of the same seeds, and the reads
  avoided relative to the smallest fixed coverage with equal or higher EXACT (prereg §6). The fixed-coverage read count
  is taken from the seed's own trajectory at that coverage (batches of 0.5x).
- Archive size: noisy decodes per size (scale/results/noisy.jsonl): EXACT with Wilson 95 %, false success, median
  per-strand and per-row success, median decode seconds and the host-load range.

    python experiments/v9/report_numbers.py      # writes experiments/v9/results/report-numbers.json
"""
from __future__ import annotations

import json
import math
import random
import statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "report-numbers.json"


def load(rel: str) -> list[dict]:
    return [json.loads(x) for x in (HERE / rel).read_text().splitlines() if x.strip()]


def wilson(k: int, n: int, z: float = 1.959964) -> list[float]:
    p, d = k / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def h1() -> dict:
    rows = load("consensus/results/benchmark-d13f1.jsonl")
    sp = [r["speedup"] for r in rows]
    rng = random.Random(94000)
    boot = sorted(st.median(rng.choices(sp, k=len(sp))) for _ in range(10_000))
    return {"source": "experiments/v9/consensus/results/benchmark-d13f1.jsonl", "decodes": len(rows),
            "byte_identical": sum(r["identical"] for r in rows), "median_speedup": round(st.median(sp), 3),
            "bootstrap95": [round(boot[249], 3), round(boot[9749], 3)], "min": min(sp), "max": max(sp),
            "load1_range": [min(r["load1"] for r in rows), max(r["load1"] for r in rows)]}


def h4() -> dict:
    rows = load("coverage/results/eval.jsonl")
    out = {"source": "experiments/v9/coverage/results/eval.jsonl", "profiles": {}}
    for prof in sorted({r["profile"] for r in rows}):
        rs = [r for r in rows if r["profile"] == prof]
        n = len(rs)
        exact = sum(r["stop"]["outcome"] == "EXACT" for r in rs)
        ft = sum(r["false_termination"] for r in rs)
        fixed = {c: sum(r["fixed"][c] == "EXACT" for r in rs) for c in sorted(rs[0]["fixed"], key=float)}
        ref = next((c for c in fixed if fixed[c] >= exact), None)
        cell = {"n": n, "exact_at_stop": exact, "false_success": sum(r["false_success"] for r in rs),
                "false_terminations": ft, "false_termination_wilson95": wilson(ft, n),
                "mean_reads_at_stop": round(st.mean(r["stop"]["reads"] for r in rs), 1),
                "mean_coverage_at_stop": round(st.mean(r["stop"]["coverage_used"] for r in rs), 3),
                "coverage_at_stop_range": [min(r["stop"]["coverage_used"] for r in rs),
                                           max(r["stop"]["coverage_used"] for r in rs)],
                "fixed_exact": fixed, "reference_fixed_coverage": ref}
        if ref is not None:
            k = int(round(2 * float(ref)))  # trajectory batch index at that coverage
            ref_reads = []
            for r in rs:
                t = [b for b in r["trajectory"] if b["batch"] == k]
                ref_reads.append(t[0]["reads"] if t else r["pool_reads"])
            m = st.mean(ref_reads)
            cell["mean_reads_reference"] = round(m, 1)
            cell["reads_avoided_fraction"] = round(1 - cell["mean_reads_at_stop"] / m, 4)
        out["profiles"][prof] = cell
    return out


def noisy() -> dict:
    rows = load("scale/results/noisy.jsonl")
    out = {"source": "experiments/v9/scale/results/noisy.jsonl", "sizes": {}}
    for size in sorted({r["size"] for r in rows}):
        rs = [r for r in rows if r["size"] == size]
        k = sum(r["outcome"] == "EXACT" for r in rs)
        out["sizes"][str(size)] = {
            "n": len(rs), "exact": k, "exact_wilson95": wilson(k, len(rs)),
            "false_success": sum(bool(r["false_success"]) for r in rs), "strands": rs[0]["strands"],
            "rows": rs[0]["rows"], "median_per_strand_success": st.median(r["per_strand_success"] for r in rs),
            "median_per_row_success": st.median(r["per_row_success"] for r in rs),
            "median_decode_seconds": round(st.median(r["decode_seconds"] for r in rs), 2),
            "load1_range": [min(r["load1"] for r in rs), max(r["load1"] for r in rs)]}
    return out


def main() -> int:
    res = {"label": "SIMULATED: derived from committed V9 results; no DNA was synthesised, stored or sequenced",
           "H1": h1(), "H4": h4(), "noisy": noisy()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
