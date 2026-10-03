"""V5 Phase 2 benchmark: native (C) marker aligner vs the V4 NumPy reference. All data is SIMULATED.

Reuses the Phase 1 read generator (`benchmarks/v5/phase1_baseline.py`: same seeds, same 4,096 reads per case;
every case asserts that its reads SHA-256 matches Phase 1). Both backends run in the same session on the same
machine. Every number is the **median of 5 repetitions**; min, max and all repetitions are recorded, and nothing is
best-of. Every case checks that the two backends produce the same projection SHA-256 (and the Phase 1 golden
SHA-256 where one exists) **before** its timing is recorded.

Sections (``python benchmarks/v5/native_alignment/bench_native.py SECTION``):
  align     read-length × error matrix, timing split (reference: DP fill / traceback; native: pack / DP / traceback /
            projection / Python wrapper)
  scaling   error-rate scaling on 313 nt: 0, 0.1, 0.5, 1, 2 % insertions + the same deletions
  memory    peak RSS growth while aligning 4,096 reads, each backend in a fresh process
  workers   the Phase 1 32,768-read worker benchmark (1/2/4/8 processes) for both backends
  e2e       the Phase 1 4 MiB end-to-end runs (clean; EXP-0011 channel on 1/2/4/8 workers) for both backends
  profile   cProfile of the single-worker noisy decode for both backends, cumulative time per pipeline stage
  all       everything
"""
from __future__ import annotations

import argparse
import cProfile
import ctypes
import hashlib
import json
import multiprocessing as mp
import os
import pstats
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import phase1_baseline as p1  # noqa: E402
from provenance import provenance  # noqa: E402

from vnxdna.v4.sync import TemplateAligner  # noqa: E402
from vnxdna.v5 import native_alignment as na  # noqa: E402

OUT = HERE / "results"
REPS = 5
GOLDEN = {(c["layout_name"], c["error_point"]): c for c in
          json.loads((HERE.parent / "baseline-v4" / "align.json").read_text())["cases"]}

MATRIX_POINTS = {
    "clean": {},
    "sub-0.5%": {"substitution_rate": 0.005},
    "indel-0.1%+0.1%": {"insertion_rate": 0.001, "deletion_rate": 0.001},
    "indel-0.5%+0.5%": {"insertion_rate": 0.005, "deletion_rate": 0.005},
    "indel-1%+1%": {"insertion_rate": 0.01, "deletion_rate": 0.01},
}
SCALING_POINTS = {
    "indel-0%+0%": {},
    "indel-0.1%+0.1%": {"insertion_rate": 0.001, "deletion_rate": 0.001},
    "indel-0.5%+0.5%": {"insertion_rate": 0.005, "deletion_rate": 0.005},
    "indel-1%+1%": {"insertion_rate": 0.01, "deletion_rate": 0.01},
    "indel-2%+2%": {"insertion_rate": 0.02, "deletion_rate": 0.02},
}


def _stats(values: list[float]) -> dict:
    return {"median": round(statistics.median(values), 6), "min": round(min(values), 6), "max": round(max(values), 6),
            "reps": [round(v, 6) for v in values]}


def native_info() -> dict:
    st = na.status()
    lib = st.get("library")
    st["library_sha256"] = hashlib.sha256(Path(lib).read_bytes()).hexdigest() if lib else None
    st["lanes"] = na._load().vnx_align_lanes() if na.available() else None
    # The results are produced before the commit that contains them, so tie them to the kernel source directly.
    st["source_sha256"] = hashlib.sha256(na._SOURCE.read_bytes()).hexdigest()
    st["build"] = "setup.py Extension (pip install): -O3 -std=c11, no -march; the portable x86-64 baseline ISA"
    return st


