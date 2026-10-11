"""V10 candidate-pair memory at 10 MB (SIMULATED reads; measurement of the clustering candidate step only).

``capture`` rebuilds W3 case <seed> exactly as ``experiments/v10/w3/run.py`` did (the reads hash must equal the
recorded row's), decodes it and, at the point the decoder calls the candidate step, saves that step's inputs (the
unplaced reads' sketch hashes and orientation bits) to an .npz outside the repository; the decode then stops there.
``measure`` runs the candidate step of the tree it is run from on those inputs, without a work budget, in a fresh
process, and appends one row to ``results/pairs.jsonl``: slot pairs, kept pairs, seconds, peak RSS (getrusage of this
process, and the RSS sampled before the call, i.e. the inputs), the commit, ``load1``.

    PYTHONPATH=src python experiments/v10/mem/pairs_10m.py capture 93100 --npz /path/c93100.npz
    PYTHONPATH=src python experiments/v10/mem/pairs_10m.py measure --npz /path/c93100.npz --label current
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / "results" / "pairs.jsonl"
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"


class _Captured(Exception):
    pass


def capture(seed: int, npz: Path) -> dict:
    import dataclasses
    import tempfile

    import numpy as np
    sys.path.insert(0, str(ROOT / "experiments/v10/w3"))
    import run as R
    import nanofunnel as nf
    from candidates import cluster_config

    from vnxdna.recovery.cluster import graph
    from vnxdna.simulation import engine
    rec = next(json.loads(x) for x in R.OUT.read_text().splitlines() if json.loads(x)["seed"] == seed)
    os.environ.pop("VNXDNA_CLUSTER_THREADS", None)
    MX = R.W1._load("v8_matrix_run", "experiments/v8/matrix/run.py")
    case = {"profile": R.W1.PROFILE, "decoder": {"read_clustering": "fallback",
                                                 "cluster_config": cluster_config(R.W1.CANDIDATE)}}
    opts = dataclasses.replace(nf.decoder_options(case), workers=R.W1.WORKERS, stage_counters=True)
    seen = {}

    def grab(hashes, orient, bucket_cap, max_pairs, counts, min_shared=1, chunk_pairs=None):  # graph.candidate_pairs
        np.savez(npz, hashes=hashes, orient=orient, bucket_cap=bucket_cap, max_pairs=max_pairs, min_shared=min_shared)
        seen.update(n=int(hashes.shape[0]), s=int(hashes.shape[1]), bucket_cap=int(bucket_cap),
                    max_pairs=int(max_pairs), min_shared=int(min_shared))
        raise _Captured
    graph.candidate_pairs = grab
    with tempfile.TemporaryDirectory(prefix="vnx-v10mem-", dir=R.W1._TMP) as tmp:
        w = Path(tmp)
        arc = R.build(w / "a", rec["size"], seed)
        engine.simulate_file_with_truth(arc["strands_path"], w / "r.fastq", MX.case_model(R.W1.CHANNEL, R.W1.COVERAGE),
                                        seed)
        sha = hashlib.sha256((w / "r.fastq").read_bytes()).hexdigest()
        if sha != rec["reads_sha256"]:
            raise SystemExit(f"reads differ from the recorded case: {sha} != {rec['reads_sha256']}")
        # decode_reads directly: the harness's decode_claim would catch _Captured and report a failed decode
        from vnxdna.pipeline.decode import decode_reads
        try:
            decode_reads(w / "r.fastq", w / "o-mem.vnx", opts)
        except _Captured:
            pass
    if not seen:
        raise SystemExit("the decoder did not reach the candidate step")
    return {"seed": seed, "size": rec["size"], "reads_sha256": sha, **seen}


def _rss_now() -> int:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def _slot_pairs(hashes, bucket_cap: int) -> int:
    """Slot pairs the candidate step enumerates: per sketch slot, sum m(m-1)/2 over buckets of 2..bucket_cap reads
    (the decoder reports this counter only on a budget stop, so the measurement recomputes it from the inputs)."""
    import numpy as np
    total = 0
    for i in range(hashes.shape[1]):
        _, m = np.unique(hashes[:, i], return_counts=True)
        m = m[(m >= 2) & (m <= bucket_cap)].astype(np.int64)
        total += int((m * (m - 1) // 2).sum())
    return total


def measure(npz: Path, label: str, max_pairs: int | None, chunk: int | None) -> dict:
    import numpy as np

    import vnxdna

    from vnxdna.recovery.cluster import ClusterConfig
    from vnxdna.recovery.cluster.graph import candidate_pairs
    from collections import Counter
    d = np.load(npz)
    hashes, orient = d["hashes"], d["orient"]
    cfg = ClusterConfig()
    budget_in = int(d["max_pairs"])
    limit = (1 << 62) if max_pairs is None else max_pairs
    rss_inputs = _rss_now()
    counts: Counter = Counter()
    load = round(os.getloadavg()[0], 2)
    t0 = time.perf_counter()
    extra = () if chunk is None else (chunk,)   # the pre-V10 tree has no chunk argument
    a, b, rel, budget = candidate_pairs(hashes, orient, int(d["bucket_cap"]), limit, counts, int(d["min_shared"]),
                                        *extra)
    sec = round(time.perf_counter() - t0, 2)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024   # before the slot count below allocates
    slots = _slot_pairs(hashes, int(d["bucket_cap"]))
    tree = Path(vnxdna.__file__).resolve().parents[2]   # the tree whose candidate step ran (PYTHONPATH)
    commit = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(tree), "status", "--porcelain", "--", "src"], capture_output=True,
                                text=True).stdout.strip())
    row = {"label": LABEL, "run_label": label, "commit": commit, "src_dirty": dirty, "npz_sha256":
           hashlib.sha256(npz.read_bytes()).hexdigest(), "reads": int(hashes.shape[0]), "sketch_size":
           int(hashes.shape[1]), "bucket_cap": int(d["bucket_cap"]), "min_shared": int(d["min_shared"]),
           "decoder_budget": budget_in, "limit_used": limit, "budget_stop": budget,
           "slot_pairs": slots, "kept_pairs": int(a.size),
           "counters": dict(counts), "seconds": sec, "rss_inputs_bytes": rss_inputs,
           "peak_rss_bytes": peak,
           "pairs_sha256": hashlib.sha256(a.tobytes() + b.tobytes() + rel.tobytes()).hexdigest(),
           "load1_start": load, "load1_end": round(os.getloadavg()[0], 2), "default_max_pairs": cfg.max_candidate_pairs,
           "time": time.time(), "tree": str(tree), "chunk_pairs": chunk}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as f:
        f.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return row


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["capture", "measure"])
    ap.add_argument("seed", nargs="?", type=int, default=93100)
    ap.add_argument("--npz", type=Path, required=True)
    ap.add_argument("--label", default="current")
    ap.add_argument("--max-pairs", type=int)
    ap.add_argument("--chunk", type=int)
    a = ap.parse_args()
    if a.cmd == "capture":
        print(json.dumps(capture(a.seed, a.npz)))
    else:
        r = measure(a.npz, a.label, a.max_pairs, a.chunk)
        print(json.dumps({k: r[k] for k in ("run_label", "reads", "slot_pairs", "kept_pairs", "seconds",
                                            "rss_inputs_bytes", "peak_rss_bytes", "budget_stop", "load1_start")}))
