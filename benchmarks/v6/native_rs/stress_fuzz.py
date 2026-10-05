"""Differential stress fuzz: native inner RS (every usable level) vs the reference vnxdna.v4.rs_fast. SIMULATED data.

Each round draws a code (n in 2..255, nsym in 1..n-1, biased towards the profiles' (70, 12/16/20)), a batch of words
(valid codewords with errors and erasures around the correction bound, uniformly random garbage, structured patterns)
and erasure masks of varied density (including more flags than parity). Every level must return the reference's
corrected words, ok flags and errata counts exactly. Exit status 1 on any mismatch.

usage: python benchmarks/v6/native_rs/stress_fuzz.py [--rounds 2000] [--seed 1] [--levels scalar,avx2,avx512]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np

from vnxdna.v4 import rs_fast
from vnxdna.v6 import native_rs as nr


def make_code(rng: np.random.Generator) -> tuple[int, int]:
    u = rng.random()
    if u < 0.35:
        return 70, int(rng.choice([12, 16, 20]))
    if u < 0.5:
        n = int(rng.integers(2, 256))
        return n, int(rng.integers(1, min(n, 65)))
    n = int(rng.integers(2, 256))
    return n, int(rng.integers(1, n))


def valid_codewords(rng: np.random.Generator, words: int, n: int, nsym: int) -> np.ndarray:
    """Random messages with parity solved by erasure decoding of the last nsym symbols (any nsym < n)."""
    w = rng.integers(0, 256, (words, n), dtype=np.uint8)
    er = np.zeros((words, n), dtype=bool)
    er[:, n - nsym:] = True
    out, ok, _ = rs_fast.decode_batch(w, nsym, er)
    assert ok.all()
    return out


def make_batch(rng: np.random.Generator, n: int, nsym: int) -> tuple[np.ndarray, np.ndarray | None]:
    words = int(rng.choice([1, 2, 7, 64, 300]))
    cw = valid_codewords(rng, words, n, nsym)
    er = np.zeros((words, n), dtype=bool)
    t = nsym // 2
    for i in range(words):
        kind = rng.random()
        if kind < 0.08:
            cw[i] = rng.integers(0, 256, n, dtype=np.uint8)                       # garbage
        elif kind < 0.12:
            cw[i] = rng.choice([0, 255, int(rng.integers(0, 256))])               # constant word
        elif kind < 0.15:
            start = int(rng.integers(0, n))
            cw[i, start:start + int(rng.integers(1, nsym + 3))] ^= rng.integers(1, 256, dtype=np.uint8)  # burst
        else:
            f = int(rng.integers(0, nsym + 3))
            e = max(0, (nsym - min(f, nsym)) // 2 + int(rng.integers(-t - 1, 4)))
            e, pos = min(e, n), rng.permutation(n)
            cw[i, pos[:e]] ^= rng.integers(1, 256, e, dtype=np.uint8)
            f = min(f, n - e)
            er[i, pos[e:e + f]] = True
            if rng.random() < 0.7:
                cw[i, pos[e:e + f]] = rng.integers(0, 256, f, dtype=np.uint8)
        if rng.random() < 0.05:
            er[i] |= rng.random(n) < rng.random()                                # random dense flags
    if not er.any() and rng.random() < 0.5:
        return cw, None
    return cw, er


def run(rounds: int, seed: int, levels: list[str]) -> dict:
    rng = np.random.default_rng(seed)
    stats = {"rounds": rounds, "seed": seed, "levels": levels, "words": 0, "comparisons": 0, "mismatches": 0,
             "reference_ok": 0, "codes": set()}
    t0 = time.perf_counter()
    for r in range(rounds):
        n, nsym = make_code(rng)
        cw, er = make_batch(rng, n, nsym)
        ref = rs_fast.decode_batch(cw, nsym, er)
        stats["words"] += cw.shape[0]
        stats["reference_ok"] += int(ref[1].sum())
        stats["codes"].add((n, nsym))
        for lv in levels:
            got = nr.decode_batch(cw, nsym, er, backend=lv)
            stats["comparisons"] += cw.shape[0]
            same = all(a.dtype == b.dtype and np.array_equal(a, b) for a, b in zip(got, ref))
            if not same:
                stats["mismatches"] += 1
                print(f"MISMATCH round {r} level {lv} n={n} nsym={nsym}", file=sys.stderr)
    stats["codes"] = len(stats["codes"])
    stats["seconds"] = round(time.perf_counter() - t0, 2)
    stats["native_status"] = {k: nr.status()[k] for k in ("library", "supported_levels", "cpu_levels")}
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--levels", default=None)
    args = ap.parse_args()
    if not nr.available():
        print(json.dumps(nr.status()))
        return 2
    levels = args.levels.split(",") if args.levels else nr.supported_levels()
    stats = run(args.rounds, args.seed, levels)
    print(json.dumps(stats))
    return 1 if stats["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
