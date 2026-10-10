"""Held-out evaluation of decoder preset F vs E (see PREREG.md). SIMULATED.

    PYTHONPATH=src python experiments/arch/outer_list_eval/run.py run [--seeds 93020-93029]
    PYTHONPATH=src python experiments/arch/outer_list_eval/run.py summary
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / "results" / "eval.jsonl"
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"


def _paths():
    sys.path[:0] = [str(ROOT / "tests/nanopore"), str(ROOT / "experiments/v9/consensus"), str(ROOT / "src")]


def prepare(work: Path, seed: int, size: int) -> str:
    _paths()
    import nanofunnel as nf
    from vnxdna.simulation import engine
    spec = importlib.util.spec_from_file_location("mx", ROOT / "experiments/v8/matrix/run.py")
    mx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mx)
    arc = nf.build_archive(work / "a", size, seed, "v4-balanced")
    engine.simulate_file_with_truth(arc["strands_path"], work / "r.fastq", mx.case_model("d13-f1", 10), seed)
    return arc["container_sha256"]


def one(work: Path, preset: str) -> dict:
    _paths()
    import dataclasses
    import nanofunnel as nf
    from candidates import cluster_config
    from vnxdna.pipeline.decode import decode_reads
    case = {"profile": "v4-balanced", "decoder": {"read_clustering": "fallback", "cluster_config": cluster_config(preset)}}
    opts = dataclasses.replace(nf.decoder_options(case), workers=4)
    out = work / f"o-{preset}.vnx"
    t = time.perf_counter()
    try:
        res = decode_reads(work / "r.fastq", out, opts)
        status, rep = res.status, res.report or {}
    except Exception as error:  # noqa: BLE001 - an explicit failure is an outcome
        status, rep = f"ERROR:{type(error).__name__}", getattr(error, "report", None) or {}
    sha = hashlib.sha256(out.read_bytes()).hexdigest() if out.is_file() else None
    return {"preset": preset, "status": status, "seconds": round(time.perf_counter() - t, 2), "output_sha256": sha,
            "failed_groups": rep.get("failed_groups"),
            "outer_list": (rep.get("clustering") or {}).get("outer_list")}


def run(seeds: list[int], size: int) -> None:
    done = set()
    if OUT.is_file():
        done = {(r["seed"], r["preset"]) for r in map(json.loads, OUT.read_text().splitlines())}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    for seed in seeds:
        if {(seed, "E"), (seed, "F")} <= done:
            continue
        with tempfile.TemporaryDirectory(prefix="vnxeval-", dir=os.environ.get("VNX_EVAL_TMP")) as tmp:
            work = Path(tmp)
            truth = prepare(work, seed, size)
            for preset in ("E", "F") if seed % 2 == 0 else ("F", "E"):
                load = os.getloadavg()[0]
                p = subprocess.run([sys.executable, str(Path(__file__)), "one", "--work", str(work), "--preset", preset],
                                   capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
                if p.returncode != 0:
                    raise SystemExit(f"harness failure seed {seed} {preset}: {p.stderr[-2000:]}")
                row = json.loads(p.stdout.strip().splitlines()[-1])
                row.update(seed=seed, size=size, commit=commit, load1_start=load, truth_sha256=truth,
                           exact=row["output_sha256"] == truth, label=LABEL, time=time.time())
                row["false_success"] = row["status"] == "SUCCESS" and not row["exact"]
                with open(OUT, "a") as f:
                    f.write(json.dumps(row) + "\n")
                print(seed, preset, row["status"], row["exact"], row["seconds"], flush=True)


def summary() -> dict:
    from math import comb
    rows = [json.loads(line) for line in OUT.read_text().splitlines()]
    by: dict = {}
    for r in rows:
        by.setdefault(r["seed"], {})[r["preset"]] = r
    pairs = {s: v for s, v in by.items() if {"E", "F"} <= set(v)}
    ok = {p: sum(v[p]["status"] == "SUCCESS" and v[p]["exact"] for v in pairs.values()) for p in ("E", "F")}
    b = sum(v["E"]["status"] == "SUCCESS" and v["F"]["status"] != "SUCCESS" for v in pairs.values())
    c = sum(v["F"]["status"] == "SUCCESS" and v["E"]["status"] != "SUCCESS" for v in pairs.values())
    n = b + c
    p_mcnemar = min(1.0, 2 * sum(comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n) if n else 1.0
    both = [v for v in pairs.values() if v["E"]["status"] == "SUCCESS" and v["F"]["status"] == "SUCCESS"]
    out = {"label": LABEL, "seeds": sorted(pairs), "n": len(pairs), "success_exact": ok,
           "false_success": sum(r["false_success"] for r in rows), "E_only": b, "F_only": c,
           "mcnemar_exact_p": round(p_mcnemar, 4),
           "identical_where_both_succeed": all(v["E"]["output_sha256"] == v["F"]["output_sha256"] for v in both),
           "seconds_median": {p: sorted(v[p]["seconds"] for v in pairs.values())[len(pairs) // 2] for p in ("E", "F")},
           "commit": sorted({r["commit"] for r in rows}),
           "acceptance": {"no_false_success": sum(r["false_success"] for r in rows) == 0, "F_never_loses": b == 0,
                          "identical_where_both_succeed": None}}
    out["acceptance"]["identical_where_both_succeed"] = out["identical_where_both_succeed"]
    (HERE / "results" / "summary.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "one", "summary"])
    ap.add_argument("--seeds", default="93020-93029")
    ap.add_argument("--size", type=int, default=1 << 20)
    ap.add_argument("--work", type=Path)
    ap.add_argument("--preset")
    a = ap.parse_args(argv)
    if a.cmd == "one":
        print(json.dumps(one(a.work, a.preset)))
    elif a.cmd == "run":
        lo, _, hi = a.seeds.partition("-")
        run(list(range(int(lo), int(hi or lo) + 1)), a.size)
    else:
        print(json.dumps(summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
