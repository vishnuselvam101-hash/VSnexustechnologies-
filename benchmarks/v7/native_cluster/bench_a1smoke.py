"""A1-SMOKE decode wall time: read clustering on the NumPy reference vs the native kernels (MEASURED; SIMULATED data).

The inputs are exactly those of ``experiments/v7/a1-smoke`` (config.json): the A-DIAG archive (20,000 random bytes,
data seed 6201, uncompressed, v4-balanced), the unfitted V6 nanopore-like and deletion-heavy coverage-10 models and
the exploration seeds 82020-82024. ``prepare`` re-simulates the ten read files and checks that each one has the
SHA-256 recorded in ``experiments/v7/a1-smoke/trials.jsonl``. ``run`` decodes every read file with
``DecodeOptions(stage_counters=True, read_clustering="fallback", workers=1)`` (the smoke's ``fallback`` arm) once per
backend (``VNXDNA_CLUSTER_BACKEND=reference`` / ``native``; every other kernel native in both), each decode in a fresh
process, alternating the backend order per trial; it requires identical outcomes (status, container SHA-256, protocol
§6 outcome, and the whole decode report except timings and the ``native_backends`` provenance). Then hyperfine times
seed 82020 of each cell with ``--runs 3`` per backend. Results: ``results/a1smoke_bench.json`` (+ hyperfine JSON).

    PYTHONPATH=src python benchmarks/v7/native_cluster/bench_a1smoke.py run [--work DIR] [--hyperfine-runs 3]

No DNA was synthesised, stored or sequenced; this measures software speed on simulated reads only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SMOKE = REPO / "experiments" / "v7" / "a1-smoke"
RESULTS = HERE / "results"
VOLATILE = {"seconds", "stage_seconds", "peak_rss_bytes", "elapsed", "elapsed_seconds", "wall_seconds",
            "native_backends"}


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def decode_one(reads: Path, out: Path, container_sha: str) -> dict:
    """One decode in this process (the smoke's fallback arm); JSON-able result with the report digest."""
    sys.path.insert(0, str(REPO / "src"))
    from vnxdna.benchmark.outcome import classify_outcome, decode_claim
    from vnxdna.native import backend_summary
    from vnxdna.pipeline.decode import decode_reads
    from vnxdna.recovery.options import DecodeOptions
    opts = DecodeOptions(stage_counters=True, workers=1, read_clustering="fallback")
    t = time.perf_counter()
    claim, res, err = decode_claim(lambda: decode_reads(reads, out, opts), out)
    secs = time.perf_counter() - t
    oc = classify_outcome(claim, {"container": container_sha})
    rep = res.report if res is not None else dict(getattr(err, "details", {}) or {})
    digest = hashlib.sha256(json.dumps(_strip(json.loads(json.dumps(rep, default=str))), sort_keys=True).encode())
    out_sha = sha256_file(out) if out.exists() else None
    if out.exists():
        out.unlink()
    return {"decode_seconds": round(secs, 3), "status": None if res is None else res.status,
            "error_code": getattr(err, "code", None), "outcome": oc["outcome"], "output_sha256": out_sha,
            "report_sha256": digest.hexdigest(), "cluster_backend": backend_summary()["cluster"]["backend"],
            "clustering_status": (rep.get("clustering") or {}).get("status")}


def prepare(work: Path) -> dict:
    sys.path.insert(0, str(REPO / "experiments" / "v6" / "phase4"))
    import phase4 as p4
    cfg = json.loads((SMOKE / "config.json").read_text())
    recorded = {(t["cell"], t["seed"]): t["reads_sha256"] for t in map(json.loads, open(SMOKE / "trials.jsonl"))
                if t.get("record") == "trial"}
    info = p4.prepare(work, {"size": cfg["size"], "profile": cfg["profile"]}, cfg["data_seed"])
    trials = []
    for cell in cfg["cells"]:
        model = p4.chn.load_model(REPO / cell["model"])
        for i in range(cfg["seeds"]):
            seed = cfg["base_seed"] + i
            reads = work / f"{cell['id'].replace('/', '_')}-{seed}.fastq"
            sc = p4.chn.simulate_with_sidecar(model, str(work / "strands.fasta"), reads, seed, workers=1,
                                              command="bench_a1smoke prepare")
            sha = sc["output"]["sha256"]
            if sha != recorded[(cell["id"], seed)]:
                raise SystemExit(f"{cell['id']} {seed}: reads {sha} differ from A1-SMOKE {recorded[(cell['id'], seed)]}")
            trials.append({"cell": cell["id"], "seed": seed, "reads": str(reads), "reads_sha256": sha,
                           "reads_count": sc["output"]["reads"]})
    return {"setup": info, "trials": trials}


def _decode_subprocess(backend: str, reads: str, out: Path, sha: str) -> dict:
    env = {**os.environ, "VNXDNA_CLUSTER_BACKEND": backend, "PYTHONPATH": str(REPO / "src")}
    t = time.perf_counter()
    res = subprocess.run([sys.executable, str(Path(__file__).resolve()), "decode", "--reads", reads, "--out", str(out),
                          "--container-sha256", sha], env=env, capture_output=True, text=True, check=True)
    wall = time.perf_counter() - t
    rec = json.loads(res.stdout.strip().splitlines()[-1])
    rec["process_seconds"] = round(wall, 3)
    if rec["cluster_backend"] != backend:
        raise SystemExit(f"cluster backend {rec['cluster_backend']} ran instead of {backend}")
    return rec


def provenance() -> dict:
    sys.path.insert(0, str(REPO / "src"))
    import numpy as np

    from vnxdna.native import cluster as nc
    from vnxdna.native import native_status
    git = {"commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip(),
           "dirty_tracked": bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO,
                                                capture_output=True, text=True).stdout.strip())}
    cpu = next((ln.split(":", 1)[1].strip() for ln in open("/proc/cpuinfo") if ln.startswith("model name")), None)
    lib = nc.status()["library"]
    hf = shutil.which("hyperfine")
    return {"git": git, "cpu": cpu, "logical_cpus": os.cpu_count(), "python": platform.python_version(),
            "numpy": np.__version__, "loadavg_before": list(os.getloadavg()), "nice": os.nice(0),
            "cluster_library": lib, "cluster_library_sha256": sha256_file(Path(lib)) if lib else None,
            "cluster_source_sha256": sha256_file(nc._SOURCE), "fb_level": nc.fb_level(),
            "native_status": {k: v.get("backend") for k, v in native_status()["kernels"].items()},
            "hyperfine": subprocess.run([hf, "--version"], capture_output=True, text=True).stdout.strip() if hf else None}


