"""Differential stress fuzz: the native V7 read-clustering kernels vs the NumPy reference. SYNTHETIC data.

Each round draws one seeded case per kernel from ``tests/v7/native_cluster_support.py`` (sketch, candidate pairs,
banded distance, forward-backward, whole strand pools through ``cluster_reads``) and, every ``--full-every`` rounds,
a full-size case (313-nt reads, the stage's default parameters). The forward-backward cases also run every SIMD level
and lane width the CPU supports, with 1 and 3 threads. Every output must equal the reference field by field (dtype,
shape, values, counters, budgets). Prints one JSON summary line; exit status 1 on any mismatch.

usage: python benchmarks/v7/native_cluster/stress_fuzz.py [--rounds 200] [--seed 1] [--full-every 10]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from v7.native_cluster_support import (CASES, fb_case, pairs_case, polish_case, pool_case, run_kernel, same,  # noqa: E402
                                       sketch_case)

from vnxdna.native import cluster as nc  # noqa: E402
from vnxdna.recovery.cluster import consensus  # noqa: E402


def units(kind: str, case) -> int:
    """Reads (sketch, forward-backward, pools), read pairs (distance) or sketched reads (candidates) in a case."""
    if kind == "sketch":
        return int(case["raw"].shape[0])
    if kind == "candidates":
        return int(case["hashes"].shape[0])
    if kind == "banded":
        return len(case["a"])
    if kind == "pool":
        return len(case[0])
    return len(case["reads"])


def full_case(kind: str, rng: np.random.Generator):
    if kind == "sketch":
        c = sketch_case(rng, n=2000)
        c.update(k=12, s=32)
        return c
    if kind == "banded":
        return pairs_case(rng, p=1000, lmax=330)
    if kind == "fb":
        return fb_case(rng, n=600, T=313)
    if kind == "polish":
        return polish_case(rng, n=200, T=313)
    if kind == "pool":
        return pool_case(rng, strands=40)
    return None


def fb_variants(case) -> int:
    """Every level x width x thread count against the reference; returns the number of mismatching variants."""
    ref = consensus.fb_calls_reference(case["tpl"], case["mc"], case["reads"], case["band"], case["c_indel"],
                                       case["slack"])
    buf, off, lens = nc.pack(case["reads"])
    B = int(case["band"].max(initial=0))
    T = case["tpl"].shape[1]
    step = max(int(case["mc"].max(initial=0)), case["c_indel"], 1)
    fits16 = (T + int(lens.max(initial=0)) + 2) * step + case["slack"] < 15000
    if not (B <= nc.MAX_BAND and T <= nc.MAX_TEMPLATE and step <= nc.MAX_COST):
        return 0
    bad = 0
    for level in [1] + ([2] if nc.fb_level() == "avx2" else []):
        for width in [0, 32] + ([16] if fits16 else []):
            for threads in (1, 3):
                got = nc.fb(case["tpl"], case["mc"], buf, off, lens, case["band"], B, case["c_indel"], case["slack"],
                            level=level, nthreads=threads, width=width)
                bad += not same(ref, got)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--full-every", type=int, default=10)
    args = ap.parse_args()
    if not nc.available():
        print(json.dumps({"error": f"native cluster library not available: {nc.status()['load_error']}"}))
        return 2
    t0 = time.time()
    cases, items, mismatches = Counter(), Counter(), Counter()
    examples = []
    for r in range(args.rounds):
        for kind in CASES:
            rng = np.random.default_rng([args.seed, r, list(CASES).index(kind)])
            todo = [CASES[kind](rng)]
            if args.full_every and r % args.full_every == 0:
                fc = full_case(kind, rng)
                if fc is not None:
                    todo.append(fc)
            for case in todo:
                ok = same(run_kernel(kind, case, "reference"), run_kernel(kind, case, "native"))
                if kind == "fb":
                    ok = ok and fb_variants(case) == 0
                cases[kind] += 1
                items[kind] += units(kind, case)
                if not ok:
                    mismatches[kind] += 1
                    if len(examples) < 10:
                        examples.append({"kernel": kind, "round": r, "seed": args.seed})
    out = {"rounds": args.rounds, "seed": args.seed, "cases": dict(cases), "reads_or_pairs": dict(items),
           "mismatches": sum(mismatches.values()), "mismatches_by_kernel": dict(mismatches), "examples": examples,
           "library": nc.status()["library"], "fb_level": nc.fb_level(), "seconds": round(time.time() - t0, 1)}
    print(json.dumps(out))
    return 1 if out["mismatches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
