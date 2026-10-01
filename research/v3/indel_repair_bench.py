"""Single-read (coverage-1) indel repair: success rate and time per read (software simulation).

Usage::

    python research/v3/indel_repair_bench.py --reads 200 --out research/results/v3/indel_repair.json

Builds random frame-5 strands of the ``balanced`` geometry (2bit, 40 payload bytes, 8 inner parity bytes),
applies exactly ``k`` random single-base indels of one direction (plus ``s`` random substitutions) to each strand
(one extra row uses mixed directions) and runs
:func:`vnxdna.v2.sync.repair_read` with ``max_indel = k``. A repair counts as correct only if the recovered
payload and address equal the original; a repaired read that differs would be counted as ``wrong``.
Runs with whatever ``vnxdna`` is importable, so the same script measures V2 (tag v2.0.0) and V3.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

import vnxdna
from vnxdna.dna.constraints import ConstraintSpec
from vnxdna.v2.frame import FrameGeometry, build_strands
from vnxdna.v2.sync import repair_read


def damage(rng: np.random.Generator, codes: np.ndarray, indels: int, subs: int, mixed: bool) -> np.ndarray:
    """``indels`` single-base indels, all deletions or all insertions (one direction per read) unless ``mixed``."""
    out = codes.copy()
    for _ in range(subs):
        i = int(rng.integers(out.size))
        out[i] = (out[i] + int(rng.integers(1, 4))) % 4
    deletion = rng.random() < 0.5
    for _ in range(indels):
        if mixed:
            deletion = rng.random() < 0.5
        if deletion:
            out = np.delete(out, int(rng.integers(out.size)))
        else:
            out = np.insert(out, int(rng.integers(out.size + 1)), int(rng.integers(4)))
    return out


def run(reads: int, seed: int, max_candidates: int) -> list[dict]:
    geometry = FrameGeometry("2bit", 40, 8)
    rng = np.random.default_rng(seed)
    payloads = rng.integers(0, 256, (reads, 40), dtype=np.uint8)
    stripes = np.arange(reads, dtype=np.uint32)
    codes, _ = build_strands(geometry, ConstraintSpec(), 0x1234ABCD, np.zeros(reads, np.uint8), stripes,
                             np.zeros(reads, np.uint8), payloads)
    rows = []
    for indels, subs, mixed in ((1, 0, False), (1, 3, False), (2, 0, False), (2, 2, False), (3, 0, False), (2, 0, True)):
        # mixed directions can cancel (net length 0): such reads are counted as skipped (the length-based search
        # cannot see them; they reach the decoder as full-length reads and are usually rejected by the CRC)
        outcome = {"correct": 0, "wrong": 0, "failed": 0, "skipped_net_zero": 0}
        started = time.perf_counter()
        attempted = 0
        for i in range(reads):
            read = damage(rng, codes[i], indels, subs, mixed)
            if read.size == geometry.strand_nt:
                outcome["skipped_net_zero"] += 1
                continue
            attempted += 1
            result = repair_read(read, geometry, max_indel=3, max_candidates=max_candidates, reverse_complement=False)
            if result is None:
                outcome["failed"] += 1
            elif result[0][2] == i and result[0][4] == payloads[i].tobytes():
                outcome["correct"] += 1
            else:
                outcome["wrong"] += 1
        elapsed = time.perf_counter() - started
        rows.append({"indels": indels, "substitutions": subs, "directions": "mixed" if mixed else "same", "attempted": attempted, **outcome,
                     "seconds": round(elapsed, 3), "ms_per_read": round(1000 * elapsed / max(1, attempted), 2)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reads", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--max-candidates", type=int, default=100_000)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    result = {"vnxdna_version": vnxdna.__version__, "geometry": "2bit P=40 r=8 (balanced)", "reads": a.reads, "seed": a.seed,
              "max_candidates": a.max_candidates, "note": "software simulation; exactly k random single-base indels per read",
              "rows": run(a.reads, a.seed, a.max_candidates)}
    text = json.dumps(result, indent=2)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
