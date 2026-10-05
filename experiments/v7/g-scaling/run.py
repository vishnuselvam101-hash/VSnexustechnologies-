"""G-SCALE: wall time and speed-up of encode and decode with 1, 2, 4 and 8 workers on a 16 MiB input (SIMULATED
channel; time and memory MEASURED on this host). The host is shared: the 1-minute load average before and after every
run is recorded, and the timings are only comparable within this file.

    PYTHONPATH=src python experiments/v7/g-scaling/run.py --out experiments/v7/g-scaling/results

Setup: a 16 MiB random payload (seed 7016, the G-MEM 16 MiB payload), encoded once to strands, simulated once per
model (illumina-like seed 71016: the decode succeeds; nanopore-like seed 71016: unfitted, the decode fails after
the full recovery effort). Measured: `vnx encode --workers W` and `vnx decode --workers W` in fresh processes
(experiments/v7/g-memory/measure.py), REPS repetitions per (command, W) with the worker order rotated per repetition.
Every run's output hash is recorded: the strands and the decoded container must not depend on W.
Speed-up = median wall(W=1) / median wall(W). Memory: the main process's VmHWM and the sampled process-tree peak.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "g-memory"))
import measure  # noqa: E402
from run import git, host, sha256_file  # noqa: E402  (experiments/v7/g-memory/run.py)

WORKERS = [1, 2, 4, 8]
STATEMENT = ("SIMULATED: software strands through shipped, unfitted channel models; no DNA was synthesised, stored or "
             "sequenced. Wall time and peak RSS are MEASURED on a shared host.")


def one(args: list[str], work: Path) -> dict:
    la0 = os.getloadavg()[0]
    r = measure.run_vnx(args, work, env={"PYTHONPATH": str(REPO / "src")})
    r.pop("_raw_samples")
    r.pop("timeline")
    res = r.pop("result", None) or {}
    r["status"] = res.get("status")
    r["stage_seconds"] = res.get("stage_seconds")
    r["load_1min_before"], r["load_1min_after"] = round(la0, 2), round(os.getloadavg()[0], 2)
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--size-mib", type=int, default=16)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--models", default="illumina-like:3,nanopore-like:1", help="model:reps,...")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    cli = [sys.executable, "-c", "from vnxdna.commands import main; main()"]
    t0 = time.time()
    rows = []
    with tempfile.TemporaryDirectory(prefix="vnx-g-scale-") as d:
        w = Path(d)
        payload, strands = w / "payload.bin", w / "strands.fasta"
        subprocess.run([*cli, "generate", str(payload), "--size", f"{a.size_mib}MiB", "--pattern", "random", "--seed",
                        str(7000 + a.size_mib)], check=True, env=env, capture_output=True)
        for rep in range(a.reps):
            order = WORKERS[rep % len(WORKERS):] + WORKERS[:rep % len(WORKERS)]
            for wk in order:
                s = w / f"s-{wk}.fasta"
                r = one(["encode", str(payload), str(s), "--workers", str(wk), "--compression", "none",
                         "--keep-archive", str(w / f"a-{wk}.vnx"), "--force"], w)
                r.update(op="encode", workers=wk, rep=rep, output_sha256=sha256_file(s),
                         container_sha256=sha256_file(w / f"a-{wk}.vnx"))
                rows.append(r)
                print(json.dumps({k: r[k] for k in ("op", "workers", "rep", "wall_seconds", "load_1min_before")}), flush=True)
                if wk == 1 and rep == 0:
                    s.replace(strands)
                    (w / "a-1.vnx").replace(w / "a.vnx")
                else:
                    s.unlink()
                    (w / f"a-{wk}.vnx").unlink()
        container_sha = sha256_file(w / "a.vnx")
        for spec in a.models.split(","):
            model, reps = spec.split(":")
            reads = w / f"reads-{model}.fastq"
            subprocess.run([*cli, "channel", "simulate", str(strands), str(reads), "--model", model, "--seed",
                            str(71000 + a.size_mib), "--workers", "6", "--force"], check=True, env=env, capture_output=True)
            reads_sha = sha256_file(reads)
            for rep in range(int(reps)):
                order = WORKERS[rep % len(WORKERS):] + WORKERS[:rep % len(WORKERS)]
                for wk in order:
                    o = w / "decoded.vnx"
                    r = one(["decode", str(reads), "-o", str(o), "--workers", str(wk), "--force"], w)
                    r.update(op="decode", model=model, workers=wk, rep=rep, reads_sha256=reads_sha,
                             output_sha256=sha256_file(o) if o.exists() else None)
                    r["exact"] = r["output_sha256"] == container_sha
                    rows.append(r)
                    print(json.dumps({k: r[k] for k in ("op", "model", "workers", "rep", "exit", "wall_seconds",
                                                        "load_1min_before")}), flush=True)
                    if o.exists():
                        o.unlink()
            reads.unlink()
    summary = []
    for key in [("encode", None)] + [("decode", s.split(":")[0]) for s in a.models.split(",")]:
        sel = [r for r in rows if r["op"] == key[0] and r.get("model") == key[1]]
        base = statistics.median(r["wall_seconds"] for r in sel if r["workers"] == 1)
        for wk in WORKERS:
            x = [r for r in sel if r["workers"] == wk]
            med = statistics.median(r["wall_seconds"] for r in x)
            summary.append({"op": key[0], "model": key[1], "workers": wk, "runs": len(x),
                            "wall_median_s": round(med, 2), "wall_min_s": min(r["wall_seconds"] for r in x),
                            "wall_max_s": max(r["wall_seconds"] for r in x), "speedup": round(base / med, 2),
                            "efficiency": round(base / med / wk, 2),
                            "main_vmhwm_mib": round(max(r["peak_rss_bytes"] for r in x) / 2**20, 1),
                            "tree_rss_peak_mib": round(max(r["tree_rss_peak_bytes"] for r in x) / 2**20, 1),
                            "load_1min_range": [min(r["load_1min_before"] for r in x), max(r["load_1min_after"] for r in x)],
                            "outputs_identical": len({r["output_sha256"] for r in x}) == 1,
                            "exits": sorted({r["exit"] for r in x})})
    identical = {op: len({r["output_sha256"] for r in rows if r["op"] == op and r.get("model") == m}) == 1
                 for op, m in [("encode", None)] + [("decode", s.split(":")[0]) for s in a.models.split(",")]}
    doc = {"experiment": "G-SCALE", "classification": "SIMULATED channel; wall time and peak RSS MEASURED",
           "statement": STATEMENT, "size_mib": a.size_mib, "workers": WORKERS, "container_sha256": container_sha,
           "git": {"commit": git("rev-parse", "HEAD"),
                           "dirty_tracked": bool(git("status", "--porcelain", "--untracked-files=no"))}, "host": host(),
           "started": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t0)), "elapsed_seconds": round(time.time() - t0, 1),
           "outputs_independent_of_workers": identical, "summary": summary, "rows": rows}
    (out / f"scaling-{a.size_mib}MiB.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(summary, indent=0))
    return 0 if all(identical.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
