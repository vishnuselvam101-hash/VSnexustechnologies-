"""V10 W3: whole-archive success under the D13-F1 stress channel with the V6 column-parity layer (SIMULATED).

Pre-registration: experiments/v10/w3/PREREG.md (committed before any evaluation decode). The archive is v4-balanced
(row code 64 + 16) plus the existing opt-in V6 column parity (stripe depth 128, 4 column-parity rows per stripe,
superblock version 2). Channel, coverage, decoder and workers are those of the V9 noisy runs and V10 W1/W2:
D13-F1 at coverage 10, decoder E, 4 workers (kernel threads = workers).

    PYTHONPATH=src python experiments/v10/w3/run.py smoke            # 20 KB, seed 95000 (not an evaluation case)
    PYTHONPATH=src python experiments/v10/w3/run.py eval --size 1m   # seeds 93000-93009
    PYTHONPATH=src python experiments/v10/w3/run.py eval --size 10m  # seeds 93100-93101
    PYTHONPATH=src python experiments/v10/w3/run.py summary
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _p in ("tests/nanopore", "experiments/v9/consensus"):
    sys.path.insert(0, str(ROOT / _p))


def _load(name: str, rel: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


W2 = _load("v10_w2_bench", "experiments/v10/w2/bench.py")

W1 = W2.W1
STRIPE_DEPTH = 128
COLUMN_PARITY = 4
SIZES = {"20k": 20_000, "1m": 1 << 20, "10m": 10_000_000}
SEEDS = {"smoke": range(95000, 95001), "1m": range(93000, 93010), "10m": range(93100, 93102)}
OUT = HERE / "results" / "w3.jsonl"
SMOKE = HERE / "results" / "smoke.jsonl"
SUMMARY = HERE / "results" / "summary.json"


def build(work: Path, size: int, seed: int) -> dict:
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import datagen
    from vnxdna.v4 import encoder as en
    work.mkdir(parents=True, exist_ok=True)
    datagen.generate(work / "in.bin", size, "random", seed)
    ar.build_archive([work / "in.bin"], work / "a.vnx", ar.ArchiveOptions(compression="none"))
    opt = en.DNAOptions(profile=W1.PROFILE, stripe_depth=STRIPE_DEPTH, column_parity=COLUMN_PARITY,
                        workers=W1.WORKERS)
    rep = en.encode_container(work / "a.vnx", work / "strands.fasta", opt)
    return {"container_sha256": hashlib.sha256((work / "a.vnx").read_bytes()).hexdigest(),
            "strands_path": work / "strands.fasta", "strands": rep.get("strands"), "groups": rep.get("groups")}


def run_case(size: int, seed: int) -> dict:
    import dataclasses

    import nanofunnel as nf
    from candidates import cluster_config
    from vnxdna.simulation import engine
    os.environ.pop("VNXDNA_CLUSTER_THREADS", None)
    MX = W1._load("v8_matrix_run", "experiments/v8/matrix/run.py")
    case = {"profile": W1.PROFILE, "decoder": {"read_clustering": "fallback", "cluster_config": cluster_config(W1.CANDIDATE)}}
    opts = dataclasses.replace(nf.decoder_options(case), workers=W1.WORKERS, stage_counters=True)
    with tempfile.TemporaryDirectory(prefix="vnx-v10w3-", dir=W1._TMP) as tmp:
        w = Path(tmp)
        arc = build(w / "a", size, seed)
        truth = engine.simulate_file_with_truth(arc["strands_path"], w / "r.fastq",
                                                MX.case_model(W1.CHANNEL, W1.COVERAGE), seed)
        row, rep = W2._decode(w, "w3", opts, arc)
    oe = ((rep.get("stage_counters") or {}).get("stages") or {}).get("outer_ecc", {})
    nrows = oe.get("rows_attempted", 0)
    return {**W2._base(seed, size, truth), "config": f"col-D{STRIPE_DEPTH}-Mc{COLUMN_PARITY}", **row,
            "strands": arc["strands"], "groups": arc["groups"],
            "rows": nrows, "rows_decoded": oe.get("rows_decoded"),
            "rows_recovered_by_columns": oe.get("rows_recovered_by_columns"),
            "per_row_success": round(oe.get("rows_decoded", 0) / nrows, 6) if nrows else None}


def summarize(rows: list[dict]) -> dict:
    from vnxdna.benchmark import stats as S
    v9 = {}
    if W2.V9_NOISY.exists():
        for line in W2.V9_NOISY.read_text().splitlines():
            d = json.loads(line)
            v9[(d["seed"], d["size"])] = d
    cells = {}
    for size in sorted({r["size"] for r in rows}):
        rs = [r for r in rows if r["size"] == size]
        base = [v9[(r["seed"], size)] for r in rs if (r["seed"], size) in v9]
        exact = sum(r["outcome"] == "EXACT" for r in rs)
        cells[str(size)] = {
            "n": len(rs), "exact": exact, "exact_wilson95": S.wilson(exact, len(rs)),
            "false_success": sum(r["false_success"] for r in rs),
            "outcomes": sorted({r["outcome"] for r in rs}),
            "rows_recovered_by_columns": [r["rows_recovered_by_columns"] for r in rs],
            "per_row_success": [r["per_row_success"] for r in rs],
            "median_decode_seconds": sorted(r["decode_seconds"] for r in rs)[len(rs) // 2] if rs else None,
            "strands": sorted({r["strands"] for r in rs}),
            "v9_same_seeds": {"n": len(base), "exact": sum(b["outcome"] == "EXACT" for b in base)} if base else None,
        }
    return {"label": W1.LABEL, "geometry": {"profile": W1.PROFILE, "stripe_depth": STRIPE_DEPTH,
                                            "column_parity": COLUMN_PARITY},
            "cells": cells, "load_rule": S.load_ok([r["load1"] for r in rows]) if rows else None,
            "commits": sorted({r["commit"] for r in rows})}


def analytic(q: float = 0.005) -> dict:
    """Conservative model of the PREREG: a stripe of D + Mc rows decodes iff at most Mc rows fail row-wise (i.i.d. q)."""
    def cdf(k, n, p):
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))
    out = {}
    for name, size in SIZES.items():
        G = math.ceil(size / (40 * 64))
        stripes = math.ceil(G / STRIPE_DEPTH)
        out[name] = {"groups": G, "without": round((1 - q) ** G, 6),
                     "with": round(cdf(COLUMN_PARITY, STRIPE_DEPTH + COLUMN_PARITY, q) ** stripes, 6)}
    return {"row_failure_q": q, "model": analytic.__doc__, "archive_success": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["smoke", "eval", "summary"])
    ap.add_argument("--size", choices=["1m", "10m"], default="1m")
    a = ap.parse_args(argv)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if a.cmd in ("smoke", "eval"):
        path, size, seeds = (SMOKE, SIZES["20k"], SEEDS["smoke"]) if a.cmd == "smoke" else (OUT, SIZES[a.size], SEEDS[a.size])
        done = {(r["seed"], r["size"]) for r in W2._read(path)}
        for seed in seeds:
            if (seed, size) in done:
                continue
            r = run_case(size, seed)
            with path.open("a") as f:
                f.write(json.dumps(r, sort_keys=True) + "\n")
            print(seed, size, r["outcome"], r["decode_seconds"], r["rows_recovered_by_columns"], flush=True)
    s = {**summarize(W2._read(OUT)), "analytic": analytic()}
    SUMMARY.write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")
    print(json.dumps(s["cells"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
