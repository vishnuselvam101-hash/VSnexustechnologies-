"""V9 consensus development proxy on DEV seeds only (SIMULATED; docs/V9_PREREGISTRATION.md §5 "development").

For each configuration, decodes a fixed set of DEV cases (D13-F1 stress channel by default) and records the cluster
stage's verified frames, EXACT and FALSE SUCCESS per case. More verified frames from the same reads is the development
signal; the candidates are compared on ORE only in the one EVAL evaluation. Results are kept (rejected configurations
included) in ``results/dev-proxy.jsonl``.

    PYTHONPATH=src python experiments/v9/consensus/dev_proxy.py --name A-c3-4 --config '{"consensus_template": "full", "c_indel": 4}'
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests" / "nanopore"))
sys.path.insert(0, str(ROOT / "experiments" / "v8" / "matrix"))
OUT = HERE / "results" / "dev-proxy.jsonl"
CASES = [(c, p, s) for c in (3, 5) for p in ("v4-balanced", "v7-lowcov") for s in range(90000, 90004)]


def one(job: tuple) -> dict:
    import nanofunnel as nf
    import run as MX
    from vnxdna.simulation import engine
    channel, cov, profile, seed, cfg, decoder = job
    with tempfile.TemporaryDirectory(prefix="vnx-v9dp-") as tmp:
        w = Path(tmp)
        arc = nf.build_archive(w / "a", MX.SIZE, seed, profile)
        engine.simulate_file_with_truth(arc["strands_path"], w / "r.fastq", MX.case_model(channel, cov), seed)
        opts = nf.decoder_options({"decoder": {"read_clustering": "fallback", "cluster_config": cfg}})
        opts = dataclasses.replace(opts, **decoder)
        dec, cap = nf.run_decode(w / "r.fastq", w / "o.vnx", arc["container_sha256"], opts)
    return {"coverage": cov, "profile": profile, "seed": seed, "outcome": dec["outcome"],
            "frames": len(cap.frames), "seconds": dec["decode_seconds"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--config", required=True, help="cluster configuration (JSON)")
    ap.add_argument("--decoder", default="{}", help="extra DecodeOptions fields (JSON)")
    ap.add_argument("--channel", default="d13-f1")
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args()
    cfg, decoder = json.loads(a.config), json.loads(a.decoder)
    t = time.time()
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        rows = list(pool.map(one, [(a.channel, c, p, s, cfg, decoder) for c, p, s in CASES]))
    rec = {"name": a.name, "config": cfg, "decoder": decoder, "channel": a.channel, "seeds": "DEV 90000-90003",
           "frames": sum(r["frames"] for r in rows), "exact": sum(r["outcome"] == "EXACT" for r in rows),
           "false_success": sum(r["outcome"] == "FALSE_SUCCESS" for r in rows), "cases": rows,
           "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2], "wall": round(time.time() - t, 1),
           "load1": round(os.getloadavg()[0], 2), "label": "SIMULATED; DEV development record"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    print(f"{a.name}: frames={rec['frames']} exact={rec['exact']}/{len(rows)} false_success={rec['false_success']} "
          f"median {rec['median_seconds']}s")
    return 1 if rec["false_success"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
