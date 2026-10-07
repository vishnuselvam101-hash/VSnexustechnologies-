"""V9 oracle-gap experiment (SIMULATED; UPPER-BOUND / ORACLE levels; docs/V9_PREREGISTRATION.md §3, §5).

The V8.10 methodology (``experiments/v8/oracle/run.py``) with V9 seeds, channels and consensus candidates. For each case
(channel, coverage, profile, seed) the same read file is decoded at the requested levels:

* ``production``: the decoder with consensus candidate ``--candidate`` (``experiments/v9/consensus/candidates.py``);
* ``oracle_strand_identity``: reads grouped by their true source strand, the candidate's own consensus;
* ``oracle_consensus_2reads`` / ``oracle_consensus_1read``: the true frame of every strand with >= 2 / >= 1 reads.

The consensus oracles do not depend on the candidate and are stored once per case (``candidate: null``). ORE per cell is
|P ∩ O| / |O| (P: seeds where production is EXACT, O: seeds where oracle_consensus_2reads is EXACT); ``--summarise``
writes it with Wilson 95 % intervals. Oracle results are upper bounds, not achievable production performance.

    PYTHONPATH=src python experiments/v9/oracle/run.py --seeds 90000-90009 --levels production,oracle_consensus_2reads \
        --candidate v8 --out experiments/v9/oracle/results/dev.jsonl --jobs 3
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (ROOT / "tests" / "nanopore", ROOT / "experiments" / "v8" / "matrix", ROOT / "experiments" / "v9" / "consensus"):
    sys.path.insert(0, str(p))
LEVELS = ("production", "oracle_strand_identity", "oracle_consensus_2reads", "oracle_consensus_1read")
CANDIDATE_LEVELS = ("production", "oracle_strand_identity")
LABEL = "SIMULATED; oracle levels are UPPER-BOUND / ORACLE results, not achievable production performance"


def _commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_one(job: tuple) -> list[dict]:
    import nanofunnel as nf
    import run as MX                                    # experiments/v8/matrix/run.py (the V8 oracle is loaded by path)
    from candidates import cluster_config
    from vnxdna.native.reads import iter_reads
    from vnxdna.simulation import engine

    channel, cov, profile, seed, levels, candidate = job
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("v8_oracle", ROOT / "experiments" / "v8" / "oracle" / "run.py")
    v8o = module_from_spec(spec)
    spec.loader.exec_module(v8o)                        # type: ignore[union-attr]
    model = MX.case_model(channel, cov)
    case = {"profile": profile, "decoder": {"read_clustering": "fallback",
                                            "cluster_config": cluster_config(candidate)}}
    rows = []
    with tempfile.TemporaryDirectory(prefix="vnx-v9o-") as tmp:
        w = Path(tmp)
        arc = nf.build_archive(w / "archive", MX.SIZE, seed, profile)
        truth = engine.simulate_file_with_truth(arc["strands_path"], w / "reads.fastq", model, seed)
        reads = []
        for b in iter_reads(w / "reads.fastq", 1 << 17):
            o = np.concatenate([[0], np.cumsum(b.lengths)])
            reads += [b.codes[o[k]:o[k + 1]].copy() for k in range(b.count)]
        sim = {"src": truth["source"], "rc": truth["reverse_complement"], "reads": reads}
        opts = nf.decoder_options(case)
        rps = np.bincount(truth["source"][truth["source"] >= 0], minlength=len(arc["keys"]))
        for level in levels:
            rep = None
            if level == "oracle_strand_identity":
                rep = nf.oracle_frames_factory(sim, [])
            elif level == "oracle_consensus_2reads":
                rep = v8o.truth_frames_factory(arc, sim, 2)
            elif level == "oracle_consensus_1read":
                rep = v8o.truth_frames_factory(arc, sim, 1)
            dec, cap = nf.run_decode(w / "reads.fastq", w / f"{level}.vnx", arc["container_sha256"], opts,
                                     replace_frames=rep)
            rows.append({"channel": channel, "coverage": cov, "profile": profile, "seed": seed, "level": level,
                         "candidate": candidate if level in CANDIDATE_LEVELS else None,
                         "cluster_config": case["decoder"]["cluster_config"] if level in CANDIDATE_LEVELS else None,
                         "outcome": dec["outcome"], "false_success": dec["outcome"] == "FALSE_SUCCESS",
                         "error_code": dec["error_code"], "terminal_stage": dec["terminal_stage"],
                         "cluster_stage_ran": cap.reads is not None, "frames": len(cap.frames),
                         "decode_seconds": dec["decode_seconds"], "peak_rss_bytes": dec["peak_rss_bytes"],
                         "container_sha256": arc["container_sha256"], "reads_sha256": truth["reads_sha256"],
                         "strands": len(arc["keys"]), "strands_below_1_read": int((rps < 1).sum()),
                         "strands_below_2_reads": int((rps < 2).sum()), "label": LABEL,
                         "d13_verdict": MX.d13_verdict() if channel == "d13-f1" else None,
                         "load1": round(os.getloadavg()[0], 2), "commit": _commit(), "time": time.time()})
    return rows


def _key(r: dict) -> tuple:
    return (r["channel"], r["coverage"], r["profile"], r["seed"], r["level"], r["candidate"])


def wilson(k: int, n: int) -> list[float] | None:
    if n == 0:
        return None
    z = 1.959963984540054
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def summarise(rows: list[dict], candidate: str) -> dict:
    """Per cell: EXACT counts per level, ORE and ORE_SI with Wilson intervals, |P \\ O|, false success."""
    cells: dict = {}
    for r in rows:
        if r["candidate"] not in (None, candidate):
            continue
        c = cells.setdefault((r["channel"], r["coverage"], r["profile"]), {})
        c.setdefault(r["level"], {})[r["seed"]] = r["outcome"]
    out, tot_po, tot_o = [], 0, 0
    for (ch, cov, prof), lv in sorted(cells.items()):
        ex = {k: {s for s, o in v.items() if o == "EXACT"} for k, v in lv.items()}
        seeds = set.intersection(*(set(v) for v in lv.values())) if lv else set()
        P = ex.get("production", set()) & seeds
        O = ex.get("oracle_consensus_2reads", set()) & seeds
        SI = ex.get("oracle_strand_identity", set()) & seeds
        cell = {"channel": ch, "coverage": cov, "profile": prof, "n": len(seeds),
                **{k: len(v & seeds) for k, v in ex.items()},
                "false_success": sum(o == "FALSE_SUCCESS" for v in lv.values() for o in v.values()),
                "ORE": round(len(P & O) / len(O), 4) if O else None, "ORE_wilson95": wilson(len(P & O), len(O)),
                "ORE_SI": round(len(P & SI) / len(SI), 4) if SI else None, "P_not_O": len(P - O)}
        if "production" in lv and "oracle_consensus_2reads" in lv:
            tot_po += len(P & O)
            tot_o += len(O)
        out.append(cell)
    return {"label": LABEL, "candidate": candidate, "cells": out,
            "pooled_ORE": round(tot_po / tot_o, 4) if tot_o else None, "pooled_ORE_wilson95": wilson(tot_po, tot_o)}


def _seeds(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", default="d13-f1")
    ap.add_argument("--coverages", default="3,5,10")
    ap.add_argument("--profiles", default="v4-balanced,v7-lowcov")
    ap.add_argument("--seeds", default="90000-90009")
    ap.add_argument("--levels", default=",".join(LEVELS))
    ap.add_argument("--candidate", default="v8")
    ap.add_argument("--out", default=str(HERE / "results" / "dev.jsonl"))
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--summarise", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(x) for x in out.read_text().splitlines()] if out.exists() else []
    if not a.summarise:
        done = {_key(r) for r in rows}
        levels = a.levels.split(",")
        todo = []
        for cov in map(int, a.coverages.split(",")):
            for prof in a.profiles.split(","):
                for s in _seeds(a.seeds):
                    need = tuple(lv for lv in levels if (a.channel, cov, prof, s, lv,
                                                         a.candidate if lv in CANDIDATE_LEVELS else None) not in done)
                    if need:
                        todo.append((a.channel, cov, prof, s, need, a.candidate))
        with ProcessPoolExecutor(max_workers=a.jobs) as pool, out.open("a") as fh:
            for new in pool.map(run_one, todo):
                for r in new:
                    fh.write(json.dumps(r, sort_keys=True) + "\n")
                fh.flush()
                rows += new
                print(f"{new[0]['coverage']} {new[0]['profile']} s{new[0]['seed']}: "
                      + " ".join(f"{r['level']}={r['outcome']}" for r in new), flush=True)
    summ = summarise(rows, a.candidate)
    sp = out.with_name(out.stem + f"-summary-{a.candidate}.json")
    sp.write_text(json.dumps(summ, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"pooled_ORE": summ["pooled_ORE"], "pooled_ORE_wilson95": summ["pooled_ORE_wilson95"]}))
    return 1 if any(c["false_success"] for c in summ["cells"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
