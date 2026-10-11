"""Hybrid architecture: final v9.0.0 vs arch/hybrid-native comparison of encoding and decoding (SIMULATED channels).

Configurations: ``v9`` (BASE_TREE, the v9.0.0 reference), ``v10`` (MID_TREE, optional: the branch base cc42747, to
separate the V10 work from this branch), ``arch`` (this tree) and, for encode scenarios, ``arch-strand-ref`` (this tree
with ``VNXDNA_STRAND_BACKEND=reference``: the native strand builder's share).

Per (scenario, seed) the inputs are prepared once and shared: random input data, a container built by the *base* tree,
and (decode scenarios) one read file simulated with this tree's v4 channel from the base tree's strands. Each tree then
encodes and/or decodes in its own subprocess (``PYTHONPATH=<tree>/src``, checked), in an order that alternates per seed
(even seed: listed order, odd seed: reversed). Recorded per run: seconds, outcome, SHA-256 of the output (FASTA for encode, recovered
container for decode), the summed RSS peak of the process tree (sampled every 0.5 s, includes interpreter start-up),
the host load and the native backends of the tree.

Acceptance (fixed before the run): byte-identical FASTA from both trees for every encode; identical outcome and output
on every decode; 0 false success. Speed: paired per-seed ratio v9/config, geometric mean and range.

    BASE_TREE=/root/vnx-dna-lab/v9-base MID_TREE=/root/vnx-dna-lab/v10-base PYTHONPATH=src \
        python experiments/arch/compare.py run [--scenarios a,b]
    PYTHONPATH=src python experiments/arch/compare.py summary

The nanopore-like stress case (D13-F1, coverage 10, decoder E) uses the V10 harness unchanged:
    BASE_TREE=/root/vnx-dna-lab/v9-base VNX_OPT_OUT=experiments/arch/results/nanopore.jsonl \
        PYTHONPATH=src python experiments/v10/opt/bench.py run --size 1m --seeds 93000-93002
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = Path(os.environ.get("VNX_ARCH_OUT") or HERE / "results" / "compare.jsonl")
MIB = 1 << 20

#: name: (kind, input bytes, workers, channel parameters or None, seeds)
SCENARIOS = {
    "encode-w1": ("encode", 16 * MIB, 1, None, range(95000, 95005)),
    "encode-w4": ("encode", 16 * MIB, 4, None, range(95010, 95015)),
    "clean": ("decode", 4 * MIB, 4, {"coverage": 3}, range(95100, 95105)),
    "substitution": ("decode", 4 * MIB, 4, {"substitution_rate": 0.02, "coverage": 5}, range(95200, 95205)),
    "indel": ("decode", 4 * MIB, 4, {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002,
                                     "coverage": 5}, range(95300, 95305)),
    "low-coverage": ("decode", 4 * MIB, 4, {"substitution_rate": 0.005, "coverage": 2}, range(95400, 95405)),
    "indel-w1": ("decode", 4 * MIB, 1, {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002,
                                        "coverage": 5}, range(95500, 95503)),
    "indel-w2": ("decode", 4 * MIB, 2, {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002,
                                        "coverage": 5}, range(95600, 95603)),
}


def _load(name: str, rel: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------------------------------------------- in the subprocess
def one(step: str, work: Path, label: str, workers: int) -> dict:
    import vnxdna
    from vnxdna.v4 import decoder as de
    from vnxdna.v4 import encoder as en
    row = {"vnxdna_file": vnxdna.__file__, "native": {k: v["backend"] for k, v in vnxdna.native_status()["kernels"].items()},
           "stale_libraries": [k for k, v in vnxdna.native_status()["kernels"].items() if v.get("library_older_than_source")]}
    t = time.perf_counter()
    if step == "encode":
        out = work / f"s-{label}.fasta"
        en.encode_container(work / "a.vnx", out, en.DNAOptions(workers=workers))
        row.update(seconds=time.perf_counter() - t, outcome="ENCODED", output_sha256=_sha(out))
    else:
        out = work / f"o-{label}.vnx"
        try:
            res = de.decode_reads(work / "r.fastq", out, de.DecodeOptions(workers=workers))
            row["outcome"] = res.status
            row["peak_rss_bytes"] = (res.report or {}).get("peak_rss_bytes")
        except Exception as error:  # noqa: BLE001 - an explicit failure is an outcome, recorded as such
            row["outcome"] = f"ERROR:{type(error).__name__}"
        row["seconds"] = time.perf_counter() - t
        row["output_sha256"] = _sha(out) if out.is_file() else None
        row["matches_input"] = row["output_sha256"] == _sha(work / "a.vnx") if out.is_file() else False
    return row


# ---------------------------------------------------------------------------------------------------- driver
def _prepare(work: Path, base: Path, kind: str, size: int, channel: dict | None, seed: int) -> None:
    code = ("import sys; from pathlib import Path; from vnxdna.v4 import archive as ar, datagen; w = Path(sys.argv[1]); "
            "datagen.generate(w / 'in.bin', int(sys.argv[2]), 'random', int(sys.argv[3])); "
            "ar.build_archive([w / 'in.bin'], w / 'a.vnx', ar.ArchiveOptions())")
    subprocess.run([sys.executable, "-c", code, str(work), str(size), str(seed)], check=True,
                   env={**os.environ, "PYTHONPATH": str(base / "src")})
    if kind == "decode":
        from vnxdna.v4 import channel as ch
        from vnxdna.v4 import encoder as en
        en.encode_container(work / "a.vnx", work / "s.fasta", en.DNAOptions())
        ch.simulate_file(work / "s.fasta", work / "r.fastq", ch.ChannelConfig(seed=seed + 7, **channel))


def configs(base: Path, mid: Path | None, kind: str) -> dict:
    """label -> (tree, extra environment)."""
    out = {"v9": (base, {})}
    if mid is not None:
        out["v10"] = (mid, {})
    out["arch"] = (ROOT, {})
    if kind == "encode":
        out["arch-strand-ref"] = (ROOT, {"VNXDNA_STRAND_BACKEND": "reference"})
    return out


def run(names: list[str], base: Path, mid: Path | None) -> None:
    H = _load("v10_opt_bench", "experiments/v10/opt/bench.py")
    commits = {t: _git(t, "rev-parse", "HEAD") for t in {base, ROOT, *([mid] if mid else [])}}
    done = set()
    if OUT.is_file():
        for line in OUT.read_text().splitlines():
            r = json.loads(line)
            done.add((r["scenario"], r["seed"], r["config"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for name in names:
        kind, size, workers, channel, seeds = SCENARIOS[name]
        trees = configs(base, mid, kind)
        for seed in seeds:
            if all((name, seed, c) in done for c in trees):
                continue
            with tempfile.TemporaryDirectory(prefix="vnxarch-", dir=os.environ.get("VNX_ARCH_TMP")) as tmp:
                work = Path(tmp)
                _prepare(work, base, kind, size, channel, seed)
                order = list(trees) if seed % 2 == 0 else list(trees)[::-1]
                rows = {}
                for c in order:
                    load = os.getloadavg()[0]
                    tree, extra = trees[c]
                    env = {**os.environ, **extra, "PYTHONPATH": str(tree / "src")}
                    p, tree_peak = H._run_sampled([sys.executable, str(Path(__file__)), "one", "--step", kind, "--work",
                                                   str(work), "--label", c, "--workers", str(workers)], env)
                    if p.returncode != 0:
                        raise H.HarnessError(f"{c} subprocess failed: {p.stderr[-2000:]}")
                    row = json.loads(p.stdout.strip().splitlines()[-1])
                    if not row["vnxdna_file"].startswith(str(tree)):
                        raise H.HarnessError(f"{c} imported {row['vnxdna_file']}, not its tree {tree}")
                    rows[c] = {"scenario": name, "kind": kind, "seed": seed, "size": size, "workers": workers,
                               "channel": channel, "config": c, "commit": commits[tree], "env": extra,
                               "load1_start": load,
                               "time": time.time(), "tree_rss_peak_bytes": tree_peak, **row}
                for c in order:
                    with open(OUT, "a") as f:
                        f.write(json.dumps(rows[c]) + "\n")
                same = len({(r["output_sha256"], r["outcome"]) for r in rows.values()}) == 1
                print(f"{name} seed {seed}: " + " | ".join(f"{c} {r['seconds']:.2f}s {r['outcome']}" for c, r in rows.items())
                      + f" | identical={same}", flush=True)


def summary() -> dict:
    import math
    rows = [json.loads(line) for line in OUT.read_text().splitlines()]
    by: dict = {}
    for r in rows:
        by.setdefault(r["scenario"], {}).setdefault(r["seed"], {})[r["config"]] = r
    out = {"host": _host(), "scenarios": {}}
    for name, seeds in by.items():
        labels = sorted({c for s in seeds.values() for c in s}, key=lambda c: ["v9", "v10", "arch", "arch-strand-ref"].index(c))
        runs = [s for s in seeds.values() if set(labels) <= set(s)]
        if not runs or "v9" not in labels:
            continue
        first = runs[0]["v9"]
        size = first["size"]
        sec = {c: [r[c]["seconds"] for r in runs] for c in labels}
        rec = {"kind": first["kind"], "workers": first["workers"], "channel": first["channel"], "input_mib": size / MIB,
               "n": len(runs), "commits": {c: runs[0][c]["commit"] for c in labels},
               "env": {c: runs[0][c].get("env", {}) for c in labels},
               "identical_outcome_and_output": all(len({(r[c]["output_sha256"], r[c]["outcome"]) for c in labels}) == 1
                                                   for r in runs),
               "outcomes": {c: sorted({r[c]["outcome"] for r in runs}) for c in labels},
               "false_success": sum(r[c]["outcome"] == "SUCCESS" and not r[c].get("matches_input", True)
                                    for r in runs for c in labels),
               "seconds_median": {c: round(statistics.median(v), 3) for c, v in sec.items()},
               "seconds_range": {c: [round(min(v), 3), round(max(v), 3)] for c, v in sec.items()},
               "mib_per_s_median": {c: round(size / MIB / statistics.median(v), 3) for c, v in sec.items()},
               "tree_rss_peak_mib_median": {c: round(statistics.median(r[c]["tree_rss_peak_bytes"] / MIB for r in runs), 1)
                                            for c in labels},
               "load1_range": [round(min(r[c]["load1_start"] for r in runs for c in labels), 2),
                               round(max(r[c]["load1_start"] for r in runs for c in labels), 2)],
               "native": {c: runs[0][c]["native"] for c in labels},
               "stale_libraries": {c: sorted({k for r in runs for k in r[c]["stale_libraries"]}) for c in labels},
               "speedup_vs_v9_geomean": {}, "speedup_vs_v9_range": {}}
        for c in labels[1:]:
            ratio = [r["v9"]["seconds"] / r[c]["seconds"] for r in runs]
            rec["speedup_vs_v9_geomean"][c] = round(math.exp(statistics.fmean(math.log(x) for x in ratio)), 3)
            rec["speedup_vs_v9_range"][c] = [round(min(ratio), 3), round(max(ratio), 3)]
        out["scenarios"][name] = rec
    path = OUT.with_name(OUT.stem + "-summary.json")
    path.write_text(json.dumps(out, indent=2) + "\n")
    return out


def _host() -> dict:
    import platform
    from vnxdna.core.concurrency import available_cpus
    cpu = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")), platform.processor())
    return {"cpu": cpu, "logical_cpus": os.cpu_count(), "available_cpus": available_cpus(),
            "python": platform.python_version(), "kernel": platform.release()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "one", "summary"])
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--step")
    ap.add_argument("--work", type=Path)
    ap.add_argument("--label")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    if a.cmd == "one":
        print(json.dumps(one(a.step, a.work, a.label, a.workers)))
    elif a.cmd == "run":
        base = Path(os.environ.get("BASE_TREE", "")).resolve()
        if not (base / "src" / "vnxdna").is_dir():
            raise SystemExit("set BASE_TREE to the v9.0.0 checkout")
        mid = Path(os.environ["MID_TREE"]).resolve() if os.environ.get("MID_TREE") else None
        if mid is not None and not (mid / "src" / "vnxdna").is_dir():
            raise SystemExit("MID_TREE is not a VNX-DNA checkout")
        run([s for s in a.scenarios.split(",") if s], base, mid)
    else:
        print(json.dumps(summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