def _write(name: str, payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {OUT / name}.json")


# ============================================================================ aligner timing
def time_case(layout, channel: dict, layout_name: str, point_name: str) -> dict:
    reads, meta = p1.make_reads(layout, channel)
    golden = GOLDEN.get((layout_name, point_name))
    if golden:
        assert meta["reads_sha256"] == golden["reads_sha256"], "reads differ from Phase 1"
    n = len(reads)
    bases = int(sum(r.size for r in reads))
    # --- correctness first: identical projections (and the Phase 1 golden hash) before any timing is kept
    ref_sha = p1._projection_sha(TemplateAligner(layout, p1.BAND, backend="reference").project(reads))
    nat_sha = p1._projection_sha(TemplateAligner(layout, p1.BAND, backend="native").project(reads))
    if ref_sha != nat_sha or (golden and nat_sha != golden["projection_sha256"]):
        raise AssertionError(f"{layout_name} {point_name}: native/reference projection mismatch — no timing reported")
    # --- reference: wall, DP fill, traceback
    ref_wall, ref_tb = [], []
    for _ in range(REPS):
        al = p1._TimedAligner(layout, p1.BAND, backend="reference")
        t = time.perf_counter()
        al.project(reads)
        ref_wall.append(time.perf_counter() - t)
        ref_tb.append(al.traceback_s)
    # --- native: wall through TemplateAligner.project, plus the in-kernel stage split from the profiled entry point
    nat_wall, split = [], {k: [] for k in ("pack", "dp", "traceback", "projection", "c_call", "wrapper")}
    al = TemplateAligner(layout, p1.BAND, backend="native")
    usable = [r for r in reads if abs(r.size - al.T) <= p1.BAND]
    for _ in range(REPS):
        t = time.perf_counter()
        al.project(reads)
        nat_wall.append(time.perf_counter() - t)
        tm: dict = {}
        na.align_usable(al, usable, None, 0, timings=tm)
        for k in split:
            split[k].append(tm.get(k, 0.0))
    rw, nw = statistics.median(ref_wall), statistics.median(nat_wall)
    return {
        "layout_name": layout_name, "error_point": point_name, "strand_nt": layout.strand_nt, "reads": n, "bases": bases,
        "reads_in_band": len(usable), "projection_sha256": nat_sha, "identical_to_reference": True,
        "matches_phase1_golden": bool(golden) or None, "reads_sha256": meta["reads_sha256"], "channel": meta["channel"],
        "reference": {"seconds": _stats(ref_wall), "dp_fill_seconds": _stats([w - b for w, b in zip(ref_wall, ref_tb)]),
                      "traceback_seconds": _stats(ref_tb), "reads_per_second": round(n / rw, 1), "us_per_read": round(1e6 * rw / n, 3),
                      "mbases_per_second": round(bases / rw / 1e6, 3)},
        "native": {"seconds": _stats(nat_wall), **{f"{k}_seconds": _stats(v) for k, v in split.items()},
                   "reads_per_second": round(n / nw, 1), "us_per_read": round(1e6 * nw / n, 3),
                   "mbases_per_second": round(bases / nw / 1e6, 3)},
        "speedup_median": round(rw / nw, 2),
    }


def section_align() -> dict:
    cases = []
    for lname, lay in p1.LAYOUTS.items():
        for pname, chan in MATRIX_POINTS.items():
            r = time_case(lay, chan, lname, pname)
            cases.append(r)
            print(f"  {lname:14s} {pname:16s} ref {r['reference']['reads_per_second']:>9.0f}  native {r['native']['reads_per_second']:>9.0f}"
                  f" reads/s  ×{r['speedup_median']}")
    cfg = {"points": MATRIX_POINTS, "layouts": {k: v.to_dict() for k, v in p1.LAYOUTS.items()}, "reps": REPS, "band": p1.BAND,
           "strands": p1.ALIGN_STRANDS, "payload_seed": p1.ALIGN_SEED, "channel_seed": p1.CHANNEL_SEED, "workers": 1, "statistic": "median"}
    payload = {"section": "align", "simulated": True, "config": cfg, "provenance": provenance(cfg), "native": native_info(), "cases": cases}
    _write("align", payload)
    return payload


def section_scaling() -> dict:
    lay = p1.LAYOUTS["default-313nt"]
    cases = []
    for pname, chan in SCALING_POINTS.items():
        r = time_case(lay, chan, "default-313nt", pname)
        cases.append(r)
        print(f"  {pname:16s} ref {r['reference']['us_per_read']:7.1f} us/read (tb {r['reference']['traceback_seconds']['median']:.3f} s)"
              f"  native {r['native']['us_per_read']:6.2f} us/read (dp {r['native']['dp_seconds']['median']:.4f} s,"
              f" tb {r['native']['traceback_seconds']['median']:.4f} s)  ×{r['speedup_median']}")
    cfg = {"points": SCALING_POINTS, "layout": lay.to_dict(), "reps": REPS, "band": p1.BAND, "strands": p1.ALIGN_STRANDS,
           "payload_seed": p1.ALIGN_SEED, "channel_seed": p1.CHANNEL_SEED, "statistic": "median"}
    payload = {"section": "scaling", "simulated": True, "config": cfg, "provenance": provenance(cfg), "native": native_info(), "cases": cases}
    _write("scaling", payload)
    return payload


# ============================================================================ memory (fresh process per backend)
def _proc_status_kb(field: str) -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1])
    raise RuntimeError(f"{field} not in /proc/self/status")


