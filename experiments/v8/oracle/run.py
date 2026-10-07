"""V8.10 oracle / upper-bound experiments under the D13-derived channel (UPPER-BOUND / ORACLE; SIMULATED).

For each case (D13 F1 channel as a stress condition, coverage 3/5/10, v4-balanced and v7-lowcov, seeds 83000-83004) the same
reads are decoded four times:
* ``production``: the decoder as shipped (Phase 1 full consensus);
* ``oracle_strand_identity``: reads grouped by their TRUE source strand (oracle clustering and strand identity); the
  decoder's own orientation, consensus and frame decoding (``nanofunnel.oracle_frames_factory``);
* ``oracle_consensus_2reads``: the TRUE frame of every strand with >= 2 stored reads (perfect alignment and consensus);
* ``oracle_consensus_1read``: the TRUE frame of every strand with >= 1 stored read (only coverage limits recovery).
The gaps between consecutive levels attribute the losses: clustering → consensus (incl. alignment and indel placement) →
the two-read minimum → coverage. Oracle alignment is not separable from consensus in this decoder (alignment happens
inside it) and is covered by the consensus oracle. Oracle channel knowledge is NOT IMPLEMENTED: the decoder takes no
channel model as input.

These results are upper bounds. They are not achievable production performance.

    PYTHONPATH=src python experiments/v8/oracle/run.py --jobs 4
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests" / "nanopore"))
sys.path.insert(0, str(ROOT / "experiments" / "v8" / "matrix"))
OUT = HERE / "results" / "oracle.jsonl"
SEEDS = tuple(range(83000, 83005))
COVERAGES = (3, 5, 10)
PROFILES_RUN = ("v4-balanced", "v7-lowcov")
LEVELS = ("production", "oracle_strand_identity", "oracle_consensus_2reads", "oracle_consensus_1read")
LABEL = "UPPER-BOUND / ORACLE (SIMULATED): not achievable production performance"


def truth_frames_factory(arc: dict, sim: dict, min_reads: int):
    """ORACLE: the true frame of every strand with >= ``min_reads`` stored reads, in place of the cluster stage."""
    import nanofunnel as nf

    def frames(reads, lay, cfg, marker_mismatch=4, counts=None, cons=None, checkpoint=None, raw=None):
        idx = nf.map_stored(list(reads), sim["reads"])
        n = Counter(int(sim["src"][j]) for j in idx.tolist() if sim["src"][j] >= 0)
        out = []
        for s, c in sorted(n.items()):
            if c >= min_reads:
                kind, tag, group, symbol = arc["keys"][s]
                out.append({"kind": kind, "tag": tag, "group": group, "symbol": symbol,
                            "payload": np.array(arc["payload"][s], dtype=np.uint8), "gmd_step": 0, "cluster": s, "peel": 0,
                            "reads": c})
        return out, None
    return frames


def run_one(job: tuple) -> dict:
    import nanofunnel as nf
    import run as MX
    from vnxdna.native.reads import iter_reads
    from vnxdna.simulation import engine
    cov, profile, seed = job
    model = MX.case_model("d13-f1", cov)
    case = {"profile": profile, "decoder": {"read_clustering": "fallback", "cluster_config": {"consensus_template": "full"}}}
    with tempfile.TemporaryDirectory(prefix="vnx-v8o-") as tmp:
        w = Path(tmp)
        arc = nf.build_archive(w / "archive", MX.SIZE, seed, profile)
        truth = engine.simulate_file_with_truth(arc["strands_path"], w / "reads.fastq", model, seed)
        reads = []
        for b in iter_reads(w / "reads.fastq", 1 << 17):
            o = np.concatenate([[0], np.cumsum(b.lengths)])
            reads += [b.codes[o[k]:o[k + 1]].copy() for k in range(b.count)]
        sim = {"src": truth["source"], "rc": truth["reverse_complement"], "reads": reads}
        opts = nf.decoder_options(case)
        res = {}
        for level in LEVELS:
            rep = None
            if level == "oracle_strand_identity":
                rep = nf.oracle_frames_factory(sim, [])
            elif level == "oracle_consensus_2reads":
                rep = truth_frames_factory(arc, sim, 2)
            elif level == "oracle_consensus_1read":
                rep = truth_frames_factory(arc, sim, 1)
            dec, cap = nf.run_decode(w / "reads.fastq", w / f"{level}.vnx", arc["container_sha256"], opts, replace_frames=rep)
            res[level] = {"outcome": dec["outcome"], "terminal_stage": dec["terminal_stage"],
                          "cluster_stage_ran": cap.reads is not None, "decode_seconds": dec["decode_seconds"],
                          "frames": len(cap.frames)}
    rps = np.bincount(truth["source"][truth["source"] >= 0], minlength=len(arc["keys"]))
    return {"channel": "d13-f1", "d13_verdict": MX.d13_verdict(), "coverage": cov, "profile": profile, "seed": seed,
            "label": LABEL, "container_sha256": arc["container_sha256"], "reads_sha256": truth["reads_sha256"],
            "strands": len(arc["keys"]), "strands_below_1_read": int((rps < 1).sum()), "strands_below_2_reads": int((rps < 2).sum()),
            "levels": res}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if OUT.exists():
        done = {(r["coverage"], r["profile"], r["seed"]) for r in map(json.loads, OUT.read_text().splitlines())}
    todo = [(c, p, s) for c in COVERAGES for p in PROFILES_RUN for s in SEEDS if (c, p, s) not in done]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool, OUT.open("a") as fh:
        for row in pool.map(run_one, todo):
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
    rows = [json.loads(x) for x in OUT.read_text().splitlines()]
    summ: dict = {"label": LABEL, "cells": []}
    for c in COVERAGES:
        for p in PROFILES_RUN:
            rs = [r for r in rows if r["coverage"] == c and r["profile"] == p]
            summ["cells"].append({"coverage": c, "profile": p, "n": len(rs),
                                  **{lv: sum(r["levels"][lv]["outcome"] == "EXACT" for r in rs) for lv in LEVELS},
                                  "false_success": sum(r["levels"][lv]["outcome"] == "FALSE_SUCCESS" for r in rs for lv in LEVELS)})
    (HERE / "results" / "summary.json").write_text(json.dumps(summ, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
