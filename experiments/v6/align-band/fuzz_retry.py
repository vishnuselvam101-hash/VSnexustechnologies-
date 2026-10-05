"""AB-FUZZ: differential fuzz of the retry-band aligner, native vs reference, plus the in-band identity (SYNTHETIC reads).

    PYTHONPATH=src python experiments/v6/align-band/fuzz_retry.py --rounds 400 --reads 256 \
        --out experiments/v6/align-band/AB-FUZZ/results.json

Each round draws a layout (the V5 test layouts), a band B in 1..12, a retry band R in B+1..64, alignment costs, a quality
setting and ``--reads`` reads whose net drift is uniform in [−(R+3), R+3] (random deletions/insertions plus cancelling
pairs, substitutions, N calls, out-of-alphabet bytes, reverse complements). Checks, all bit for bit:

1. ``TemplateAligner(B, retry_band=R).project``: native == reference, every Projection field;
2. ``align_with_path``: native == reference projection and read positions (every 4th round, the path kernel);
3. in-band identity: for reads with |drift| ≤ B, the retry aligner's projection == the plain band-B aligner's;
4. window routing: for reads with B < |drift| ≤ R, the projection == the plain band-R aligner's.

Exits 1 on the first disagreement (the case is printed and written to the results file).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tests"))
from v5.native_support import FIELDS, LAYOUTS, strand  # noqa: E402

from vnxdna.core.provenance import environment  # noqa: E402
from vnxdna.native import align as na  # noqa: E402
from vnxdna.sync.smart.path import align_with_path  # noqa: E402
from vnxdna.sync.template import SyncCosts, TemplateAligner  # noqa: E402


def reads_for(layout, rng, n, max_drift):
    out = []
    for _ in range(n):
        s = strand(layout, rng)
        drift = int(rng.integers(-max_drift, max_drift + 1))
        extra = int(rng.integers(0, 4))
        for _ in range(abs(drift)):
            if drift < 0 and s.size:
                s = np.delete(s, int(rng.integers(0, s.size)))
            elif drift > 0:
                s = np.insert(s, int(rng.integers(0, s.size + 1)), np.uint8(rng.integers(0, 4)))
        for _ in range(extra):                                   # cancelling pairs
            s = np.insert(s, int(rng.integers(0, s.size + 1)), np.uint8(rng.integers(0, 4)))
            if s.size:
                s = np.delete(s, int(rng.integers(0, s.size)))
        s = s.copy()
        flip = rng.random(s.size) < rng.choice([0.0, 0.01, 0.05])
        s[flip] = (s[flip] + rng.integers(1, 4, int(flip.sum()))) % 4
        kind = int(rng.integers(0, 10))
        if kind == 0:
            s = (3 - s[::-1]).astype(np.uint8)
        elif kind == 1 and s.size:
            s[rng.integers(0, s.size, 3)] = rng.choice([4, 5, 200, 255], 3)
        elif kind == 2:
            s = rng.integers(0, 4, max(0, s.size)).astype(np.uint8)      # garbage of the same length
        out.append(s.astype(np.uint8))
    return out


def differs(a, b, fields=FIELDS, rows=None):
    for f in fields:
        x, y = getattr(a, f), getattr(b, f)
        if rows is not None:
            x, y = x[rows], y[rows]
        if x.dtype != y.dtype or not np.array_equal(x, y):
            return f
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--reads", type=int, default=256)
    ap.add_argument("--seed", type=int, default=80_080)
    ap.add_argument("--out")
    a = ap.parse_args()
    if not na.available():
        print("native unavailable:", na.status().get("load_error"))
        return 2
    rng = np.random.default_rng(a.seed)
    names = sorted(LAYOUTS)
    counts = {"reads": 0, "in_band": 0, "window": 0, "beyond": 0, "path_reads": 0}
    bands = {}
    failure = None
    t0 = time.perf_counter()
    for rnd in range(a.rounds):
        name = names[int(rng.integers(0, len(names)))]
        layout = LAYOUTS[name]
        band = int(rng.integers(1, 13))
        retry = int(rng.integers(band + 1, 65))
        costs = SyncCosts(*(int(v) for v in rng.choice([0, 1, 2, 4, 6, 7, 100, 65536], 4)), int(rng.integers(0, 3)))
        if rng.random() < 0.5:
            costs = SyncCosts()
        reads = reads_for(layout, rng, a.reads, retry + 3)
        quals = None
        if rng.random() < 0.5:
            quals = [rng.integers(0, 60, r.size).astype(np.uint8) for r in reads]
        minq = int(rng.integers(0, 40))
        case = {"round": rnd, "layout": name, "band": band, "retry_band": retry, "costs": str(costs), "min_quality": minq}
        ref = TemplateAligner(layout, band, costs, backend="reference", retry_band=retry).project(reads, quals, minq)
        nat = TemplateAligner(layout, band, costs, backend="native", retry_band=retry).project(reads, quals, minq)
        f = differs(ref, nat)
        if f:
            failure = {"check": "native-vs-reference", "field": f, **case}
            break
        drift = np.abs(np.array([r.size for r in reads]) - layout.strand_nt)
        inb, win = drift <= band, (drift > band) & (drift <= retry)
        plain = TemplateAligner(layout, band, costs, backend="native").project(reads, quals, minq)
        f = differs(nat, plain, rows=inb)
        if f:
            failure = {"check": "in-band-identity", "field": f, **case}
            break
        wide = TemplateAligner(layout, retry, costs, backend="native").project(reads, quals, minq)
        f = differs(nat, wide, rows=win)
        if f:
            failure = {"check": "window-routing", "field": f, **case}
            break
        if rnd % 4 == 0:
            al_n = TemplateAligner(layout, band, costs, backend="native", retry_band=retry)
            al_r = TemplateAligner(layout, band, costs, backend="reference", retry_band=retry)
            pn, rn = align_with_path(al_n, reads, quals, minq)
            pr, rr = align_with_path(al_r, reads, quals, minq, backend="reference")
            f = differs(pn, pr) or (None if np.array_equal(rn, rr) else "readpos") or differs(pn, nat)
            if f:
                failure = {"check": "path", "field": f, **case}
                break
            counts["path_reads"] += len(reads)
        counts["reads"] += len(reads)
        counts["in_band"] += int(inb.sum())
        counts["window"] += int(win.sum())
        counts["beyond"] += int((drift > retry).sum())
        bands[f"{band}/{retry}"] = bands.get(f"{band}/{retry}", 0) + 1
    doc = {"experiment": "AB-FUZZ", "classification": "SYNTHETIC reads; native vs reference comparison MEASURED",
           "args": vars(a), "counts": counts, "mismatches": 0 if failure is None else 1, "failure": failure,
           "band_retry_pairs_drawn": len(bands), "library": na.status().get("library"),
           "seconds": round(time.perf_counter() - t0, 1), "environment": environment()}
    print(json.dumps({k: doc[k] for k in ("counts", "mismatches", "failure", "seconds")}))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(doc, indent=1) + "\n")
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
