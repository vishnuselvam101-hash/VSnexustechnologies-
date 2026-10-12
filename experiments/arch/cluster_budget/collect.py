"""Collect the job-86 measurements (SIMULATED) into results/summary.json.

    python experiments/arch/cluster_budget/collect.py WORKDIR

WORKDIR holds w1m/one-SEED-{BASE,NEW}.json, w1m/time-SEED-*.txt, w1m/SEED/sha (1 MiB) and diag10m/results/diag-SEED-E.json,
diag10m/time-SEED-E.txt (10 MB), as written by the chain script."""
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"


def _time(p: Path) -> dict:
    t = p.read_text() if p.is_file() else ""
    rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", t)
    wall = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): ([\d:.]+)", t)
    ex = re.search(r"Exit status: (\d+)", t)
    sec = None
    if wall:
        sec = 0.0
        for part in wall.group(1).split(":"):
            sec = sec * 60 + float(part)
    return {"peak_rss_kib": int(rss.group(1)) if rss else None, "wall_seconds": sec, "exit": int(ex.group(1)) if ex else None}


def main(work: Path) -> dict:
    out: dict = {"label": LABEL, "mib1": [], "mb10": []}
    (HERE / "results").mkdir(exist_ok=True)
    for f in sorted((work / "w1m").glob("one-*.json")):
        _, seed, tree = f.stem.split("-")
        txt = f.read_text().strip()
        row = json.loads(txt) if txt else {"status": "NO_RESULT"}
        truth = (work / "w1m" / seed / "sha").read_text().split()[-1]
        out["mib1"].append({"seed": int(seed), "tree": tree, "status": row.get("status"), "exact": row.get("output_sha256") == truth,
                            "output_sha256": row.get("output_sha256"), "failed_groups": row.get("failed_groups"),
                            "decode_seconds": row.get("seconds"), "clustering_status": row.get("clustering_status"),
                            "budget": row.get("budget"), **_time(work / "w1m" / f"time-{seed}-{tree}.txt")})
    for f in sorted((work / "diag10m" / "results").glob("diag-*-E.json")):
        d = json.loads(f.read_text())
        seed = f.stem.split("-")[1]
        cl = d.get("clustering") or {}
        c = cl.get("clustering") or {}
        out["mb10"].append({"seed": int(seed), "outcome": d.get("outcome"), "commit": d.get("commit"), "load1": d.get("load1"),
                            "decode_seconds": d.get("decode_seconds"), "clustering_status": cl.get("status"),
                            "budget": cl.get("budget_exceeded"), "frames_verified": cl.get("frames_verified"),
                            **{k: c.get(k) for k in ("reads", "clusters", "clustered_reads", "unassigned", "candidate_pairs",
                                                     "pairs_verified", "candidate_bucket_cap", "buckets_over_budget_cap")},
                            "fill": cl.get("fill"), **_time(work / "diag10m" / f"time-{seed}-E.txt")})
        shutil.copy(f, HERE / "results" / f.name)
    (HERE / "results" / "summary.json").write_text(json.dumps(out, indent=1) + "\n")
    return out


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1])), indent=1))
