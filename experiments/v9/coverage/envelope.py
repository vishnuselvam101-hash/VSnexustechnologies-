"""V9 coverage envelope (SIMULATED; docs/V9_PREREGISTRATION.md §9). The V8.11 method with V9 seeds, coverages and the
pre-specified q rule.

Cases: D13-F1 stress channel x coverage {1, 2, 3, 4, 5, 7, 10, 15} (negative binomial, dispersion 4) x profile
{v4-balanced, v7-lowcov} x seeds 92000-92029; 20,000-byte archives; the V9 production decoder (``--candidate``).

Per cell: mean coverage and dispersion; fractions of strands with 0, 1 and < 2 reads; row-level failure probability
(analytic, and empirical = failed outer rows / rows); archive-level success with a Wilson 95 % interval; the class:

* q = P(reads < 1) where the cluster stage ran in under half the decodes, otherwise P(reads < 2) (pre-specified in V9),
  plus (1 - that) x the empirical late-loss share q_cons (V8 definition);
* SUPPORTED: analytic P(archive) >= 0.95 and Wilson lower >= 0.70; NOT SUPPORTED: analytic < 0.50 or Wilson upper < 0.50;
  MARGINALLY SUPPORTED otherwise. One successful seed never makes a level supported.

    PYTHONPATH=src python experiments/v9/coverage/envelope.py run --candidate v8 [--jobs 3]
    PYTHONPATH=src python experiments/v9/coverage/envelope.py summarise
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in ("tests/nanopore", "experiments/v8/matrix", "experiments/v9/consensus"):
    sys.path.insert(0, str(ROOT / p))
OUT = HERE / "results" / "envelope-cases.jsonl"
SUMMARY = HERE / "results" / "envelope.json"
CHANNEL = "d13-f1"
COVERAGES = (1, 2, 3, 4, 5, 7, 10, 15)
PROFILES = ("v4-balanced", "v7-lowcov")
SEEDS = tuple(range(92000, 92030))
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"


def _load(name: str, rel: str):
    """A V8 harness module by path (``run.py`` and ``envelope.py`` exist in several experiment folders)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _commit() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def run_one(job: tuple) -> dict:
    import nanofunnel as nf
    import taxonomy as TX
    MX = _load("v8_matrix_run", "experiments/v8/matrix/run.py")
    from candidates import cluster_config
    from vnxdna.dnaenc.layout import PROFILES as LP
    from vnxdna.native.reads import iter_reads
    from vnxdna.simulation import engine
    cov, profile, seed, candidate = job
    t0 = time.perf_counter()
    model = MX.case_model(CHANNEL, cov)
    case = {"profile": profile, "decoder": {"read_clustering": "fallback", "cluster_config": cluster_config(candidate)}}
    with tempfile.TemporaryDirectory(prefix="vnx-v9env-") as tmp:
        work = Path(tmp)
        arc = nf.build_archive(work / "archive", MX.SIZE, seed, profile)
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
        tax = TX.classify(doc, LP[profile][2], set(truth["lost_strands"]), cluster_ran=cap.reads is not None)
    rps = np.bincount(truth["source"][truth["source"] >= 0], minlength=len(arc["keys"]))
    gf = dec.get("groups_failed")
    return {"channel": CHANNEL, "coverage": cov, "profile": profile, "seed": seed, "candidate": candidate,
            "cluster_config": case["decoder"]["cluster_config"], "label": LABEL, "container_sha256": arc["container_sha256"],
            "reads_sha256": truth["reads_sha256"], "reads": len(reads), "strands": len(arc["keys"]),
            "reads_per_strand_mean": float(rps.mean()), "reads_per_strand_var": float(rps.var()),
            "strands_0_reads": int((rps == 0).sum()), "strands_1_read": int((rps == 1).sum()), "strands_below_2_reads": int((rps < 2).sum()),
            "outcome": dec["outcome"], "false_success": dec["outcome"] == "FALSE_SUCCESS", "error_code": dec["error_code"],
            "rows_failed": len(gf) if isinstance(gf, (list, tuple)) else (int(gf) if gf is not None else None),
            "terminal_stage": dec["terminal_stage"], "cluster_stage_ran": cap.reads is not None, "taxonomy": tax,
            "false_frames": doc.get("frames", {}).get("false_frames") if isinstance(doc.get("frames"), dict) else None,
            "decode_seconds": dec["decode_seconds"], "peak_rss_bytes": dec["peak_rss_bytes"],
            "seconds": round(time.perf_counter() - t0, 2), "load1": round(os.getloadavg()[0], 2), "commit": _commit()}


