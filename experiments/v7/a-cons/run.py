"""A-CONS: reference consensus ("wildcard") vs full-template polish ("full"), nanopore-like channel (EXPERIMENTAL,
SIMULATED). Only ``ClusterConfig.consensus_template`` differs between the arms; everything else is the corpus case.

    PYTHONPATH=src python experiments/v7/a-cons/run.py frozen  --jobs 4   # tests/nanopore corpus (seeds 82040-82045)
    PYTHONPATH=src python experiments/v7/a-cons/run.py heldout --jobs 4   # seeds 82060-82099 x cov 3/5/10 (pre-registered)

One JSON line per (case, arm) in ``results/<set>.jsonl`` (resumable: finished lines are kept). Each case runs in a fresh
worker process, so ``peak_rss_bytes`` (ru_maxrss) is that decode's own peak."""
from __future__ import annotations

import argparse
import copy
import json
import os
import resource
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests" / "nanopore"))
import nanofunnel as nf  # noqa: E402

ARMS = ("wildcard", "full")
HELDOUT_SEEDS = range(82060, 82100)
COVERAGES = (3, 5, 10)


def heldout_cases() -> list[dict]:
    tmpl = {c["channel"]["coverage"]: c for c in nf.load_corpus() if c["tier"] == "slow"}
    out = []
    for seed in HELDOUT_SEEDS:
        for cov in COVERAGES:
            c = copy.deepcopy(tmpl[float(cov)])
            c["id"] = f"heldout-nanopore-cov{cov}-s{seed}"
            c["channel"]["seed"] = seed
            out.append(c)
    return out


def one(job) -> dict:
    case, arm = job
    c = copy.deepcopy(case)
    c["decoder"]["cluster_config"]["consensus_template"] = arm
    with tempfile.TemporaryDirectory(prefix="vnx-acons-") as tmp:
        d = nf.run_case(c, Path(tmp), oracle=False)
    fd = d["funnel_data"]
    return {"case": case["id"], "arm": arm, "coverage": case["channel"]["coverage"], "seed": case["channel"]["seed"],
            "outcome": d["decode"]["outcome"], "sha256_match": d["decode"]["outcome"] == "EXACT",
            "false_success": d["decode"]["outcome"] == "FALSE_SUCCESS", "data_total": fd["total"],
            "two_reads": fd["two_reads"], "clustered": fd["clustered"], "candidate": fd["candidate"],
            "consensus_ok": fd["valid_frame"], "data_frames": d["frames"]["data_recovered"],
            "superblock_frames": d["frames"]["superblock_recovered"], "false_frames": d["frames"]["false_frames"],
            "rows_decodable": d["rows"]["decodable_from_cluster_frames"], "decode_seconds": d["decode"]["decode_seconds"],
            "case_seconds": d["seconds"], "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "load1": round(os.getloadavg()[0], 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set", choices=("frozen", "heldout"))
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    cases = nf.load_corpus() if a.set == "frozen" else heldout_cases()
    out = HERE / "results" / f"{a.set}.jsonl"
    out.parent.mkdir(exist_ok=True)
    done = set()
    if out.exists():
        done = {(r["case"], r["arm"]) for r in map(json.loads, out.read_text().splitlines())}
    jobs = [(c, arm) for c in cases for arm in ARMS if (c["id"], arm) not in done]
    with ProcessPoolExecutor(max_workers=a.jobs, max_tasks_per_child=1) as pool, out.open("a") as fh:
        for r in pool.map(one, jobs):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
            print(r["case"], r["arm"], r["outcome"], r["data_frames"], f"{r['decode_seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
