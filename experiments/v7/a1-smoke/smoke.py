"""A1-SMOKE: the V7 read-clustering reference runs end to end (EXPERIMENTAL, SIMULATED; no claim).

    PYTHONPATH=src python experiments/v7/a1-smoke/smoke.py run --config experiments/v7/a1-smoke/config.json --jobs 4
    PYTHONPATH=src python experiments/v7/a1-smoke/smoke.py summarise --config experiments/v7/a1-smoke/config.json

A trial = (cell, seed): the cell's model (the UNFITTED V6 coverage variants written by A-DIAG) is simulated with the
trial seed on the strands of the A-DIAG archive (20,000 random bytes, data seed 6201, uncompressed, v4-balanced); the
same read file is decoded by the 6.0 default decoder ("off") and with ``read_clustering="fallback"``, both with the
observability-only stage counters. Outcomes follow protocol §6 (``vnxdna.benchmark.outcome``). The funnel is read
from the decoder's own report (``report["clustering"]``, ``stage_counters``); no ground truth is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "experiments" / "v6" / "phase4"))
import phase4 as p4  # noqa: E402

from vnxdna.benchmark.outcome import OUTCOMES, classify_outcome, decode_claim  # noqa: E402
from vnxdna.core.provenance import environment  # noqa: E402
from vnxdna.pipeline.decode import decode_reads  # noqa: E402
from vnxdna.recovery.options import DecodeOptions  # noqa: E402

CLASSIFICATION = "EXPERIMENTAL / SIMULATED"
STATEMENT = ("EXPERIMENTAL (protocol §7 exploration seeds 82020-82024), SIMULATED: software strands and the unfitted V6 "
             "channel models. No DNA was synthesised, stored or sequenced; no public data and no fitted model is used. "
             "A smoke check that the reference runs, not an efficacy result; no tuning.")
ARMS = {"off": {}, "fallback": {"read_clustering": "fallback"}}


def decode_arm(reads: Path, out: Path, sha: str, arm: str) -> dict:
    opts = DecodeOptions(stage_counters=True, workers=1, **ARMS[arm])
    t = time.perf_counter()
    claim, res, err = decode_claim(lambda: decode_reads(reads, out, opts), out)
    secs = time.perf_counter() - t
    oc = classify_outcome(claim, {"container": sha})
    rep = res.report if res is not None else dict(getattr(err, "details", {}) or {})
    if out.exists():
        out.unlink()
    sc = rep.get("stage_counters") or {}
    return {"outcome": oc, "status": None if res is None else res.status, "error_code": getattr(err, "code", None),
            "groups_failed": rep.get("groups_failed"), "terminal_stage": sc.get("terminal_stage"),
            "clustering": rep.get("clustering"), "stages": sc.get("stages"), "decode_seconds": round(secs, 2),
            "peak_rss_bytes": rep.get("peak_rss_bytes")}


def run_trial(job: dict) -> dict:
    model = p4.chn.load_model(REPO / job["model"])
    work = Path(tempfile.mkdtemp(prefix="vnx-a1smoke-", dir=job["tmp"]))
    try:
        reads = work / "reads.fastq"
        sc = p4.chn.simulate_with_sidecar(model, job["strands"], reads, job["seed"], workers=1, command=job["command"])
        arms = {arm: decode_arm(reads, work / f"{arm}.vnx", job["container_sha256"], arm) for arm in ARMS}
        return {"record": "trial", "classification": CLASSIFICATION, "cell": job["cell"], "seed": job["seed"],
                "model": model.name, "model_sha256": model.sha256, "reads_sha256": sc["output"]["sha256"],
                "reads": sc["output"]["reads"], "arms": arms}
    finally:
        shutil.rmtree(work, ignore_errors=True)


FUNNEL = (("reads", lambda c: c["stages"]["read_parsing"].get("reads", 0)),
          ("unplaced_stored", lambda c: c["clustering"]["store"]["unplaced_stored"]),
          ("clustered_reads", lambda c: c["clustering"]["clustering"].get("clustered_reads", 0)),
          ("unassigned_reads", lambda c: c["clustering"]["clustering"].get("unassigned", 0)),
          ("clusters", lambda c: c["clustering"]["clustering"].get("clusters", 0)),
          ("consensus_attempts", lambda c: c["clustering"]["consensus"].get("consensus_attempted", 0)),
          ("insufficient_reads", lambda c: c["clustering"]["consensus"].get("insufficient_reads", 0)),
          ("erasures_exceed_parity", lambda c: c["clustering"]["consensus"].get("erasures_exceed_parity", 0)),
          ("decode_failed", lambda c: c["clustering"]["consensus"].get("decode_failed", 0)),
          ("frames_verified", lambda c: c["clustering"]["frames_verified"]),
          ("frames_superblock", lambda c: c["clustering"]["clustering"].get("frames_superblock", 0)),
          ("frames_data", lambda c: c["clustering"]["clustering"].get("frames_data", 0)),
          ("fill_superblock_symbols", lambda c: c["clustering"]["fill"].get("superblock_symbols", 0)),
          ("fill_data_symbols", lambda c: c["clustering"]["fill"].get("data_symbols", 0)),
          ("fill_confirmations", lambda c: c["clustering"]["fill"].get("data_confirmations", 0)
           + c["clustering"]["fill"].get("superblock_confirmations", 0)),
          ("fill_conflicts", lambda c: c["clustering"]["fill"].get("data_conflicts", 0)
           + c["clustering"]["fill"].get("superblock_conflicts", 0)),
          ("rows_short", lambda c: c["clustering"]["fill"].get("rows_short", 0)),
          ("rows_filled", lambda c: c["clustering"]["fill"].get("rows_filled", 0)))


def summarise(trials: list) -> dict:
    out: dict = {}
    for cell in sorted({t["cell"] for t in trials}):
        ts = [t for t in trials if t["cell"] == cell]
        row: dict = {"trials": len(ts)}
        for arm in ARMS:
            oc = Counter(t["arms"][arm]["outcome"]["outcome"] for t in ts)
            row[arm] = {"outcomes": {o: oc.get(o, 0) for o in OUTCOMES},
                        "terminal_stage": dict(Counter(str(t["arms"][arm]["terminal_stage"]) for t in ts)),
                        "error_codes": dict(Counter(str(t["arms"][arm]["error_code"]) for t in ts)),
                        "median_decode_seconds": sorted(t["arms"][arm]["decode_seconds"] for t in ts)[len(ts) // 2]}
        fb = [t["arms"]["fallback"] for t in ts if t["arms"]["fallback"].get("clustering")]
        row["fallback"]["stage_status"] = dict(Counter(c["clustering"]["status"] for c in fb))
        row["fallback"]["trigger"] = dict(Counter(str(c["clustering"]["trigger"]) for c in fb))
        ran = [c for c in fb if c["clustering"]["status"] == "ran" and c.get("stages")]
        row["fallback"]["funnel_mean"] = {k: round(sum(f(c) for c in ran) / len(ran), 2) if ran else None
                                          for k, f in FUNNEL}
        out[cell] = row
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--jobs", type=int, default=4)
    s = sub.add_parser("summarise")
    s.add_argument("--config", required=True)
    a = ap.parse_args(argv)
    cfg_path = Path(a.config).resolve()
    cfg = json.loads(cfg_path.read_text())
    exp_dir = cfg_path.parent
    tp = exp_dir / "trials.jsonl"
    if a.cmd == "summarise":
        lines = [json.loads(x) for x in open(tp)]
        trials = [x for x in lines if x.get("record") == "trial"]
        header = next(x for x in lines if x.get("record") == "header")
        summ = summarise(trials)
        doc = {"record": "summary", "experiment": cfg["experiment"], "classification": CLASSIFICATION,
               "statement": STATEMENT, "config_sha256": header["config_sha256"], "git": header["git"],
               "trials": len(trials),
               "false_success_total": sum(v[arm]["outcomes"]["FALSE_SUCCESS"] for v in summ.values() for arm in ARMS),
               "crash_total": sum(v[arm]["outcomes"]["CRASH"] for v in summ.values() for arm in ARMS),
               "cells": summ}
        (exp_dir / "summary.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(json.dumps({k: doc[k] for k in ("trials", "false_success_total", "crash_total")}))
        return 2 if doc["false_success_total"] else 0
    tmp = Path(tempfile.mkdtemp(prefix="vnx-a1smoke-setup-"))
    command = " ".join(sys.argv)
    t0 = time.perf_counter()
    try:
        info = p4.prepare(tmp, {"size": cfg["size"], "profile": cfg["profile"]}, cfg["data_seed"])
        jobs = []
        for cell in cfg["cells"]:
            for i in range(cfg["seeds"]):
                seed = cfg["base_seed"] + i
                if not 82020 <= seed <= 82039:
                    raise SystemExit(f"seed {seed} outside the smoke range 82020-82039")
                jobs.append({"cell": cell["id"], "model": cell["model"], "seed": seed,
                             "strands": str(tmp / "strands.fasta"), "container_sha256": info["container_sha256"],
                             "tmp": str(tmp), "command": command})
        header = {"record": "header", "classification": CLASSIFICATION, "statement": STATEMENT, "command": command,
                  "config": cfg, "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
                  "git": p4.chn.git_info(), "environment": environment(), "backends": p4.backends(), "setup": info,
                  "jobs": a.jobs, "nice": os.nice(0)}
        with open(tp, "w") as fh:
            print(json.dumps(header, sort_keys=True, default=str), file=fh, flush=True)
            with ProcessPoolExecutor(max_workers=a.jobs) as pool:
                for res in pool.map(run_trial, jobs):
                    print(json.dumps(res, sort_keys=True, default=str), file=fh, flush=True)
                    print(res["cell"], res["seed"], {k: v["outcome"]["outcome"] for k, v in res["arms"].items()},
                          res["arms"]["fallback"]["decode_seconds"], flush=True)
            print(json.dumps({"record": "end", "wall_seconds": round(time.perf_counter() - t0, 1)}), file=fh, flush=True)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
