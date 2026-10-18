"""V14 Phase 6 workstream C: end-to-end A/B of the engine address schedulers on one tree (SIMULATED reads).

    strand        engine_scheduler="strand" (the V14 adaptive scheduler, the candidate)
    corroborated  engine_scheduler="corroborated" (the corrected V13 rule, the baseline)
    corroborated2 the baseline again (A/A: the noise band of this host)

Protocol (PREREGISTERED in docs/V14_SCHEDULER.md §6 before any row was measured): inputs are generated once per
(scenario, seed) with the bench generator (vnxdna datagen random -> archive -> default DNA options -> channel simulator,
channel seed = seed + 7, as experiments/arch/compare.py); one discarded warm-up per config on the first seed of each
scenario; then per seed the configs run in the order A B B A (A = strand on even seeds, corroborated on odd seeds; the
A/A config runs after them); every run is a separate process decoding the whole read file (decode_reads, engine auto)
and reports wall seconds, peak RSS of that process, status, output SHA-256 and the engine's pass-1 counters. Per seed,
a config's time is the median of its runs; the ratio is corroborated / strand (> 1 = the candidate is faster); the 95 %
CI is the percentile bootstrap of the median per-seed ratio (10000 resamples, seed 11; with n = 5 it equals the min-max
range). The host is shared, so every number is PROVISIONAL until re-measured on a quiet host.

    PYTHONPATH=src python experiments/v14/bench/p6c-scheduler/ab.py run [--scenarios a,b] [--reps 2]
    PYTHONPATH=src python experiments/v14/bench/p6c-scheduler/ab.py summary
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = Path(os.environ.get("VNX_BENCH_OUT") or HERE / "ab.jsonl")
CACHE = Path(os.environ.get("VNX_AB_INPUTS") or HERE / "inputs")      # one input at a time, deleted after use
MIB = 1 << 20

INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002, "coverage": 5}
#: name: (input bytes, workers, channel, seeds) — the V13/V14 bench scenarios plus a shuffled pool (preregistered)
SCENARIOS = {
    "indel-w1": (4 * MIB, 1, INDEL, range(95500, 95505)),
    "indel-w4": (4 * MIB, 4, INDEL, range(95300, 95305)),
    "indel-rc-w4": (4 * MIB, 4, {**INDEL, "reverse_complement_rate": 0.5}, range(97000, 97005)),
    "clean-w4": (4 * MIB, 4, {"coverage": 3}, range(95100, 95105)),
    "substitution-w4": (4 * MIB, 4, {"substitution_rate": 0.02, "coverage": 5}, range(95200, 95205)),
    "indel-shuffled-w1": (4 * MIB, 1, {**INDEL, "shuffle_window": 1 << 21}, range(97400, 97405)),
}
CONFIGS = {"strand": "strand", "corroborated": "corroborated", "corroborated2": "corroborated"}

ONE = r"""
import hashlib, json, resource, sys, time
from pathlib import Path
from vnxdna.v4 import decoder as de
from vnxdna.recovery.options import DecodeOptions
w, out, sched, workers = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
t0 = time.perf_counter()
res = de.decode_reads(w / "r.fastq", out, DecodeOptions(workers=workers, engine_scheduler=sched), overwrite=True)
sec = time.perf_counter() - t0
data = out.read_bytes() if out.exists() else b""
eng = res.report.get("engine") or {}
print(json.dumps({"seconds": sec, "status": res.status, "output_sha256": hashlib.sha256(data).hexdigest(),
                  "matches_archive": data == (w / "a.vnx").read_bytes(), "rung": eng.get("rung"),
                  "fallbacks": eng.get("fallbacks"), "pass1": eng.get("pass1"),
                  "stage_seconds": res.report.get("stage_seconds"), "vnxdna_file": de.__file__,
                  "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                  "cpu_seconds": resource.getrusage(resource.RUSAGE_SELF).ru_utime
                  + resource.getrusage(resource.RUSAGE_SELF).ru_stime}))
