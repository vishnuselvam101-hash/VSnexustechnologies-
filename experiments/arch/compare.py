"""Hybrid architecture: final v9.0.0 vs arch/hybrid-native comparison of encoding and decoding (SIMULATED channels).

Per (scenario, seed) the inputs are prepared once and shared: random input data, a container built by the *base* tree,
and (decode scenarios) one read file simulated with this tree's v4 channel from the base tree's strands. Each tree then
encodes and/or decodes in its own subprocess (``PYTHONPATH=<tree>/src``, checked), in an order that alternates per seed
(even seed: base first). Recorded per run: seconds, outcome, SHA-256 of the output (FASTA for encode, recovered
container for decode), the summed RSS peak of the process tree (sampled every 0.5 s, includes interpreter start-up),
the host load and the native backends of the tree.

Acceptance (fixed before the run): byte-identical FASTA from both trees for every encode; identical outcome and output
on every decode; 0 false success. Speed: paired per-seed ratio base/arch, geometric mean and range.

    BASE_TREE=/root/vnx-dna-lab/v9-base PYTHONPATH=src python experiments/arch/compare.py run [--scenarios a,b]
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


def run(names: list[str], base: Path) -> None:
    H = _load("v10_opt_bench", "experiments/v10/opt/bench.py")
    trees = {"v9": base, "arch": ROOT}
    commits = {c: _git(t, "rev-parse", "HEAD") for c, t in trees.items()}
    done = set()
    if OUT.is_file():
        for line in OUT.read_text().splitlines():
            r = json.loads(line)
            done.add((r["scenario"], r["seed"], r["config"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for name in names:
        kind, size, workers, channel, seeds = SCENARIOS[name]
        for seed in seeds:
            if all((name, seed, c) in done for c in trees):
                continue
            with tempfile.TemporaryDirectory(prefix="vnxarch-", dir=os.environ.get("VNX_ARCH_TMP")) as tmp:
                work = Path(tmp)
                _prepare(work, base, kind, size, channel, seed)
                order = ["v9", "arch"] if seed % 2 == 0 else ["arch", "v9"]
                rows = {}
                for c in order:
                    load = os.getloadavg()[0]
                    env = {**os.environ, "PYTHONPATH": str(trees[c] / "src")}
                    p, tree_peak = H._run_sampled([sys.executable, str(Path(__file__)), "one", "--step", kind, "--work",
                                                   str(work), "--label", c, "--workers", str(workers)], env)
                    if p.returncode != 0:
                        raise H.HarnessError(f"{c} subprocess failed: {p.stderr[-2000:]}")
                    row = json.loads(p.stdout.strip().splitlines()[-1])
                    if not row["vnxdna_file"].startswith(str(trees[c])):
                        raise H.HarnessError(f"{c} imported {row['vnxdna_file']}, not its tree {trees[c]}")
                    rows[c] = {"scenario": name, "kind": kind, "seed": seed, "size": size, "workers": workers,
                               "channel": channel, "config": c, "commit": commits[c], "load1_start": load,
                               "time": time.time(), "tree_rss_peak_bytes": tree_peak, **row}
                for c in order:
                    with open(OUT, "a") as f:
                        f.write(json.dumps(rows[c]) + "\n")
                v9, ar = rows["v9"], rows["arch"]
                same = v9["output_sha256"] == ar["output_sha256"] and v9["outcome"] == ar["outcome"]
                print(f"{name} seed {seed}: v9 {v9['seconds']:.2f}s {v9['outcome']} | arch {ar['seconds']:.2f}s "
                      f"{ar['outcome']} | identical={same}", flush=True)


def summary() -> dict:
    import math
    rows = [json.loads(line) for line in OUT.read_text().splitlines()]
    by: dict = {}
    for r in rows:
        by.setdefault(r["scenario"], {}).setdefault(r["seed"], {})[r["config"]] = r
    out = {"host": _host(), "scenarios": {}}
    for name, seeds in by.items():
        pairs = [s for s in seeds.values() if {"v9", "arch"} <= set(s)]
        if not pairs:
            continue
        sec = {c: [p[c]["seconds"] for p in pairs] for c in ("v9", "arch")}
        ratio = [p["v9"]["seconds"] / p["arch"]["seconds"] for p in pairs]
        rss = {c: [p[c]["tree_rss_peak_bytes"] / MIB for p in pairs] for c in ("v9", "arch")}
        size = pairs[0]["v9"]["size"]
        out["scenarios"][name] = {
            "kind": pairs[0]["v9"]["kind"], "workers": pairs[0]["v9"]["workers"], "channel": pairs[0]["v9"]["channel"],
            "input_mib": size / MIB, "n": len(pairs), "commits": {c: pairs[0][c]["commit"] for c in ("v9", "arch")},
            "identical_outcome_and_output": all(p["v9"]["output_sha256"] == p["arch"]["output_sha256"]
                                                and p["v9"]["outcome"] == p["arch"]["outcome"] for p in pairs),
            "outcomes": {c: sorted({p[c]["outcome"] for p in pairs}) for c in ("v9", "arch")},
            "false_success": sum(p[c]["outcome"] == "SUCCESS" and not p[c].get("matches_input", True)
                                 for p in pairs for c in ("v9", "arch")),
            "seconds_median": {c: round(statistics.median(v), 3) for c, v in sec.items()},
            "seconds_range": {c: [round(min(v), 3), round(max(v), 3)] for c, v in sec.items()},
            "mib_per_s_median": {c: round(size / MIB / statistics.median(v), 3) for c, v in sec.items()},
            "speedup_geomean": round(math.exp(statistics.fmean(math.log(x) for x in ratio)), 3),
            "speedup_range": [round(min(ratio), 3), round(max(ratio), 3)],
            "tree_rss_peak_mib_median": {c: round(statistics.median(v), 1) for c, v in rss.items()},
            "load1_range": [round(min(p[c]["load1_start"] for p in pairs for c in ("v9", "arch")), 2),
                            round(max(p[c]["load1_start"] for p in pairs for c in ("v9", "arch")), 2)],
            "native": {c: pairs[0][c]["native"] for c in ("v9", "arch")},
            "stale_libraries": {c: sorted({k for p in pairs for k in p[c]["stale_libraries"]}) for c in ("v9", "arch")},
        }
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
        run([s for s in a.scenarios.split(",") if s], base)
    else:
        print(json.dumps(summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
