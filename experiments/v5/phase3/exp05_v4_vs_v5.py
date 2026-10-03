"""P3-EXP-05 — V4 vs V5 on whole archives with equal archive, channel, coverage, seeds and redundancy (Phase 3 §15).

SIMULATED. Default profile v4-balanced (outer Cauchy RS 64 + 16), so DNA overhead is identical by construction
(9.79 nt per input byte, measured in Phase 1); only the decoder differs. Each read file is decoded by V4 ("segment") and
V5 ("smart"), one worker, each decode in a fresh process. Success = SUCCESS with a verified output SHA-256.

Part B repeats the Phase 2 end-to-end workload (4 MiB, EXP-0011 channel) to measure what smart mode costs where V4
already succeeds.

usage: python experiments/v5/phase3/exp05_v4_vs_v5.py [--size 262144] [--seeds 3] [--skip-4mib]
"""
from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import p3common as pc
import p3archive as pa

OUT = pc.HERE / "P3-EXP-05-v4-vs-v5"
INPUT_SEED = 3501
CHANNELS = {
    "indel 0.2%+0.2%, cov 1": {"insertion_rate": 0.002, "deletion_rate": 0.002, "coverage": 1},
    "indel 0.3%+0.3%, cov 1": {"insertion_rate": 0.003, "deletion_rate": 0.003, "coverage": 1},
    "indel 0.4%+0.4%, cov 1": {"insertion_rate": 0.004, "deletion_rate": 0.004, "coverage": 1},
    "indel 0.5%+0.5%, cov 1": {"insertion_rate": 0.005, "deletion_rate": 0.005, "coverage": 1},
    "mixed L3 (0.5% sub, 0.1%+0.1%, 5% dropout), cov 1": {"substitution_rate": 0.005, "insertion_rate": 0.001,
                                                          "deletion_rate": 0.001, "dropout_rate": 0.05, "coverage": 1},
    "mixed (0.3% sub, 0.25%+0.25%, 2% dropout), cov 1": {"substitution_rate": 0.003, "insertion_rate": 0.0025,
                                                         "deletion_rate": 0.0025, "dropout_rate": 0.02, "coverage": 1},
    "harsh (0.5% sub, 0.6%+0.6%, 2% dropout), Poisson cov 3": {"substitution_rate": 0.005, "insertion_rate": 0.006,
                                                               "deletion_rate": 0.006, "dropout_rate": 0.02, "coverage": 3,
                                                               "coverage_model": "poisson"},
    "EXP-0011 (0.2% sub, 0.05%+0.05%, 2% dropout), Poisson cov 3": {"substitution_rate": 0.002, "insertion_rate": 0.0005,
                                                                    "deletion_rate": 0.0005, "dropout_rate": 0.02,
                                                                    "coverage": 3, "coverage_model": "poisson"},
}
EXP0011 = CHANNELS["EXP-0011 (0.2% sub, 0.05%+0.05%, 2% dropout), Poisson cov 3"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=262144)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--skip-4mib", action="store_true")
    a = ap.parse_args()
    t_all = time.perf_counter()
    runs = []
    with tempfile.TemporaryDirectory(prefix="p3exp05-") as tmp:
        work = Path(tmp)
        info = pa.build(work, a.size, INPUT_SEED)
        for name, chan in CHANNELS.items():
            for k in range(a.seeds):
                c = {**chan, "seed": 35000 + 10 * k}
                reads = pa.simulate(work, f"ch{len(runs)}", c)
                row = {"channel_name": name, "channel": c, "seed_index": k}
                for mode in ("segment", "smart"):
                    row[mode] = pa.decode(reads, work, mode, 1, info["input_sha256"])
                reads.unlink()
                runs.append(row)
                print(f"{name:58s} seed {k}: V4 {row['segment']['status']:8s} {row['segment']['seconds']:6.1f}s  "
                      f"V5 {row['smart']['status']:8s} {row['smart']['seconds']:6.1f}s  "
                      f"groups V4 {row['segment']['groups_recovered_fraction']} V5 {row['smart']['groups_recovered_fraction']}",
                      flush=True)
        part_b = []
        if not a.skip_4mib:
            big = work / "big"
            big.mkdir()
            binfo = pa.build(big, 4 << 20, 42)
            reads = pa.simulate(big, "exp0011", {**EXP0011, "seed": 1011})
            for workers in (1, 8):
                row = {"workers": workers}
                for mode in ("segment", "smart"):
                    row[mode] = pa.decode(reads, big, mode, workers, binfo["input_sha256"])
                part_b.append(row)
                print(f"4 MiB EXP-0011 workers {workers}: V4 {row['segment']['status']} {row['segment']['seconds']}s  "
                      f"V5 {row['smart']['status']} {row['smart']['seconds']}s", flush=True)
    summary = {}
    for name in CHANNELS:
        rs = [r for r in runs if r["channel_name"] == name]
        summary[name] = {m: {"verified_success": sum(r[m]["verified"] for r in rs), "trials": len(rs),
                             "false_success": sum(r[m]["false_success"] for r in rs),
                             "groups_recovered_fraction": [r[m]["groups_recovered_fraction"] for r in rs],
                             "decode_seconds": [r[m]["seconds"] for r in rs],
                             "peak_rss_mb": [r[m]["peak_rss_mb"] for r in rs],
                             "reads_smart_path": [(r[m]["report"].get("reads") or {}).get("smart") for r in rs],
                             "smart_trials": [(r[m]["report"].get("indel_recovery") or {}).get("trials") for r in rs],
                             "false_accept_bound_sum": [(r[m]["report"].get("indel_recovery") or {}).get("false_accept_bound")
                                                        for r in rs]}
                         for m in ("segment", "smart")}
    cfg = {"input": {"size": a.size, "pattern": "random", "seed": INPUT_SEED}, "profile": "v4-balanced (Cauchy RS 64+16)",
           "channels": CHANNELS, "seeds_per_channel": a.seeds, "channel_seed_rule": "35000 + 10·seed_index", "workers": 1,
           "part_b": {"input": "4 MiB random seed 42", "channel": {**EXP0011, "seed": 1011}, "workers": [1, 8]}}
    pc.write_result(OUT, "P3-EXP-05 V4 vs V5 archives", cfg,
                    {"summary": summary, "runs": runs, "part_b_4mib": part_b, "wall_seconds": round(time.perf_counter() - t_all, 1)})


if __name__ == "__main__":
    main()
