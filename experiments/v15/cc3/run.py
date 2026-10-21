"""V15 CC-3: nanopore-like decode failures, before/after the ported fixes (SIMULATED). Pre-registration: PREREG.md.

Per (case, seed) the archive and the read file are prepared once; every configuration decodes the same file in its
own subprocess, against the source tree given by ``VNX_CC3_SRC`` (default: this tree's ``src``), so the baseline and
the changed tree decode identical reads.

    python experiments/v15/cc3/run.py run --case d13f1-1m --seeds 93120-93139 --configs E,F
    python experiments/v15/cc3/run.py summary [--out DIR]

Configurations: ``default`` (no read clustering), ``E`` and ``F`` (``experiments/v9/consensus/candidates.py``; F is the
``nanopore`` preset). Rows go to ``$VNX_CC3_OUT/rows.jsonl`` (default ``results/``).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SRC = Path(os.environ.get("VNX_CC3_SRC") or ROOT / "src")
OUTDIR = Path(os.environ.get("VNX_CC3_OUT") or HERE / "results")
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"
WORKERS = int(os.environ.get("VNX_CC3_WORKERS", "4"))
INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002, "coverage": 5}   # p6e INDEL

# case -> (bytes, channel, column parity geometry or None). Channel: a model name for experiments/v8/matrix case_model
# at coverage 10 ("d13-f1" = the D13-F1 nanopore-like stress model of V8-V11; "nanopore-like" = the versioned model),
# or a ChannelConfig dict.
CASES = {
    "d13f1-1m": (1 << 20, "d13-f1", None),                 # = experiments/arch/outer_list_eval (evaluations 1-5)
    "d13f1-10m": (10_000_000, "d13-f1", (128, 4)),          # = experiments/v10/w3 10m and job 86 (cluster_budget)
    "d13f1-cp-1m": (1 << 20, "d13-f1", (128, 4)),           # = experiments/v10/w3 1m (existing opt-in column parity)
    "nanopore-like-1m": (1 << 20, "nanopore-like", None),
    "clean-1m": (1 << 20, "clean", None),
    "illumina-like-1m": (1 << 20, "illumina-like", None),
    "indel-1m": (1 << 20, INDEL, None),
    "smoke-d13f1-20k": (20_000, "d13-f1", None),          # harness validation only, never evidence
    "smoke-indel-20k": (20_000, INDEL, (16, 2)),
}


def _paths() -> None:
    sys.path[:0] = [str(ROOT / "tests/nanopore"), str(ROOT / "experiments/v9/consensus"), str(SRC)]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def prepare(work: Path, case: str, seed: int) -> dict:
    _paths()
    from vnxdna.simulation import engine
    from vnxdna.simulation.channel import ChannelConfig, simulate_file
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import datagen
    from vnxdna.v4 import encoder as en
    size, channel, geom = CASES[case]
    a = work / "a"
    a.mkdir(parents=True, exist_ok=True)
    datagen.generate(a / "in.bin", size, "random", seed)
    ar.build_archive([a / "in.bin"], a / "a.vnx", ar.ArchiveOptions(compression="none"))
    extra = {"stripe_depth": geom[0], "column_parity": geom[1]} if geom else {}
    rep = en.encode_container(a / "a.vnx", a / "strands.fasta", en.DNAOptions(profile="v4-balanced", workers=WORKERS,
                                                                               **extra))
    if isinstance(channel, str):
        mx = _load("v8_matrix_run", "experiments/v8/matrix/run.py")
        engine.simulate_file_with_truth(a / "strands.fasta", work / "r.fastq", mx.case_model(channel, 10), seed)
    else:
        simulate_file(a / "strands.fasta", work / "r.fastq", ChannelConfig(**channel, seed=seed), fmt="fastq",
                      workers=1, overwrite=True)
    truth = hashlib.sha256((a / "a.vnx").read_bytes()).hexdigest()
    for p in (a / "in.bin", a / "a.vnx"):
        p.unlink()
    return {"truth_sha256": truth, "strands": rep.get("strands"),
            "reads_sha256": hashlib.sha256((work / "r.fastq").read_bytes()).hexdigest()}


def one(work: Path, config: str) -> dict:
    _paths()
    from candidates import cluster_config
    from vnxdna.pipeline.decode import decode_reads
    from vnxdna.recovery.cluster import ClusterConfig
    from vnxdna.recovery.options import DecodeOptions
    if config == "default":
        opts = DecodeOptions(workers=WORKERS, stage_counters=True)
    else:
        opts = DecodeOptions(workers=WORKERS, stage_counters=True, read_clustering="fallback",
                             cluster_config=ClusterConfig(**cluster_config(config)))
    out = work / f"o-{config}.vnx"
    t = time.perf_counter()
    try:
        res = decode_reads(work / "r.fastq", out, opts)
        status, rep = res.status, res.report or {}
    except Exception as error:  # noqa: BLE001 - an explicit failure is an outcome
        status, rep = f"ERROR:{type(error).__name__}", dict(getattr(error, "details", None) or {})
    secs = time.perf_counter() - t
    sha = hashlib.sha256(out.read_bytes()).hexdigest() if out.is_file() else None
    if out.is_file():
        out.unlink()
    cl = rep.get("clustering") or {}
    oe = ((rep.get("stage_counters") or {}).get("stages") or {}).get("outer_ecc") or {}
    rss = max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    return {"config": config, "status": status, "output_sha256": sha, "decode_seconds": round(secs, 2),
            "peak_rss_kib": rss, "failed_groups": rep.get("failed_groups") or rep.get("groups_failed"),
            "cluster_status": cl.get("status"), "cluster_budget": cl.get("budget_exceeded"),
            "cluster_frames_verified": cl.get("frames_verified"),
            "candidate_bucket_cap": (cl.get("clustering") or {}).get("candidate_bucket_cap"),
            "outer_list": {k: v for k, v in (cl.get("outer_list") or {}).items() if not isinstance(v, (list, dict))},
            "rows_attempted": oe.get("rows_attempted"), "rows_decoded": oe.get("rows_decoded"),
            "rows_recovered_by_columns": oe.get("rows_recovered_by_columns"),
            "engine_rung": (rep.get("engine") or {}).get("rung"), "native": _native()}


def _native() -> dict:
    """Which kernels ran (the cluster ABI also tells the baseline tree, ABI 5, from the changed one, ABI 6)."""
    from vnxdna.native import cluster as nc
    from vnxdna.native import outerlist as nol
    return {"cluster_abi": nc.ABI_VERSION, "cluster_native": nc.available(), "outerlist_native": nol.available(),
            "vnxdna_file": __import__("vnxdna").__file__}


def _commit(src: Path) -> str:
    r = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(src), "status", "--porcelain", "--", "."], capture_output=True, text=True)
    return r.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")


def run(case: str, seeds: list[int], configs: list[str], tag: str) -> None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / "rows.jsonl"
    done = set()
    if out.is_file():
        done = {(r["case"], r["seed"], r["config"], r["tree"]) for r in map(json.loads, out.read_text().splitlines())}
    commit = _commit(SRC)
    for seed in seeds:
        todo = [c for c in configs if (case, seed, c, tag) not in done]
        if not todo:
            continue
        with tempfile.TemporaryDirectory(prefix="vnx-cc3-", dir=os.environ.get("VNX_EVAL_TMP")) as tmp:
            work = Path(tmp)
            t = time.perf_counter()
            prep = prepare(work, case, seed)
            prep_s = round(time.perf_counter() - t, 1)
            for config in todo if seed % 2 == 0 else todo[::-1]:
                load = os.getloadavg()[0]
                p = subprocess.run([sys.executable, str(Path(__file__)), "one", "--work", str(work), "--config", config],
                                   capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(SRC)})
                if p.returncode != 0:
                    row = {"config": config, "status": f"CRASH:{p.returncode}", "output_sha256": None,
                           "stderr_tail": p.stderr[-1500:]}
                else:
                    row = json.loads(p.stdout.strip().splitlines()[-1])
                row.update(case=case, seed=seed, tree=tag, commit=commit, label=LABEL, load1_start=load,
                           prep_seconds=prep_s, workers=WORKERS, **prep, time=time.time())
                row["exact"] = row["output_sha256"] == prep["truth_sha256"]
                row["false_success"] = row["status"] == "SUCCESS" and not row["exact"]
                with open(out, "a") as f:
                    f.write(json.dumps(row) + "\n")
                print(case, seed, tag, config, row["status"], row["exact"], row.get("decode_seconds"), flush=True)


def summary(outdir: Path) -> dict:
    rows = [json.loads(x) for p in sorted(outdir.rglob("rows.jsonl")) for x in p.read_text().splitlines()]
    cells: dict = {}
    for r in rows:
        c = cells.setdefault(f'{r["case"]}|{r["tree"]}|{r["config"]}', {"seeds": [], "exact": 0, "false_success": 0,
                                                                         "failures": {}, "seconds": []})
        c["seeds"].append(r["seed"])
        c["exact"] += r["exact"]
        c["false_success"] += r["false_success"]
        c["seconds"].append(r.get("decode_seconds"))
        if not r["exact"]:
            c["failures"][str(r["seed"])] = [r["status"], r.get("failed_groups")]
    for c in cells.values():
        c["n"] = len(c["seeds"])
        s = sorted(x for x in c.pop("seconds") if x is not None)
        c["median_decode_seconds"] = s[len(s) // 2] if s else None
        c["seeds"] = sorted(c["seeds"])
    out = {"label": LABEL, "cells": cells, "false_success_total": sum(r["false_success"] for r in rows),
           "commits": sorted({f'{r["tree"]}:{r["commit"]}' for r in rows})}
    (outdir / "summary.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "one", "summary"])
    ap.add_argument("--case", choices=sorted(CASES))
    ap.add_argument("--seeds", default="")
    ap.add_argument("--configs", default="E,F")
    ap.add_argument("--tree", default="new", help="label of the tree under test (VNX_CC3_SRC)")
    ap.add_argument("--work", type=Path)
    ap.add_argument("--config")
    ap.add_argument("--out", type=Path, default=OUTDIR)
    a = ap.parse_args(argv)
    if a.cmd == "one":
        print(json.dumps(one(a.work, a.config)))
    elif a.cmd == "run":
        seeds = [int(x) for x in a.seeds.split(",")] if "," in a.seeds else \
            list(range(int(a.seeds.partition("-")[0]), int(a.seeds.partition("-")[2] or a.seeds.partition("-")[0]) + 1))
        run(a.case, seeds, a.configs.split(","), a.tree)
    else:
        print(json.dumps(summary(a.out), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
