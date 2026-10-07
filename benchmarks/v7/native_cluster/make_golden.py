"""Write the golden hashes of the V7 read-clustering kernels (tests/v7/native_cluster_golden.json).

Each record is (kernel, seed, SHA-256 of the kernel's output) for a seeded case of ``tests/v7/native_cluster_support.py``
(SYNTHETIC reads). The hashes are computed with the NumPy reference (``VNXDNA_CLUSTER_BACKEND=reference``), the
specification; the tests require the native kernel to reproduce every one. Regenerate only after a reviewed change of
the reference or of the case generators.

usage: python benchmarks/v7/native_cluster/make_golden.py [--out tests/v7/native_cluster_golden.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from v7.native_cluster_support import case_for, digest, run_kernel  # noqa: E402

SEEDS = {"sketch": 20, "candidates": 20, "banded": 20, "fb": 20, "pool": 10}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "tests" / "v7" / "native_cluster_golden.json"))
    args = ap.parse_args()
    cases = []
    for kernel, n in SEEDS.items():
        for seed in range(n):
            out = run_kernel(kernel, case_for(kernel, seed), "reference")
            cases.append({"kernel": kernel, "seed": seed, "sha256": digest(*out)})
    doc = {"schema": "vnx.native-cluster-golden/1", "backend": "reference",
           "generator": "tests/v7/native_cluster_support.py case_for(kernel, seed); digest of run_kernel output",
           "cases": cases}
    Path(args.out).write_text(json.dumps(doc, indent=1) + "\n")
    print(f"{len(cases)} golden hashes -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
