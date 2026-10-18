"""Summarize ab_bench.py rows: per (pool, workers) median wall per config, the median of the paired per-rep ratios
(base / new, > 1 = new faster) with its min-max, and the median stage split. SIMULATED input."""
from __future__ import annotations

import json
import random
import statistics
import sys
from collections import defaultdict


def boot_ci(xs: list[float], n: int = 10000, seed: int = 11) -> tuple[float, float]:
    """Percentile bootstrap 95 % CI of the median (10000 resamples, seed 11, as the V13/V14 benches)."""
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(xs, k=len(xs))) for _ in range(n))
    return meds[int(0.025 * n)], meds[int(0.975 * n) - 1]


def summarize(path: str) -> dict:
    rows = [json.loads(x) for x in open(path)]
    by = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        by[(r["pool"], r["workers"])][r["rep"]][r["config"]] = r
    out = {}
    for (pool, w), reps in sorted(by.items()):
        cfgs = sorted({c for rep in reps.values() for c in rep})
        a, b = cfgs[0], cfgs[1]
        ratios = [rep[a]["wall_s"] / rep[b]["wall_s"] for rep in reps.values() if a in rep and b in rep]
        ent = {"n": len(ratios), "ratio_%s_over_%s" % (a, b): {"median": round(statistics.median(ratios), 3),
                                                            "min": round(min(ratios), 3), "max": round(max(ratios), 3),
                                                            "ci95": [round(x, 3) for x in boot_ci(ratios)]},
               "all_outputs_match": all(rep[c]["matches"] for rep in reps.values() for c in rep)}
        p2 = [rep[a]["report"]["seconds"] - rep[a]["report"]["pass1_seconds"] - rep[a]["report"]["partition_seconds"]
              for rep in reps.values() if a in rep and b in rep]
        q2 = [rep[b]["report"]["seconds"] - rep[b]["report"]["pass1_seconds"] - rep[b]["report"]["partition_seconds"]
              for rep in reps.values() if a in rep and b in rep]
        r2 = [x / y for x, y in zip(p2, q2)]
        ent["pass2_wall_ratio_%s_over_%s" % (a, b)] = {"median": round(statistics.median(r2), 3),
                                                     "ci95": [round(x, 3) for x in boot_ci(r2)]}
        for c in cfgs:
            rs = [rep[c] for rep in reps.values() if c in rep]
            ent[c] = {"wall_median_s": round(statistics.median(r["wall_s"] for r in rs), 4),
                      "wall_min_s": round(min(r["wall_s"] for r in rs), 4), "wall_max_s": round(max(r["wall_s"] for r in rs), 4)}
            for k in ("pass1_seconds", "partition_seconds", "pool_pass2_seconds"):
                ent[c][k + "_median"] = round(statistics.median(r["report"][k] for r in rs), 4)
            # pass-2 wall (what the caller waits for after pass 1 + partition); pool_pass2_seconds is a per-extent sum
            ent[c]["pass2_wall_median_s"] = round(statistics.median(
                r["report"]["seconds"] - r["report"]["pass1_seconds"] - r["report"]["partition_seconds"] for r in rs), 4)
        out[f"{pool}/w{w}"] = ent
    return out


if __name__ == "__main__":
    print(json.dumps(summarize(sys.argv[1]), indent=1))
