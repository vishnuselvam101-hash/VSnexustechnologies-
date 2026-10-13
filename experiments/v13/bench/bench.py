"""V12 (build/v12 6bf7749) vs V13 (build/v13) end-to-end benchmark, paired per seed. SIMULATED channel data only.

Same method as experiments/v11/bench/bench.py (paired per seed, configuration order reversed on odd seeds, one discarded
warm-up per configuration and scenario, process-tree RSS sampler), with the V12 scenario set (unchanged, docs/V13_PLAN.md §2): worker scaling (w1/w2/w4/w8),
reverse-complement, dropout, small-input latency and a 16 MiB input. Every configuration decodes/encodes the same input.

    BASE_TREE=/root/vnx-dna-lab/v13-base PYTHONPATH=src python experiments/v13/bench/bench.py run [--scenarios a,b]
    PYTHONPATH=src python experiments/v13/bench/bench.py summary

Configurations: ``v12`` (BASE_TREE, a checkout of build/v12 6bf7749) and ``v13`` (this tree). Run with both trees at the same
commit, the result is an A/A noise floor. Output (VNX_BENCH_OUT, default next to this file): e2e.jsonl (measured rows),
warmup.jsonl (discarded rows), e2e-summary.json.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MIB = 1 << 20
KIB = 1 << 10
INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002, "coverage": 5}
#: name: (kind, input bytes, workers, channel parameters or None, seeds)
SCENARIOS = {
    "indel-w1": ("decode", 4 * MIB, 1, INDEL, range(95500, 95505)),
    "indel-w2": ("decode", 4 * MIB, 2, INDEL, range(95500, 95505)),
    "indel-w4": ("decode", 4 * MIB, 4, INDEL, range(95300, 95305)),
    "indel-w8": ("decode", 4 * MIB, 8, INDEL, range(95300, 95305)),
    "indel-rc-w4": ("decode", 4 * MIB, 4, {**INDEL, "reverse_complement_rate": 0.5}, range(97000, 97005)),
    "indel-dropout-w4": ("decode", 4 * MIB, 4, {**INDEL, "dropout_rate": 0.05}, range(97100, 97105)),
    "clean-w4": ("decode", 4 * MIB, 4, {"coverage": 3}, range(95100, 95105)),
    "substitution-w4": ("decode", 4 * MIB, 4, {"substitution_rate": 0.02, "coverage": 5}, range(95200, 95205)),
    "indel-64k-w1": ("decode", 64 * KIB, 1, INDEL, range(97200, 97205)),
    "indel-16m-w4": ("decode", 16 * MIB, 4, INDEL, range(97300, 97303)),
    "encode-1m-w1": ("encode", 1 * MIB, 1, None, range(96000, 96005)),
    "encode-4m-w1": ("encode", 4 * MIB, 1, None, range(96020, 96025)),
    "encode-4m-w4": ("encode", 4 * MIB, 4, None, range(96030, 96035)),
}
OUT = Path(os.environ.get("VNX_BENCH_OUT") or HERE / "e2e.jsonl")
WARM = OUT.with_name("warmup.jsonl")


def _load(name: str, rel: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


V11B = _load("v11_bench", "experiments/v11/bench/bench.py")      # _step, _boot_ci, compare (C), RSS sampler (H)
C = V11B.C
LABELS = ("v12", "v13")


def run(names: list[str], base: Path) -> None:
    trees = {"v12": (base, {}), "v13": (ROOT, {})}
    commits = {c: C._git(t, "rev-parse", "HEAD") for c, (t, _) in trees.items()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for name in names:
        kind, size, workers, channel, seeds = SCENARIOS[name]
        for i, seed in enumerate(seeds):
            with tempfile.TemporaryDirectory(prefix="vnxv13-", dir=os.environ.get("VNX_ARCH_TMP")) as tmp:
                work = Path(tmp)
                C._prepare(work, base, kind, size, channel, seed)          # inputs always come from the V12 tree
                if i == 0:                       # warm-up: page cache, imports, library loads; discarded
                    for c, (tree, extra) in trees.items():
                        w = V11B._step(c, tree, extra, kind, work, workers, label=f"warm-{c}")
                        with open(WARM, "a") as f:
                            f.write(json.dumps({"scenario": name, "seed": seed, **w}) + "\n")
                order = list(trees) if seed % 2 == 0 else list(trees)[::-1]
                rows = {}
                for c in order:
                    tree, extra = trees[c]
                    rows[c] = {"scenario": name, "kind": kind, "seed": seed, "size": size, "workers": workers,
                               "channel": channel, "commit": commits[c], "order": order,
                               **V11B._step(c, tree, extra, kind, work, workers)}
                with open(OUT, "a") as f:
                    for c in order:
                        f.write(json.dumps(rows[c]) + "\n")
                same = len({(r["output_sha256"], r["outcome"]) for r in rows.values()}) == 1
                print(f"{name} seed {seed}: " + " | ".join(f"{c} {r['seconds']:.2f}s {r['outcome']}"
                                                         for c, r in rows.items()) + f" | identical={same}", flush=True)


def summary() -> dict:
    rows = [json.loads(line) for line in OUT.read_text().splitlines()]
    by: dict = {}
    for r in rows:
        by.setdefault(r["scenario"], {}).setdefault(r["seed"], {})[r["config"]] = r
    out = {"label": "SIMULATED", "host": C._host(), "method": "paired per seed, alternating order, 1 warm-up per "
           "config per scenario discarded; ratio = v12 seconds / v13 seconds (>1 = V13 faster); 95 % CI = percentile "
           "bootstrap of the median ratio (10000 resamples, seed 11); with n = 5 it equals the min-max range",
           "scenarios": {}}
    for name in SCENARIOS:
        seeds = by.get(name)
        if not seeds:
            continue
        runs = [s for s in seeds.values() if set(LABELS) <= set(s)]
        first = runs[0]["v12"]
        sec = {c: [r[c]["seconds"] for r in runs] for c in LABELS}
        ratio = [r["v12"]["seconds"] / r["v13"]["seconds"] for r in runs]
        rss = [r["v13"]["tree_rss_peak_bytes"] / r["v12"]["tree_rss_peak_bytes"] for r in runs
               if r["v13"]["tree_rss_peak_bytes"] and r["v12"]["tree_rss_peak_bytes"]]
        rec = {"kind": first["kind"], "workers": first["workers"], "channel": first["channel"],
               "input_bytes": first["size"], "n": len(runs), "seeds": sorted(r["v12"]["seed"] for r in runs),
               "commits": {c: runs[0][c]["commit"] for c in LABELS},
               "identical_outcome_and_output": all(len({(r[c]["output_sha256"], r[c]["outcome"]) for c in LABELS}) == 1
                                                   for r in runs),
               "outcomes": {c: sorted({r[c]["outcome"] for r in runs}) for c in LABELS},
               "matches_input": {c: sum(bool(r[c].get("matches_input", True)) for r in runs) for c in LABELS},
               "false_success": {c: sum(r[c]["outcome"] == "SUCCESS" and not r[c].get("matches_input", True)
                                        for r in runs) for c in LABELS},
               "seconds_median": {c: round(statistics.median(v), 3) for c, v in sec.items()},
               "seconds_range": {c: [round(min(v), 3), round(max(v), 3)] for c, v in sec.items()},
               "tree_rss_peak_mib_median": {c: round(statistics.median(r[c]["tree_rss_peak_bytes"] / MIB for r in runs), 1)
                                            for c in LABELS},
               "load1_range": [round(min(r[c]["load1_start"] for r in runs for c in LABELS), 2),
                               round(max(r[c]["load1_start"] for r in runs for c in LABELS), 2)],
               "native": {c: runs[0][c]["native"] for c in LABELS},
               "stale_libraries": {c: sorted({k for r in runs for k in r[c]["stale_libraries"]}) for c in LABELS},
               "ratio_v12_over_v13": {"median": round(statistics.median(ratio), 3), "ci95": V11B._boot_ci(ratio),
                                      "per_seed": [round(x, 3) for x in ratio],
                                      "rss_ratio_median": round(statistics.median(rss), 3) if rss else None,
                                      "rss_ratio_n": len(rss)}}
        out["scenarios"][name] = rec
    # worker scaling of each tree on the 4 MiB indel decode (w1 seeds 95500.., w4/w8 seeds 95300..: medians, not pairs)
    sc = {k: out["scenarios"][k]["seconds_median"] for k in ("indel-w1", "indel-w2", "indel-w4", "indel-w8")
          if k in out["scenarios"]}
    if "indel-w1" in sc:
        out["scaling_vs_w1"] = {c: {k: round(sc["indel-w1"][c] / v[c], 3) for k, v in sc.items()} for c in LABELS}
    path = OUT.with_name(OUT.stem + "-summary.json")
    path.write_text(json.dumps(out, indent=2) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a.scenarios.split(","), Path(os.environ.get("BASE_TREE", "/root/vnx-dna-lab/v13-base")))
    else:
        s = summary()
        for name, r in s["scenarios"].items():
            print(f"{name:18s} v12 {r['seconds_median']['v12']:7.3f}s  v13 {r['seconds_median']['v13']:7.3f}s  "
                  f"ratio {r['ratio_v12_over_v13']['median']:.3f} {r['ratio_v12_over_v13']['ci95']}  "
                  f"identical={r['identical_outcome_and_output']}  outcomes={r['outcomes']['v13']}  "
                  f"false_success={r['false_success']}")
        if "scaling_vs_w1" in s:
            print("scaling vs w1:", json.dumps(s["scaling_vs_w1"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
