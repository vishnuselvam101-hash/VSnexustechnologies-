"""V9 Stage D/E: native polish kernel vs the V8 decoder on full noisy decodes (SIMULATED; docs/V9_PREREGISTRATION.md §7).

Each case simulates one 20,000-byte archive under a V8 matrix channel (the D13-F1 stress channel by default: the model is
INADEQUATE, so these are stress conditions, not realistic-channel results) and decodes the SAME read file twice:

* ``v8``: the V8 decoder exactly: native forward-backward, the NumPy polish (``polish.edit_costs_reference``);
* ``v9``: the same decoder with the native polish kernel (``polish.edit_costs`` resolves to native).

Required: identical outcome, identical decoded-container SHA-256 and identical stage counters (correctness first; any
difference stops V9 work). Decode seconds are recorded for both, with the host load.

    PYTHONPATH=src python experiments/v9/consensus/native_equivalence.py --seeds 90000-90004 --out results/equivalence-dev.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests" / "nanopore"))
sys.path.insert(0, str(ROOT / "experiments" / "v8" / "matrix"))
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"


def _decode(reads: Path, work: Path, profile: str, mode: str) -> dict:
    import nanofunnel as nf
    from vnxdna.pipeline.decode import decode_reads
    from vnxdna.recovery.cluster import polish

    case = {"decoder": {"read_clustering": "fallback", "cluster_config": {"consensus_template": "full"}}}
    out = work / f"out-{mode}.vnx"
    saved = polish.edit_costs
    if mode == "v8":
        polish.edit_costs = polish.edit_costs_reference
    try:
        t = time.perf_counter()
        try:
            res = decode_reads(reads, out, nf.decoder_options(case))
            err, rep = None, res.report
        except Exception as e:                      # explicit typed failures are outcomes, not crashes
            res, err, rep = None, e, dict(getattr(e, "details", {}) or {})
        secs = time.perf_counter() - t
    finally:
        polish.edit_costs = saved
    sha = hashlib.sha256(out.read_bytes()).hexdigest() if out.exists() else None
    if out.exists():
        out.unlink()
    return {"status": None if res is None else res.status, "error": None if err is None else type(err).__name__,
            "error_code": getattr(err, "code", None), "output_sha256": sha,
            "stage_counters": rep.get("stage_counters"), "seconds": round(secs, 3)}


def run_one(job: tuple) -> dict:
    import nanofunnel as nf
    from run import SIZE, case_model
    from vnxdna.simulation import engine

    channel, cov, profile, seed = job
    with tempfile.TemporaryDirectory(prefix="vnx-v9eq-") as tmp:
        work = Path(tmp)
        arc = nf.build_archive(work / "archive", SIZE, seed, profile)
        truth = engine.simulate_file_with_truth(arc["strands_path"], work / "reads.fastq", case_model(channel, cov), seed)
        v8 = _decode(work / "reads.fastq", work, profile, "v8")
        v9 = _decode(work / "reads.fastq", work, profile, "v9")
    same = all(v8[k] == v9[k] for k in ("status", "error", "error_code", "output_sha256", "stage_counters"))
    return {"channel": channel, "coverage": cov, "profile": profile, "seed": seed, "label": LABEL,
            "container_sha256": arc["container_sha256"], "reads_sha256": truth["reads_sha256"],
            "exact": v9["output_sha256"] == arc["container_sha256"],
            "identical": same, "v8": v8, "v9": v9, "speedup": round(v8["seconds"] / max(v9["seconds"], 1e-9), 3),
            "load1": round(os.getloadavg()[0], 2), "commit": _commit()}


def _commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _seeds(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="d13-f1")
    ap.add_argument("--coverages", default="3,5,10")
    ap.add_argument("--profiles", default="v4-balanced,v7-lowcov")
    ap.add_argument("--seeds", default="90000-90001")
    ap.add_argument("--out", default=str(HERE / "results" / "equivalence-dev.jsonl"))
    a = ap.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    bad = 0
    for cov in (int(c) for c in a.coverages.split(",")):
        for prof in a.profiles.split(","):
            for s in _seeds(a.seeds):
                row = run_one((a.channel, cov, prof, s))
                bad += not row["identical"]
                with out.open("a") as f:
                    f.write(json.dumps(row, sort_keys=True) + "\n")
                print(f"{a.channel} cov{cov} {prof} s{s}: identical={row['identical']} exact={row['exact']} "
                      f"v8 {row['v8']['seconds']}s v9 {row['v9']['seconds']}s x{row['speedup']}", flush=True)
    print("NOT IDENTICAL:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
