"""A-PAR: outer parity for low coverage — profile v4-balanced (M=16) vs v7-lowcov (M=48), both with the full-template
consensus (EXPERIMENTAL, SIMULATED). Only the encoding profile differs between the arms.

    PYTHONPATH=src python experiments/v7/a-par/run.py dev     --jobs 4   # seeds 82043-82045, cov 5 (development)
    PYTHONPATH=src python experiments/v7/a-par/run.py heldout --jobs 4   # seeds 82060-82099 x cov 3/5/10 (pre-registered)

One JSON line per (case, profile) in ``results/<set>.jsonl`` (resumable)."""
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

PROFILES = ("v4-balanced", "v7-lowcov")
SETS = {"dev": (range(82043, 82046), (5,), ("v7-lowcov",)),
        "heldout": (range(82060, 82100), (3, 5, 10), ("v7-lowcov",))}


def one(job) -> dict:
    seed, cov, profile = job
    tmpl = {c["channel"]["coverage"]: c for c in nf.load_corpus() if c["tier"] == "slow"}[float(cov)]
    c = copy.deepcopy(tmpl)
    c["id"] = f"apar-{profile}-cov{cov}-s{seed}"
    c["profile"] = profile
    c["channel"]["seed"] = seed
    c["decoder"]["cluster_config"]["consensus_template"] = "full"
    with tempfile.TemporaryDirectory(prefix="vnx-apar-") as tmp:
        d = nf.run_case(c, Path(tmp), oracle=False)
    fd = d["funnel_data"]
    return {"case": c["id"], "profile": profile, "coverage": cov, "seed": seed, "outcome": d["decode"]["outcome"],
            "sha256_match": d["decode"]["outcome"] == "EXACT", "false_success": d["decode"]["outcome"] == "FALSE_SUCCESS",
            "strands_total": d["strands_total"], "data_total": fd["total"], "consensus_ok": fd["valid_frame"],
            "data_frames": d["frames"]["data_recovered"], "false_frames": d["frames"]["false_frames"],
            "rows_decodable": d["rows"]["decodable_from_cluster_frames"], "decode_seconds": d["decode"]["decode_seconds"],
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "load1": round(os.getloadavg()[0], 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set", choices=tuple(SETS))
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    seeds, covs, profiles = SETS[a.set]
    out = HERE / "results" / f"{a.set}.jsonl"
    out.parent.mkdir(exist_ok=True)
    done = {(r["seed"], r["coverage"], r["profile"]) for r in map(json.loads, out.read_text().splitlines())} \
        if out.exists() else set()
    jobs = [(s, c, p) for s in seeds for c in covs for p in profiles if (s, c, p) not in done]
    with ProcessPoolExecutor(a.jobs, max_tasks_per_child=1) as pool, out.open("a") as fh:
        for r in pool.map(one, jobs):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
            print(r["case"], r["outcome"], r["data_frames"], r["data_total"], f"{r['decode_seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