def _mem_child(backend: str, layout_name: str, q) -> None:
    import gc
    reads, _ = p1.make_reads(p1.LAYOUTS[layout_name], p1.ERROR_POINTS["mixed-L2"])
    al = TemplateAligner(p1.LAYOUTS[layout_name], p1.BAND, backend=backend)
    al.project(reads[:8])                                           # load the library / warm imports
    gc.collect()
    ctypes.CDLL("libc.so.6").malloc_trim(0)                         # hand freed arena pages back, or project() reuses them
    # Read generation already pushed the high-water mark above anything project() reaches, so ru_maxrss would show
    # no growth. Linux lets a process reset its own VmHWM ("5" -> /proc/self/clear_refs); peak growth is then
    # VmHWM after project() minus VmRSS just before it.
    Path("/proc/self/clear_refs").write_text("5")
    before = _proc_status_kb("VmRSS")
    al.project(reads)
    peak = _proc_status_kb("VmHWM")
    tracemalloc.start()                                             # second, independent run: NumPy/Python heap peak only
    al.project(reads)
    _, tm_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    q.put({"backend": backend, "layout_name": layout_name, "reads": len(reads), "vmrss_before_kb": before, "vmhwm_after_kb": peak,
           "peak_growth_mb": round((peak - before) / 1024, 2), "tracemalloc_peak_mb": round(tm_peak / 2**20, 2)})


def section_memory() -> dict:
    ctx = mp.get_context("spawn")
    rows = []
    for lname in p1.LAYOUTS:
        for backend in ("reference", "native"):
            q = ctx.Queue()
            pr = ctx.Process(target=_mem_child, args=(backend, lname, q))
            pr.start()
            rows.append(q.get(timeout=600))
            pr.join()
            print(f"  {lname:14s} {backend:9s} peak RSS growth {rows[-1]['peak_growth_mb']:7.2f} MB  tracemalloc peak {rows[-1]['tracemalloc_peak_mb']:7.2f} MB")
    lanes = na._load().vnx_align_lanes()
    analytic = {}
    for lname, lay in p1.LAYOUTS.items():
        T, B = lay.strand_nt, p1.BAND
        W, width = 2 * B + 1, T + B + 2
        analytic[lname] = {"native_scratch_bytes": (T + 1) * W * lanes + (B + width) * 4 * lanes + 2 * (W + 1) * 4 * lanes + 2 * T + 64,
                           "reference_ptr_bytes_per_2048_chunk": (T + 1) * W * 2048}
    cfg = {"reads": p1.ALIGN_STRANDS, "error_point": "mixed-L2", "band": p1.BAND, "method": "VmHWM (reset via /proc/self/clear_refs) minus VmRSS around one project() call on all reads, "
           "in a fresh spawned process after malloc_trim(0); plus tracemalloc peak of a second call (sees NumPy, not C malloc); outputs (n × frame_nt × 2 bytes) are included for both backends"}
    payload = {"section": "memory", "simulated": True, "config": cfg, "provenance": provenance(cfg), "native": native_info(), "rows": rows,
               "analytic": analytic}
    _write("memory", payload)
    return payload


# ============================================================================ worker scaling (Phase 1 workload)
def section_workers() -> dict:
    out = {}
    for backend in ("reference", "native"):
        os.environ["VNXDNA_ALIGN_BACKEND"] = backend
        try:
            tmp = Path(tempfile.mkdtemp(prefix="vnx5-w-"))
            out[backend] = p1.section_align_workers(tmp)["rows"]
        finally:
            os.environ.pop("VNXDNA_ALIGN_BACKEND", None)
    same = len({r["result_sha256"] for b in out.values() for r in b}) == 1
    for r in out["native"]:
        r["efficiency"] = round(r["speedup"] / r["workers"], 3)
    for r in out["reference"]:
        r["efficiency"] = round(r["speedup"] / r["workers"], 3)
    cfg = {"reads": 32768, "chunk": 2048, "error_point": "mixed-L2", "band": p1.BAND, "pool_startup_included": True,
           "statistic": "best of 2 (as Phase 1, for comparability)"}
    payload = {"section": "workers", "simulated": True, "config": cfg, "provenance": provenance(cfg), "native": native_info(),
               "rows": out, "identical_across_backends_and_workers": same}
    _write("workers", payload)
    return payload


# ============================================================================ end to end
def section_e2e() -> dict:
    out = {}
    for backend in ("reference", "native"):
        os.environ["VNXDNA_ALIGN_BACKEND"] = backend
        try:
            tmp = Path(tempfile.mkdtemp(prefix="vnx5-e2e-"))
            out[backend] = p1.section_e2e(tmp)["runs"]
        finally:
            os.environ.pop("VNXDNA_ALIGN_BACKEND", None)
    cfg = {"input_size": p1.E2E_SIZE, "pattern": "random", "seed": 42, "profile": "v4-balanced", "noisy_channel": p1.NOISY_CHANNEL,
           "runs": "fresh process each; same input and channel seeds for both backends"}
    payload = {"section": "e2e", "simulated": True, "config": cfg, "provenance": provenance(cfg), "native": native_info(), "runs": out}
    _write("e2e", payload)
    return payload


