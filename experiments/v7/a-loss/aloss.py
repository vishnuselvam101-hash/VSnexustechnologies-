"""A-LOSS: per-original-strand loss funnel and ORACLE address test of the V7 read-clustering decode
(EXPERIMENTAL / DIAGNOSTIC / SIMULATED; descriptive).

    PYTHONPATH=src python experiments/v7/a-loss/aloss.py run --config experiments/v7/a-loss/config.json --jobs 6
    PYTHONPATH=src python experiments/v7/a-loss/aloss.py summarise --config experiments/v7/a-loss/config.json

A trial = (cell, arm, seed): the nanopore regression corpus machinery (``tests/nanopore/nanofunnel.py``) is run on a
case built from the cell's channel (coverage and the trial seed) and the arm's decoder options: rebuild the archive,
simulate with per-read ground truth, decode, attribute every original strand to the first funnel stage that loses it
(with a reason), and run the ORACLE address test (reads grouped by true source strand only). Outcomes follow protocol
§6. The ORACLE rows are diagnostic, never decoding or acceptance results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tests" / "nanopore"))
sys.path.insert(0, str(REPO / "experiments" / "v6" / "phase4"))
import nanofunnel as nf  # noqa: E402
import phase4 as p4  # noqa: E402

from vnxdna.core.provenance import environment  # noqa: E402

CLASSIFICATION = "EXPERIMENTAL / DIAGNOSTIC / SIMULATED"
STATEMENT = ("EXPERIMENTAL (protocol §7 exploration seeds 82000-82099), SIMULATED: software strands and the unfitted V6 "
             "nanopore-like channel. No DNA was synthesised, stored or sequenced; no public data and no fitted model is "
             "used. Descriptive loss taxonomy with ground truth; ORACLE rows supply the true strand identity and are "
             "not decoding results.")


CACHE = Path(os.environ.get("VNX_ALOSS_CACHE", "/root/vnx-dna-lab/results/cache/a-loss"))


def code_key() -> str:
    """Commit of the code a trial depends on plus a hash of any uncommitted change under src/ and tests/nanopore/."""
    import subprocess
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    diff = subprocess.run(["git", "diff", "HEAD", "--", "src", "tests/nanopore"], cwd=REPO, capture_output=True).stdout
    return f"{head}+{hashlib.sha256(diff).hexdigest()[:16]}"


def run_trial(job: dict) -> dict:
    """Cached by (code key, case, oracle flag): a valid result is never recomputed."""
    case = job["case"]
    key = hashlib.sha256(json.dumps([job["code_key"], case, job["oracle"]], sort_keys=True).encode()).hexdigest()
    cpath = CACHE / f"{key}.json"
    if cpath.exists():
        res = json.loads(cpath.read_text())
        res["cached"] = True
        return res
    res = _compute(job)
    CACHE.mkdir(parents=True, exist_ok=True)
    tmpf = cpath.with_suffix(".tmp")
    tmpf.write_text(json.dumps(res, sort_keys=True, default=str))
    tmpf.replace(cpath)
    res["cached"] = False
    return res


def _compute(job: dict) -> dict:
    case = job["case"]
    with tempfile.TemporaryDirectory(prefix="vnx-aloss-", dir=job["tmp"]) as tmp:
        doc = nf.run_case(case, Path(tmp), oracle=job["oracle"])
    lost = [r for r in doc.pop("strands") if r["lost_at"] is not None]
    cand = [r["candidate"] for r in lost if "candidate" in r]
    reads_hist = Counter(min(r["reads"], 12) for r in lost)
    return {"record": "trial", "classification": CLASSIFICATION, "cell": job["cell"], "arm": job["arm"],
            "seed": case["channel"]["seed"], "code_key": job["code_key"], "doc": doc, "lost_reads_hist": dict(reads_hist),
            "lost_candidates": {"n": len(cand), "mean_e": _m(cand, "e"), "mean_f": _m(cand, "f"),
                                "mean_wrong_nt": _m(cand, "wrong_nt"), "mean_shifted_nt": _m(cand, "shifted_nt"),
                                "mean_reads": _m(cand, "reads")}}


def _m(xs: list, k: str):
    return round(float(np.mean([x[k] for x in xs])), 2) if xs else None


def summarise(trials: list) -> dict:
    out: dict = {}
    for key in sorted({(t["cell"], t["arm"]) for t in trials}):
        ts = sorted((t for t in trials if (t["cell"], t["arm"]) == key), key=lambda t: t["seed"])
        n = len(ts)
        docs = [t["doc"] for t in ts]
        oc = Counter(d["decode"]["outcome"] for d in docs)
        reasons: Counter = Counter()
        for d in docs:
            reasons.update(d["loss_reasons"])

        def mean(f):
            return round(float(np.mean([f(d) for d in docs])), 2)
        row = {"trials": n, "seeds": [t["seed"] for t in ts], "outcomes": dict(oc),
               "false_success": oc.get("FALSE_SUCCESS", 0),
               "funnel_data_mean": {k: mean(lambda d, k=k: d["funnel_data"][k]) for k in ("total",) + nf.STAGES},
               "first_large_drop": dict(Counter(d["first_large_drop"]["stage"] for d in docs)),
               "loss_reasons_mean_per_trial": {k: round(v / n, 2) for k, v in sorted(reasons.items())},
               "frames_data_recovered_mean": mean(lambda d: d["frames"]["data_recovered"]),
               "rows_decodable_mean": mean(lambda d: d["rows"]["decodable_from_cluster_frames"]),
               "false_frames_total": sum(d["frames"]["false_frames"] for d in docs),
               "cluster_read_purity_mean": mean(lambda d: (d["clustering"] or {}).get("read_purity", 0)),
               "impure_clusters_mean": mean(lambda d: (d["clustering"] or {}).get("impure_clusters", 0)),
               "decode_seconds_median": float(np.median([d["decode"]["decode_seconds"] for d in docs])),
               "lost_candidates_mean": {k: round(float(np.mean([t["lost_candidates"][k] or 0 for t in ts])), 2)
                                        for k in ("n", "mean_e", "mean_f", "mean_wrong_nt", "mean_shifted_nt", "mean_reads")}}
        orc = [d["oracle"] for d in docs if "oracle" in d]
        if orc:
            row["oracle"] = {"label": "ORACLE / DIAGNOSTIC / SIMULATED (true strand identity supplied)",
                             "classes_failed_strands_mean": {c: round(float(np.mean([o["classes_failed_strands"][c]
                                                                                     for o in orc])), 2)
                                                             for c in nf.ORACLE_CLASSES},
                             "failed_reads_by_class_mean": {c: round(float(np.mean([o["failed_reads_by_class"][c]
                                                                                    for o in orc])), 1)
                                                            for c in nf.ORACLE_CLASSES},
                             "frames_data_recovered_mean": round(float(np.mean([o["frames_data_recovered"] for o in orc])), 2),
                             "rows_decodable_mean": round(float(np.mean([o["rows_decodable"] for o in orc])), 2),
                             "archive_sha_match": sum(o["archive_sha_match"] for o in orc),
                             "false_frames_total": sum(o["false_frames"] for o in orc)}
        out[f"{key[0]}|{key[1]}"] = row
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--cells", help="comma-separated cell ids (default: all)")
    r.add_argument("--arms", help="comma-separated arm names (default: all)")
    r.add_argument("--out", help="trials JSON-lines (default: <config dir>/trials.jsonl)")
    s = sub.add_parser("summarise")
    s.add_argument("--config", required=True)
    s.add_argument("--trials", help="default: <config dir>/trials.jsonl")
    s.add_argument("--out", help="default: <config dir>/summary.json")
    a = ap.parse_args(argv)
    cfg_path = Path(a.config).resolve()
    cfg = json.loads(cfg_path.read_text())
    exp_dir = cfg_path.parent
    if a.cmd == "summarise":
        tp = Path(a.trials) if a.trials else exp_dir / "trials.jsonl"
        lines = [json.loads(x) for x in open(tp)]
        trials = [x for x in lines if x.get("record") == "trial"]
        header = next(x for x in lines if x.get("record") == "header")
        summ = summarise(trials)
        doc = {"record": "summary", "experiment": cfg["experiment"], "classification": CLASSIFICATION,
               "statement": STATEMENT, "config_sha256": header["config_sha256"], "git": header["git"],
               "trials": len(trials), "false_success_total": sum(v["false_success"] for v in summ.values()),
               "false_frames_total": sum(v["false_frames_total"] for v in summ.values()), "cells": summ}
        Path(a.out or exp_dir / "summary.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(json.dumps({k: doc[k] for k in ("trials", "false_success_total", "false_frames_total")}))
        return 2 if doc["false_success_total"] else 0
    cells = cfg["cells"] if not a.cells else [c for c in cfg["cells"] if c["id"] in a.cells.split(",")]
    arms = cfg["arms"] if not a.arms else {k: v for k, v in cfg["arms"].items() if k in a.arms.split(",")}
    out_path = Path(a.out) if a.out else exp_dir / "trials.jsonl"
    tmp = Path(tempfile.mkdtemp(prefix="vnx-aloss-setup-"))
    command = " ".join(sys.argv)
    t0 = time.perf_counter()
    try:
        jobs = []
        ck = code_key()
        for cell in cells:
            for arm, spec in arms.items():
                for seed in cell["seeds"]:
                    if not 82000 <= seed <= 82099:
                        raise SystemExit(f"seed {seed} outside the EXPERIMENTAL range 82000-82099 (protocol §7)")
                    case = {"id": f"{cell['id']}|{arm}|{seed}", "tier": "experiment", "size": cfg["size"],
                            "data_seed": cfg["data_seed"], "profile": cfg["profile"],
                            "channel": {**cfg["channels"][cell["channel"]], **cell.get("channel_overrides", {}),
                                        "seed": seed},
                            "decoder": spec["decoder"]}
                    jobs.append({"cell": cell["id"], "arm": arm, "case": case, "oracle": bool(spec.get("oracle")),
                                 "tmp": str(tmp), "code_key": ck})
        header = {"record": "header", "classification": CLASSIFICATION, "statement": STATEMENT, "command": command,
                  "config": cfg, "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
                  "git": p4.chn.git_info(), "environment": environment(), "backends": p4.backends(),
                  "cells_run": [c["id"] for c in cells], "arms_run": list(arms), "jobs": a.jobs, "nice": os.nice(0),
                  "code_key": ck, "loadavg_start": os.getloadavg()}
        with open(out_path, "w") as fh:
            print(json.dumps(header, sort_keys=True, default=str), file=fh, flush=True)
            with ProcessPoolExecutor(max_workers=a.jobs) as pool:
                for res in pool.map(run_trial, jobs):
                    print(json.dumps(res, sort_keys=True, default=str), file=fh, flush=True)
                    d = res["doc"]
                    print(f"{res['cell']} {res['arm']} {res['seed']} {d['decode']['outcome']} "
                          f"frames={d['frames']['data_recovered']} rows={d['rows']['decodable_from_cluster_frames']}"
                          f" oracle={(d.get('oracle') or {}).get('frames_data_recovered')} "
                          f"t={d['decode']['decode_seconds']}", flush=True)
            print(json.dumps({"record": "end", "wall_seconds": round(time.perf_counter() - t0, 1),
                              "loadavg_end": os.getloadavg()}), file=fh, flush=True)
        trials = [json.loads(x) for x in open(out_path) if '"record": "trial"' in x]
        fs = sum(t["doc"]["decode"]["outcome"] == "FALSE_SUCCESS" for t in trials)
        print(f"{len(trials)} trials, {fs} FALSE SUCCESS, {round(time.perf_counter() - t0, 1)} s -> {out_path}")
        return 2 if fs else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
