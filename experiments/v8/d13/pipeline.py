"""V8.1 D13 public-data driver: RAW PUBLIC DATA → PINNED PREPROCESSING → NORMALISED OBSERVATIONS → MODEL-FIT TABLES.

    PYTHONPATH=src python experiments/v8/d13/pipeline.py segment run15 [--workers 4]   # stages 1-3 for one run
    PYTHONPATH=src python experiments/v8/d13/pipeline.py tables [--workers 4]          # stage 4, all runs, FIT and DEV

Stage 1 (RAW): the run's FASTQ and its reference file are SHA-256 checked against experiments/v7/datasets/MANIFEST.json.
Stage 2 (PREPROCESSING): the V7 D3 subsample (first 100,000 read IDs in SHA-256('VNX-D3-SUBSAMPLE/' + ID) order) is split
into reference segments by ``nanolib.split_read`` with the frozen PR-4.2 constants; segments are oriented to the reference
strand; ambiguous ones are discarded. A segment belongs to the split of its reference's canonical bucket (FIT 0-5, DEV 6-7);
segments of held-out-bucket references are counted and discarded at once.
Stage 3 (NORMALISED OBSERVATIONS): FIT and DEV segments, grouped by reference, are written to a local cache
(``DERIVED/<run>.<split>.tsv.gz``: reference index, segment, quality), never committed (licence: internal use only);
its SHA-256 is recorded. Read-level aggregates (length, mean quality, segments per read) are split by read-ID bucket.
Stage 4 (MODEL-FIT TABLES): tallies (``simulation.fit.tally``; Layout L = 150, min_runs 2..6, max_run 32, qualities,
per-read edit rate) of the cached segments, saved locally as ``DERIVED/tables.<split>.npz`` with their SHA-256.

Every stage writes ``experiments/v8/d13/results/pipeline-<run>.json`` / ``tables.json``: inputs and outputs with hashes,
parameters, counts, code commit, environment. Only aggregate counts are committed. The output does not depend on the
worker count (reads are processed independently; results are merged in input order).
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "experiments" / "v7" / "nanodata"))
sys.path.insert(0, str(HERE))
import access as A                                       # noqa: E402
import nanolib as nl                                     # noqa: E402

DATA = Path(os.environ.get("VNX_PUBLIC_DATA", "/root/vnx-dna-lab/data/public"))
DERIVED = Path(os.environ.get("VNX_V8_DERIVED", "/root/vnx-dna-lab/data/derived/v8/d13"))
RESULTS = HERE / "results"
MANIFEST = REPO / "experiments" / "v7" / "datasets" / "MANIFEST.json"
MAX_READS = 100_000
PARAMS = nl.SplitParams()          # frozen PR-4.2 constants
L = 150
MAX_RUN = 32
SPLITS = (A.FIT, A.DEV)
_G: dict = {}


def sha256_path(p: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while b := fh.read(chunk):
            h.update(b)
    return h.hexdigest()


def manifest_entry(rel: str) -> dict:
    d = json.loads(MANIFEST.read_text())["datasets"]["d13-lopez-nanopore"]
    return next(f for f in d["files"] if f["path"] == rel)


def git_state() -> dict:
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True, check=False).stdout.strip()  # noqa: E731
    return {"commit": run("rev-parse", "HEAD"), "dirty_tracked": bool(run("status", "--porcelain", "--untracked-files=no"))}


def environment() -> dict:
    import platform
    return {"python": sys.version.split()[0], "numpy": np.__version__, "platform": platform.platform(),
            "cpus": os.cpu_count(), "load_average": list(os.getloadavg())}


def verified(rel: str) -> dict:
    """Stage 1: SHA-256 of a raw file against the manifest; raises if it differs."""
    p = DATA / rel
    want = manifest_entry(rel)["sha256"]
    got = sha256_path(p)
    if got != want:
        raise SystemExit(f"{rel}: SHA-256 {got} differs from the manifest {want}")
    return {"path": rel, "sha256": got, "bytes": p.stat().st_size}


def subsample_ids(path: Path, max_reads: int) -> set | None:
    """V7 D3 PR-9: the first ``max_reads`` read IDs in SHA-256('VNX-D3-SUBSAMPLE/' + ID) order (None: all reads)."""
    ids = [rid for rid, _, _ in nl.read_fastq(path)]
    if len(ids) <= max_reads:
        return None
    ids.sort(key=lambda r: hashlib.sha256(b"VNX-D3-SUBSAMPLE/" + r).digest())
    return set(ids[:max_reads])


def _init(refs, buckets):
    _G["refs"], _G["buckets"], _G["index"] = refs, buckets, nl.build_index(refs, PARAMS)


def _batch(batch):
    """Segments of FIT/DEV references and read-level aggregates of one batch of reads (pure function of the batch)."""
    segs: dict = {s: [] for s in SPLITS}
    c: Counter = Counter()
    reads = {s: {"n": 0, "lengths": Counter(), "mean_q": Counter(), "segments": Counter()} for s in SPLITS}
    for rid, seq, qual in batch:
        res = nl.split_read(seq, qual, _G["index"], PARAMS)
        c["reads"] += 1
        c["ambiguous"] += res.ambiguous
        c["rejected_error"] += res.rejected_error
        for g in res.segments:
            split = nl.bucket_split(int(_G["buckets"][g.ref]))
            if split == nl.HELDOUT:
                c["segments_heldout_reference_discarded"] += 1
                continue
            c[f"segments_{split}"] += 1
            segs[split].append((g.ref, g.seq, g.qual))
        rs = nl.bucket_split(nl.id_bucket(rid))
        if rs == nl.HELDOUT:
            c["reads_heldout_id_bucket_readlevel_skipped"] += 1
            continue
        r = reads[rs]
        r["n"] += 1
        r["lengths"][min(len(seq) // 100, 200)] += 1
        r["mean_q"][int(nl.mean_phred(qual))] += 1
        r["segments"][min(len(res.segments), 80)] += 1
    return segs, c, reads


def _batches(path: Path, size: int, keep: set | None):
    buf = []
    for rec in nl.read_fastq(path):
        if keep is not None and rec[0] not in keep:
            continue
        buf.append(rec)
        if len(buf) == size:
            yield buf
            buf = []
    if buf:
        yield buf


def cache_path(run: str, split: str) -> Path:
    return DERIVED / f"{run}.{split}.tsv.gz"


def write_cache(p: Path, by_ref: dict) -> str:
    """Segments grouped by reference index (ascending), each line 'ref<TAB>segment<TAB>quality'; deterministic gzip."""
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as fh:
        for ref in sorted(by_ref):
            for seq, qual in by_ref[ref]:
                fh.write(b"%d\t%s\t%s\n" % (ref, seq, qual))
    return sha256_path(p)


def read_cache(p: Path):
    """(reference index, [segments], [qualities]) per reference, in the cached order."""
    cur, seqs, quals = None, [], []
    with gzip.open(p, "rb") as fh:
        for line in fh:
            r, s, q = line.rstrip(b"\n").split(b"\t")
            ref = int(r)
            if ref != cur and cur is not None:
                yield cur, seqs, quals
                seqs, quals = [], []
            cur = ref
            seqs.append(s)
            quals.append(q)
    if cur is not None:
        yield cur, seqs, quals


def do_segment(run: str, workers: int) -> Path:
    guard = A.V8Guard(f"experiments/v8/d13/pipeline.py segment {run}")
    for s in SPLITS:
        guard.authorize(run, s, "V8.1 stage 2-3: segment FIT/DEV reads, cache normalised observations")
    file = A.RUNS[run]
    t0 = time.time()
    raw = [verified(f"d13/nanopore_{run}.fastq.gz"), verified(A.REFERENCE_FILES[file])]
    fastq = DATA / raw[0]["path"]
    refs = nl.parse_d13_refs(DATA / raw[1]["path"])
    buckets = np.array([nl.bucket(r) for r in refs], dtype=np.int8)
    keep = subsample_ids(fastq, MAX_READS)
    by_ref: dict = {s: {} for s in SPLITS}
    counts: Counter = Counter()
    reads = {s: {"n": 0, "lengths": Counter(), "mean_q": Counter(), "segments": Counter()} for s in SPLITS}
    with mp.get_context("fork").Pool(workers, _init, (refs, buckets)) as pool:
        for segs, c, rd in pool.imap(_batch, _batches(fastq, 200, keep), chunksize=1):
            counts.update(c)
            for s in SPLITS:
                for ref, seq, qual in segs[s]:
                    by_ref[s].setdefault(ref, []).append((seq, qual))
                reads[s]["n"] += rd[s]["n"]
                for k in ("lengths", "mean_q", "segments"):
                    reads[s][k].update(rd[s][k])
    outputs = {}
    for s in SPLITS:
        p = cache_path(run, s)
        outputs[s] = {"path": str(p), "sha256": write_cache(p, by_ref[s]), "segments": sum(len(v) for v in by_ref[s].values()),
                      "references_with_segment": len(by_ref[s]),
                      "references_in_split": int(sum(1 for b in buckets if nl.bucket_split(int(b)) == s))}
    rec = {"stage": "V8.1 stages 1-3 (raw → segments → normalised observations)", "run": run, "file": file,
           "evidence_class": "PUBLIC-DATA-DERIVED (aggregate counts only)", "inputs": raw,
           "subsample": {"rule": "first 100,000 read IDs in SHA-256('VNX-D3-SUBSAMPLE/' + ID) order (V7 D3 PR-9)",
                         "reads_selected": len(keep) if keep is not None else "all"},
           "split_params": PARAMS.__dict__, "splits": "reference canonical bucket: FIT 0-5, DEV 6-7, HELD-OUT 8-9 (discarded)",
           "counts": dict(counts), "outputs": outputs,
           "read_level": {s: {"reads": reads[s]["n"],
                              "length_hist_100nt_bins": [reads[s]["lengths"].get(i, 0) for i in range(201)],
                              "mean_phred_hist": [reads[s]["mean_q"].get(i, 0) for i in range(60)],
                              "segments_per_read_hist": [reads[s]["segments"].get(i, 0) for i in range(81)]} for s in SPLITS},
           "code": git_state(), "environment": environment(), "workers": workers, "seconds": round(time.time() - t0, 1),
           "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"pipeline-{run}.json"
    out.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"run": run, "counts": dict(counts), "seconds": rec["seconds"]}))
    return out


def layout():
    from vnxdna.simulation.fit.tally import Layout
    return Layout(L, minruns=(2, 3, 4, 5, 6), max_run=MAX_RUN, quality=True, cycles=L, read_rate=True)


def run_refs(run: str) -> list[bytes]:
    return nl.parse_d13_refs(DATA / A.REFERENCE_FILES[A.RUNS[run]])


def split_pairs(run: str, split: str, refs: list[bytes]):
    """(reference, segments, qualities) of one run's cached split, for the tally pipeline."""
    rec = json.loads((RESULTS / f"pipeline-{run}.json").read_text())
    p = cache_path(run, split)
    if sha256_path(p) != rec["outputs"][split]["sha256"]:
        raise SystemExit(f"{p}: cache SHA-256 differs from pipeline-{run}.json (altered?)")
    for ref, seqs, quals in read_cache(p):
        yield refs[ref], seqs, quals


