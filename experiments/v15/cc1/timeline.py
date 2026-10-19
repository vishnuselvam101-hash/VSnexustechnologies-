"""V15 CC-1: wall-clock timeline of one decode (SIMULATED input), no profiler overhead.

    PYTHONPATH=src python experiments/v15/cc1/timeline.py --work DIR --workers W [--reps N]

Wraps the stage entry points (layout probe, engine pass 1, pass 2) with perf_counter timers and prints one JSON row
per run: total, the wrapped stages, the decoder's own stage timers and the engine's ns_* counters.
"""
from __future__ import annotations

import argparse
import functools
import json
import time
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--reps", type=int, default=1)
    a = ap.parse_args(argv)
    from vnxdna.native import engine as ne
    from vnxdna.pipeline import decode as pd
    from vnxdna.v4 import decoder as de
    acc: dict = {}

    def wrap(mod, name, label):
        fn = getattr(mod, name)

        @functools.wraps(fn)
        def inner(*args, **kw):
            t = time.perf_counter()
            try:
                return fn(*args, **kw)
            finally:
                acc[label] = acc.get(label, 0.0) + time.perf_counter() - t
        setattr(mod, name, inner)

    wrap(pd, "detect_layout", "layout_probe")
    wrap(ne, "run_pass1", "engine_pass1")
    wrap(pd, "_pass2", "pass2_total")
    for _ in range(a.reps):
        acc.clear()
        out = a.work / "o-timeline.vnx"
        t = time.perf_counter()
        res = de.decode_reads(a.work / "r.fastq", out, de.DecodeOptions(workers=a.workers))
        total = time.perf_counter() - t
        out.unlink(missing_ok=True)
        rep = res.report
        p1 = (rep.get("engine") or {}).get("pass1") or {}
        row = {"total": round(total, 4), **{k: round(v, 4) for k, v in acc.items()},
               "stage": {k: round(v, 4) for k, v in (rep.get("stage_seconds") or {}).items()},
               "engine_ns": {k: v for k, v in p1.items() if k.startswith("ns_")}, "status": res.status}
        row["other"] = round(total - sum(acc.values()), 4)
        print(json.dumps(row), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
