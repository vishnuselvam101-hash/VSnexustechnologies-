"""PHASE 12: reproducible DNA-storage experiment framework.

An experiment suite (experiments/<suite>.yaml) is a list of trials. Each trial runs the real CLI stage by stage:

    benchmark generate → store [constraints] → encode → sequence [channel] → (cluster → consensus) → decode → restore

and records: seed, input size, store config, error model and rates, number of reads, recovered bytes, failed bytes,
input/output SHA-256, per-stage runtime, peak RSS and CPU seconds (from wait4), and the outcome:

    RECOVERED       output SHA-256 == input SHA-256
    FAILED_CLEAN    a stage refused (exit ≠ 0) — loss was *detected*
    WRONG_OUTPUT    restore succeeded but bytes differ — undetected corruption (must never happen)

Results go to /opt/vnx-dna/experiments/<suite>/<run-id>/results.jsonl + summary.md. Same suite + seeds ⇒ same
input hashes and (for a deterministic codec) the same outcomes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from . import paths

# The CLI under test: by default this checkout's own src/ (so a gate in an agent workspace measures that workspace's
# code); VNXDNA_CLI=/path/to/vnx-dna measures another installation (e.g. the pristine V3 baseline venv).
CLI_ENV = os.environ.get("VNXDNA_CLI")


def cli_argv() -> list[str]:
    return [CLI_ENV] if CLI_ENV else [sys.executable, "-m", "vnxdna"]


def cli_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "VNXDNA_KEY"}
    if not CLI_ENV:
        env["PYTHONPATH"] = str(paths.REPO / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env
SEQ_KEYS = ["coverage", "coverage_model", "abundance_sigma", "dropout_rate", "synthesis_substitution_rate",
            "synthesis_insertion_rate", "synthesis_deletion_rate", "substitution_rate", "insertion_rate", "deletion_rate",
            "duplication_rate", "truncation_rate", "n_rate", "reverse_complement_rate", "burst_rate", "burst_length", "burst_kind"]
STORE_KEYS = ["profile", "chunk_size", "gc_min", "gc_max", "max_homopolymer", "gc_window", "forbid_motif", "mapping",
              "inner_parity", "data_shards", "parity_shards"]
DECODE_KEYS = ["experimental_indel_repair", "max_indel", "burst_repair", "quality_erasure_below"]


def _flags(params: dict[str, Any], keys: list[str]) -> list[str]:
    out: list[str] = []
    for k in keys:
        if k not in params or params[k] is None:
            continue
        flag = "--" + k.replace("_", "-")
        v = params[k]
        if isinstance(v, bool):
            out += [flag] if v else []
        elif isinstance(v, list):
            for item in v:
                out += [flag, str(item)]
        else:
            out += [flag, str(v)]
    return out


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _stage(argv: list[str], cwd: Path, log: Path) -> dict[str, Any]:
    t0 = time.perf_counter()
    with open(log, "ab") as fh:
        fh.write(("$ " + " ".join(argv) + "\n").encode())
        fh.flush()
        p = subprocess.Popen(argv, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=cli_env())
        _, status, ru = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(status)
    return {"exit": p.returncode, "seconds": round(time.perf_counter() - t0, 3), "peak_rss_bytes": ru.ru_maxrss * 1024,
            "cpu_seconds": round(ru.ru_utime + ru.ru_stime, 3)}


def _count_reads(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, "rb") as f:
        if path.suffix == ".fastq":
            return sum(1 for _ in f) // 4
        return sum(1 for line in f if line.startswith(b">"))


def run_trial(trial: dict[str, Any], work: Path, workers: int = 2) -> dict[str, Any]:
    work.mkdir(parents=True, exist_ok=True)
    log = work / "stages.log"
    seed = int(trial.get("seed", 0))
    size = str(trial.get("size", "64KB"))
    stages: dict[str, Any] = {}
    j = ["--workers", str(workers)]

    def go(name: str, argv: list[str]) -> bool:
        stages[name] = _stage([*cli_argv(), *argv], work, log)
        return stages[name]["exit"] == 0

    ok = go("generate", ["benchmark", "generate", "--size", size, "--pattern", trial.get("pattern", "mixed"),
                         "--seed", str(seed), "-o", "in.dat", "--force"])
    in_bytes = (work / "in.dat").stat().st_size if (work / "in.dat").exists() else 0
    in_sha = _sha(work / "in.dat") if in_bytes else None
    store = {k: trial[k] for k in STORE_KEYS if k in trial}
    channel = {k: trial[k] for k in SEQ_KEYS if k in trial}
    channel.setdefault("coverage", 10)
    decode = {k: trial[k] for k in DECODE_KEYS if k in trial}
    use_consensus = trial.get("consensus", float(channel["coverage"]) > 1)
    ok = ok and go("store", ["store", "in.dat", "-o", "a.vxdna", "--no-encrypt", *j, *_flags(store, STORE_KEYS)])
    ok = ok and go("encode", ["encode", "a.vxdna", "-o", "s.fasta", *j])
    ok = ok and go("sequence", ["sequence", "s.fasta", "-o", "r.fastq", "--seed", str(seed), *_flags(channel, SEQ_KEYS)])
    reads = _count_reads(work / "r.fastq")
    src = "r.fastq"
    if ok and use_consensus:
        ok = go("cluster", ["cluster", "r.fastq", "-o", "c.jsonl"])
        ok = ok and go("consensus", ["consensus", "c.jsonl", "-o", "x.fasta"])
        src = "x.fasta"
    decoded = ok and go("decode", ["decode", src, "-o", "b.vxdna", *j, *_flags(decode, DECODE_KEYS)])
    restored = decoded and go("restore", ["restore", "b.vxdna", "-o", "out.dat"])
    out_sha = _sha(work / "out.dat") if restored and (work / "out.dat").exists() else None
    if restored and out_sha == in_sha:
        outcome, recovered = "RECOVERED", in_bytes
    elif restored:
        outcome, recovered = "WRONG_OUTPUT", 0
    else:
        outcome, recovered = "FAILED_CLEAN", 0
    failed_stage = next((n for n, s in stages.items() if s["exit"] != 0), None)
    return {"trial": trial.get("name"), "seed": seed, "input_size": size, "input_bytes": in_bytes, "pattern": trial.get("pattern", "mixed"),
            "store_config": store, "error_model": channel, "decode_options": decode, "consensus": use_consensus,
            "reads": reads, "recovered_bytes": recovered, "failed_bytes": in_bytes - recovered,
            "input_sha256": in_sha, "output_sha256": out_sha, "outcome": outcome, "failed_stage": failed_stage,
            "runtime_s": round(sum(s["seconds"] for s in stages.values()), 3),
            "peak_rss_bytes": max((s["peak_rss_bytes"] for s in stages.values()), default=0),
            "cpu_seconds": round(sum(s["cpu_seconds"] for s in stages.values()), 3), "stages": stages}


def expand(suite: dict[str, Any]) -> list[dict[str, Any]]:
    """`trials` are explicit; `matrix` expands {param: [values]} × seeds into trials."""
    base = suite.get("defaults", {})
    trials = [{**base, **t} for t in suite.get("trials", [])]
    for m in suite.get("matrix", []):
        keys = [k for k in m if isinstance(m[k], list) and k != "seeds"]
        for combo in itertools.product(*(m[k] for k in keys)):
            fixed = {k: v for k, v in m.items() if k not in keys and k not in ("seeds", "name")}
            for seed in m.get("seeds", [base.get("seed", 0)]):
                t = {**base, **fixed, **dict(zip(keys, combo)), "seed": seed}
                t["name"] = f"{m.get('name', 'm')}:" + ",".join(f"{k}={v}" for k, v in zip(keys, combo)) + f":seed={seed}"
                trials.append(t)
    return trials


def load_suite(name: str) -> dict[str, Any]:
    p = paths.REPO / "experiments" / f"{name}.yaml"
    if not p.exists():
        raise SystemExit(f"no experiment suite {p}")
    return yaml.safe_load(p.read_text())


def run_suite(name: str, workers: int | None = None, keep: bool = False, limit: int | None = None) -> dict[str, Any]:
    from . import profile
    suite = load_suite(name)
    workers = workers or min(4, int(profile.limit("SAFE_CPU_THREADS", 2)))
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(paths.HOME / "experiments" / name / run_id)
    out.mkdir(parents=True, exist_ok=True)
    trials = expand(suite)[:limit] if limit else expand(suite)
    results = []
    with open(out / "results.jsonl", "w") as fh:
        for i, t in enumerate(trials):
            work = out / "work" / f"{i:03d}"
            r = run_trial(t, work, workers)
            results.append(r)
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
            if not keep and r["outcome"] != "WRONG_OUTPUT":  # keep evidence of any undetected corruption
                shutil.rmtree(work, ignore_errors=True)
    summary = summarize(name, run_id, results, suite)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "summary.md").write_text(render(summary, results))
    return {"run_dir": str(out), **summary}


def summarize(name: str, run_id: str, results: list[dict[str, Any]], suite: dict[str, Any]) -> dict[str, Any]:
    by = {k: sum(1 for r in results if r["outcome"] == k) for k in ("RECOVERED", "FAILED_CLEAN", "WRONG_OUTPUT")}
    expect = suite.get("expect", {})
    violations = []
    for r in results:
        exp = next((e["outcome"] for e in expect.get("rules", []) if e["match"] in (r["trial"] or "")), None)
        if exp and r["outcome"] != exp:
            violations.append({"trial": r["trial"], "expected": exp, "got": r["outcome"]})
    return {"suite": name, "run_id": run_id, "trials": len(results), "outcomes": by,
            "undetected_corruption": by["WRONG_OUTPUT"], "expectation_violations": violations,
            "pass": by["WRONG_OUTPUT"] == 0 and not violations,
            "machine": os.uname().nodename, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def render(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [f"# Experiment `{summary['suite']}` — run {summary['run_id']}", "",
             f"*Generated by `vnxdna experiment {summary['suite']}`; all values measured in this run.*", "",
             f"Trials: {summary['trials']} · outcomes: {summary['outcomes']} · undetected corruption: "
             f"**{summary['undetected_corruption']}** · expectation violations: {len(summary['expectation_violations'])}", "",
             "| trial | seed | bytes | reads | outcome | failed stage | runtime s | peak RSS MiB | CPU s |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['trial']} | {r['seed']} | {r['input_bytes']} | {r['reads']} | {r['outcome']} | "
                     f"{r['failed_stage'] or ''} | {r['runtime_s']} | {r['peak_rss_bytes'] / 2**20:.0f} | {r['cpu_seconds']} |")
    return "\n".join(lines) + "\n"
