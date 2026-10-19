"""V15 CC-1: paired A/B harness for decode speed (SIMULATED inputs).

Inputs are produced once with the V14 P6E suite's own ``prepare`` (archive from the V12 tree, strands + reads from
this tree's simulator), so every configuration decodes the very same files as in docs/V14_BENCHMARK_REPORT.md §1.

    PYTHONPATH=src python experiments/v15/cc1/ab.py prep --workload T-A-indel-4m-w1 --seeds 95500-95508 --dir D
    PYTHONPATH=src python experiments/v15/cc1/ab.py run  --workload T-A-indel-4m-w1 --seeds 95500-95508 --dir D \
        --config base=/path/tree --config new=/path/tree --config newaa=/path/tree --out rows.jsonl
    python experiments/v15/cc1/ab.py summary rows.jsonl --pair base/new --pair new/newaa

``run``: one decode per process (``suite.py one``), configurations in list order on even seeds and reversed on odd
seeds, one discarded warm-up per configuration on the first seed. Ratio = time(a) / time(b) per seed (> 1 = b
faster); CI = percentile bootstrap of the median ratio (10000 resamples, RNG seed 11; suite.boot_ci).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SUITE_PATH = ROOT / "experiments" / "v14" / "bench" / "p6e-channels" / "suite.py"


def _suite():
    spec = importlib.util.spec_from_file_location("p6e_suite", SUITE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["p6e_suite"] = mod
    spec.loader.exec_module(mod)
    return mod


def _seeds(text: str) -> list[int]:
    a, _, b = text.partition("-")
    return list(range(int(a), int(b or a) + 1))


def _git(tree: Path) -> str:
    sha = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(tree), "status", "--porcelain", "--untracked-files=no", "--", "src"],
                           capture_output=True, text=True).stdout.strip()
    return sha + ("+dirty" if dirty else "")


def prep(a) -> None:
    S = _suite()
    m = S.load_manifest()
    w = S.workload(m, a.workload)
    base = Path(os.environ.get("BASE_TREE", "/root/vnx-dna-lab/v13-base"))
    for seed in _seeds(a.seeds):
        work = a.dir / f"{a.workload}-{seed}"
        if (work / "prep.json").is_file():
            continue
        work.mkdir(parents=True, exist_ok=True)
        size = S.effective_size(w, m["method"]["smoke"] if a.smoke else None)
        info = S.prepare(work, m, w, seed, base, size)
        (work / "prep.json").write_text(json.dumps(info, indent=1) + "\n")
        print(a.workload, seed, info["prepare_seconds"], "s", flush=True)


def run(a) -> None:
    S = _suite()
    m = S.load_manifest()
    w = S.workload(m, a.workload)
    cfgs = dict(c.split("=", 1) for c in a.config)
    trees = {k: Path(v).resolve() for k, v in cfgs.items()}
    commits = {k: _git(t) for k, t in trees.items()}
    workers = a.workers or w["workers"]
    seeds = _seeds(a.seeds)
    for rep in range(a.reps):
        for i, seed in enumerate(seeds):
            work = a.dir / f"{a.workload}-{seed}"
            if i == 0 and rep == 0 and not a.no_warm:
                for c, t in trees.items():
                    S.step(c, t, w["kind"], work, workers, 1800, f"warm-{c}")
            order = list(trees) if (seed + rep) % 2 == 0 else list(trees)[::-1]
            rows = []
            for c in order:
                r = S.step(c, trees[c], w["kind"], work, workers, 1800)
                r.update(workload=a.workload, seed=seed, rep=rep, workers=workers, commit=commits[c], order=order,
                         label=a.label)
                rows.append(r)
            with open(a.out, "a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            print(a.workload, seed, rep, " | ".join(f"{r['config']} {r['seconds']:.3f}s {r['outcome']}"
                                                      f" {str(r.get('output_sha256'))[:8]}" for r in rows), flush=True)


def summary(a) -> None:
    S = _suite()
    rows = [json.loads(x) for x in Path(a.rows).read_text().splitlines() if x.strip()]
    if a.workload:
        rows = [r for r in rows if r["workload"] in a.workload.split(",")]
    out = {}
    for wl in sorted({r["workload"] for r in rows}):
        rr = [r for r in rows if r["workload"] == wl]
        by = {}
        for r in rr:
            by.setdefault(r["config"], {})[(r["seed"], r.get("rep", 0))] = r
        rec = {"n_rows": len(rr), "configs": {}}
        for c, d in by.items():
            secs = [x["seconds"] for x in d.values()]
            rec["configs"][c] = {"median_s": round(statistics.median(secs), 4), "min_s": round(min(secs), 4),
                                 "max_s": round(max(secs), 4), "n": len(secs),
                                 "outcomes": sorted({x["outcome"] for x in d.values()}),
                                 "all_match_input": all(x.get("matches_input") for x in d.values()),
                                 "commit": next(iter(d.values()))["commit"]}
        keys = sorted(set.intersection(*(set(d) for d in by.values())))
        same = all(len({(by[c][k].get("output_sha256"), by[c][k]["outcome"]) for c in by}) == 1 for k in keys)
        rec["identical_outcome_and_output"] = same
        rec["pairs"] = {}
        for p in a.pair:
            x, y = p.split("/")
            if x in by and y in by:
                rec["pairs"][p] = S.ratio_record([by[x][k]["seconds"] for k in keys], [by[y][k]["seconds"] for k in keys])
        rec["components"] = {}
        for comp in a.component:
            def val(r, comp=comp):
                st = (r.get("report") or {}).get("stage_seconds") or {}
                p1 = ((r.get("report") or {}).get("engine") or {}).get("pass1") or {}
                return st.get(comp, p1.get(comp))
            have = [k for k in keys if all(val(by[c][k]) is not None for c in by)]
            if not have:
                continue
            crec = {"median_s": {c: round(statistics.median(val(by[c][k]) for k in have), 4) for c in by}}
            for p in a.pair:
                x, y = p.split("/")
                if x in by and y in by and all(val(by[y][k]) > 0 for k in have):
                    crec[p] = S.ratio_record([val(by[x][k]) for k in have], [val(by[y][k]) for k in have])
            rec["components"][comp] = crec
        out[wl] = rec
        print(wl, json.dumps({c: v["median_s"] for c, v in rec["configs"].items()}), "identical=", same)
        for p, v in rec["pairs"].items():
            print(f"   {p:16s} {v['median']:.3f} {v['ci95']} n={v['n']}")
        for comp, c in rec["components"].items():
            print(f"   [{comp}] medians {json.dumps(c['median_s'])} " + " ".join(
                f"{p} {v['median']:.3f} {v['ci95']}" for p, v in c.items() if p != "median_s"))
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep")
    p.add_argument("--workload", required=True)
    p.add_argument("--seeds", required=True)
    p.add_argument("--dir", type=Path, required=True)
    p.add_argument("--smoke", action="store_true")
    p = sub.add_parser("run")
    p.add_argument("--workload", required=True)
    p.add_argument("--seeds", required=True)
    p.add_argument("--dir", type=Path, required=True)
    p.add_argument("--config", action="append", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--workers", type=int)
    p.add_argument("--label", default="")
    p.add_argument("--no-warm", action="store_true")
    p = sub.add_parser("summary")
    p.add_argument("rows")
    p.add_argument("--pair", action="append", default=[])
    p.add_argument("--workload", default="")
    p.add_argument("--json")
    p.add_argument("--component", action="append", default=[],
                   help="a decoder stage timer (stage_seconds) or engine counter (engine.pass1) to compare per config")
    a = ap.parse_args(argv)
    {"prep": prep, "run": run, "summary": summary}[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
