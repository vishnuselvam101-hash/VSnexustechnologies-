"""V11 WP1 micro-benchmark: the native aligner's C call at every SIMD level (scalar 4 / avx2 8 / avx512 16 lanes).

Same reads as the V5 Phase 1 align matrix (benchmarks/v5/phase1_baseline.make_reads, recorded seeds). For every case
the projections of all levels are first checked byte-identical (else no timing is reported), then each level is timed
REPS times in rotating order (warm-up call first) through ``align_usable`` with the profiled entry point, so the C
stages (pack / dp / traceback / projection) are recorded as well. Statistic: median; dispersion: min/max. Host load
(1-minute load average before and after) is recorded because the host may be shared. All data SIMULATED.

usage: PYTHONPATH=src python experiments/v11/wp1/micro_levels.py [--reps 5] [--out results.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "benchmarks" / "v5"))
import numpy as np  # noqa: E402
import phase1_baseline as p1  # noqa: E402

from vnxdna.native import align as na  # noqa: E402
from vnxdna.v4.sync import TemplateAligner  # noqa: E402

POINTS = {
    "clean": {},
    "sub-0.5%": {"substitution_rate": 0.005},
    "indel-0.5%+0.5%": {"insertion_rate": 0.005, "deletion_rate": 0.005},
    "indel-1%+1%": {"insertion_rate": 0.01, "deletion_rate": 0.01},
}


def _sha(arrays) -> str:
    h = hashlib.sha256()
    for a in arrays:
        h.update(a.tobytes())
    return h.hexdigest()


def case(layout, lname: str, pname: str, chan: dict, levels: list[str], reps: int) -> dict:
    reads, meta = p1.make_reads(layout, chan)
    al = TemplateAligner(layout, p1.BAND, backend="native")
    usable = [r for r in reads if abs(r.size - al.T) <= p1.BAND]
    shas = {lv: _sha(na.align_usable(al, usable, None, 0, backend=lv)) for lv in levels}
    ref = al._align(usable, np.array([r.size for r in usable], dtype=np.int64), None, 0)
    ref_sha = _sha(np.asarray(x) for x in ref)
    if any(s != ref_sha for s in shas.values()):
        raise AssertionError(f"{lname} {pname}: level outputs differ ({shas}, reference {ref_sha}) - no timing reported")
    t = {lv: {k: [] for k in ("c_call", "dp", "traceback", "projection", "pack", "wall")} for lv in levels}
    for lv in levels:                                   # warm-up
        na.align_usable(al, usable, None, 0, backend=lv)
    for rep in range(reps):
        order = levels[rep % len(levels):] + levels[: rep % len(levels)]
        for lv in order:
            tm: dict = {}
            t0 = time.perf_counter()
            na.align_usable(al, usable, None, 0, timings=tm, backend=lv)
            t[lv]["wall"].append(time.perf_counter() - t0)
            for k in ("c_call", "dp", "traceback", "projection", "pack"):
                t[lv][k].append(tm.get(k, 0.0))

    def st(v):
        return {"median": round(statistics.median(v), 6), "min": round(min(v), 6), "max": round(max(v), 6)}
    out = {"layout": lname, "error_point": pname, "reads": len(reads), "reads_in_band": len(usable),
           "reads_sha256": meta["reads_sha256"], "projection_sha256_all_levels": ref_sha,
           "levels": {lv: {k: st(v) for k, v in t[lv].items()} for lv in levels}}
    base = statistics.median(t["scalar"]["c_call"])
    out["c_call_speedup_vs_scalar"] = {lv: round(base / statistics.median(t[lv]["c_call"]), 3) for lv in levels}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default=str(Path(__file__).with_name("micro_levels.json")))
    a = ap.parse_args()
    levels = na.supported_levels()
    load0 = os.getloadavg()
    cases = []
    for lname, lay in p1.LAYOUTS.items():
        for pname, chan in POINTS.items():
            r = case(lay, lname, pname, chan, levels, a.reps)
            cases.append(r)
            print(f"{lname:14s} {pname:16s} " + "  ".join(
                f"{lv} {1e3 * r['levels'][lv]['c_call']['median']:7.2f} ms (x{r['c_call_speedup_vs_scalar'][lv]})" for lv in levels))
    load1 = os.getloadavg()
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    payload = {"experiment": "v11-wp1-micro-levels", "simulated": True, "statistic": "median", "reps": a.reps,
               "levels": levels, "commit": commit, "kernel_source_sha256": hashlib.sha256(na._SOURCE.read_bytes()).hexdigest(),
               "library": na.status()["library"], "python": sys.version.split()[0], "machine": platform.machine(),
               "cpu_count": os.cpu_count(), "loadavg_before": load0, "loadavg_after": load1,
               "note": "single shared host; levels timed in rotating order within one process; C call = native kernel time",
               "cases": cases}
    Path(a.out).write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {a.out}; load {load0[0]:.2f} -> {load1[0]:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
