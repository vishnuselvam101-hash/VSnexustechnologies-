"""Differential stress fuzz: native streaming read parser vs the reference vnxdna.v4.reads.

Each case is one seeded generated file (FASTQ / FASTA / plain; odd symbols, CRLF, blank lines, long lines, then
random mutations) with a random block size, record cap, batch size and max_reads. The reference parses it once;
the native parser parses it with block-aligned reads and with random chunk sizes. Every batch array and the
final exception must be identical. Prints one JSON summary line; exit status 1 on any mismatch.

usage: python benchmarks/v6/native_reads/stress_fuzz.py [--cases N] [--start S] [--workers W]
(run from the repository root with PYTHONPATH=src)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]


def _run(args: tuple[int, int]) -> dict:
    from v6.native.native_reads_support import check_case, fuzz_case

    from vnxdna.v6 import native_reads as nr

    assert nr.available(), nr.status()
    start, stop = args
    bad = []
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "case"
        for seed in range(start, stop):
            data, opts = fuzz_case(seed)
            try:
                check_case(path, data, opts)
            except AssertionError as error:
                bad.append({"seed": seed, "error": str(error)[:300]})
    return {"cases": stop - start, "comparisons": 2 * (stop - start), "mismatches": bad}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=100_000)
    ap.add_argument("--start", type=int, default=1_000_000)
    ap.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1))
    a = ap.parse_args()
    step = 500
    jobs = [(s, min(s + step, a.start + a.cases)) for s in range(a.start, a.start + a.cases, step)]
    t0 = time.perf_counter()
    if a.workers <= 1:
        results = [_run(j) for j in jobs]
    else:
        with ProcessPoolExecutor(a.workers) as ex:
            results = list(ex.map(_run, jobs))
    from vnxdna.v6 import native_reads as nr

    mism = [m for r in results for m in r["mismatches"]]
    out = {"cases": sum(r["cases"] for r in results), "comparisons": sum(r["comparisons"] for r in results),
           "mismatches": len(mism), "first_mismatches": mism[:5], "seeds": [a.start, a.start + a.cases],
           "library": nr.status()["library"], "seconds": round(time.perf_counter() - t0, 1)}
    print(json.dumps(out))
    return 1 if mism else 0


if __name__ == "__main__":
    raise SystemExit(main())
