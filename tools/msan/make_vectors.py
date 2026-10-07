"""Write the golden vector files replayed by the MemorySanitizer builds of the native kernels (tools/msan.sh).

The replay binaries (tools/msan/replay_*.c) are plain C programs that #include one kernel: no CPython or NumPy is
instrumented or linked. This script produces their inputs and expected outputs with the Python reference, which is the
specification (SIMULATED data only):

  align.bin  The Phase 1 golden read sets (benchmarks/v5/baseline-v4/align.json: the reference projection SHA-256 of
             every set is re-checked here) and randomized rounds drawn like benchmarks/v5/native_alignment/stress_fuzz.py
             (all layouts, bands, costs, qualities, edits, reverse complements, N and out-of-alphabet bytes). Kernel
             inputs are built exactly as vnxdna.native.align.align_usable builds them; expected arrays come from the
             NumPy reference TemplateAligner._align, readpos from the uninstrumented native library.
  rs.bin     The committed golden vectors (tests/v6/native/native_rs_golden.json) and randomized batches drawn like
             benchmarks/v6/native_rs/stress_fuzz.py; expected outputs from vnxdna.v4.rs_fast.decode_batch.
  reads.bin  Seeded files from tests/v6/native/native_reads_support.fuzz_case (FASTQ / FASTA / plain, odd symbols, CRLF,
             long lines, mutations, small blocks and record caps) plus clean simulated FASTQ; expected records from the
             reference parser vnxdna.v4.reads (or "error" where the reference raises).

usage: python tools/msan/make_vectors.py OUTDIR [--align-rounds N] [--rs-rounds N] [--reads-cases N] [--seed S]
(run from the repository root with PYTHONPATH=src)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "benchmarks" / "v5"),
                str(ROOT / "benchmarks" / "v5" / "native_alignment"), str(ROOT / "benchmarks" / "v6" / "native_rs")]

TYPES = {np.dtype(np.uint8): 1, np.dtype(np.bool_): 1, np.dtype(np.int16): 2, np.dtype(np.int32): 3,
         np.dtype(np.int64): 4}


class Writer:
    def __init__(self, path: Path):
        self.f = open(path, "wb")
        self.f.write(struct.pack("<q", 0))
        self.records = 0
        self.sha = hashlib.sha256()

    def array(self, a: np.ndarray, dtype) -> None:
        a = np.ascontiguousarray(a, dtype=dtype)
        blob = struct.pack("<iq", TYPES[a.dtype], a.size) + a.tobytes()
        self.f.write(blob)
        self.sha.update(blob)

    def end_record(self) -> None:
        self.records += 1

    def close(self) -> dict:
        self.f.seek(0)
        self.f.write(struct.pack("<q", self.records))
        self.f.close()
        return {"records": self.records, "sha256": self.sha.hexdigest()}


# ============================================================================ align
def _align_record(w: Writer, aligner, reads: list, quals: list | None, minq: int) -> int:
    from vnxdna.native import align as na

    T, band = aligner.T, aligner.band
    keep = [i for i, r in enumerate(reads) if abs(r.size - T) <= band]      # what TemplateAligner.project passes
    reads = [reads[i] for i in keep]
    quals = None if quals is None else [quals[i] for i in keep]
    n = len(reads)
    lay, g, c = aligner.layout, na.geometry(aligner), aligner.costs
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    offsets = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(lengths, out=offsets[1:])
    total = int(offsets[-1])
    codes = np.concatenate(reads) if n else np.zeros(0, dtype=np.uint8)
    qflat = has_q = None
    if quals is not None:
        has_q = np.fromiter((q is not None for q in quals), dtype=np.uint8, count=n)
        qflat = np.zeros(total, dtype=np.uint8)
        for k in np.flatnonzero(has_q).tolist():
            qflat[offsets[k]:offsets[k + 1]] = quals[k]
    if n:
        ref = aligner._align(reads, lengths, quals, minq)
    else:
        ref = (np.zeros((0, lay.frame_nt), np.uint8), np.zeros((0, lay.frame_nt), bool), *(np.zeros(0, t) for t in
               (bool, np.int64, np.int64, np.int64, np.int64)))
    nat = na.align_usable(aligner, reads, quals, minq, readpos=True)
    assert nat is not None, "inputs outside the native domain"
    for x, y in zip(ref, nat[:7]):                                        # the uninstrumented library agrees too
        assert np.array_equal(x, y), "uninstrumented native library differs from the reference"
    flags = (1 if total and qflat is not None else 0) | (2 if has_q is not None else 0) | (4 if total else 0)
    w.array(np.array([n, total, T, aligner.n_segments, lay.frame_nt, band, c.marker_mismatch, c.insertion, c.deletion,
                      c.marker_deletion_extra, c.guard_segments, minq, flags]), np.int64)
    w.array(codes, np.uint8)
    w.array(offsets, np.int64)
    w.array(qflat if qflat is not None else np.zeros(0), np.uint8)
    w.array(has_q if has_q is not None else np.zeros(0), np.uint8)
    for key, dt in (("tpl", np.int16), ("seg_of", np.int32), ("prev_seg", np.int32), ("next_seg", np.int32),
                    ("frame_pos", np.int32), ("seg_frame", np.int32)):
        w.array(g[key], dt)
    for a, dt in zip(ref, (np.uint8, np.uint8, np.uint8, np.int64, np.int64, np.int64, np.int64)):
        w.array(a, dt)
    w.array(nat[7], np.int16)
    w.end_record()
    return n


def make_align(path: Path, rounds: int, seed: int) -> dict:
    import phase1_baseline as p1
    from stress_fuzz import reads_for
    from v5.native_support import LAYOUTS

    from vnxdna.v4.sync import SyncCosts, TemplateAligner

    w = Writer(path)
    golden = json.loads((ROOT / "benchmarks/v5/baseline-v4/align.json").read_text())["cases"]
    n_reads = 0
    for case in golden:
        layout = p1.LAYOUTS[case["layout_name"]]
        reads, meta = p1.make_reads(layout, p1.ERROR_POINTS[case["error_point"]])
        assert meta["reads_sha256"] == case["reads_sha256"], "Phase 1 read generation drifted"
        ref = TemplateAligner(layout, case["band"], backend="reference")
        assert p1._projection_sha(ref.project(reads)) == case["projection_sha256"], "reference no longer golden"
        n_reads += _align_record(w, TemplateAligner(layout, case["band"], backend="native"), reads, None, 0)
    rng = np.random.default_rng(seed)
    names = sorted(LAYOUTS)
    for _ in range(rounds):
        layout = LAYOUTS[names[int(rng.integers(0, len(names)))]]
        band = int(rng.choice([0, 1, 2, 3, 5, 6, 8, 12, 16, 32, 64]))
        costs = SyncCosts(*(int(v) for v in rng.choice([0, 1, 2, 4, 6, 7, 100, 65536], 4)), int(rng.integers(0, 4)))
        reads = reads_for(layout, rng, 120)
        quals = None
        if rng.random() < 0.5:
            quals = [rng.integers(0, 60, r.size).astype(np.uint8) if rng.random() < 0.9 else None for r in reads]
        minq = int(rng.integers(-5, 60))
        n_reads += _align_record(w, TemplateAligner(layout, band, costs, backend="native"), reads, quals, minq)
    return {**w.close(), "golden_sets": len(golden), "random_rounds": rounds, "reads": n_reads}


# ============================================================================ rs
def _rs_record(w: Writer, cw: np.ndarray, nsym: int, er: np.ndarray | None, expected=None) -> None:
    from vnxdna.v4 import rs_fast

    out, ok, errata = rs_fast.decode_batch(cw, nsym, er) if expected is None else expected
    words, n = cw.shape
    w.array(np.array([words, n, nsym, 0 if er is None else 1]), np.int64)
    w.array(cw, np.uint8)
    w.array(er if er is not None else np.zeros(0), np.uint8)
    w.array(out, np.uint8)
    w.array(ok, np.uint8)
    w.array(errata, np.int64)
    w.end_record()


def make_rs(path: Path, rounds: int, seed: int) -> dict:
    from stress_fuzz import make_batch, make_code  # benchmarks/v6/native_rs/stress_fuzz.py (path order: see run())

    w = Writer(path)
    golden = json.loads((ROOT / "tests/v6/native/native_rs_golden.json").read_text())["cases"]
    for c in golden:
        cw = np.frombuffer(bytes.fromhex(c["received"]), np.uint8).reshape(1, -1)
        er = np.zeros_like(cw, dtype=bool)
        er[0, c["erasures"]] = True
        exp = (np.frombuffer(bytes.fromhex(c["corrected"]), np.uint8).reshape(1, -1), np.array([c["ok"]]),
               np.array([c["errata"]], np.int64))
        _rs_record(w, cw, c["nsym"], er if c["erasures"] else None, exp)
    rng = np.random.default_rng(seed)
    words = len(golden)
    for _ in range(rounds):
        n, nsym = make_code(rng)
        cw, er = make_batch(rng, n, nsym)
        _rs_record(w, cw, nsym, er)
        words += cw.shape[0]
    return {**w.close(), "golden_vectors": len(golden), "random_rounds": rounds, "words": words}


# ============================================================================ reads
def _reads_record(w: Writer, path: Path, data: bytes, block: int, max_nt: int, chunk: int) -> bool | None:
    from v6.native.native_reads_support import limits

    from vnxdna.native import reads as nreads
    from vnxdna.v4 import reads as ref

    path.write_bytes(data)
    with limits(block, max_nt):
        try:
            fmt = ref.detect(path)
        except Exception:  # noqa: BLE001 - undetectable input never reaches the kernel
            return None
        if fmt == "vxs":
            return None
        batches, err = [], False
        try:
            batches = list(ref.iter_reads(path, 1 << 40))
        except Exception:  # noqa: BLE001 - any reference exception is an expected native error code
            err = True
        mx, blk = ref.MAX_READ_NT, ref.BLOCK
    cat = (lambda xs, dt: np.concatenate(xs).astype(dt) if xs else np.zeros(0, dt))
    w.array(np.array([nreads.FORMATS[fmt], mx, mx * 2 + 4096, blk, chunk, int(err)]), np.int64)
    w.array(np.frombuffer(data, np.uint8), np.uint8)
    w.array(cat([b.codes for b in batches], np.uint8), np.uint8)
    w.array(cat([b.quals for b in batches if b.quals is not None], np.uint8), np.uint8)
    w.array(cat([b.lengths for b in batches], np.int64), np.int64)
    w.array(cat([b.invalid for b in batches], np.uint8), np.uint8)
    w.end_record()
    return err


def make_reads(path: Path, cases: int, seed: int) -> dict:
    from v6.native.native_reads_support import fuzz_case, gen_fastq

    w = Writer(path)
    errors = skipped = 0
    rng = np.random.default_rng(seed)
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "case"
        for i in range(cases):
            data, opts = fuzz_case(seed + i)
            chunk = int(rng.choice([1, 2, 3, 7, 61, 1000, 4096, 1 << 20]))
            r = _reads_record(w, f, data, opts["block"], opts["max_nt"], chunk)
            skipped += r is None
            errors += bool(r)
        for k in range(4):                                         # clean simulated FASTQ, production block size
            data = gen_fastq(np.random.default_rng(seed + 10_000 + k), 3000, 300)
            _reads_record(w, f, data, 8 << 20, 100_000, [1 << 16, 8 << 20, 4093, 997][k])
    return {**w.close(), "fuzz_cases": cases, "reference_errors": errors, "not_parsed_by_kernel": skipped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--align-rounds", type=int, default=200)
    ap.add_argument("--rs-rounds", type=int, default=1000)
    ap.add_argument("--reads-cases", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--only", choices=("align", "rs", "reads"))
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    # the align and rs stress scripts share the module name stress_fuzz: import each with its own directory first
    if a.only in (None, "align"):
        sys.path.insert(0, str(ROOT / "benchmarks" / "v5" / "native_alignment"))
        sys.modules.pop("stress_fuzz", None)
        summary["align"] = make_align(out / "align.bin", a.align_rounds, a.seed)
    if a.only in (None, "rs"):
        sys.path.insert(0, str(ROOT / "benchmarks" / "v6" / "native_rs"))
        sys.modules.pop("stress_fuzz", None)
        summary["rs"] = make_rs(out / "rs.bin", a.rs_rounds, a.seed)
    if a.only in (None, "reads"):
        summary["reads"] = make_reads(out / "reads.bin", a.reads_cases, a.seed)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