out.unlink(missing_ok=True)
"""


def prepare(name: str, seed: int) -> Path:
    size, _, channel, _ = SCENARIOS[name]
    w = CACHE / f"{name}-{seed}"
    w.mkdir(parents=True, exist_ok=True)
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import channel as ch
    from vnxdna.v4 import datagen
    from vnxdna.v4 import encoder as en
    datagen.generate(w / "in.bin", size, "random", seed)
    ar.build_archive([w / "in.bin"], w / "a.vnx", ar.ArchiveOptions())
    en.encode_container(w / "a.vnx", w / "s.fasta", en.DNAOptions())
    ch.simulate_file(w / "s.fasta", w / "r.fastq", ch.ChannelConfig(seed=seed + 7, **channel))
    (w / "s.fasta").unlink()
    return w


def step(w: Path, config: str, workers: int) -> dict:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    load = os.getloadavg()[0]
    t0 = time.perf_counter()
    p = subprocess.Popen([sys.executable, "-c", ONE, str(w), str(w / f"o-{config}.vnx"), CONFIGS[config], str(workers)],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate()
    if p.returncode != 0:
        raise RuntimeError(f"{config} failed: {err[-2000:]}")
    row = json.loads(out.strip().splitlines()[-1])
    if not row["vnxdna_file"].startswith(str(ROOT)):
        raise RuntimeError(f"imported {row['vnxdna_file']}, not this tree")
    return {"config": config, "load1_start": load, "process_seconds": round(time.perf_counter() - t0, 3), **row}


def run(names: list[str], reps: int) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "src"], capture_output=True,
                                text=True).stdout.strip())
    for name in names:
        size, workers, channel, seeds = SCENARIOS[name]
        for i, seed in enumerate(seeds):
            w = prepare(name, seed)
            if i == 0:
                for c in CONFIGS:
                    step(w, c, workers)                       # warm-up, discarded
            if os.environ.get("VNX_AB_ORDER") == "v1":     # the first indel-w1 pass: A B B A, then the A/A runs
                a, b = ("strand", "corroborated") if seed % 2 == 0 else ("corroborated", "strand")
                order = [a, b] + [b, a] * (reps - 1) if reps > 1 else [a, b]
                order += ["corroborated2"] * reps
            else:                                            # amended: the A/A config interleaved, mirrored order
                perm = list(itertools.permutations(CONFIGS))[(seed + i) % 6]
                order = (list(perm) + list(perm)[::-1]) * ((reps + 1) // 2)
            rows = []
            for k, c in enumerate(order):
                r = step(w, c, workers)
                rows.append({"scenario": name, "seed": seed, "workers": workers, "size": size, "channel": channel,
                             "commit": commit, "src_dirty": dirty, "position": k, "order": order, **r})
            with open(OUT, "a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            for p in w.iterdir():
                p.unlink()
            w.rmdir()
            med = {c: statistics.median(r["seconds"] for r in rows if r["config"] == c) for c in CONFIGS}
            same = len({(r["output_sha256"], r["status"]) for r in rows}) == 1
            print(f"{name} seed {seed}: " + " | ".join(f"{c} {v:.3f}s" for c, v in med.items())
                  + f" | ratio {med['corroborated'] / med['strand']:.3f} | identical={same}", flush=True)


def _boot_ci(x: list[float], n: int = 10000, seed: int = 11) -> list[float]:
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(x, k=len(x))) for _ in range(n))
    return [round(meds[int(0.025 * n)], 3), round(meds[int(0.975 * n) - 1], 3)]


def summary() -> dict:
    rows = [json.loads(x) for x in OUT.read_text().splitlines()]
    out: dict = {"label": "SIMULATED", "provisional": True,
                 "method": "same tree, engine_scheduler strand vs corroborated (+ corroborated A/A), paired per seed, "
                           "order A B B A, per-seed median of the runs; ratio = corroborated / strand (> 1 = strand "
                           "faster); 95 % CI = percentile bootstrap of the median per-seed ratio (10000 resamples, seed "
                           "11)", "commits": sorted({r["commit"] for r in rows}), "scenarios": {}}
    for name in SCENARIOS:
        rs = [r for r in rows if r["scenario"] == name]
        if not rs:
            continue
        seeds = sorted({r["seed"] for r in rs})
        med = {s: {c: statistics.median(r["seconds"] for r in rs if r["seed"] == s and r["config"] == c) for c in CONFIGS}
               for s in seeds}
        ratio = [med[s]["corroborated"] / med[s]["strand"] for s in seeds]
        cpu = {s: {c: statistics.median(r["cpu_seconds"] for r in rs if r["seed"] == s and r["config"] == c)
                   for c in CONFIGS} for s in seeds if all("cpu_seconds" in r for r in rs if r["seed"] == s)}
        cratio = [cpu[s]["corroborated"] / cpu[s]["strand"] for s in cpu]
        caa = [cpu[s]["corroborated"] / cpu[s]["corroborated2"] for s in cpu]
        aa = [med[s]["corroborated"] / med[s]["corroborated2"] for s in seeds]
        p1 = {c: [r["pass1"] for r in rs if r["config"] == c and r.get("pass1")] for c in ("strand", "corroborated")}
        out["scenarios"][name] = {
            "n_seeds": len(seeds), "runs": len(rs), "workers": rs[0]["workers"],
            "seconds_median": {c: round(statistics.median(med[s][c] for s in seeds), 3) for c in CONFIGS},
            "ratio_corroborated_over_strand": {"median": round(statistics.median(ratio), 3), "ci95": _boot_ci(ratio),
                                               "per_seed": [round(x, 3) for x in ratio]},
            "aa_corroborated_over_corroborated2": {"median": round(statistics.median(aa), 3), "ci95": _boot_ci(aa),
                                                   "per_seed": [round(x, 3) for x in aa]},
            "cpu_seconds_median": {c: round(statistics.median(cpu[s][c] for s in cpu), 3) for c in CONFIGS} if cpu else None,
            "cpu_ratio_corroborated_over_strand": {"median": round(statistics.median(cratio), 3), "ci95": _boot_ci(cratio),
                                                   "per_seed": [round(x, 3) for x in cratio]} if cratio else None,
            "cpu_aa": {"median": round(statistics.median(caa), 3), "ci95": _boot_ci(caa)} if caa else None,
            "all_success_and_identical": all(r["status"] == "SUCCESS" and r["matches_archive"] for r in rs)
            and all(len({r["output_sha256"] for r in rs if r["seed"] == s}) == 1 for s in seeds),
            "false_success": sum(r["status"] == "SUCCESS" and not r["matches_archive"] for r in rs),
            "fallbacks": sum(bool(r["fallbacks"]) for r in rs),
            "attempted_median": {c: statistics.median(p["attempted"] for p in v) for c, v in p1.items() if v},
            "full_attempts_median": {c: statistics.median(p["full_attempts"] for p in v) for c, v in p1.items() if v},
            "peak_rss_mib_median": {c: round(statistics.median(r["peak_rss_bytes"] for r in rs if r["config"] == c) / MIB, 1)
                                    for c in CONFIGS},
            "load1_range": [round(min(r["load1_start"] for r in rs), 2), round(max(r["load1_start"] for r in rs), 2)],
        }
    path = OUT.with_name(OUT.stem + "-summary.json")
    path.write_text(json.dumps(out, indent=2) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a.scenarios.split(","), a.reps)
    else:
        for name, r in summary()["scenarios"].items():
            print(f"{name:18s} {r['seconds_median']}  ratio {r['ratio_corroborated_over_strand']['median']} "
                  f"{r['ratio_corroborated_over_strand']['ci95']}  A/A {r['aa_corroborated_over_corroborated2']['median']} "
                  f"{r['aa_corroborated_over_corroborated2']['ci95']}  ok={r['all_success_and_identical']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
