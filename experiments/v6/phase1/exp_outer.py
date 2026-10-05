"""V6 Phase 1 experiments: outer-code resilience, V5 versus V6 (SIMULATED).

Every result is SIMULATED: software-generated strands, software strand-loss models (vnxdna.v6.loss) and the V4
channel simulator (vnxdna.v4.channel). No DNA was synthesised, stored or sequenced.

    python experiments/v6/phase1/exp_outer.py dropout   --out experiments/v6/phase1/P1-EXP-01-dropout
    python experiments/v6/phase1/exp_outer.py coverage  --out experiments/v6/phase1/P1-EXP-02-coverage
    python experiments/v6/phase1/exp_outer.py burst     --out experiments/v6/phase1/P1-EXP-03-burst
    python experiments/v6/phase1/exp_outer.py analytic  --out experiments/v6/phase1/P1-EXP-04-analytic

A trial = encode once per configuration → strand loss (seeded) → [channel: coverage + base errors (seeded)] → decode
with the default V5 decoder options (workers = 1; trials run in parallel processes) → compare the published
container's SHA-256 with the original. A *false SUCCESS* is status SUCCESS with a different container (never
expected: the decoder publishes only after the SHA-256 check). The decoder sees reads with neutral names.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.constraints import iter_fasta
from vnxdna.v4.errors import VNXError
from vnxdna.v4.util import environment
from vnxdna.v6 import outer as ou
from vnxdna.v6.loss import LossConfig, loss_mask

REPO = Path(__file__).resolve().parents[3]
P = 40                                  # v4-balanced payload
SIZE = 1 << 20                          # input bytes (random, incompressible)
DATA_SEED = 6201
BUDGET = 0.25                           # = V5 default M / K = 16 / 64


def configs(container_size: int) -> dict:
    planned = ou.plan(container_size, P, BUDGET, order="interleaved").geometry
    big_m = 51
    return {
        "V5": {"desc": "V5 default: v4-balanced, Cauchy RS 64+16 per group, sequential order", "opt": {}},
        "V6-int": {"desc": "64+16 rows, one stripe, interleaved order (no extra strands)",
                   "opt": {"strand_order": "interleaved"}},
        "V6-rows255": {"desc": f"{255 - big_m}+{big_m} rows (n = 255), interleaved, no column parity",
                       "opt": {"data_symbols": 255 - big_m, "parity_symbols": big_m, "strand_order": "interleaved"}},
        "V6-plan-seq": {"desc": "adaptive-plan geometry, sequential order (isolates the column code)",
                        "opt": {"data_symbols": planned.K, "parity_symbols": planned.M, "stripe_depth": planned.D,
                                "column_parity": planned.Mc, "strand_order": "sequential"}},
        "V6-adaptive": {"desc": f"outer_plan=adaptive, budget {BUDGET}, interleaved", "opt": {"outer_plan": "adaptive"}},
    }


# ---------------------------------------------------------------------------------------------------------------- provenance
def provenance(cmd: list[str]) -> dict:
    def git(*a):
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True).stdout.strip()
    env = environment()
    return {**env, "git_commit": git("rev-parse", "HEAD"), "worktree_dirty": bool(git("status", "--porcelain", "--", "src")),
            "command": " ".join(cmd), "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


# ---------------------------------------------------------------------------------------------------------------- setup
def prepare(scratch: Path, names: list[str]) -> dict:
    datagen.generate(scratch / "in.bin", SIZE, "random", DATA_SEED)
    ar.build_archive([scratch / "in.bin"], scratch / "a.vnx", ar.ArchiveOptions())
    vnx = scratch / "a.vnx"
    sha = hashlib.sha256(vnx.read_bytes()).hexdigest()
    cfgs = configs(vnx.stat().st_size)
    out = {"container_bytes": vnx.stat().st_size, "container_sha256": sha, "configs": {}}
    for name in names:
        c = cfgs[name]
        t = time.perf_counter()
        rep = en.encode_container(vnx, scratch / f"{name}.fasta", en.DNAOptions(**c["opt"]))
        out["configs"][name] = {"desc": c["desc"], "options": c["opt"], "strands": rep["strands"],
                                "superblock_strands": rep["superblock_strands"],
                                "overhead_strands_per_data_strand": round(rep["strands"] / -(-out["container_bytes"] // P) - 1, 5),
                                "outer_v6": rep.get("outer_v6"), "outer_code": rep["outer_code"],
                                "encode_seconds": round(time.perf_counter() - t, 3)}
    return out


def _write_reads(path: Path, seqs) -> None:
    with open(path, "w") as f:
        for i, s in enumerate(seqs):
            f.write(f">r{i}\n{s}\n")


def trial(args: tuple) -> dict:
    """One decode. args = (scratch, config, cell dict, seed, container sha)."""
    scratch, name, cell, seed, sha = args
    scratch = Path(scratch)
    work = Path(tempfile.mkdtemp(prefix=f"t-{name}-", dir=scratch))
    try:
        seqs = [s for _, s in iter_fasta(scratch / f"{name}.fasta")]
        loss = LossConfig(dropout=cell.get("dropout", 0.0), burst_count=cell.get("bursts", 0),
                          burst_length=cell.get("burst_length", 0), seed=seed)
        keep = loss_mask(len(seqs), loss)
        kept = [s for s, k in zip(seqs, keep.tolist()) if k]
        reads = work / "reads.fasta"
        if "coverage" in cell:
            _write_reads(work / "strands.fasta", kept)
            cfg = ch.ChannelConfig(coverage=cell["coverage"], coverage_model="poisson",
                                   substitution_rate=cell.get("sub", 0.0), insertion_rate=cell.get("ins", 0.0),
                                   deletion_rate=cell.get("del", 0.0), seed=seed)
            ch.simulate_file(work / "strands.fasta", reads, cfg, fmt="fasta")
        else:
            _write_reads(reads, kept)
        t = time.perf_counter()
        out = work / "out.vnx"
        try:
            res = de.decode_reads(reads, out, de.DecodeOptions(workers=1), overwrite=True, workdir=work)
            status, rep = res.status, res.report
        except VNXError as error:
            status, rep = f"ERROR:{type(error).__name__}", {}
        seconds = time.perf_counter() - t
        match = status == "SUCCESS" and hashlib.sha256(out.read_bytes()).hexdigest() == sha
        v6 = rep.get("outer_v6") or {}
        return {"config": name, **cell, "seed": seed, "status": status, "sha_match": bool(match),
                "false_success": bool(status == "SUCCESS" and not match), "strands_lost": int((~keep).sum()),
                "groups_failed": rep.get("groups_failed"), "decode_seconds": round(seconds, 3),
                "data_rows_recovered_by_columns": v6.get("data_rows_recovered_by_columns"),
                "rows_lost_entirely": v6.get("rows_lost_entirely")}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def summarize(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        c = out.setdefault(r["config"], {}).setdefault(str(r[key]), {"trials": 0, "success": 0, "false_success": 0,
                                                                      "decode_seconds": []})
        c["trials"] += 1
        c["success"] += int(r["sha_match"])
        c["false_success"] += int(r["false_success"])
        c["decode_seconds"].append(r["decode_seconds"])
    for cfg in out.values():
        for c in cfg.values():
            c["median_decode_seconds"] = round(float(np.median(c.pop("decode_seconds"))), 3)
    return out


def thresholds(summary: dict, larger_is_harder: bool = True) -> dict:
    """Per config: the hardest cell value at which every trial succeeded, with every easier cell also all-success."""
    out = {}
    for cfg, cells in summary.items():
        vals = sorted(cells, key=float, reverse=not larger_is_harder)
        best = None
        for v in vals:
            if cells[v]["success"] == cells[v]["trials"]:
                best = float(v)
            else:
                break
        out[cfg] = best
    return out


def run_grid(exp: str, cells: list[dict], seeds: list[int], key: str, names: list[str], out_dir: Path, workers: int,
             cmd: list[str], larger_is_harder: bool, extra: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f"vnx6-{exp}-"))
    log = open(out_dir / "log.txt", "w")
    try:
        t0 = time.perf_counter()
        setup = prepare(scratch, names)
        config = {"experiment": exp, "input": {"bytes": SIZE, "pattern": "random", "seed": DATA_SEED},
                  "cells": cells, "seeds": seeds, "configs": {n: setup["configs"][n]["options"] for n in names},
                  "decode_options": "DecodeOptions(workers=1) (V5 defaults)", **extra}
        (out_dir / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
        jobs = [(str(scratch), n, c, s, setup["container_sha256"]) for n in names for c in cells for s in seeds]
        rows = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(trial, j) for j in jobs]
            for f in as_completed(futs):
                r = f.result()
                rows.append(r)
                print(json.dumps(r, sort_keys=True), file=log, flush=True)
        rows.sort(key=lambda r: (names.index(r["config"]), r[key], r["seed"]))
        summ = summarize(rows, key)
        result = {"experiment": exp, "simulated": True,
                  "label": "SIMULATED: software strands, software loss/channel models; no DNA synthesised or sequenced",
                  "config": config, "setup": setup, "provenance": provenance(cmd),
                  "wall_seconds": round(time.perf_counter() - t0, 1),
                  "false_success_total": sum(r["false_success"] for r in rows), "decodes": len(rows),
                  "threshold_key": key, "thresholds_all_trials_success": thresholds(summ, larger_is_harder),
                  "summary": summ, "trials": rows}
        (out_dir / "results.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
        print(json.dumps({k: result[k] for k in ("experiment", "decodes", "false_success_total", "wall_seconds",
                                                 "thresholds_all_trials_success")}, indent=1))
    finally:
        log.close()
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------------------- analytic
def analytic(out_dir: Path, cmd: list[str]) -> None:
    """Analytic i.i.d. dropout bound (vnxdna.v6.outer.failure_bound) and exact burst tolerance for larger archives."""
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    sizes = {"1 MiB": 1 << 20, "64 MiB": 64 << 20, "1 GiB": 1 << 30}
    rows = []
    for label, size in sizes.items():
        pl = ou.plan(size, P, BUDGET, order="interleaved")
        geos = {
            "V5": ou.Geometry(64, 16, min(65535, -(-size // (64 * P))), 0, P, size, "sequential"),
            "V6-int": ou.Geometry(64, 16, min(65535, -(-size // (64 * P))), 0, P, size, "interleaved"),
            "V6-rows255": ou.Geometry(204, 51, min(65535, -(-size // (204 * P))), 0, P, size, "interleaved"),
            "V6-adaptive": pl.geometry,
        }
        for name, g in geos.items():
            g.validate()
            row = {"size": label, "config": name, "geometry": g.to_dict(),
                   "iid_threshold_bound_eps_1e-3": round(ou.dropout_threshold(g, 1e-3), 4),
                   "iid_threshold_bound_eps_1e-6": round(ou.dropout_threshold(g, 1e-6), 4)}
            if size <= (64 << 20) and (g.D + g.Mc) * g.n <= 40_000:
                row["burst_tolerance_strands"] = ou.burst_tolerance(g)
            rows.append(row)
            print(json.dumps(row), flush=True)
    result = {"experiment": "P1-EXP-04-analytic", "simulated": True,
              "label": "SIMULATED (analytic erasure model): i.i.d. strand loss bound and exact burst tolerance; "
                       "superblock strands excluded",
              "config": {"payload_bytes": P, "budget": BUDGET, "sizes": sizes}, "provenance": provenance(cmd),
              "wall_seconds": round(time.perf_counter() - t0, 1), "rows": rows}
    (out_dir / "results.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------------------------------------------- main
ALL = ["V5", "V6-int", "V6-rows255", "V6-plan-seq", "V6-adaptive"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment", choices=["dropout", "coverage", "burst", "analytic"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--configs", default=",".join(ALL))
    a = ap.parse_args()
    cmd = [Path(sys.executable).name, *sys.argv]
    names = a.configs.split(",")
    seeds = list(range(1000, 1000 + a.seeds))
    if a.experiment == "analytic":
        analytic(a.out, cmd)
    elif a.experiment == "dropout":
        cells = [{"dropout": round(0.02 + 0.01 * i, 2)} for i in range(19)]          # 0.02 … 0.20
        run_grid("P1-EXP-01-dropout", cells, seeds, "dropout", names, a.out, a.workers, cmd, True,
                 {"loss": "i.i.d. strand loss (superblock strands included), each surviving strand read once, no base errors"})
    elif a.experiment == "coverage":
        cells = [{"coverage": c, "sub": 0.005, "ins": 0.0005, "del": 0.0005} for c in (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0)]
        run_grid("P1-EXP-02-coverage", cells, seeds, "coverage", names, a.out, a.workers, cmd, False,
                 {"loss": "Poisson read coverage per strand (strands with 0 reads are lost), 0.5 % substitutions, "
                          "0.05 % insertions, 0.05 % deletions per base (V4 channel)"})
    else:
        cells = [{"bursts": 1, "burst_length": L, "dropout": 0.02} for L in (64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384)]
        run_grid("P1-EXP-03-burst", cells, seeds, "burst_length", names, a.out, a.workers, cmd, True,
                 {"loss": "one contiguous run of burst_length strands in file order (uniform start) plus 2 % i.i.d. "
                          "strand loss; each surviving strand read once, no base errors"})


if __name__ == "__main__":
    main()
