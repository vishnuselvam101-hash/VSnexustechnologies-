"""Default-path runtime check for Phase 4 (SIMULATED): does the Phase 4 code cost anything when soft decoding is off?

The Phase 2/3 end-to-end workload (4 MiB random input, seed 42, EXP-0011 channel, seed 1011) is decoded by two source
trees on the *same* read file: the Phase 4 tree under test and a clean detached worktree of the Phase 3 commit.
Every decode runs in a fresh interpreter (``PYTHONPATH=<tree>/src``), trees alternate within each repetition, and
each child reports the module path it imported. Both trees load the same native aligner library (``align.c`` is
identical in both commits). Peak RSS is the child's own VmHWM.

usage: python experiments/v5/phase4/runtime_default_path.py --baseline-tree <path to a clean 3633dfb worktree> [--reps 3]
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

import p4common as p4
from p4common import pc

sys.path.insert(0, str(p4.REPO / "experiments" / "v5" / "phase3"))
import p3archive as pa  # noqa: E402

EXP0011 = {"substitution_rate": 0.002, "insertion_rate": 0.0005, "deletion_rate": 0.0005, "dropout_rate": 0.02,
           "coverage": 3, "coverage_model": "poisson", "seed": 1011}
NAME = "P4-runtime-default-path"

CHILD = r"""
import hashlib, json, sys, tempfile, time
from pathlib import Path
import vnxdna
from vnxdna.v4 import archive as ar, decoder as de
reads, out, workers, opts, sha_in = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]), json.loads(sys.argv[4]), sys.argv[5]
t = time.perf_counter()
res = de.decode_reads(reads, out, de.DecodeOptions(workers=workers, **opts), overwrite=True)
secs = time.perf_counter() - t
sha = None
if res.status == "SUCCESS":
    with tempfile.TemporaryDirectory() as x:
        ar.extract(out, x)
        sha = hashlib.sha256((Path(x) / "input.bin").read_bytes()).hexdigest()
out.unlink(missing_ok=True)
hwm = next(int(l.split()[1]) for l in open("/proc/self/status") if l.startswith("VmHWM:"))
print(json.dumps({"status": res.status, "verified": sha == sha_in, "seconds": round(secs, 3),
                  "peak_rss_mb": round(hwm / 1024, 1), "module": vnxdna.__file__}))
"""


def run(tree: Path, reads: Path, work: Path, workers: int, opts: dict, sha: str, lib: str) -> dict:
    env = {**os.environ, "PYTHONPATH": str(tree / "src"), "VNXDNA_NATIVE_LIB": lib}
    out = work / f"o-{time.time_ns()}.vnx"
    cp = subprocess.run([sys.executable, "-c", CHILD, str(reads), str(out), str(workers), json.dumps(opts), sha],
                        env=env, capture_output=True, text=True, check=True)
    r = json.loads(cp.stdout.strip().splitlines()[-1])
    assert Path(r["module"]).resolve().is_relative_to(tree.resolve()), (r["module"], tree)
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-tree", type=Path, required=True)
    ap.add_argument("--reps", type=int, default=3)
    a = ap.parse_args()
    lib = str(next((p4.REPO / "src" / "vnxdna" / "v5").glob("_vnx_align*.so")))
    trees = {"phase4": p4.REPO, "3633dfb": a.baseline_tree}
    configs = {"V4 default": {}, "V5 smart (Phase 3)": {"indel_recovery": "smart"},
               "V5 smart + soft auto": {"indel_recovery": "smart", "soft_decoding": "auto"}}
    t_all = time.perf_counter()
    rows = []
    with tempfile.TemporaryDirectory(prefix="p4rt-") as tmp:
        work = Path(tmp)
        info = pa.build(work, 4 << 20, 42)
        reads = pa.simulate(work, "exp0011", EXP0011)
        for workers in (1, 8):
            for cname, opts in configs.items():
                for rep in range(a.reps):
                    for tname, tree in trees.items():
                        if tname == "3633dfb" and "soft_decoding" in opts:
                            continue
                        r = run(tree, reads, work, workers, opts, info["input_sha256"], lib)
                        rows.append({"workers": workers, "config": cname, "tree": tname, "rep": rep, **r})
                        print(f"workers {workers}  {cname:22s} {tname:8s} rep {rep}: {r['status']} verified {r['verified']} "
                              f"{r['seconds']}s  {r['peak_rss_mb']} MB", flush=True)
    summary = {}
    for workers in (1, 8):
        for cname in configs:
            for tname in trees:
                rs = [r for r in rows if (r["workers"], r["config"], r["tree"]) == (workers, cname, tname)]
                if rs:
                    summary[f"workers {workers} | {cname} | {tname}"] = {
                        "verified_success": sum(r["verified"] for r in rs), "runs": len(rs),
                        "seconds_median": statistics.median(r["seconds"] for r in rs),
                        "seconds": [r["seconds"] for r in rs], "peak_rss_mb": [r["peak_rss_mb"] for r in rs]}
    cfg = {"input": {"size": 4 << 20, "pattern": "random", "seed": 42}, "channel": EXP0011, "reps": a.reps,
           "baseline_tree_commit": subprocess.run(["git", "-C", str(a.baseline_tree), "rev-parse", "HEAD"],
                                                  capture_output=True, text=True).stdout.strip(),
           "native_lib": lib, "configs": configs}
    pc.write_result(p4.HERE / NAME, NAME, cfg, {"summary": summary, "rows": rows,
                                                "wall_seconds": round(time.perf_counter() - t_all, 1)})


if __name__ == "__main__":
    main()
