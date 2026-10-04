"""Long differential fuzz: native vs reference aligner, every Projection field (SIMULATED data).

Larger than the test-suite fuzz. Meant to run against a sanitizer build as well:

    VNXDNA_NATIVE_LIB=/path/asan.so LD_PRELOAD="$(gcc -print-file-name=libasan.so) $(gcc -print-file-name=libubsan.so)" \
        ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 python benchmarks/v5/native_alignment/stress_fuzz.py --rounds 400

Each round draws a layout, band, costs, quality setting and 250 reads (random edits, reverse complements, random
lengths, N calls and out-of-alphabet bytes). It exits with status 1 on the first disagreement and prints the case.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
from v5.native_support import FIELDS, LAYOUTS, strand  # noqa: E402

from vnxdna.v4.sync import SyncCosts, TemplateAligner  # noqa: E402
from vnxdna.v5 import native_alignment as na  # noqa: E402


def reads_for(layout, rng, n):
    T = layout.strand_nt
    out = []
    for _ in range(n):
        s = strand(layout, rng)
        kind = int(rng.integers(0, 8))
        if kind == 0:
            out.append(rng.integers(0, 4, max(0, T + int(rng.integers(-30, 31)))).astype(np.uint8))
            continue
        for _ in range(int(rng.integers(0, 3 + 10 * (kind >= 4)))):
            op = int(rng.integers(0, 3))
            if op == 0 and s.size:
                p = int(rng.integers(0, s.size))
                s = s.copy()
                s[p] = (s[p] + int(rng.integers(1, 4))) % 4
            elif op == 1:
                s = np.insert(s, int(rng.integers(0, s.size + 1)), np.uint8(rng.integers(0, 4)))
            elif s.size:
                s = np.delete(s, int(rng.integers(0, s.size)))
        if kind == 5:
            s = (3 - s[::-1]).astype(np.uint8)
        if kind == 6 and s.size:
            s = s.copy()
            s[rng.integers(0, s.size, 3)] = rng.choice([4, 5, 200, 255], 3)
        out.append(s.astype(np.uint8))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--reads", type=int, default=250)
    ap.add_argument("--seed", type=int, default=20261003)
    a = ap.parse_args()
    if not na.available():
        print("native unavailable:", na.status()["load_error"])
        return 2
    rng = np.random.default_rng(a.seed)
    names = sorted(LAYOUTS)
    total = 0
    t0 = time.perf_counter()
    for rnd in range(a.rounds):
        name = names[int(rng.integers(0, len(names)))]
        layout = LAYOUTS[name]
        band = int(rng.choice([0, 1, 2, 3, 5, 6, 8, 12, 16, 32, 64]))
        costs = SyncCosts(*(int(v) for v in rng.choice([0, 1, 2, 4, 6, 7, 100, 65536], 4)), int(rng.integers(0, 4)))
        reads = reads_for(layout, rng, a.reads)
        quals = None
        if rng.random() < 0.5:
            quals = [rng.integers(0, 60, r.size).astype(np.uint8) if rng.random() < 0.9 else None for r in reads]
        minq = int(rng.integers(-5, 60))
        ref = TemplateAligner(layout, band, costs, backend="reference").project(reads, quals, minq)
        nat = TemplateAligner(layout, band, costs, backend="native").project(reads, quals, minq)
        for f in FIELDS:
            x, y = getattr(ref, f), getattr(nat, f)
            if x.dtype != y.dtype or not np.array_equal(x, y):
                print(json.dumps({"MISMATCH": f, "round": rnd, "layout": name, "band": band, "costs": str(costs), "min_quality": minq}))
                return 1
        total += len(reads)
    print(json.dumps({"rounds": a.rounds, "reads": total, "mismatches": 0, "library": na.status()["library"],
                      "seconds": round(time.perf_counter() - t0, 1)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