def run(work: Path, hf_runs: int) -> int:
    RESULTS.mkdir(exist_ok=True)
    prov = provenance()
    t0 = time.perf_counter()
    prep = prepare(work)
    sha = prep["setup"]["container_sha256"]
    rows = []
    for i, t in enumerate(prep["trials"]):
        order = ("reference", "native") if i % 2 == 0 else ("native", "reference")
        rec = {b: _decode_subprocess(b, t["reads"], work / f"o-{b}.vnx", sha) for b in order}
        same = all(rec["reference"][k] == rec["native"][k] for k in
                   ("status", "error_code", "outcome", "output_sha256", "report_sha256", "clustering_status"))
        rows.append({**{k: t[k] for k in ("cell", "seed", "reads_sha256", "reads_count")}, "order": list(order),
                     "reference": rec["reference"], "native": rec["native"], "identical": same,
                     "speedup_decode": round(rec["reference"]["decode_seconds"] / rec["native"]["decode_seconds"], 2)})
        print(t["cell"], t["seed"], rec["reference"]["outcome"], rec["reference"]["decode_seconds"], "->",
              rec["native"]["decode_seconds"], "identical" if same else "DIFFERENT", flush=True)
    cells: dict = {}
    for cell in sorted({r["cell"] for r in rows}):
        rs = [r for r in rows if r["cell"] == cell]
        med = {b: sorted(r[b]["decode_seconds"] for r in rs)[len(rs) // 2] for b in ("reference", "native")}
        cells[cell] = {"trials": len(rs), "median_decode_seconds": med,
                       "median_speedup": round(med["reference"] / med["native"], 2),
                       "speedup_range": [min(r["speedup_decode"] for r in rs), max(r["speedup_decode"] for r in rs)],
                       "outcomes": sorted({r["reference"]["outcome"] for r in rs})}
    hyper = {}
    hf = shutil.which("hyperfine")
    if hf and hf_runs > 0:
        for t in [x for x in prep["trials"] if x["seed"] == 82020]:
            name = t["cell"].replace("/", "_")
            out_json = RESULTS / f"hyperfine-{name}-82020.json"
            cmds = []
            for b in ("reference", "native"):
                cmds += ["-n", b, f"VNXDNA_CLUSTER_BACKEND={b} PYTHONPATH={REPO / 'src'} {sys.executable} "
                                  f"{Path(__file__).resolve()} decode --reads {t['reads']} --out {work / ('h-' + b + '.vnx')} "
                                  f"--container-sha256 {sha}"]
            subprocess.run([hf, "--runs", str(hf_runs), "--export-json", str(out_json), "--style", "basic", *cmds],
                           check=True)
            res = json.loads(out_json.read_text())["results"]
            for r in res:     # paths of the scratch directory are not provenance; keep the command shape only
                r["command"] = r["command"].replace(str(work), "<work>").replace(str(REPO), "<repo>")
            out_json.write_text(json.dumps({"results": res}, indent=1) + "\n")
            # with -n the result's "command" is the name; otherwise it starts with VNXDNA_CLUSTER_BACKEND=<name>
            m = {(r["command"] if r["command"] in ("reference", "native") else r["command"].split()[0].split("=")[1]): r
                 for r in res}
            hyper[f"{t['cell']}/82020"] = {"runs": hf_runs, "mean_seconds": {b: round(m[b]["mean"], 2) for b in m},
                                           "stddev_seconds": {b: round(m[b]["stddev"], 2) for b in m},
                                           "speedup_mean": round(m["reference"]["mean"] / m["native"]["mean"], 2),
                                           "file": out_json.name}
    doc = {"benchmark": "A1-SMOKE decode wall time, read clustering reference vs native", "label": "MEASURED",
           "data": "SIMULATED (A1-SMOKE inputs; reads SHA-256 checked against experiments/v7/a1-smoke/trials.jsonl)",
           "command": " ".join([Path(sys.argv[0]).name, *sys.argv[1:]]), "provenance": prov,
           "decoder": "DecodeOptions(stage_counters=True, read_clustering='fallback', workers=1); one decode per "
                      "process; VNXDNA_CLUSTER_BACKEND=reference|native, every other kernel native",
           "setup": prep["setup"], "trials": rows, "cells": cells, "hyperfine": hyper,
           "all_identical": all(r["identical"] for r in rows), "loadavg_after": list(os.getloadavg()),
           "wall_seconds": round(time.perf_counter() - t0, 1)}
    (RESULTS / "a1smoke_bench.json").write_text(json.dumps(doc, indent=1, default=str) + "\n")
    print(json.dumps({"all_identical": doc["all_identical"], "cells": cells, "hyperfine": hyper}))
    return 0 if doc["all_identical"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode")
    d.add_argument("--reads", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--container-sha256", required=True)
    r = sub.add_parser("run")
    r.add_argument("--work", default=None)
    r.add_argument("--hyperfine-runs", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "decode":
        print(json.dumps(decode_one(Path(a.reads), Path(a.out), a.container_sha256)))
        return 0
    work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="vnx-a1bench-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        return run(work, a.hyperfine_runs)
    finally:
        if not a.work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
