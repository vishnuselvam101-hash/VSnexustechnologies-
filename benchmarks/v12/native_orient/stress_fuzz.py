"""Differential stress fuzz of the native orientation scan against the NumPy reference (SIMULATED reads).

Every round draws a layout (every profile's layout plus two synthetic ones), a channel (substitution rate, indel rate,
reverse-complement rate, junk share) and a batch of reads, then compares ``pass1._orientation`` under
``VNXDNA_ORIENT_BACKEND=native`` and ``=reference`` read by read. A case is one read.

usage: PYTHONPATH=src python benchmarks/v12/native_orient/stress_fuzz.py [rounds] [reads-per-round] [seed]
exit 1 on the first mismatch (printed with the layout, channel and read index).
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

from vnxdna.dnaenc.layout import PROFILES, Layout
from vnxdna.recovery import pass1
from vnxdna.recovery.options import DecodeOptions

LAYOUTS = sorted({lay for lay, _, _ in PROFILES.values()}, key=repr) + [Layout(20, 0, 8, 2), Layout(64, 4, 10, 5)]


def batch(rng: np.random.Generator, lay: Layout, n: int, sub: float, ind: float, rc: float, junk: float):
    tpl, _ = lay.template()
    marker = tpl >= 0
    reads = []
    for _ in range(n):
        if rng.random() < junk:
            r = rng.integers(0, 5, rng.integers(0, 2 * tpl.size + 1)).astype(np.uint8)
        else:
            r = rng.integers(0, 4, tpl.size).astype(np.uint8)
            r[marker] = tpl[marker]
            r = r[rng.random(r.size) >= ind / 2]
            r = np.where(rng.random(r.size) < sub, rng.integers(0, 4, r.size), r).astype(np.uint8)
            ins = np.flatnonzero(rng.random(r.size) < ind / 2)
            r = np.insert(r, ins, rng.integers(0, 4, ins.size).astype(np.uint8))
            if rng.random() < rc:
                r = pass1._RC[r[::-1]]
            if rng.random() < 0.1:
                r = r[: rng.integers(0, r.size + 1)]
            if rng.random() < 0.1 and r.size:
                r[rng.integers(0, r.size, rng.integers(1, 6))] = 4
        reads.append(r)
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    return (np.concatenate(reads) if n else np.zeros(0, dtype=np.uint8)), lengths


def main(argv: list[str]) -> int:
    rounds = int(argv[1]) if len(argv) > 1 else 400
    per = int(argv[2]) if len(argv) > 2 else 250
    seed = int(argv[3]) if len(argv) > 3 else 12
    rng = np.random.default_rng(seed)
    opt = DecodeOptions()
    cases = flipped = 0
    t = time.perf_counter()
    for k in range(rounds):
        lay = LAYOUTS[k % len(LAYOUTS)]
        pass1._p_init(lay, opt.band, opt.sync_costs, 0, True)
        chan = (float(rng.choice([0, 0.005, 0.01, 0.02, 0.05, 0.1])), float(rng.choice([0, 0.002, 0.004, 0.01, 0.03, 0.08])),
                float(rng.choice([0, 0.5, 1.0])), float(rng.choice([0, 0.05, 0.2, 0.5, 1.0])))
        n = int(rng.integers(0, per + 1))
        codes, lengths = batch(rng, lay, n, *chan)
        os.environ["VNXDNA_ORIENT_BACKEND"] = "native"
        nat = pass1._orientation(codes, lengths)
        os.environ["VNXDNA_ORIENT_BACKEND"] = "reference"
        ref = pass1._orientation(codes, lengths)
        if nat.shape != ref.shape or nat.dtype != ref.dtype or not np.array_equal(nat, ref):
            bad = np.flatnonzero(nat != ref)
            print(f"MISMATCH round {k} layout {lay!r} channel {chan} reads {bad.tolist()[:10]}")
            return 1
        cases += n
        flipped += int(ref.sum())
    print(f"orient stress fuzz: rounds={rounds} cases={cases} flipped={flipped} mismatches=0 "
          f"seconds={time.perf_counter() - t:.1f} (SIMULATED reads)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
