"""A-FAIL: deterministic failure taxonomy of every non-EXACT A-CONF decode (DIAGNOSTIC, SIMULATED).

Each failed (seed, coverage, arm) of ``experiments/v7/a-conf/results/confirmatory.jsonl`` is rebuilt exactly as A-CONF
built it and decoded again through ``tests/nanopore/nanofunnel.py`` with the ORACLE address test. The rerun must give
the recorded outcome and data frames (else the row is flagged ``reproduced: false``). Nothing here changes the decoder.

Trial level: an EXPLICIT_FAILURE of the outer code means at least one outer row has more missing data symbols than its
parity M. Every lost data strand gets the category of its first lost stage (``CATEGORY``); for every failed trial the
script reports the missing symbols by category inside the failing rows, the deficit (sum over rows of missing − M) and,
per category, whether restoring only that category's strands would make every row decodable (counterfactual rescue).

    PYTHONPATH=src python experiments/v7/a-fail/diag.py --jobs 4                 # results/failures.jsonl (resumable)
    PYTHONPATH=src python experiments/v7/a-fail/diag.py --source apar --jobs 4   # the A-PAR held-out failures
                                                                                   # (results/failures_apar.jsonl)
    python experiments/v7/a-fail/diag.py --summarise                  # results/summary.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests" / "nanopore"))
sys.path.insert(0, str(ROOT / "experiments" / "v7" / "a-conf"))

SOURCES = {"aconf": (ROOT / "experiments" / "v7" / "a-conf" / "results" / "confirmatory.jsonl",
                     HERE / "results" / "failures.jsonl"),
           "apar": (ROOT / "experiments" / "v7" / "a-par" / "results" / "heldout.jsonl",
                    HERE / "results" / "failures_apar.jsonl")}
OUT = SOURCES["aconf"][1]
CATEGORIES = ("dropout", "insufficient_reads", "clustering", "alignment", "addressing", "consensus_indel",
              "consensus_payload", "frame")


def category(lost_at: str, reason: str) -> str:
    """The directive's stage of a lost strand, from the funnel's first lost stage and reason."""
    if lost_at == "observed":
        return "dropout"                                    # 0 reads of the strand in the pool
    if lost_at in ("two_reads", "stored"):
        return "insufficient_reads"                         # < 2 reads (stored) of the strand: no consensus
    if lost_at in ("clustered", "oriented"):
        return "clustering"                                 # unassigned, split, merged, wrong orientation
    if lost_at == "candidate":
        return "alignment"                                  # < 2 reads inside the consensus band
    if lost_at == "valid_frame":
        return "addressing" if "another address" in reason else "frame"
    if lost_at == "rs_recoverable":
        return "consensus_indel" if reason.startswith("shifted") else "consensus_payload"
    raise ValueError(f"unknown stage {lost_at}")


def one(row: dict) -> dict:
    import nanofunnel as nf
    from run import ARMS
    from vnxdna.dnaenc.layout import PROFILES

    profile, template = ARMS[row["arm"]]
    tmpl = {c["channel"]["coverage"]: c for c in nf.load_corpus() if c["tier"] == "slow"}[float(row["coverage"])]
    c = copy.deepcopy(tmpl)
    c["id"] = f"aconf-cov{row['coverage']}-s{row['seed']}"
    c["profile"] = profile
    c["channel"]["seed"] = row["seed"]
    c["decoder"]["cluster_config"]["consensus_template"] = template
    with tempfile.TemporaryDirectory(prefix="vnx-afail-") as tmp:
        d = nf.run_case(c, Path(tmp), oracle=True)
    M = PROFILES[profile][2]
    data = [r for r in d["strands"] if r["kind"] == "data"]
    n_row = Counter(r["group"] for r in data)
    miss_row = Counter(r["group"] for r in data if r["lost_at"] is not None)
    failing = sorted(g for g in n_row if miss_row[g] > M)
    cat = Counter()
    cat_fail = Counter()
    cat_row: dict = defaultdict(Counter)
    e_f = []
    for r in data:
        if r["lost_at"] is None:
            continue
        k = category(r["lost_at"], r["reason"])
        cat[k] += 1
        cat_row[r["group"]][k] += 1
        if r["group"] in failing:
            cat_fail[k] += 1
        if "candidate" in r and r["lost_at"] == "rs_recoverable":
            e_f.append((r["candidate"]["e"], r["candidate"]["f"]))
    rescue = {k: all(miss_row[g] - cat_row[g][k] <= M for g in n_row) for k in CATEGORIES}
    only_structural = all(cat_row[g]["dropout"] + cat_row[g]["insufficient_reads"] <= M for g in n_row)
    sb = d["frames"]
    return {"seed": row["seed"], "coverage": row["coverage"], "arm": row["arm"], "profile": profile,
            "consensus_template": template, "outcome": d["decode"]["outcome"],
            "reproduced": d["decode"]["outcome"] == row["outcome"] and sb["data_recovered"] == row["data_frames"],
            "expected_container_sha256": d["container_sha256"], "reads_sha256": d["reads_sha256"],
            "decoded_container_sha256": d["container_sha256"] if d["decode"]["outcome"] == "EXACT" else None,
            "error_code": d["decode"]["error_code"], "terminal_stage": d["decode"]["terminal_stage"],
            "groups_failed": d["decode"]["groups_failed"], "M": M, "rows": len(n_row),
            "failing_rows": len(failing), "deficit": sum(max(0, miss_row[g] - M) for g in n_row),
            "max_row_missing": max(miss_row.values(), default=0),
            "superblock_recovered": sb["superblock_recovered"], "superblock_needed": sb["superblock_needed"],
            "data_strands": len(data), "lost_strands": sum(cat.values()), "data_frames": sb["data_recovered"],
            "false_frames": sb["false_frames"], "lost_by_category": {k: cat[k] for k in CATEGORIES},
            "lost_in_failing_rows_by_category": {k: cat_fail[k] for k in CATEGORIES},
            "rescued_by_restoring_only": rescue,
            "structural_losses_alone_exceed_parity": not only_structural,
            "consensus_rs_failures": len(e_f), "consensus_mean_e": round(sum(e for e, _ in e_f) / len(e_f), 2) if e_f else None,
            "consensus_mean_f": round(sum(f for _, f in e_f) / len(e_f), 2) if e_f else None,
            "clustering": d["clustering"], "funnel_data": d["funnel_data"],
            "oracle_classes": d["oracle"]["classes_failed_strands"] if "oracle" in d else None,
            "oracle_archive_outcome": d["oracle"]["archive_outcome"] if "oracle" in d else None,
            "oracle_rows_decodable": d["oracle"]["rows_decodable"] if "oracle" in d else None}


def summarise() -> dict:
    rows = [json.loads(x) for x in OUT.read_text().splitlines()]
    out: dict = {"label": "DIAGNOSTIC / SIMULATED (oracle values are ORACLE / DIAGNOSTIC)", "failed_decodes": len(rows),
                 "all_reproduced": all(r["reproduced"] for r in rows), "cells": {}}
    by = defaultdict(list)
    for r in rows:
        by[(r["coverage"], r["arm"])].append(r)
    for (cov, arm), rs in sorted(by.items()):
        lost = Counter()
        lostf = Counter()
        for r in rs:
            lost.update(r["lost_by_category"])
            lostf.update(r["lost_in_failing_rows_by_category"])
        tot, totf = sum(lost.values()), sum(lostf.values())
        out["cells"][f"cov{cov}-{arm}"] = {
            "failed": len(rs), "mean_lost_strands": round(tot / len(rs), 1),
            "lost_share_by_category": {k: round(lost[k] / tot, 4) if tot else 0.0 for k in CATEGORIES},
            "failing_row_share_by_category": {k: round(lostf[k] / totf, 4) if totf else 0.0 for k in CATEGORIES},
            "mean_deficit": round(sum(r["deficit"] for r in rs) / len(rs), 1),
            "mean_failing_rows": round(sum(r["failing_rows"] for r in rs) / len(rs), 2),
            "trials_rescued_by_restoring_only": {k: sum(r["rescued_by_restoring_only"][k] for r in rs) for k in CATEGORIES},
            "trials_where_structural_losses_alone_exceed_parity":
                sum(r["structural_losses_alone_exceed_parity"] for r in rs),
            "superblock_missing_trials": sum(r["superblock_recovered"] < r["superblock_needed"] for r in rs),
            "oracle_archive_exact": sum(r["oracle_archive_outcome"] == "EXACT" for r in rs),
            "false_frames": sum(r["false_frames"] for r in rs)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--summarise", action="store_true")
    ap.add_argument("--source", choices=tuple(SOURCES), default="aconf")
    a = ap.parse_args(argv)
    src, out = SOURCES[a.source]
    if a.summarise:
        doc = summarise()
        (HERE / "results" / "summary.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(json.dumps(doc, indent=1, sort_keys=True))
        return 0
    failed = [r for r in map(json.loads, src.read_text().splitlines()) if r["outcome"] != "EXACT"]
    for r in failed:
        r.setdefault("arm", "lowcov" if r.get("profile") == "v7-lowcov" else None)   # A-PAR rows: v7-lowcov, full
    out.parent.mkdir(exist_ok=True)
    done = set()
    if out.exists():
        done = {(r["seed"], r["coverage"], r["arm"]) for r in map(json.loads, out.read_text().splitlines())}
    jobs = [r for r in failed if (r["seed"], r["coverage"], r["arm"]) not in done]
    with ProcessPoolExecutor(max_workers=a.jobs, max_tasks_per_child=1) as pool, out.open("a") as fh:
        for r in pool.map(one, jobs):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
            print(r["seed"], r["coverage"], r["arm"], r["outcome"], "rep" if r["reproduced"] else "NOT-REPRODUCED",
                  r["deficit"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
