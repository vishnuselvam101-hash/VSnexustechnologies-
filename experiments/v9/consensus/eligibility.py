"""V9 consensus eligibility measurements (docs/V9_PREREGISTRATION.md §5: 0 false frames, deterministic). SIMULATED.

For a frozen candidate, re-decodes the EVAL primary-cell cases (the same read pools as the evaluation: same seeds, same
channel model) and records:

* false frames: cluster-stage frames that passed the frame's own RS + CRC-32 but differ from the true frame (ground
  truth from the simulator; the V8 funnel definition, ``nanofunnel.verified_keys``);
* determinism (every ``--det-stride``-th seed): output SHA-256 and outcome at 1 and 4 workers and on a repeat.

These measurements do not change the evaluation's outcomes; they decide only the eligibility conditions.

    PYTHONPATH=src python experiments/v9/consensus/eligibility.py --candidate E [--jobs 3]
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in ("tests/nanopore", "experiments/v9/consensus"):
    sys.path.insert(0, str(ROOT / p))
OUT = HERE / "results" / "eligibility.jsonl"
CELLS = [("d13-f1", c, p) for c in (3, 5, 10) for p in ("v4-balanced", "v7-lowcov")]
SEEDS = range(91000, 91100)


def _load(name: str, rel: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _commit() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def one(job: tuple) -> dict:
    import nanofunnel as nf
    from candidates import cluster_config
    from vnxdna.simulation import engine
    channel, cov, profile, seed, candidate, det = job
    MX = _load("v8_matrix_run", "experiments/v8/matrix/run.py")
    case = {"profile": profile, "decoder": {"read_clustering": "fallback", "cluster_config": cluster_config(candidate)}}
    opts = nf.decoder_options(case)
    with tempfile.TemporaryDirectory(prefix="vnx-v9el-") as tmp:
        w = Path(tmp)
        arc = nf.build_archive(w / "a", MX.SIZE, seed, profile)
        truth = engine.simulate_file_with_truth(arc["strands_path"], w / "r.fastq", MX.case_model(channel, cov), seed)
        key_index = {k: i for i, k in enumerate(arc["keys"])}

        def dec(o, name):
            out = w / f"{name}.vnx"
            d, cap = nf.run_decode(w / "r.fastq", out, arc["container_sha256"], o)
            _ok, false = nf.verified_keys(cap.frames, key_index, arc["payload"])
            return d, false, len(cap.frames)

        d1, false1, frames1 = dec(opts, "w1")
        row = {"candidate": candidate, "channel": channel, "coverage": cov, "profile": profile, "seed": seed,
               "reads_sha256": truth["reads_sha256"], "outcome": d1["outcome"], "false_frames": false1, "cluster_frames": frames1}
        if det:
            d4, false4, frames4 = dec(dataclasses.replace(opts, workers=4), "w4")
            dr, falser, framesr = dec(opts, "rep")
            same = (d1["outcome"], false1, frames1) == (d4["outcome"], false4, frames4) == (dr["outcome"], falser, framesr)
            row.update(deterministic=bool(same), det_outcomes=[d1["outcome"], d4["outcome"], dr["outcome"]],
                       det_frames=[frames1, frames4, framesr])
    row.update(load1=round(os.getloadavg()[0], 2), commit=_commit(), time=time.time())
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--det-stride", type=int, default=10)
    a = ap.parse_args(argv)
    rows = [json.loads(x) for x in OUT.read_text().splitlines()] if OUT.exists() else []
    done = {(r["candidate"], r["coverage"], r["profile"], r["seed"]) for r in rows}
    todo = [(ch, c, p, s, a.candidate, (s - SEEDS[0]) % a.det_stride == 0) for ch, c, p in CELLS for s in SEEDS
            if (a.candidate, c, p, s) not in done]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=a.jobs) as pool, OUT.open("a") as fh:
        for r in pool.map(one, todo):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
    rows = [json.loads(x) for x in OUT.read_text().splitlines() if json.loads(x)["candidate"] == a.candidate]
    print(json.dumps({"candidate": a.candidate, "cases": len(rows), "false_frames": sum(r["false_frames"] for r in rows),
                      "determinism_checked": sum("deterministic" in r for r in rows),
                      "nondeterministic": sum(r.get("deterministic") is False for r in rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
