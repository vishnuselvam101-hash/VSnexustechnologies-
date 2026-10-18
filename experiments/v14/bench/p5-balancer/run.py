"""P5 load-balancer benchmark driver: single- vs multi-worker sweeps of ``vnxd-lb-bench`` (docs/V14_LOAD_BALANCER.md §5).

    python experiments/v14/bench/p5-balancer/run.py [--bin PATH] [--seeds 3]

Writes ``results.jsonl`` (one line per run, with provenance) and ``summary.json`` (median and range per
configuration) next to this file. Backend latencies are model inputs (``LatencyMedia``); evidence category B
(software device over a modelled backend), never a measurement of DNA hardware.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DEFAULT_BIN = ROOT / "src/vnxdna/native/rust/vnxd/target/release/vnxd-lb-bench"

SWEEPS = [
    # (sweep, profile, io_workers list, lb_workers list, ops per client)
    ("io_workers", "electronic", [1, 2, 4, 8, 16], [2], 200),
    ("io_workers", "dna-sim", [1, 2, 4, 8, 16], [2], 20),
    ("lb_workers", "electronic", [8], [1, 2, 4, 8, 16], 200),
]
KEYS = ["throughput_req_per_s_milli", "throughput_kib_per_s", "latency_us_p50", "latency_us_p95", "latency_us_p99",
        "queue_wait_us_mean", "backend_slot_wait_us_total", "backend_in_flight_max", "cpu_tasks_per_s_milli",
        "lb_utilisation_milli", "peak_rss_kib", "wall_ms"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=str(DEFAULT_BIN))
    ap.add_argument("--seeds", type=int, default=3)
    a = ap.parse_args()
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    prov = {"commit": sha, "dirty_tree": dirty, "host": platform.node(), "machine": platform.machine(),
            "kernel": platform.release(), "cpus": os.cpu_count(), "date": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    rows = []
    with open(HERE / "results.jsonl", "w") as out:
        for sweep, profile, ios, lbs, ops in SWEEPS:
            for io in ios:
                for lb in lbs:
                    for seed in range(1, a.seeds + 1):
                        cmd = [a.bin, "--profile", profile, "--io-workers", str(io), "--lb-workers", str(lb),
                               "--ops", str(ops), "--seed", str(seed)]
                        r = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
                        r.update({"sweep": sweep, "provenance": prov})
                        out.write(json.dumps(r, sort_keys=True) + "\n")
                        out.flush()
                        rows.append(r)
                        print(sweep, profile, io, lb, seed, r["throughput_req_per_s_milli"] / 1000, "req/s",
                              "p99", r["latency_us_p99"], "us", flush=True)
    summary = []
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["sweep"], r["profile"], r["io_workers"], r["lb_workers"]), []).append(r)
    for (sweep, profile, io, lb), rs in groups.items():
        entry = {"sweep": sweep, "profile": profile, "io_workers": io, "lb_workers": lb, "runs": len(rs)}
        for k in KEYS:
            vals = [x[k] for x in rs]
            entry[k] = {"median": statistics.median(vals), "min": min(vals), "max": max(vals)}
        summary.append(entry)
    (HERE / "summary.json").write_text(json.dumps({"provenance": prov, "configs": summary}, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
