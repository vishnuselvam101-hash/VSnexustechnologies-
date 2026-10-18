"""V14 P6 workstream B: stage profile of one decode (SIMULATED input), cProfile + the decoder's own stage timers.

    PYTHONPATH=src python experiments/v14/bench/p6b-profile/profile_stages.py pool   --reads R --manifest M --out O [--workers W]
    PYTHONPATH=src python experiments/v14/bench/p6b-profile/profile_stages.py single --reads R --out O [--workers W]

Prints one JSON row: wall seconds, the decoder report's stage timings, and the cumulative / own seconds of the
functions that implement each stage (layout probe, pass 1, partition/grouping, superblock, consensus, outer decode,
verification, output assembly), plus the top-25 functions by own time. Run under a systemd scope (governed).
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import pstats
import resource
import sys
import time
from pathlib import Path

STAGE_FUNCS = {
    "layout_probe": ("probe.py", "detect_layout"),
    "pass1_engine": ("engine.py", "run_pass1"),
    "pass1_python": ("decode.py", "_process"),
    "pool_pass1": ("decode.py", "_pass1"),
    "pool_partition": ("decode.py", "_partition"),
    "pass2": ("outer.py", "_pass2"),
    "superblock": ("superblock.py", "_decode_superblock"),
    "resolve_vectorised": ("outer.py", "_resolve_vectorised"),
    "resolve_duplicates": (None, "resolve_duplicates"),
    "consensus_symbols": (None, "_consensus_symbols"),
    "bucket_consensus": ("outer.py", "_bucket_consensus"),
    "spill_load": ("spill.py", "load"),
    "sha256_update": (None, "<method 'update' of '_hashlib.HASH' objects>"),
    "file_write": (None, "<method 'write' of '_io.BufferedWriter' objects>"),
    "np_fromfile": (None, "<built-in method numpy.fromfile>"),
    "lexsort": (None, "<built-in method numpy.lexsort>"),
    "argsort": (None, "argsort"),
    "stream_consume": ("stream.py", "consume"),
}


def _stage_times(st: pstats.Stats) -> dict:
    out = {}
    for (fn, _line, name), (_cc, nc, tt, ct, _callers) in st.stats.items():
        for label, (fpart, fname) in STAGE_FUNCS.items():
            if name == fname and (fpart is None or fn.endswith(fpart)):
                o = out.setdefault(label, {"calls": 0, "cum_s": 0.0, "own_s": 0.0})
                o["calls"] += nc
                o["cum_s"] = round(o["cum_s"] + ct, 4)
                o["own_s"] = round(o["own_s"] + tt, 4)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("pool", "single"))
    ap.add_argument("--reads", required=True)
    ap.add_argument("--manifest")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--want-sha")
    ap.add_argument("--no-profile", action="store_true", help="wall time only (no cProfile overhead)")
    a = ap.parse_args(argv)
    from vnxdna.recovery.options import DecodeOptions
    opt = DecodeOptions(workers=a.workers)
    prof = None if a.no_profile else cProfile.Profile()
    t = time.perf_counter()
    if prof:
        prof.enable()
    if a.mode == "pool":
        from vnxdna.pipeline import stream as st_
        rep = st_.decode_stream(a.reads, a.manifest, a.out, options=opt, overwrite=True, by_range=False)
        report = {k: v for k, v in rep.items() if k not in ("output", "extents")}
    else:
        from vnxdna.pipeline.decode import decode_reads
        res = decode_reads(a.reads, a.out, opt, overwrite=True)
        report = {"status": res.status, **{k: v for k, v in (res.report or {}).items() if k in ("stages", "timings", "stage_seconds")}}
    if prof:
        prof.disable()
    wall = time.perf_counter() - t
    h = hashlib.sha256(Path(a.out).read_bytes()).hexdigest()
    row = {"label": "SIMULATED", "mode": a.mode, "workers": a.workers, "wall_s": round(wall, 4), "profiled": prof is not None,
           "output_sha256": h, "matches": (h == a.want_sha) if a.want_sha else None, "report": report,
           # peak RSS of this process and of the LARGEST reaped child process (ru_maxrss is a max, not a sum)
           "maxrss_self_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
           "maxrss_child_mib": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)}
    if prof:
        st = pstats.Stats(prof)
        row["stages"] = _stage_times(st)
        s = io.StringIO()
        pstats.Stats(prof, stream=s).sort_stats("tottime").print_stats(25)
        row["top_own"] = [ln for ln in s.getvalue().splitlines() if ln.strip()][-26:]
    print(json.dumps(row, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