def summarise(rows: list) -> dict:
    E8 = _load("v8_envelope", "experiments/v8/coverage/envelope.py")
    geom = {p: E8.row_sizes(p) for p in PROFILES}
    cells: dict = {}
    for r in rows:
        cells.setdefault((r["coverage"], r["profile"]), []).append(r)
    out = {"label": "SIMULATED (analytic model + simulated decodes); no DNA was synthesised, stored or sequenced",
           "channel": CHANNEL, "dispersion": E8.DISPERSION, "seeds": [SEEDS[0], SEEDS[-1]],
           "q_rule": "pre-specified (V9 §9): q_struct = P(reads<1) if the cluster stage ran in < half the decodes, else P(reads<2)",
           "geometry": {p: {"rows": len(g[0]), "data_strands_per_row": sorted(set(g[0])), "M": g[1]} for p, g in geom.items()},
           "cells": []}
    late = ["clustering", "alignment", "consensus", "indel_placement", "substitution_correction"]
    for (cov, prof), rs in sorted(cells.items()):
        sizes, M = geom[prof]
        qs = E8.q_struct(float(cov))
        share = sum(bool(r["cluster_stage_ran"]) for r in rs) / len(rs)
        cl = [r for r in rs if r["cluster_stage_ran"] and r["taxonomy"].get("lost_by_category") is not None]
        if cl:
            lost_late = np.mean([sum(r["taxonomy"]["lost_by_category"].get(k, 0) for k in late) for r in cl])
            ge2 = np.mean([sum(sizes) - r["strands_below_2_reads"] * sum(sizes) / r["strands"] for r in cl])
            q_cons = float(lost_late / max(ge2, 1.0))
        else:
            q_cons = 0.0
        qb = qs["p_below_1"] if share < 0.5 else qs["p_below_2"]
        q = qb + (1 - qb) * q_cons
        p_rows = [E8.binom_cdf(M, n, q) for n in sizes]
        p_arch = float(np.prod(p_rows))
        k = sum(r["outcome"] == "EXACT" for r in rs)
        lo, hi = E8.wilson(k, len(rs))
        rf = [r["rows_failed"] / len(sizes) for r in rs if r["rows_failed"] is not None]
        n_str = np.mean([r["strands"] for r in rs])
        out["cells"].append({
            "coverage": cov, "profile": prof, "n": len(rs), "exact": k, "wilson95": [round(lo, 4), round(hi, 4)],
            "false_success": sum(r["false_success"] for r in rs),
            "mean_reads_per_strand": round(float(np.mean([r["reads_per_strand_mean"] for r in rs])), 3),
            "var_reads_per_strand": round(float(np.mean([r["reads_per_strand_var"] for r in rs])), 3),
            "frac_strands_0_reads": round(float(np.mean([r["strands_0_reads"] for r in rs]) / n_str), 5),
            "frac_strands_1_read": round(float(np.mean([r["strands_1_read"] for r in rs]) / n_str), 5),
            "frac_strands_below_2": round(float(np.mean([r["strands_below_2_reads"] for r in rs]) / n_str), 5),
            "q_struct_used": "P(reads<1)" if share < 0.5 else "P(reads<2)", "q_struct": round(qb, 6), "q_consensus": round(q_cons, 5),
            "q_total": round(q, 6), "row_failure_analytic": round(1 - float(np.mean(p_rows)), 5),
            "row_failure_empirical": round(float(np.mean(rf)), 5) if rf else None,
            "p_archive_analytic": round(p_arch, 4), "cluster_stage_share": round(share, 2),
            "class": E8.classify(p_arch, lo, hi),
            "median_decode_seconds": round(float(np.median([r["decode_seconds"] for r in rs])), 2)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "summarise"])
    ap.add_argument("--candidate", default="v8")
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args(argv)
    rows = [json.loads(x) for x in OUT.read_text().splitlines()] if OUT.exists() else []
    if a.cmd == "run":
        done = {(r["coverage"], r["profile"], r["seed"], r["candidate"]) for r in rows}
        todo = [(c, p, s, a.candidate) for c in COVERAGES for p in PROFILES for s in SEEDS if (c, p, s, a.candidate) not in done]
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with ProcessPoolExecutor(max_workers=a.jobs) as pool, OUT.open("a") as fh:
            for r in pool.map(run_one, todo):
                fh.write(json.dumps(r, sort_keys=True) + "\n")
                fh.flush()
                print(f"cov{r['coverage']} {r['profile']} s{r['seed']}: {r['outcome']}", flush=True)
        rows = [json.loads(x) for x in OUT.read_text().splitlines()]
    cands = {r["candidate"] for r in rows}
    if len(cands) > 1:
        raise SystemExit(f"envelope rows from several decoders {cands}; summarise one")
    out = summarise(rows)
    out["candidate"] = next(iter(cands), None)
    out["commit"] = _commit()
    SUMMARY.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    for c in out["cells"]:
        print(c["coverage"], c["profile"], c["class"], c["p_archive_analytic"], f"{c['exact']}/{c['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
