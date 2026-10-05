"""EXP-PROBE-1 (V6_ARCHITECTURE §9): does the version probe (spec §3.10) ever refuse a supported pool, or accept an
unsupported one? SIMULATED: software-generated strands and software channel models; no DNA was synthesised, stored or
sequenced.

    PYTHONPATH=src:tests python experiments/v6/phase2/EXP-PROBE-1/run.py [--workers 6] [--seeds 20]

Grid (config.json): 12 pools (VNX4 frame 4 for 4 profiles with superblock 1 and 2, V1 frame 4, V3 frame 5, a frame-4
pool with version nibble 7, uniformly random sequences of the v4-balanced strand length) × 6 channel models × coverage
{1, 3, 10} × 20 seeds; each cell's reads are probed with the first 64, 1,000 and 20,000 reads (stage D1:
``vnxdna.recovery.probe.detect_layout``). Pass criterion: 0 refusals of supported pools, 0 acceptances of unsupported
ones, and the unsupported ones identified with the right code in ≥ 99 % of cells with n ≥ 1,000.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "experiments" / "v6" / "channel"))
sys.path.insert(0, str(REPO / "tests"))

import channel as chm  # noqa: E402
from v6 import probe_support as ps  # noqa: E402

CONFIG = json.loads((HERE / "config.json").read_text())
EXPECTED = {"supported": "accept", "v1-frame4": "LEGACY_FORMAT", "v3-frame5": "LEGACY_FORMAT",
            "nibble7": "FRAME_VERSION_UNSUPPORTED", "random": "LAYOUT_UNDETECTED"}


def build_pools(d: Path) -> dict:
    """name -> (strand FASTA, class, expected layout name or None). Strand counts are ≥ the reads the largest sample needs
    at coverage 1 (the cell subsets strands for higher coverage)."""
    pools = {}
    size = CONFIG["pool_bytes"]
    for prof in ("v4-balanced", "v4-dense", "v4-indel", "v4-archival"):
        for sb, v6 in (("sb1", {}), ("sb2", {"stripe_depth": 8, "column_parity": 2})):
            f = ps.frame4_pool(d / f"{prof}-{sb}", prof, size=size, seed=7000 + len(pools), **v6)
            pools[f"{prof}-{sb}"] = (f, "supported", prof)
    pools["v1-frame4"] = (ps.v1_pool(d / "v1", size=size // 2, seed=7100), "v1-frame4", None)
    pools["v3-frame5"] = (ps.v3_pool(d / "v3", size=size, seed=7101), "v3-frame5", None)
    pools["nibble7"] = (ps.nibble_pool(d / "n7", "v4-balanced", n=CONFIG["sample_sizes"][-1] + 2000, seed=7102), "nibble7", None)
    lay = ps.PROFILES["v4-balanced"][0]
    pools["random"] = (ps.random_pool(d / "rnd", n=CONFIG["sample_sizes"][-1] + 2000, seed=7103,
                                      lengths=(lay.strand_nt, lay.strand_nt + 1)), "random", None)
    return pools


def _subset(src: Path, dst: Path, n: int) -> None:
    seqs = ps.read_fasta(src)[:n]
    ps.write_fasta(dst, seqs)


def _first_reads(src: Path, dst: Path, n: int) -> int:
    lines = src.read_text().splitlines()
    step = 4 if lines and lines[0].startswith("@") else 2
    keep = lines[: n * step]
    dst.write_text("\n".join(keep) + "\n")
    return len(keep) // step


def run_cell(task: tuple) -> list[dict]:
    pool, strands, cls, want, model_name, coverage, seed, workdir = task
    from vnxdna.core.errors import VNXError
    from vnxdna.dnaenc.layout import PROFILES
    from vnxdna.recovery.probe import choose_layout, probe_reads
    model = chm.load_model(model_name)
    model.channel["coverage"] = float(coverage)
    out = []
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        t = Path(tmp)
        need = max(CONFIG["sample_sizes"])
        drop = model.loss["dropout"]
        n_strands = int(math.ceil(need / coverage / max(0.05, 1 - drop) * 1.3)) + 64
        sub = t / "strands.fasta"
        _subset(Path(strands), sub, n_strands)
        reads = t / "reads.fastq"
        chm.compose(model, sub, reads, seed)
        for n in CONFIG["sample_sizes"]:
            part = t / f"r{n}.fastq"
            got = _first_reads(reads, part, n)
            t0 = time.perf_counter()
            verified = False
            try:
                lay, verified = choose_layout(probe_reads(part))
                name = next((p for p, (lay_, _, _) in PROFILES.items() if lay_ == lay), "?")
                outcome = "accept"
                same_layout = want is not None and PROFILES[want][0] == lay
            except VNXError as error:
                outcome, name, same_layout = error.code, None, False
            out.append({"pool": pool, "class": cls, "model": model_name, "coverage": coverage, "seed": seed,
                        "n_requested": n, "n": got, "outcome": outcome, "layout": name, "layout_correct": same_layout,
                        "layout_verified": verified,
                        "probe_seconds": round(time.perf_counter() - t0, 5)})
    return out


def git_state() -> dict:
    def git(*a):
        r = subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seeds", type=int, default=CONFIG["seeds"])
    args = ap.parse_args()
    import vnxdna
    git = git_state()                     # at the start: the code that produces the results
    if git["dirty"]:
        print("warning: the working tree has uncommitted changes to tracked files", file=sys.stderr)
    t0 = time.perf_counter()
    work = Path(tempfile.mkdtemp(prefix="exp-probe-1-"))
    pools = build_pools(work / "pools")
    tasks = [(p, str(f), cls, want, m, c, CONFIG["seed_base"] + s, str(work))
             for p, (f, cls, want) in pools.items() for m in CONFIG["models"] for c in CONFIG["coverages"]
             for s in range(args.seeds)]
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for i, res in enumerate(ex.map(run_cell, tasks, chunksize=4)):
            rows.extend(res)
            if i % 100 == 0:
                print(f"{i}/{len(tasks)} cells, {time.perf_counter() - t0:.0f} s", file=sys.stderr, flush=True)
    # ---- verdict
    def ok(r):
        if r["class"] == "supported":
            return r["outcome"] == "accept" and (r["layout_correct"] or not r["layout_verified"])
        return r["outcome"] == EXPECTED[r["class"]]
    false_refusals = [r for r in rows if r["class"] == "supported" and r["outcome"] != "accept"]
    # a layout chosen without any verified frame (equal-length layouts, nothing decodes) is reported, not failed: the
    # decode then ends NO_SUPERBLOCK (exit 5, retryable) exactly as with the right layout
    wrong_layout = [r for r in rows if r["class"] == "supported" and r["outcome"] == "accept" and r["layout_verified"]
                    and not r["layout_correct"]]
    unverified = [r for r in rows if r["class"] == "supported" and r["outcome"] == "accept" and not r["layout_verified"]]
    false_accepts = [r for r in rows if r["class"] != "supported" and r["outcome"] == "accept"]
    big = [r for r in rows if r["class"] != "supported" and r["n"] >= 1000]
    identified = sum(r["outcome"] == EXPECTED[r["class"]] for r in big)
    confusion: dict = {}
    for r in rows:
        key = f"{r['class']}|n={r['n_requested']}"
        confusion.setdefault(key, {}).setdefault(r["outcome"], 0)
        confusion[key][r["outcome"]] += 1
    per_model: dict = {}
    for r in rows:
        k = f"{r['class']}|{r['model']}"
        e = per_model.setdefault(k, {"cells": 0, "correct": 0})
        e["cells"] += 1
        e["correct"] += int(ok(r))
    secs = sorted(r["probe_seconds"] for r in rows)
    verdict = {"false_refusals_of_supported": len(false_refusals), "wrong_layout_of_supported": len(wrong_layout),
               "supported_accepted_without_a_verified_frame": {
                   "cells": len(unverified), "by_model": {m: sum(r["model"] == m for r in unverified) for m in CONFIG["models"]},
                   "layout_correct": sum(r["layout_correct"] for r in unverified)},
               "acceptances_of_unsupported": len(false_accepts),
               "unsupported_identified_n_ge_1000": {"correct": identified, "cells": len(big),
                                                    "share": round(identified / max(1, len(big)), 6)}}
    verdict["pass"] = (not false_refusals and not wrong_layout and not false_accepts and identified >= 0.99 * len(big))
    doc = {"experiment": "EXP-PROBE-1", "evidence_class": "SIMULATED", "schema": "vnx.experiment/1",
           "statement": "SIMULATED: software-generated strands, software channel models; no DNA was synthesised, stored or sequenced.",
           "software": {"name": "vnxdna", "version": vnxdna.__version__}, "spec": "6.0", "git": git,
           "python": platform.python_version(), "platform": f"{sys.platform}-{platform.machine()}", "cpus": os.cpu_count(),
           "config": CONFIG, "seeds": {"base": CONFIG["seed_base"], "count": args.seeds},
           "models": {m: chm.load_model(m).sha256 for m in CONFIG["models"]},
           "pools": {p: {"class": cls, "expected_layout": want, "strands": len(ps.read_fasta(Path(f))),
                         "strands_sha256": hashlib.sha256(Path(f).read_bytes()).hexdigest()} for p, (f, cls, want) in pools.items()},
           "cells": len(rows), "verdict": verdict, "confusion": confusion, "per_class_model": per_model,
           "probe_seconds": {"median": secs[len(secs) // 2], "p99": secs[int(len(secs) * 0.99)], "max": secs[-1]},
           "failures": (false_refusals + wrong_layout + false_accepts)[:200],
           "wall_seconds": round(time.perf_counter() - t0, 1), "workers": args.workers}
    (HERE / "results.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(json.dumps(verdict, indent=1))
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
