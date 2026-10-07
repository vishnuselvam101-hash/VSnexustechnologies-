"""A-CONF: confirmatory test of Nanopore Phase 1 on fresh seeds 82100-82139 (EXPERIMENTAL, SIMULATED).

Arms (see PREREGISTRATION.md): ``old`` (v4-balanced, wildcard), ``phase1`` (v4-balanced, full), ``lowcov`` (v7-lowcov,
full). Everything else is the slow-tier corpus case of each coverage.

    PYTHONPATH=src python experiments/v7/a-conf/run.py --jobs 4

One JSON line per (case, arm) in ``results/confirmatory.jsonl`` (resumable). Each decode runs in a fresh worker process,
so ``peak_rss_bytes`` (ru_maxrss) is that decode's own peak."""
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
sys.path.insert(0, str(HERE.parents[2] / "tests" / "nanopore"))
import nanofunnel as nf  # noqa: E402

SEEDS = range(82100, 82140)
COVERAGES = (3, 5, 10)
ARMS = {"old": ("v4-balanced", "wildcard"), "phase1": ("v4-balanced", "full"), "lowcov": ("v7-lowcov", "full")}


def one(job) -> dict:
    seed, cov, arm = job
    profile, template = ARMS[arm]
    tmpl = {c["channel"]["coverage"]: c for c in nf.load_corpus() if c["tier"] == "slow"}[float(cov)]
    c = copy.deepcopy(tmpl)
    c["id"] = f"aconf-cov{cov}-s{seed}"
    c["profile"] = profile
    c["channel"]["seed"] = seed
    c["decoder"]["cluster_config"]["consensus_template"] = template
    with tempfile.TemporaryDirectory(prefix="vnx-aconf-") as tmp:
        d = nf.run_case(c, Path(tmp), oracle=False)
    fd = d["funnel_data"]
    return {"case": c["id"], "arm": arm, "profile": profile, "consensus_template": template, "coverage": cov,
            "seed": seed, "outcome": d["decode"]["outcome"], "false_success": d["decode"]["outcome"] == "FALSE_SUCCESS",
            "strands_total": d["strands_total"], "data_total": fd["total"], "two_reads": fd["two_reads"],
            "clustered": fd["clustered"], "candidate": fd["candidate"], "consensus_ok": fd["valid_frame"],
            "data_frames": d["frames"]["data_recovered"], "false_frames": d["frames"]["false_frames"],
            "rows_decodable": d["rows"]["decodable_from_cluster_frames"], "decode_seconds": d["decode"]["decode_seconds"],
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "load1": round(os.getloadavg()[0], 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    out = HERE / "results" / "confirmatory.jsonl"
    out.parent.mkdir(exist_ok=True)
    done = set()
    if out.exists():
        done = {(r["seed"], r["coverage"], r["arm"]) for r in map(json.loads, out.read_text().splitlines())}
    jobs = [(s, cov, arm) for s in SEEDS for cov in COVERAGES for arm in ARMS if (s, cov, arm) not in done]
    with ProcessPoolExecutor(max_workers=a.jobs, max_tasks_per_child=1) as pool, out.open("a") as fh:
        for r in pool.map(one, jobs):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
            print(r["case"], r["arm"], r["outcome"], r["data_frames"], f"{r['decode_seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
