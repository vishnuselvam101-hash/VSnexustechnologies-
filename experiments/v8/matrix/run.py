"""V8.8 decoder evaluation matrix (SIMULATED; docs/V8_PREREGISTRATION.md §5).

Cells: channel x coverage x profile, seeds 83000-83009. Each case builds a 20,000-byte random archive (data seed = seed),
simulates reads with ``engine.simulate_file_with_truth`` (the model's own stages, coverage replaced by a negative binomial
with the cell's mean and dispersion 4, as the V7 nanopore corpus), decodes with Phase 1 full-template consensus, and records
the strand funnel and the V8.9 taxonomy of every failure.

    PYTHONPATH=src python experiments/v8/matrix/run.py --jobs 4                       # resumable; results/matrix.jsonl
    PYTHONPATH=src python experiments/v8/matrix/run.py --summarise                    # results/summary.json

Every row records model SHA-256 and source dataset, seed, configuration, archive (container) SHA-256, read-file SHA-256 and
the decoded outcome. Rows of the D13 channel carry the D13 model's adequacy verdict; unless it is ADEQUATE that column is a
stress condition, not a realistic-channel claim.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests" / "nanopore"))
sys.path.insert(0, str(HERE))
OUT = HERE / "results" / "matrix.jsonl"
SEEDS = tuple(range(83000, 83010))
COVERAGES = (3, 5, 10)
PROFILES_RUN = ("v4-balanced", "v7-lowcov")
SIZE = 20_000
D13_MODEL = ROOT / "experiments/v8/d13/models/d13-nanopore-f1.json"
CHANNELS = ("clean", "substitution-heavy", "insertion-heavy", "deletion-heavy", "mixed-harsh", "d13-f1")
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"


def load_channel(name: str):
    from vnxdna.simulation import model as cm, registry
    if name == "d13-f1":
        return cm.from_doc(json.loads(D13_MODEL.read_text()))[0]
    return registry.load_model(name)


def d13_verdict() -> str:
    p = ROOT / "experiments/v8/d13/results/VERDICT.json"
    return json.loads(p.read_text())["verdict"] if p.exists() else "UNVALIDATED"


def case_model(name: str, cov: int):
    m = load_channel(name)
    return m.with_parameters({"sequencing.coverage": {"model": "negative-binomial", "mean": float(cov), "dispersion": 4.0}},
                             label=f"V8 matrix coverage {cov}")


def run_one(job: tuple) -> dict:
    import nanofunnel as nf
    import taxonomy as TX
    from vnxdna.dnaenc.layout import PROFILES
    from vnxdna.native.reads import iter_reads
    from vnxdna.simulation import engine
    channel, cov, profile, seed = job
    t0 = time.perf_counter()
    base = load_channel(channel)
    model = case_model(channel, cov)
    case = {"id": f"v8-{channel}-cov{cov}-{profile}-s{seed}", "size": SIZE, "data_seed": seed, "profile": profile,
            "decoder": {"read_clustering": "fallback", "cluster_config": {"consensus_template": "full"}}}
    with tempfile.TemporaryDirectory(prefix="vnx-v8m-") as tmp:
        work = Path(tmp)
        arc = nf.build_archive(work / "archive", SIZE, seed, profile)
        truth = engine.simulate_file_with_truth(arc["strands_path"], work / "reads.fastq", model, seed)
        reads = []
        for b in iter_reads(work / "reads.fastq", 1 << 17):
            o = np.concatenate([[0], np.cumsum(b.lengths)])
            reads += [b.codes[o[k]:o[k + 1]].copy() for k in range(b.count)]
        sim = {"src": truth["source"], "rc": truth["reverse_complement"], "reads": reads, "reads_sha256": truth["reads_sha256"]}
        dec, cap = nf.run_decode(work / "reads.fastq", work / "out.vnx", arc["container_sha256"], nf.decoder_options(case))
        idx = nf.map_stored(cap.reads, sim["reads"]) if cap.reads else np.zeros(0, dtype=np.int64)
        strands, verified, false = nf.strand_funnel(arc, sim, cap, idx)
        cap.src = sim["src"][idx] if idx.size else np.zeros(0, dtype=np.int64)
        doc = {"decode": dec, "strands": strands, "reads": len(reads)}
        doc.update(nf.summarise_strands(arc, strands, verified, false, cap))
        tax = TX.classify(doc, PROFILES[profile][2], set(truth["lost_strands"]), cluster_ran=cap.reads is not None)
    reads_per_strand = np.bincount(truth["source"][truth["source"] >= 0], minlength=len(arc["keys"]))
    return {"channel": channel, "coverage": cov, "profile": profile, "seed": seed, "label": LABEL,
            "d13_verdict": d13_verdict() if channel == "d13-f1" else None,
            "model": {"name": base.doc["name"], "version": base.doc["version"], "sha256": base.sha256,
                      "case_sha256": model.sha256, "evidence_class": base.doc["evidence_class"],
                      "datasets": [d.get("id") for d in base.doc["provenance"].get("datasets", [])]},
            "configuration": case, "container_sha256": arc["container_sha256"], "reads_sha256": truth["reads_sha256"],
            "reads": len(reads), "strands": len(arc["keys"]),
            "strands_below_1_read": int((reads_per_strand < 1).sum()), "strands_below_2_reads": int((reads_per_strand < 2).sum()),
            "outcome": dec["outcome"], "false_success": dec["outcome"] == "FALSE_SUCCESS", "error_code": dec["error_code"],
            "terminal_stage": dec["terminal_stage"], "cluster_stage_ran": cap.reads is not None,
            "frames": doc.get("frames") if cap.reads is not None else None, "taxonomy": tax,
            "decode_seconds": dec["decode_seconds"], "peak_rss_bytes": dec["peak_rss_bytes"],
            "seconds": round(time.perf_counter() - t0, 2)}


def jobs() -> list:
    return [(c, cov, p, s) for c in CHANNELS for cov in COVERAGES for p in PROFILES_RUN for s in SEEDS]


def wilson(k: int, n: int) -> list:
    if n == 0:
        return [0.0, 1.0]
    z = 1.959964
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def summarise() -> dict:
    import taxonomy as TX
    rows = [json.loads(x) for x in OUT.read_text().splitlines()]
    cells: dict = {}
    for r in rows:
        cells.setdefault((r["channel"], r["coverage"], r["profile"]), []).append(r)
    out = {"label": LABEL, "seeds": list(SEEDS), "rows": len(rows), "cells": []}
    total_fail, classified = 0, 0
    for (ch, cov, prof), rs in sorted(cells.items(), key=lambda kv: (CHANNELS.index(kv[0][0]), kv[0][1], kv[0][2])):
        n = len(rs)
        exact = sum(r["outcome"] == "EXACT" for r in rs)
        fails = [r for r in rs if r["outcome"] != "EXACT"]
        total_fail += len(fails)
        classified += sum(r["taxonomy"]["primary"] in TX.CATEGORIES for r in fails)
        prim = Counter(r["taxonomy"]["primary"] for r in fails)
        contrib = Counter(r["taxonomy"]["contributing"] for r in fails if r["taxonomy"]["contributing"])
        out["cells"].append({"channel": ch, "coverage": cov, "profile": prof, "n": n, "exact": exact, "wilson95": wilson(exact, n),
                             "false_success": sum(r["false_success"] for r in rs),
                             "false_frames": sum((r.get("frames") or {}).get("false_frames", 0) for r in rs),
                             "failures_by_primary": dict(prim), "failures_by_contributing": dict(contrib),
                             "mean_strands_below_2_reads": round(float(np.mean([r["strands_below_2_reads"] for r in rs])), 1),
                             "mean_data_frames_cluster_decodes": (round(float(np.mean([r["frames"]["data_recovered"] for r in rs if r.get("frames")])), 1)
                                                                  if any(r.get("frames") for r in rs) else None),
                             "median_decode_seconds": float(np.median([r["decode_seconds"] for r in rs])),
                             "max_peak_rss_mib": round(max((r["peak_rss_bytes"] or 0) for r in rs) / 2 ** 20, 1),
                             "d13_verdict": rs[0]["d13_verdict"]})
    out["failures_total"], out["failures_classified"] = total_fail, classified
    out["classified_equals_total"] = classified == total_fail
    (HERE / "results" / "summary.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--summarise", action="store_true")
    ap.add_argument("--channels", nargs="*", default=None)
    a = ap.parse_args(argv)
    if a.summarise:
        s = summarise()
        print(json.dumps({k: s[k] for k in ("rows", "failures_total", "failures_classified", "classified_equals_total")}))
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if OUT.exists():
        done = {(r["channel"], r["coverage"], r["profile"], r["seed"]) for r in map(json.loads, OUT.read_text().splitlines())}
    todo = [j for j in jobs() if j not in done and (a.channels is None or j[0] in a.channels)]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool, OUT.open("a") as fh:
        for row in pool.map(run_one, todo):
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
