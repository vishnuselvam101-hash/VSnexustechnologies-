"""V14 Phase 6C, lead decision (docs/V14_SCHEDULER.md §9.4): end-to-end A/B of the new default scheduler ("auto": the V13
rule, plus one strand retry before the V13-path fallback) against build/v14 (the V13 rule as shipped), and the
zero-run-payload repro, SIMULATED reads.

    BUILD_TREE=/root/vnx-dna-lab/v14 PYTHONPATH=src python experiments/v14/bench/p6c-scheduler/ab_default.py run [--scenarios a,b]
    PYTHONPATH=src python experiments/v14/bench/p6c-scheduler/ab_default.py summary

Configs: "auto" = this tree's default; "build" and "build2" = the build/v14 tree's default (the A/A pair). Same protocol
as ab.py (inputs from the bench generator, one discarded warm-up per config on a scenario's first seed, per seed the
three configs in a seed-dependent permutation followed by its mirror, process wall seconds, CPU seconds and peak RSS,
ratio build / auto with the percentile-bootstrap CI of the median per-seed ratio). Every number is PROVISIONAL.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ab  # noqa: E402  (the scenarios, the input generator and the bootstrap of ab.py)

ROOT = ab.ROOT
BUILD = Path(os.environ.get("BUILD_TREE", "/root/vnx-dna-lab/v14"))
OUT = Path(os.environ.get("VNX_BENCH_OUT") or HERE / "ab-default.jsonl")
CONFIGS = {"auto": ROOT, "build": BUILD, "build2": BUILD}

ONE = r"""
import hashlib, json, resource, sys, time
from pathlib import Path
from vnxdna.v4 import decoder as de
from vnxdna.recovery.options import DecodeOptions
w, out, workers = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
t0 = time.perf_counter()
res = de.decode_reads(w / "r.fastq", out, DecodeOptions(workers=workers), overwrite=True)
sec = time.perf_counter() - t0
data = out.read_bytes() if out.exists() else b""
eng = res.report.get("engine") or {}
ru = resource.getrusage(resource.RUSAGE_SELF)
print(json.dumps({"seconds": sec, "status": res.status, "output_sha256": hashlib.sha256(data).hexdigest(),
                  "matches_archive": data == (w / "a.vnx").read_bytes(), "rung": eng.get("rung"),
                  "fallbacks": [f.get("reason") for f in eng.get("fallbacks") or []], "scheduler": eng.get("scheduler"),
                  "attempted": (eng.get("pass1") or {}).get("attempted"), "vnxdna_file": de.__file__,
                  "peak_rss_bytes": ru.ru_maxrss * 1024, "cpu_seconds": ru.ru_utime + ru.ru_stime}))
out.unlink(missing_ok=True)
"""


def step(w: Path, config: str, workers: int) -> dict:
    tree = CONFIGS[config]
    env = {**os.environ, "PYTHONPATH": str(tree / "src")}
    load = os.getloadavg()[0]
    p = subprocess.run([sys.executable, "-c", ONE, str(w), str(w / f"o-{config}.vnx"), str(workers)], env=env,
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{config} failed: {p.stderr[-2000:]}")
    row = json.loads(p.stdout.strip().splitlines()[-1])
    if not row["vnxdna_file"].startswith(str(tree)):
        raise RuntimeError(f"{config} imported {row['vnxdna_file']}, not {tree}")
    return {"config": config, "load1_start": load, **row}


def run(names: list[str]) -> None:
    commits = {c: subprocess.run(["git", "-C", str(t), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
               for c, t in CONFIGS.items()}
    for name in names:
        size, workers, _, seeds = ab.SCENARIOS[name]
        for i, seed in enumerate(seeds):
            w = ab.prepare(name, seed)
            if i == 0:
                for c in CONFIGS:
                    step(w, c, workers)                       # warm-up, discarded
            perm = list(itertools.permutations(CONFIGS))[(seed + i) % 6]
            rows = [{"scenario": name, "seed": seed, "workers": workers, "commit": commits[c], "position": k,
                     **step(w, c, workers)} for k, c in enumerate(list(perm) + list(perm)[::-1])]
            with open(OUT, "a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            for p in w.iterdir():
                p.unlink()
            w.rmdir()
            med = {c: statistics.median(r["seconds"] for r in rows if r["config"] == c) for c in CONFIGS}
            print(f"{name} seed {seed}: " + " | ".join(f"{c} {v:.3f}s" for c, v in med.items())
                  + f" | ratio {med['build'] / med['auto']:.3f}", flush=True)


def summary() -> dict:
    rows = [json.loads(x) for x in OUT.read_text().splitlines()]
    out: dict = {"label": "SIMULATED", "provisional": True, "ratio": "build / auto (> 1 = the new default faster)",
                 "commits": {r["config"]: r["commit"] for r in rows}, "scenarios": {}}
    for name in ab.SCENARIOS:
        rs = [r for r in rows if r["scenario"] == name]
        if not rs:
            continue
        seeds = sorted({r["seed"] for r in rs})
        med = {s: {c: statistics.median(r["seconds"] for r in rs if r["seed"] == s and r["config"] == c) for c in CONFIGS}
               for s in seeds}
        ratio = [med[s]["build"] / med[s]["auto"] for s in seeds]
        aa = [med[s]["build"] / med[s]["build2"] for s in seeds]
        out["scenarios"][name] = {
            "n_seeds": len(seeds), "seconds_median": {c: round(statistics.median(med[s][c] for s in seeds), 3) for c in CONFIGS},
            "ratio_build_over_auto": {"median": round(statistics.median(ratio), 3), "ci95": ab._boot_ci(ratio),
                                      "per_seed": [round(x, 3) for x in ratio]},
            "aa_build_over_build2": {"median": round(statistics.median(aa), 3), "ci95": ab._boot_ci(aa)},
            "all_success_and_identical": all(r["status"] == "SUCCESS" and r["matches_archive"] for r in rs),
            "retries": sum(any("sched_retry" in f for f in r["fallbacks"]) for r in rs if r["config"] == "auto"),
            "fallbacks": {c: sum(bool(r["fallbacks"]) for r in rs if r["config"] == c) for c in CONFIGS},
            "peak_rss_mib_median": {c: round(statistics.median(r["peak_rss_bytes"] for r in rs if r["config"] == c) / ab.MIB, 1)
                                    for c in CONFIGS},
            "load1_range": [round(min(r["load1_start"] for r in rs), 2), round(max(r["load1_start"] for r in rs), 2)],
        }
    OUT.with_name(OUT.stem + "-summary.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--scenarios", default=",".join(ab.SCENARIOS))
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a.scenarios.split(","))
    else:
        for name, r in summary()["scenarios"].items():
            print(f"{name:18s} {r['seconds_median']}  ratio {r['ratio_build_over_auto']['median']} "
                  f"{r['ratio_build_over_auto']['ci95']}  A/A {r['aa_build_over_build2']['median']} "
                  f"{r['aa_build_over_build2']['ci95']}  retries={r['retries']} ok={r['all_success_and_identical']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