# ============================================================================ decode profile, both backends
STAGES = {  # cumulative time of the function that owns each pipeline stage (the stages do not nest in one another)
    "marker alignment": ("sync.py", "project"),
    "read parsing": ("reads.py", "iter_reads"),
    "inner RS decode": ("rs_fast.py", "decode_batch"),
    "pass 2 (consensus, snapping, outer, verify)": ("decoder.py", "_pass2"),
    "orientation pre-pass": ("decoder.py", "_orientation"),
}


def _profile_once(backend: str, tmp: Path) -> dict:
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import channel as ch
    from vnxdna.v4 import datagen
    from vnxdna.v4 import decoder as de
    from vnxdna.v4 import encoder as en
    src = tmp / "input.bin"
    in_sha = datagen.generate(src, p1.E2E_SIZE, "random", 42)
    if not (tmp / "r.fastq").exists():
        ar.build_archive([src], tmp / "a.vnx", ar.ArchiveOptions(workers=1))
        en.encode_container(tmp / "a.vnx", tmp / "s.fasta", en.DNAOptions(workers=1))
        ch.simulate_file(tmp / "s.fasta", tmp / "r.fastq", ch.ChannelConfig.from_dict(p1.NOISY_CHANNEL), workers=1)
    os.environ["VNXDNA_ALIGN_BACKEND"] = backend
    try:
        # unprofiled wall time first (3 repetitions, median), then one profiled run for the stage shares
        walls = []
        for k in range(3):
            t = time.perf_counter()
            res = de.decode_reads(tmp / "r.fastq", tmp / f"{backend}-{k}.vnx", de.DecodeOptions(workers=1))
            walls.append(time.perf_counter() - t)
            assert res.status == "SUCCESS"
        prof = cProfile.Profile()
        t = time.perf_counter()
        prof.enable()
        res = de.decode_reads(tmp / "r.fastq", tmp / f"{backend}-prof.vnx", de.DecodeOptions(workers=1))
        prof.disable()
        pwall = time.perf_counter() - t
    finally:
        os.environ.pop("VNXDNA_ALIGN_BACKEND", None)
    st = pstats.Stats(prof).stats
    total = next(ct for (fn, _, name), (_, _, _, ct, _) in st.items() if fn.endswith("decoder.py") and name == "decode_reads")
    stages = {}
    for stage, (fname, func) in STAGES.items():
        stages[stage] = round(sum(ct for (fn, _, name), (_, _, _, ct, _) in st.items() if fn.endswith(fname) and name == func), 4)
    stages["everything else"] = round(total - sum(stages.values()), 4)
    return {"backend": backend, "status": res.status, "container_sha256": res.report.get("container_sha256"), "input_sha256": in_sha,
            "decode_reads": res.report.get("reads"), "unprofiled_seconds": _stats(walls), "profiled_seconds": round(pwall, 3),
            "profiled_total": round(total, 4), "stage_seconds": stages,
            "stage_share": {k: round(v / total, 4) for k, v in stages.items()}}


def section_profile() -> dict:
    with tempfile.TemporaryDirectory(prefix="vnx5-prof-") as tmp:
        tmp = Path(tmp)
        rows = [_profile_once("reference", tmp), _profile_once("native", tmp)]
    assert rows[0]["container_sha256"] == rows[1]["container_sha256"], "decoded containers differ between backends"
    for r in rows:
        print(f"  {r['backend']:9s} decode {r['unprofiled_seconds']['median']:6.2f} s (median of 3, unprofiled); profiled shares: "
              + ", ".join(f"{k} {v:.1%}" for k, v in r["stage_share"].items()))
    cfg = {"input_size": p1.E2E_SIZE, "pattern": "random", "seed": 42, "profile": "v4-balanced", "workers": 1, "channel": p1.NOISY_CHANNEL,
           "stage_definition": {k: f"{a}:{b} cumulative" for k, (a, b) in STAGES.items()}}
    payload = {"section": "profile", "simulated": True, "config": cfg, "provenance": provenance(cfg), "native": native_info(), "rows": rows}
    _write("profile", payload)
    return payload


SECTIONS = {"align": section_align, "scaling": section_scaling, "memory": section_memory, "workers": section_workers,
            "e2e": section_e2e, "profile": section_profile}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("section", choices=[*SECTIONS, "all"])
    a = ap.parse_args()
    if not na.available():
        raise SystemExit(f"native aligner unavailable: {na.status()['load_error']}")
    for name in (SECTIONS if a.section == "all" else [a.section]):
        print(f"== {name}")
        SECTIONS[name]()


if __name__ == "__main__":
    main()
