"""PHASE 13: standardized benchmark suites (benchmarks/<suite>.yaml), scaled to the hardware profile.

For each input size the real CLI runs store → encode → decode → restore → extract (random access through the DNA
index), measuring per stage wall time, CPU seconds and peak RSS (wait4), plus compression ratio, DNA expansion
(nt per input byte, net bits per base), ECC overhead (parity strands / data strands), and SHA-256 identity.
Recovery/failure rate comes from seeded channel trials (experiments.run_trial).

Sizes larger than MAX_BENCHMARK_INPUT, or whose working set (≈ 8 × input) exceeds the disk budget, are SKIPPED
with the reason recorded — the suite never tries to exceed the machine's safe envelope.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from . import experiments, paths, profile
from .experiments import _sha, _stage, cli_argv
from .sysinfo import disk

SIZES = {"1KB": 1_000, "10KB": 10_000, "100KB": 100_000, "1MB": 1_000_000, "10MB": 10_000_000,
         "100MB": 100_000_000, "1GB": 1_000_000_000}


def load_suite(name: str) -> dict[str, Any]:
    return yaml.safe_load((paths.REPO / "benchmarks" / f"{name}.yaml").read_text())


def fits(size: int, prof: dict[str, Any] | None = None) -> tuple[bool, str]:
    max_in = profile.limit("MAX_BENCHMARK_INPUT", 10_000_000, prof)
    budget = min(profile.limit("DISK_WORK_BUDGET", 10 * 2**30, prof),
                 disk(str(paths.HOME))["free"] - profile.limit("DISK_FREE_FLOOR", 15 * 2**30, prof))
    if size > max_in:
        return False, f"SKIPPED: {size} B > MAX_BENCHMARK_INPUT {max_in} B (hardware profile)"
    if 8 * size > budget:
        return False, f"SKIPPED: working set ≈ {8 * size >> 20} MiB > disk budget {budget >> 20} MiB"
    return True, ""


def bench_size(label: str, size: int, workers: int, seed: int, work: Path) -> dict[str, Any]:
    j = ["--workers", str(workers)]
    log = work / "stages.log"
    st: dict[str, Any] = {}
    steps = [("generate", ["benchmark", "generate", "--size", str(size), "--seed", str(seed), "-o", "in.dat", "--force"]),
             ("store", ["store", "in.dat", "-o", "a.vxdna", "--no-encrypt", *j, "--report", "store.json"]),
             ("encode", ["encode", "a.vxdna", "-o", "s.fasta", *j, "--report", "encode.json"]),
             ("decode", ["decode", "s.fasta", "-o", "b.vxdna", *j, "--report", "decode.json"]),
             ("restore", ["restore", "b.vxdna", "-o", "out.dat"]),
             ("extract_dna", ["extract", "s.fasta", "-o", "x.bin", "--offset", str(size // 2), "--length", str(min(1000, size // 4) or 1)])]
    for name, argv in steps:
        st[name] = _stage([*cli_argv(), *argv], work, log)
        if st[name]["exit"] != 0:
            return {"size_label": label, "bytes": size, "result": "FAIL", "failed_stage": name, "stages": st,
                    "log_tail": log.read_text(errors="replace")[-800:]}
    enc = json.loads((work / "encode.json").read_text())
    eff = enc.get("efficiency", {})
    stored = eff.get("stored_bytes")
    data_strands = (eff.get("data_and_parity_strands") or 0) - (eff.get("parity_strands") or 0)
    identical = _sha(work / "in.dat") == _sha(work / "out.dat")

    def mbps(stage: str) -> float | None:
        s = st[stage]["seconds"]
        return round(size / 1e6 / s, 3) if s else None

    return {"size_label": label, "bytes": size, "result": "PASS" if identical else "FAIL",
            "sha256_identical": identical, "compression_ratio": round(size / stored, 4) if stored else None,
            "dna_bases": eff.get("dna_bases_total"), "bases_per_input_byte": eff.get("bases_per_original_byte"),
            "net_bits_per_base": eff.get("net_bits_per_base"),
            "ecc_overhead": round(eff["parity_strands"] / data_strands, 4) if data_strands else None,
            "outer_code": eff.get("outer_code"), "strands": eff.get("strands_total"),
            "throughput_MBps": {k: mbps(k) for k in ("store", "encode", "decode", "restore")},
            "random_access_s": st["extract_dna"]["seconds"],
            "peak_rss_bytes": {k: v["peak_rss_bytes"] for k, v in st.items()},
            "cpu_seconds": {k: v["cpu_seconds"] for k, v in st.items()}, "workers": workers}


def recovery(cfg: dict[str, Any], workers: int, work: Path) -> dict[str, Any]:
    trials = [{"name": f"recovery:seed={s}", "seed": s, **cfg["channel"], "size": cfg.get("size", "32KB")} for s in cfg["seeds"]]
    res = [experiments.run_trial(t, work / f"r{i}", workers) for i, t in enumerate(trials)]
    n = len(res)
    rec = sum(r["outcome"] == "RECOVERED" for r in res)
    wrong = sum(r["outcome"] == "WRONG_OUTPUT" for r in res)
    return {"channel": cfg["channel"], "size": cfg.get("size", "32KB"), "trials": n, "recovered": rec,
            "recovery_rate": round(rec / n, 4) if n else None, "failure_rate": round((n - rec) / n, 4) if n else None,
            "undetected_corruption": wrong}


def run_dna(suite_name: str = "standard", workers: int | None = None) -> dict[str, Any]:
    suite = load_suite(suite_name)
    prof = profile.load()
    workers = workers or min(int(suite.get("workers", 4)), int(profile.limit("SAFE_CPU_THREADS", 2, prof)))
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = paths.REPORTS / "benchmarks" / suite_name / run_id
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for label in suite["sizes"]:
        size = SIZES[label]
        ok, why = fits(size, prof)
        if not ok:
            rows.append({"size_label": label, "bytes": size, "result": why})
            continue
        work = Path(tempfile.mkdtemp(prefix=f"bench-{label}-", dir=paths.run_dir()))
        try:
            rows.append(bench_size(label, size, workers, int(suite.get("seed", 42)), work))
        finally:
            shutil.rmtree(work, ignore_errors=True)
    rec = None
    if suite.get("recovery"):
        work = Path(tempfile.mkdtemp(prefix="bench-recovery-", dir=paths.run_dir()))
        try:
            rec = recovery(suite["recovery"], workers, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    result = {"schema": "vnxdna.benchmark/1", "suite": suite_name, "run_id": run_id, "measured": True,
              "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "workers": workers,
              "cli": " ".join(cli_argv()), "repo_src": str(paths.REPO / "src"),
              "host_profile": {k: prof.get("host", {}).get("cpu", {}).get(k) for k in ("model", "logical_cpus")},
              "sizes": rows, "recovery": rec,
              "pass": all(r["result"] in ("PASS",) or str(r["result"]).startswith("SKIPPED") for r in rows)
                      and (rec is None or rec["undetected_corruption"] == 0)}
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    (out / "result.md").write_text(render(result))
    latest = paths.REPORTS / "benchmarks" / suite_name / "latest.json"
    latest.write_text(json.dumps(result, indent=2) + "\n")
    return {"run_dir": str(out), **result}


def render(r: dict[str, Any]) -> str:
    lines = [f"# Benchmark `{r['suite']}` — {r['run_id']}", "",
             f"*Measured on {r['host_profile'].get('model')} ({r['host_profile'].get('logical_cpus')} vCPU), workers={r['workers']}. "
             "Generated by `vnxdna benchmark --dna`; never edit by hand.*", "",
             "| size | result | compression ratio | nt per input byte | input bits per nt (after compression; can exceed 2) | ECC overhead (parity/data strands) | store MB/s | encode MB/s | decode MB/s | restore MB/s | random access s | peak RSS MiB (max stage) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in r["sizes"]:
        if "throughput_MBps" not in s:
            lines.append(f"| {s['size_label']} | {s['result']} |" + " |" * 10)
            continue
        t = s["throughput_MBps"]
        lines.append(f"| {s['size_label']} | {s['result']} | {s['compression_ratio']} | {s['bases_per_input_byte']:.3f} | "
                     f"{s['net_bits_per_base']:.3f} | {s['ecc_overhead']} | {t['store']} | {t['encode']} | {t['decode']} | "
                     f"{t['restore']} | {s['random_access_s']} | {max(s['peak_rss_bytes'].values()) / 2**20:.0f} |")
    if r.get("recovery"):
        rc = r["recovery"]
        lines += ["", f"Recovery ({rc['size']}, channel {rc['channel']}): {rc['recovered']}/{rc['trials']} recovered, "
                  f"recovery rate {rc['recovery_rate']}, failure rate {rc['failure_rate']}, undetected corruption {rc['undetected_corruption']}."]
    return "\n".join(lines) + "\n"


def run_system(quick: bool = True, write: bool = True) -> dict[str, Any]:
    """System benchmark = the hardware profile measurement. ``write`` updates hardware-capability.yaml; otherwise the
    result only goes to reports/benchmarks/system/ (so a quick validation run never replaces the full profile)."""
    prof = profile.build_profile(quick=quick)
    if write:
        profile.write(prof)
    out = paths.REPORTS / "benchmarks" / "system"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{dt.datetime.now():%Y%m%d-%H%M%S}.json").write_text(json.dumps(prof, indent=2, default=str) + "\n")
    return {"measured": prof["measured"], "limits": {k: v["value"] for k, v in prof["limits"].items()}}
