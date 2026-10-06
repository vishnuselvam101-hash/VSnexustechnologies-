"""A-RS step 1: stage-by-stage accounting of why cov 5 fails with the Phase 1 consensus (DIAGNOSTIC, SIMULATED).

    PYTHONPATH=src python experiments/v7/a-rs/diag.py --cov 5 --seeds 82043-82055 --jobs 4

Development seeds only (frozen corpus 82043-82045, A-LOSS 82046-82055). For every data row: verified strands, missing
strands, the outer erasure budget M; for every consensus candidate that failed the inner RS: wrong / erased frame bytes,
whether its variant byte and address header are decided and correct, and per payload byte column correct / wrong /
erased. ``policy_bound`` counts the rows an outer errors-and-erasures decode per byte column could decode if failed
candidates with a decided header were admitted (2e + f <= M per column; an upper-bound estimate, not a decoder)."""
from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests" / "nanopore"))
import nanofunnel as nf  # noqa: E402

from vnxdna.dnaenc.layout import HEADER_BYTES  # noqa: E402
from vnxdna.dnaenc.mapping import nt_to_bytes  # noqa: E402


def case_for(cov: int, seed: int, arm: str) -> dict:
    tmpl = {c["channel"]["coverage"]: c for c in nf.load_corpus() if c["tier"] == "slow"}[float(cov)]
    c = copy.deepcopy(tmpl)
    c["id"] = f"diag-cov{cov}-s{seed}"
    c["channel"]["seed"] = seed
    c["decoder"]["cluster_config"]["consensus_template"] = arm
    return c


def one(job) -> dict:
    cov, seed, arm = job
    case = case_for(cov, seed, arm)
    with tempfile.TemporaryDirectory(prefix="vnx-ars-") as tmp:
        work = Path(tmp)
        arc = nf.build_archive(work / "archive", case["size"], case["data_seed"], case["profile"])
        sim = nf.simulate(arc["strands_path"], work / "reads.fastq", case["channel"])
        dec, cap = nf.run_decode(work / "reads.fastq", work / "out.vnx", arc["container_sha256"], nf.decoder_options(case))
    lay, M = arc["lay"], arc["M"]
    idx = nf.map_stored(cap.reads, sim["reads"])
    strands, verified, false = nf.strand_funnel(arc, sim, cap, idx)
    s_src = sim["src"][idx]
    labels = cap.clustering.labels
    home: dict = {}
    for i, lab in enumerate(labels.tolist()):
        if lab >= 0:
            home.setdefault(int(s_src[i]), Counter())[lab] += 1
    trace_by: dict = {}
    for t in cap.trace:
        trace_by.setdefault(t["cluster"], []).append(t)
    P = lay.payload_bytes
    pay = slice(HEADER_BYTES, HEADER_BYTES + P)
    rows: dict = {}
    failed = []
    for r in strands:
        if r["kind"] != "data":
            continue
        g = r["group"]
        row = rows.setdefault(g, {"n": 0, "verified": 0, "missing": 0, "cand_failed": []})
        row["n"] += 1
        if r["lost_at"] is None:
            row["verified"] += 1
            continue
        row["missing"] += 1
        if "candidate" not in r:
            continue
        s = r["strand"]
        t0 = trace_by[home[s].most_common(1)[0][0]][0]
        true_b = nt_to_bytes(arc["frame_nt"][s][None, :])[0]
        got, er = t0["bytes"], t0["erased"]
        wrong = (got != true_b) & ~er
        hdr = slice(0, HEADER_BYTES)
        rec = {"strand": s, "group": g, "e": int(wrong.sum()), "f": int(er.sum()),
               "e_payload": int(wrong[pay].sum()), "f_payload": int(er[pay].sum()),
               "header_decided": bool(not er[hdr].any()), "header_correct": bool(not er[hdr].any() and not wrong[hdr].any()),
               "col_wrong": wrong[pay].tolist(), "col_erased": er[pay].tolist(), "reason": r["reason"].split(":")[0]}
        row["cand_failed"].append(rec)
        failed.append(rec)
    # rows: erasure-only (today) and the per-column errors-and-erasures bound with decided-header candidates admitted
    ok_now = ok_bound = ok_bound_correct_hdr = 0
    for g, row in rows.items():
        ok_now += row["missing"] <= M
        for need_correct in (False, True):
            adm = [c for c in row["cand_failed"] if (c["header_correct"] if need_correct else c["header_decided"])]
            base_er = row["missing"] - len(adm)
            col_ok = True
            for col in range(P):
                e = sum(c["col_wrong"][col] for c in adm)
                f = base_er + sum(c["col_erased"][col] for c in adm)
                if 2 * e + f > M:
                    col_ok = False
                    break
            if need_correct:
                ok_bound_correct_hdr += col_ok
            else:
                ok_bound += col_ok
    cc = dec.get("cluster_consensus") or {}
    fd = Counter(r["lost_at"] for r in strands if r["kind"] == "data")
    return {
        "cov": cov, "seed": seed, "arm": arm, "outcome": dec["outcome"], "false_frames": false,
        "reads_generated": len(sim["reads"]), "reads_stored": len(cap.reads), "reads_clustered": int((labels >= 0).sum()),
        "data_strands": sum(r["n"] for r in rows.values()), "data_verified": sum(r["verified"] for r in rows.values()),
        "lost_at": {k: v for k, v in fd.items() if k}, "candidates_failed": len(failed),
        "failed_reasons": dict(Counter(c["reason"] for c in failed)),
        "failed_e_mean": round(float(np.mean([c["e"] for c in failed])), 2) if failed else 0,
        "failed_f_mean": round(float(np.mean([c["f"] for c in failed])), 2) if failed else 0,
        "failed_header_decided": sum(c["header_decided"] for c in failed),
        "failed_header_correct": sum(c["header_correct"] for c in failed),
        "failed_header_decided_wrong": sum(c["header_decided"] and not c["header_correct"] for c in failed),
        "rs": {k: cc.get(k) for k in ("cluster_consensus_attempted", "cluster_insufficient_reads", "cluster_rs_crc_trials",
                                      "cluster_frames_verified", "cluster_frames_verified_gmd", "cluster_decode_failed",
                                      "cluster_erasures_exceed_parity", "cluster_erased_bytes")},
        "rows": len(rows), "row_missing": sorted(r["missing"] for r in rows.values()), "M": M,
        "rows_ok_erasure_only": ok_now, "rows_ok_bound_decided_header": ok_bound,
        "rows_ok_bound_correct_header": ok_bound_correct_hdr}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cov", type=int, default=5)
    ap.add_argument("--seeds", default="82043-82055")
    ap.add_argument("--arm", default="full")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    lo, hi = (int(x) for x in a.seeds.split("-"))
    if hi >= 82060:
        ap.error("diagnosis uses development seeds only (< 82060)")
    out = HERE / "results" / f"diag-cov{a.cov}-{a.arm}.jsonl"
    out.parent.mkdir(exist_ok=True)
    with ProcessPoolExecutor(a.jobs, max_tasks_per_child=1) as pool, out.open("w") as fh:
        for r in pool.map(one, [(a.cov, s, a.arm) for s in range(lo, hi + 1)]):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
            print(r["seed"], r["outcome"], r["data_verified"], r["row_missing"], r["rows_ok_bound_decided_header"],
                  flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
