"""V10 (v10.0.0) vs V11 (build/v11) end-to-end benchmark, paired per seed. SIMULATED channel data only.

Reuses the experiments/arch/compare.py subprocess step (``one``) and the experiments/v10/opt/bench.py process-tree RSS
sampler. Per scenario: one discarded warm-up run per configuration, then one paired run per seed with the
configuration order reversed on odd seeds. Every configuration decodes/encodes the same prepared input.

    V10_TREE=/root/vnx-dna-lab/v10-rel PYTHONPATH=src python experiments/v11/bench/bench.py run
    PYTHONPATH=src python experiments/v11/bench/bench.py summary

Configurations: ``v10`` (V10_TREE), ``v11`` (this tree), ``v11-avx2`` (this tree, VNXDNA_ALIGN_BACKEND=avx2; only for
the scenarios listed in AVX2). Output: e2e.jsonl (measured rows), warmup.jsonl (discarded rows), e2e-summary.json.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MIB = 1 << 20
INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002, "coverage": 5}

#: name: (kind, input bytes, workers, channel parameters or None, seeds)
SCENARIOS = {
    "indel-w4": ("decode", 4 * MIB, 4, INDEL, range(95300, 95305)),
    "indel-w1": ("decode", 4 * MIB, 1, INDEL, range(95500, 95505)),
    "clean-w4": ("decode", 4 * MIB, 4, {"coverage": 3}, range(95100, 95105)),
    "substitution-w4": ("decode", 4 * MIB, 4, {"substitution_rate": 0.02, "coverage": 5}, range(95200, 95205)),
    "encode-1m-w1": ("encode", 1 * MIB, 1, None, range(96000, 96005)),
    "encode-1m-w4": ("encode", 1 * MIB, 4, None, range(96010, 96015)),
    "encode-4m-w1": ("encode", 4 * MIB, 1, None, range(96020, 96025)),
    "encode-4m-w4": ("encode", 4 * MIB, 4, None, range(96030, 96035)),
}
AVX2 = {"indel-w4"}
OUT = Path(os.environ.get("VNX_BENCH_OUT") or HERE / "e2e.jsonl")
WARM = OUT.with_name("warmup.jsonl")


def _load(name: str, rel: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("arch_compare", "experiments/arch/compare.py")
H = _load("v10_opt_bench", "experiments/v10/opt/bench.py")


def configs(v10: Path, name: str) -> dict:
    out = {"v10": (v10, {}), "v11": (ROOT, {})}
    if name in AVX2:
        out["v11-avx2"] = (ROOT, {"VNXDNA_ALIGN_BACKEND": "avx2"})
    return out


def _step(c: str, tree: Path, extra: dict, kind: str, work: Path, workers: int, label: str | None = None) -> dict:
    env = {**os.environ, **extra, "PYTHONPATH": str(tree / "src")}
    load = os.getloadavg()[0]
    p, peak = H._run_sampled([sys.executable, str(ROOT / "experiments/arch/compare.py"), "one", "--step", kind,
                              "--work", str(work), "--label", label or c, "--workers", str(workers)], env)
    if p.returncode != 0:
        raise H.HarnessError(f"{c} subprocess failed: {p.stderr[-2000:]}")
    row = json.loads(p.stdout.strip().splitlines()[-1])
    if not row["vnxdna_file"].startswith(str(tree)):
        raise H.HarnessError(f"{c} imported {row['vnxdna_file']}, not its tree {tree}")
    return {"config": c, "env": extra, "load1_start": load, "time": time.time(), "tree_rss_peak_bytes": peak, **row}


def run(names: list[str], v10: Path) -> None:
    commits = {t: C._git(t, "rev-parse", "HEAD") for t in (v10, ROOT)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for name in names:
        kind, size, workers, channel, seeds = SCENARIOS[name]
        trees = configs(v10, name)
        for i, seed in enumerate(seeds):
            with tempfile.TemporaryDirectory(prefix="vnxv11-", dir=os.environ.get("VNX_ARCH_TMP")) as tmp:
                work = Path(tmp)
                C._prepare(work, v10, kind, size, channel, seed)
                if i == 0:                       # warm-up: page cache, imports, library loads; discarded
                    for c, (tree, extra) in trees.items():
                        w = _step(c, tree, extra, kind, work, workers, label=f"warm-{c}")   # own output file
                        with open(WARM, "a") as f:
                            f.write(json.dumps({"scenario": name, "seed": seed, **w}) + "\n")
                order = list(trees) if seed % 2 == 0 else list(trees)[::-1]
                rows = {}
                for c in order:
                    tree, extra = trees[c]
                    rows[c] = {"scenario": name, "kind": kind, "seed": seed, "size": size, "workers": workers,
                               "channel": channel, "commit": commits[tree], "order": order,
                               **_step(c, tree, extra, kind, work, workers)}
                with open(OUT, "a") as f:
                    for c in order:
                        f.write(json.dumps(rows[c]) + "\n")
                same = len({(r["output_sha256"], r["outcome"]) for r in rows.values()}) == 1
                print(f"{name} seed {seed}: " + " | ".join(f"{c} {r['seconds']:.2f}s {r['outcome']}"
                                                         for c, r in rows.items()) + f" | identical={same}", flush=True)


def _boot_ci(x: list[float], n: int = 10000, seed: int = 11) -> list[float]:
    """Percentile bootstrap 95 % CI of the median (resampling the paired ratios)."""
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(x, k=len(x))) for _ in range(n))
    return [round(meds[int(0.025 * n)], 3), round(meds[int(0.975 * n) - 1], 3)]


def summary() -> dict:
    rows = [json.loads(line) for line in OUT.read_text().splitlines()]
    by: dict = {}
    for r in rows:
        by.setdefault(r["scenario"], {}).setdefault(r["seed"], {})[r["config"]] = r
    out = {"label": "SIMULATED", "host": C._host(), "method": "paired per seed, alternating order, 1 warm-up per "
           "config per scenario discarded; ratio = v10 seconds / config seconds (>1 = faster than V10); 95 % CI = "
           "percentile bootstrap of the median ratio (10000 resamples, seed 11)", "scenarios": {}}
    for name, seeds in by.items():
        labels = [c for c in ("v10", "v11", "v11-avx2") if any(c in s for s in seeds.values())]
        runs = [s for s in seeds.values() if set(labels) <= set(s)]
        first = runs[0]["v10"]
        sec = {c: [r[c]["seconds"] for r in runs] for c in labels}
        rec = {"kind": first["kind"], "workers": first["workers"], "channel": first["channel"],
               "input_mib": first["size"] / MIB, "n": len(runs), "seeds": sorted(r["v10"]["seed"] for r in runs),
               "commits": {c: runs[0][c]["commit"] for c in labels},
               "identical_outcome_and_output": all(len({(r[c]["output_sha256"], r[c]["outcome"]) for c in labels}) == 1
                                                   for r in runs),
               "outcomes": {c: sorted({r[c]["outcome"] for r in runs}) for c in labels},
               "matches_input": {c: all(r[c].get("matches_input", True) for r in runs) for c in labels},
               "false_success": sum(r[c]["outcome"] == "SUCCESS" and not r[c].get("matches_input", True)
                                    for r in runs for c in labels),
               "seconds_median": {c: round(statistics.median(v), 3) for c, v in sec.items()},
               "seconds_range": {c: [round(min(v), 3), round(max(v), 3)] for c, v in sec.items()},
               "tree_rss_peak_mib_median": {c: round(statistics.median(r[c]["tree_rss_peak_bytes"] / MIB for r in runs), 1)
                                            for c in labels},
               "tree_rss_peak_mib_max": {c: round(max(r[c]["tree_rss_peak_bytes"] / MIB for r in runs), 1)
                                         for c in labels},
               "load1_range": [round(min(r[c]["load1_start"] for r in runs for c in labels), 2),
                               round(max(r[c]["load1_start"] for r in runs for c in labels), 2)],
               "native": {c: runs[0][c]["native"] for c in labels},
               "stale_libraries": {c: sorted({k for r in runs for k in r[c]["stale_libraries"]}) for c in labels},
               "ratio_vs_v10": {}}
        if first["kind"] == "decode":
            rec["report_peak_rss_mib_median"] = {c: round(statistics.median((r[c].get("peak_rss_bytes") or 0) / MIB
                                                                           for r in runs), 1) for c in labels}
        for c in labels[1:]:
            ratio = [r["v10"]["seconds"] / r[c]["seconds"] for r in runs]
            # the sampler polls every 0.5 s: runs shorter than that record 0 (not measured), excluded here
            rss = [r[c]["tree_rss_peak_bytes"] / r["v10"]["tree_rss_peak_bytes"] for r in runs
                   if r[c]["tree_rss_peak_bytes"] and r["v10"]["tree_rss_peak_bytes"]]
            rec["ratio_vs_v10"][c] = {"median": round(statistics.median(ratio), 3), "ci95": _boot_ci(ratio),
                                      "range": [round(min(ratio), 3), round(max(ratio), 3)],
                                      "per_seed": [round(x, 3) for x in ratio],
                                      "rss_ratio_median": round(statistics.median(rss), 3) if rss else None,
                                      "rss_ratio_n": len(rss)}
        out["scenarios"][name] = rec
    path = OUT.with_name(OUT.stem + "-summary.json")
    path.write_text(json.dumps(out, indent=2) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    a = ap.parse_args(argv)
    if a.cmd == "run":
        v10 = Path(os.environ.get("V10_TREE", "")).resolve()
        if not (v10 / "src" / "vnxdna").is_dir():
            raise SystemExit("set V10_TREE to the v10.0.0 checkout")
        run(a.scenarios.split(","), v10)
    else:
        print(json.dumps(summary(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
