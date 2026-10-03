"""V5 Phase 1: freeze the V4 baseline, profile the pipeline and locate the first bottleneck.

Runs only V4 code (``vnxdna.v4`` at the commit under test, which for the
baseline is tag v4.0.0) and writes JSON with full provenance to
``benchmarks/v5/baseline-v4/``. All data is SIMULATED.

Sections (``python benchmarks/v5/phase1_baseline.py SECTION [--out DIR]``):

* ``align``    marker-template aligner micro-benchmark: reads/s, bases/s, DP vs
               traceback split, peak traced memory, scaling with read length and
               error rate, plus SHA-256 of every projection (the golden reference
               a native kernel must reproduce bit for bit).
* ``align-workers``  aligner throughput on 1/2/4/8 processes.
* ``e2e``      end-to-end clean and noisy round trips (encode, channel, decode),
               1/2/4/8 workers for the noisy case, each in a fresh process.
* ``profile``  cProfile of a single-worker noisy decode, grouped by stage.
* ``overhead`` nucleotide cost breakdown per V4 profile (analytic, cross-checked
               against a measured encode).
* ``all``      every section above.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import os
import pstats
import sys
import tempfile
import time
import tracemalloc
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provenance import provenance  # noqa: E402

from vnxdna.v4 import archive as ar  # noqa: E402
from vnxdna.v4 import bench  # noqa: E402
from vnxdna.v4 import channel as ch  # noqa: E402
from vnxdna.v4 import datagen  # noqa: E402
from vnxdna.v4 import decoder as de  # noqa: E402
from vnxdna.v4 import encoder as en  # noqa: E402
from vnxdna.v4.constraints import ConstraintConfig  # noqa: E402
from vnxdna.v4.frame import KIND_DATA, PROFILES, Layout, build_strands  # noqa: E402
from vnxdna.v4.sync import TemplateAligner  # noqa: E402

OUT = Path(__file__).resolve().parent / "baseline-v4"

# Read-length scaling uses the default marker geometry (3-nt markers every 24 nt, inner parity 16) and changes only
# the payload, so strand length is the single variable.
LAYOUTS = {
    "short-178nt": Layout(10, 16, 24, 3),
    "default-313nt": PROFILES["v4-balanced"][0],
    "long-494nt": Layout(80, 16, 24, 3),
}
ERROR_POINTS = {
    "clean": {},
    "sub-0.5%": {"substitution_rate": 0.005},
    "indel-0.1%+0.1%": {"insertion_rate": 0.001, "deletion_rate": 0.001},
    "indel-0.5%+0.5%": {"insertion_rate": 0.005, "deletion_rate": 0.005},
    "indel-1%+1%": {"insertion_rate": 0.01, "deletion_rate": 0.01},
    "mixed-L2": {"substitution_rate": 0.005, "insertion_rate": 0.002, "deletion_rate": 0.002},
}
ALIGN_STRANDS = 4096
ALIGN_SEED = 20261003
CHANNEL_SEED = 5001
BAND = 6

NOISY_CHANNEL = {  # identical to EXP-0011 (V4 worker scaling)
    "substitution_rate": 0.002, "insertion_rate": 0.0005, "deletion_rate": 0.0005, "dropout_rate": 0.02,
    "coverage": 3, "coverage_model": "poisson", "seed": 1011,
}


def _write(name: str, out: Path, payload: dict) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{name}.json"
    p.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    print(f"wrote {p}")
    return p


def _sha(*arrays: np.ndarray) -> str:
    h = hashlib.sha256()
    for a in arrays:
        a = np.ascontiguousarray(a)
        h.update(str(a.dtype).encode() + str(a.shape).encode())
        h.update(a.tobytes())
    return h.hexdigest()


# ============================================================================ read generation
def make_reads(layout: Layout, channel: dict, n: int = ALIGN_STRANDS, seed: int = ALIGN_SEED) -> tuple[list[np.ndarray], dict]:
    """Real V4 strands (random payloads, default constraints) passed through the V4 channel at coverage 1."""
    rng = np.random.default_rng(seed)
    payloads = rng.integers(0, 256, (n, layout.payload_bytes), dtype=np.uint8)
    groups = np.arange(n, dtype=np.int64) // 80
    symbols = np.arange(n, dtype=np.int64) % 80
    strands, _ = build_strands(layout, ConstraintConfig(), 0x5A5A, KIND_DATA, groups, symbols, payloads)
    cfg = ch.ChannelConfig.from_dict({**channel, "coverage": 1, "seed": CHANNEL_SEED})
    res = ch.simulate_batch(strands, cfg, 0)
    offs = np.concatenate([[0], np.cumsum(res["lengths"])])
    reads = [res["codes"][offs[i]:offs[i + 1]] for i in range(res["lengths"].size)]
    return reads, {"strands_sha256": _sha(strands), "reads_sha256": _sha(res["codes"], res["lengths"]), "channel": cfg.to_dict(),
                   "channel_stats": res["stats"]}


class _TimedAligner(TemplateAligner):
    """Splits wall time between the DP fill (``_align`` minus traceback) and the traceback."""

    traceback_s = 0.0

    def _traceback(self, *a, **k):
        t = time.perf_counter()
        try:
            return super()._traceback(*a, **k)
        finally:
            self.traceback_s += time.perf_counter() - t


def _projection_sha(pr) -> str:
    return _sha(pr.bases, pr.erased, pr.ok, pr.insertions, pr.deletions, pr.marker_mismatches, pr.cost)


def align_case(layout: Layout, channel: dict, repeats: int = 3) -> dict:
    reads, meta = make_reads(layout, channel)
    lengths = np.array([r.size for r in reads])
    usable = int((np.abs(lengths - layout.strand_nt) <= BAND).sum())
    best = None
    for _ in range(repeats):
        al = _TimedAligner(layout, BAND)
        t = time.perf_counter()
        pr = al.project(reads)
        wall = time.perf_counter() - t
        if best is None or wall < best[0]:
            best = (wall, al.traceback_s, pr)
    wall, tb, pr = best
    tracemalloc.start()
    TemplateAligner(layout, BAND).project(reads[:2048])
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    seg_er = pr.erased.reshape(pr.erased.shape[0], -1)
    return {
        "layout": layout.to_dict(), "band": BAND, "reads": len(reads), "reads_in_band": usable,
        "bases": int(lengths.sum()), "aligned_ok": int(pr.ok.sum()),
        "seconds": round(wall, 4), "dp_seconds": round(wall - tb, 4), "traceback_seconds": round(tb, 4),
        "traceback_fraction": round(tb / wall, 3) if wall else None,
        "reads_per_second": round(len(reads) / wall, 1), "bases_per_second": round(lengths.sum() / wall, 1),
        "us_per_read": round(1e6 * wall / len(reads), 2),
        "peak_traced_bytes_per_2048_batch": int(peak),
        "mean_erased_nt_per_read": round(float(seg_er[pr.ok].sum(axis=1).mean()) if pr.ok.any() else 0.0, 3),
        "erased_nt_fraction": round(float(seg_er[pr.ok].mean()) if pr.ok.any() else 0.0, 5),
        "detected_insertions": int(pr.insertions.sum()), "detected_deletions": int(pr.deletions.sum()),
        "projection_sha256": _projection_sha(pr), **meta,
    }


def section_align(out: Path) -> dict:
    cases = []
    for lname, lay in LAYOUTS.items():
        points = ERROR_POINTS if lname == "default-313nt" else {k: ERROR_POINTS[k] for k in ("clean", "indel-0.5%+0.5%")}
        for pname, chan in points.items():
            r = align_case(lay, chan)
            r.update(layout_name=lname, error_point=pname)
            print(f"  align {lname:14s} {pname:16s} {r['reads_per_second']:>9.0f} reads/s  tb {r['traceback_fraction']:.0%}")
            cases.append(r)
    cfg = {"layouts": {k: v.to_dict() for k, v in LAYOUTS.items()}, "error_points": ERROR_POINTS, "strands": ALIGN_STRANDS,
           "payload_seed": ALIGN_SEED, "channel_seed": CHANNEL_SEED, "band": BAND, "repeats": 3, "workers": 1}
    payload = {"section": "align", "simulated": True, "config": cfg, "provenance": provenance(cfg), "cases": cases}
    _write("align", out, payload)
    return payload


# ============================================================================ aligner worker scaling
_WA: dict = {}


def _wa_init(layout: Layout) -> None:
    _WA["al"] = TemplateAligner(layout, BAND)


def _wa_task(chunk: list[np.ndarray]) -> str:
    return _projection_sha(_WA["al"].project(chunk))


def section_align_workers(out: Path) -> dict:
    lay = LAYOUTS["default-313nt"]
    chan = ERROR_POINTS["mixed-L2"]
    reads = []
    for s in range(8):          # 8 × 4096 reads
        r, _ = make_reads(lay, chan, seed=ALIGN_SEED + s)
        reads.extend(r)
    chunks = [reads[i:i + 2048] for i in range(0, len(reads), 2048)]
    rows, ref = [], None
    for w in (1, 2, 4, 8):
        best = None
        for _ in range(2):
            t = time.perf_counter()
            if w == 1:
                _wa_init(lay)
                shas = [_wa_task(c) for c in chunks]
            else:
                with ProcessPoolExecutor(w, initializer=_wa_init, initargs=(lay,)) as pool:
                    shas = list(pool.map(_wa_task, chunks))
            wall = time.perf_counter() - t
            best = wall if best is None else min(best, wall)
        sha = hashlib.sha256("".join(shas).encode()).hexdigest()
        ref = ref or sha
        rows.append({"workers": w, "seconds": round(best, 3), "reads_per_second": round(len(reads) / best, 1),
                     "speedup": round(rows[0]["seconds"] / best, 2) if rows else 1.0, "result_sha256": sha,
                     "identical_to_1_worker": sha == ref})
        print(f"  align workers={w}: {rows[-1]['reads_per_second']:.0f} reads/s ×{rows[-1]['speedup']}")
    cfg = {"layout": lay.to_dict(), "channel": chan, "reads": len(reads), "chunk": 2048, "band": BAND, "pool_startup_included": True}
    payload = {"section": "align-workers", "simulated": True, "config": cfg, "provenance": provenance(cfg), "rows": rows}
    _write("align_workers", out, payload)
    return payload


# ============================================================================ end to end
E2E_SIZE = 4 << 20


def section_e2e(out: Path) -> dict:
    runs = []
    plan = [("clean-cov1", None, 1)] + [(f"noisy-exp0011-w{w}", NOISY_CHANNEL, w) for w in (1, 2, 4, 8)]
    for name, chan, w in plan:
        r = bench.isolated("end_to_end", input_size=E2E_SIZE, pattern="random", seed=42, workers=w, channel=chan)
        r["run"] = name
        runs.append(r)
        print(f"  e2e {name:18s} {r.get('status')} decode {r.get('decode_seconds')} s  rss {r.get('peak_rss_mb')} MB")
    cfg = {"input_size": E2E_SIZE, "pattern": "random", "seed": 42, "profile": "v4-balanced", "noisy_channel": NOISY_CHANNEL}
    payload = {"section": "e2e", "simulated": True, "config": cfg, "provenance": provenance(cfg), "runs": runs}
    _write("e2e", out, payload)
    return payload


# ============================================================================ decode profile
STAGE_OF = [  # (substring of "file:function", stage) — first match wins
    ("sync.py:_traceback", "marker alignment: traceback"),
    ("sync.py:_mark", "marker alignment: traceback"),
    ("sync.py:_align", "marker alignment: DP fill"),
    ("sync.py", "marker alignment: other"),
    ("rs_fast.py", "inner RS decode"),
    ("codecs.py", "inner/outer codec"),
    ("frame.py", "frame parse / descramble"),
    ("reads.py", "read parsing"),
    ("decoder.py:_orientation", "orientation"),
    ("decoder.py:_consensus", "consensus"),
    ("decoder.py:consensus", "consensus"),
    ("decoder.py:snap", "address snapping"),
    ("decoder.py:resolve_duplicates", "duplicate resolution"),
    ("decoder.py:_process", "pass-1 glue"),
    ("decoder.py:_try", "pass-1 glue"),
    ("decoder.py", "decoder other"),
    ("container.py", "container verify"),
    ("merkle.py", "container verify"),
    ("crc", "CRC"),
    ("numpy", "numpy internals"),
]


def _stage(key: str) -> str:
    for needle, stage in STAGE_OF:
        if needle in key:
            return stage
    return "other"


def section_profile(out: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="vnx5-p1-") as tmp:
        tmp = Path(tmp)
        src = tmp / "input.bin"
        in_sha = datagen.generate(src, E2E_SIZE, "random", 42)
        ar.build_archive([src], tmp / "a.vnx", ar.ArchiveOptions(workers=1))
        en.encode_container(tmp / "a.vnx", tmp / "s.fasta", en.DNAOptions(workers=1))
        cfg_ch = ch.ChannelConfig.from_dict(NOISY_CHANNEL)
        chan = ch.simulate_file(tmp / "s.fasta", tmp / "r.fastq", cfg_ch, workers=1)
        prof = cProfile.Profile()
        t = time.perf_counter()
        prof.enable()
        res = de.decode_reads(tmp / "r.fastq", tmp / "r.vnx", de.DecodeOptions(workers=1))
        prof.disable()
        wall = time.perf_counter() - t
        st = pstats.Stats(prof)
        by_stage: dict[str, float] = {}
        rows = []
        for (fn, line, name), (cc, nc, tt, ct, _) in st.stats.items():
            key = f"{Path(fn).name}:{name}" if "vnxdna" in fn else f"{fn}:{name}"
            by_stage[_stage(key)] = by_stage.get(_stage(key), 0.0) + tt
            rows.append({"function": key, "line": line, "calls": nc, "tottime": round(tt, 4), "cumtime": round(ct, 4)})
        rows.sort(key=lambda r: -r["tottime"])
        cum = sorted(rows, key=lambda r: -r["cumtime"])
        s = io.StringIO()
        pstats.Stats(prof, stream=s).sort_stats("tottime").print_stats(25)
        total_tt = sum(by_stage.values())
        stages = sorted(({"stage": k, "seconds": round(v, 3), "fraction": round(v / total_tt, 4)} for k, v in by_stage.items()),
                        key=lambda r: -r["seconds"])
        print(f"  profile: decode {wall:.1f} s (profiled), status {res.status}")
        for r in stages[:8]:
            print(f"    {r['stage']:32s} {r['seconds']:7.2f} s  {r['fraction']:.1%}")
        cfg = {"input_size": E2E_SIZE, "pattern": "random", "seed": 42, "profile": "v4-balanced", "workers": 1,
               "channel": cfg_ch.to_dict()}
        payload = {"section": "profile", "simulated": True, "config": cfg, "provenance": provenance(cfg),
                   "input_sha256": in_sha, "channel_stats": {k: v for k, v in chan.items() if isinstance(v, int)},
                   "status": res.status, "container_sha256": res.report.get("container_sha256"),
                   "decode_reads": res.report.get("reads"), "decoder_stage_seconds": res.report.get("stage_seconds"),
                   "profiled_wall_seconds": round(wall, 3), "profiled_function_seconds": round(total_tt, 3),
                   "by_stage": stages, "top_tottime": rows[:30], "top_cumtime": cum[:30], "pstats_text": s.getvalue()}
    _write("profile", out, payload)
    return payload


# ============================================================================ nucleotide overhead
def overhead_row(name: str, lay: Layout, K: int, M: int) -> dict:
    """Nucleotides per payload byte, split by cause, for one full outer group (superblock and container framing excluded)."""
    nt_payload = 4 * lay.payload_bytes
    parts = {"payload": nt_payload, "address_header": 40, "crc": 16, "inner_rs": 4 * lay.inner_parity,
             "markers": lay.markers * lay.marker_len}
    per_strand = sum(parts.values())
    assert per_strand == lay.strand_nt
    outer = (K + M) / K
    nt_per_byte = per_strand * outer / lay.payload_bytes
    row = {"profile": name, "layout": lay.to_dict(), "outer_K": K, "outer_M": M, "strand_nt": lay.strand_nt,
           "nt_per_payload_byte": round(nt_per_byte, 4), "payload_bits_per_nt": round(8 / nt_per_byte, 4),
           "raw_bits_per_nt": 2.0}
    # each component's share of the nucleotides spent per payload byte (outer parity attributed separately)
    row["nt_per_byte_by_component"] = {k: round(v / lay.payload_bytes, 4) for k, v in parts.items()}
    row["nt_per_byte_by_component"]["outer_parity"] = round(per_strand * (outer - 1) / lay.payload_bytes, 4)
    row["share_of_total"] = {k: round(v / nt_per_byte, 4) for k, v in row["nt_per_byte_by_component"].items()}
    row["constraint_overhead_nt"] = 0   # V4 screening uses the in-frame variant byte (counted in address_header)
    return row


def section_overhead(out: Path) -> dict:
    rows = [overhead_row(name, lay, K, M) for name, (lay, K, M) in PROFILES.items()]
    # cross-check against a measured encode of 4 MiB (includes container framing and the superblock strands)
    with tempfile.TemporaryDirectory(prefix="vnx5-p1-") as tmp:
        tmp = Path(tmp)
        src = tmp / "input.bin"
        in_sha = datagen.generate(src, E2E_SIZE, "random", 42)
        a = ar.build_archive([src], tmp / "a.vnx", ar.ArchiveOptions(workers=1))
        d = en.encode_container(tmp / "a.vnx", tmp / "s.fasta", en.DNAOptions(workers=1))
        measured = {"input_sha256": in_sha, "input_bytes": E2E_SIZE, "container_bytes": a.container_bytes, "strands": d["strands"],
                    "bases": d["bases"], "nt_per_input_byte": round(d["bases"] / E2E_SIZE, 4), "strands_sha256": d["file_sha256"]}
    for r in rows:
        print(f"  {r['profile']:12s} {r['nt_per_payload_byte']:.3f} nt/byte  {r['payload_bits_per_nt']:.3f} bits/nt")
    cfg = {"profiles": list(PROFILES), "measured_input": {"size": E2E_SIZE, "pattern": "random", "seed": 42}}
    payload = {"section": "overhead", "simulated": True, "config": cfg, "provenance": provenance(cfg), "profiles": rows,
               "measured_v4_balanced": measured}
    _write("overhead", out, payload)
    return payload


SECTIONS = {"align": section_align, "align-workers": section_align_workers, "e2e": section_e2e, "profile": section_profile,
            "overhead": section_overhead}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("section", choices=[*SECTIONS, "all"])
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    os.environ.setdefault("PYTHONHASHSEED", "0")
    for name in (SECTIONS if a.section == "all" else [a.section]):
        print(f"== {name}")
        SECTIONS[name](a.out)


if __name__ == "__main__":
    main()