def do_tables(workers: int) -> Path:
    from vnxdna.simulation.fit import pipeline as P
    guard = A.V8Guard("experiments/v8/d13/pipeline.py tables")
    lay = layout()
    t0 = time.time()
    rec: dict = {"stage": "V8.1 stage 4 (normalised observations → model-fit tables)", "layout": {
        "L": lay.L, "minruns": list(lay.minruns), "max_run": lay.max_run, "quality": lay.quality, "cycles": lay.cycles,
        "read_rate": lay.read_rate, "size": lay.size}, "evidence_class": "PUBLIC-DATA-DERIVED (aggregate counts only)",
        "tally_options": {"mode": "NW", "aligner": "edlib", "shift": "left", "length_window": None}, "splits": {}}
    for s in SPLITS:
        Ms, labels, rows = [], [], {}
        rs_total = None
        for run in A.RUNS:
            guard.authorize(run, s, "V8.1 stage 4: tally cached segments")
            refs = run_refs(run)
            M, rs = P.tally_matrix(split_pairs(run, s, refs), lay, mode="NW", workers=workers)
            Ms.append(M)
            labels += [run] * M.shape[0]
            rs_total = rs if rs_total is None else rs_total + rs
            rows[run] = {"references": int(M.shape[0]), "reads_tallied": int(lay.get(M, "n_reads").sum()),
                         "excluded": int(lay.get(M, "excluded").sum())}
        M = np.concatenate(Ms)
        p = DERIVED / f"tables.{s}.npz"
        np.savez(p, M=M, rs=rs_total, run=np.array(labels))
        rec["splits"][s] = {"path": str(p), "sha256": sha256_path(p), "per_run": rows, "references": int(M.shape[0]),
                            "reads_tallied": int(lay.get(M, "n_reads").sum()),
                            "inputs": {run: json.loads((RESULTS / f"pipeline-{run}.json").read_text())["outputs"][s]["sha256"]
                                       for run in A.RUNS}}
    rec.update({"code": git_state(), "environment": environment(), "workers": workers, "seconds": round(time.time() - t0, 1)})
    out = RESULTS / "tables.json"
    out.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(json.dumps({s: {k: v for k, v in r.items() if k in ("references", "reads_tallied")} for s, r in rec["splits"].items()}))
    return out


def load_tables(split: str):
    """(layout, M, rs, run labels) of a split's fit tables, checked against tables.json."""
    rec = json.loads((RESULTS / "tables.json").read_text())["splits"][split]
    p = Path(rec["path"])
    if sha256_path(p) != rec["sha256"]:
        raise SystemExit(f"{p}: SHA-256 differs from tables.json")
    z = np.load(p)
    return layout(), z["M"], z["rs"], z["run"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["segment", "tables"])
    ap.add_argument("run", nargs="?")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    if a.cmd == "segment":
        if a.run not in A.RUNS:
            ap.error(f"run must be one of {list(A.RUNS)} (run 13 is held out)")
        do_segment(a.run, a.workers)
    else:
        do_tables(a.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
