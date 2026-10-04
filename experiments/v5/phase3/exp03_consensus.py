"""P3-EXP-03 — multi-read consensus: coverage × indel rate, V4 vs V5 (Phase 3 §14). SIMULATED.

64 KiB random input, v4-balanced, fixed coverage 1/2/3/5/10, insertion rate = deletion rate in {0, 0.1, 0.2, 0.5,
1} %, no other errors, three channel seeds per cell. Each read file is decoded with V4 ("segment") and V5 ("smart":
pass-1 local recovery + pass-2 consensus realignment). Success = SUCCESS with a verified output SHA-256.

usage: python experiments/v5/phase3/exp03_consensus.py [--size 65536] [--seeds 3] [--workers 8]
"""
from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import p3common as pc
import p3archive as pa

OUT = pc.HERE / "P3-EXP-03-consensus"
COVERAGE = [1, 2, 3, 5, 10]
RATES = [0.0, 0.001, 0.002, 0.005, 0.01]
INPUT_SEED = 3301


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=65536)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    t_all = time.perf_counter()
    cells = []
    with tempfile.TemporaryDirectory(prefix="p3exp03-") as tmp:
        work = Path(tmp)
        info = pa.build(work, a.size, INPUT_SEED)
        for cov in COVERAGE:
            for rate in RATES:
                for k in range(a.seeds):
                    chan = {"insertion_rate": rate, "deletion_rate": rate, "coverage": cov, "coverage_model": "fixed",
                            "seed": 33000 + 100 * k + cov}
                    reads = pa.simulate(work, f"c{cov}-r{rate}-s{k}", chan)
                    row = {"coverage": cov, "rate": rate, "seed_index": k, "channel": chan}
                    for mode in ("segment", "smart"):
                        row[mode] = pa.decode(reads, work, mode, a.workers, info["input_sha256"])
                    reads.unlink()
                    cells.append(row)
                    print(f"cov {cov:2d} rate {rate:.3f} seed {k}: V4 {row['segment']['status']:8s} "
                          f"({row['segment']['groups_recovered_fraction']}, {row['segment']['seconds']}s)  "
                          f"V5 {row['smart']['status']:8s} ({row['smart']['groups_recovered_fraction']}, {row['smart']['seconds']}s)"
                          + ("  FALSE SUCCESS" if row["segment"]["false_success"] or row["smart"]["false_success"] else ""),
                          flush=True)
    summary = {}
    for cov in COVERAGE:
        for rate in RATES:
            rs = [c for c in cells if c["coverage"] == cov and c["rate"] == rate]
            summary[f"cov {cov} | {rate * 100:g}%+{rate * 100:g}%"] = {
                m: {"verified_success": sum(c[m]["verified"] for c in rs), "trials": len(rs),
                    "false_success": sum(c[m]["false_success"] for c in rs),
                    "groups_recovered_fraction_mean": round(sum(c[m]["groups_recovered_fraction"] or 0 for c in rs) / len(rs), 4),
                    "decode_seconds_mean": round(sum(c[m]["seconds"] for c in rs) / len(rs), 2),
                    "consensus_groups": sum((c[m]["report"].get("indel_recovery") or {}).get("consensus_groups", 0) for c in rs),
                    "consensus_recovered_smart": sum((c[m]["report"].get("reads") or {}).get("consensus_recovered_smart", 0)
                                                     for c in rs),
                    "consensus_recovered_v4_vote": sum((c[m]["report"].get("reads") or {}).get("consensus_recovered", 0)
                                                       for c in rs)}
                for m in ("segment", "smart")}
    cfg = {"input": {"size": a.size, "pattern": "random", "seed": INPUT_SEED, "sha256": info["input_sha256"]},
           "profile": "v4-balanced", "coverage": COVERAGE, "rates_each": RATES, "seeds_per_cell": a.seeds,
           "workers": a.workers, "channel_seed_rule": "33000 + 100·seed_index + coverage"}
    pc.write_result(OUT, "P3-EXP-03 consensus coverage x indel rate", cfg,
                    {"summary": summary, "cells": cells, "wall_seconds": round(time.perf_counter() - t_all, 1)})


if __name__ == "__main__":
    main()
