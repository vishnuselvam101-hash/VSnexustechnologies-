#!/usr/bin/env python3
"""VNX-DNA V3 baseline benchmark (clean DNA path + one noisy pipeline case).

Everything measured here is software: strands are generated sequences, the noisy case uses the seeded sequencing
simulator. No DNA is synthesised or sequenced.

The script drives the *installed* V3 CLI (`vnx-dna`) in subprocesses, each wrapped in `/usr/bin/time -v`, so wall
time, CPU time and peak RSS are those of the real command-line process. Inputs are generated deterministically
(fixed seeds) into a scratch directory that is never committed.

Usage (from the repository root, with the V3 venv active):

    python benchmarks/baseline/v3/run_v3_baseline.py                # 3 repeats, scratch in .bench-tmp/v3-baseline
    python benchmarks/baseline/v3/run_v3_baseline.py --repeats 1 --keep-data

Dependencies: Python standard library + numpy.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.metadata as md
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
KiB = 1024
MiB = 1024 * 1024
TIME_BIN = "/usr/bin/time"


# ----------------------------------------------------------------------------------------------- input generators
def _text_bytes(size: int, seed: int) -> bytes:
    """English-like ASCII: a seeded vocabulary of pseudo-words drawn with a Zipf-like law, sentences and newlines."""
    rng = np.random.default_rng(seed)
    letters = np.frombuffer(b"etaoinshrdlcumwfgypbvkjxqz", dtype=np.uint8)
    freq = np.array([12.7, 9.1, 8.2, 7.5, 7.0, 6.7, 6.3, 6.1, 6.0, 4.3, 4.0, 2.8, 2.8, 2.4, 2.4, 2.2, 2.0, 2.0, 1.9,
                     1.5, 1.0, 0.8, 0.15, 0.15, 0.1, 0.07])
    freq = freq / freq.sum()
    vocab_size = 5000
    lengths = np.clip(rng.poisson(4.5, vocab_size), 1, 14)
    vocab = [bytes(rng.choice(letters, size=int(n), p=freq)) for n in lengths]
    ranks = np.arange(1, vocab_size + 1, dtype=np.float64)
    zipf = 1.0 / ranks
    zipf /= zipf.sum()
    out = bytearray()
    while len(out) < size:
        words = rng.choice(vocab_size, size=int(rng.integers(6, 20)), p=zipf)
        sentence = b" ".join(vocab[w] for w in words)
        sentence = sentence[:1].upper() + sentence[1:] + (b". " if rng.random() < 0.85 else b".\n")
        out += sentence
    return bytes(out[:size])


def _random_bytes(size: int, seed: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, size=size, dtype=np.uint8).tobytes()


def _repetitive_bytes(size: int) -> bytes:
    pattern = b"VNX-DNA:ACGT-0123456789\n"  # 24-byte pattern repeated
    return (pattern * (size // len(pattern) + 1))[:size]


def _binary_bytes(size: int, seed: int) -> bytes:
    """Structured binary: packed little-endian sensor records (u32 id, i32 time delta, f64 random walk, u16 flags, u16 pad)."""
    rng = np.random.default_rng(seed)
    dt = np.dtype([("id", "<u4"), ("dt_ms", "<i4"), ("value", "<f8"), ("flags", "<u2"), ("pad", "<u2")])
    n = size // dt.itemsize + 1
    rec = np.zeros(n, dtype=dt)
    rec["id"] = np.arange(n, dtype=np.uint32)
    rec["dt_ms"] = rng.integers(900, 1100, size=n, dtype=np.int32)
    rec["value"] = np.cumsum(rng.normal(0.0, 0.25, size=n)) + 20.0
    rec["flags"] = rng.choice(np.array([0, 1, 2, 4, 8], dtype=np.uint16), size=n, p=[0.9, 0.04, 0.03, 0.02, 0.01])
    return rec.tobytes()[:size]


def _cli_generate(path: Path, size: str, pattern: str, seed: int) -> None:
    subprocess.run(["vnx-dna", "benchmark", "generate", "--size", size, "--pattern", pattern, "--seed", str(seed),
                    "--output", str(path), "--force"], check=True, stdout=subprocess.DEVNULL)


# name, description, generator
CASES = [
    ("tiny", "100 B English-like text (seed 1)", lambda p: p.write_bytes(_text_bytes(100, 1))),
    ("small", "10 KiB English-like text (seed 2)", lambda p: p.write_bytes(_text_bytes(10 * KiB, 2))),
    ("medium", "1 MiB `vnx-dna benchmark generate --pattern mixed --seed 42`", lambda p: _cli_generate(p, "1MiB", "mixed", 42)),
    ("large", "10 MiB `vnx-dna benchmark generate --pattern mixed --seed 42`", lambda p: _cli_generate(p, "10MiB", "mixed", 42)),
    ("repetitive", "1 MiB of a repeated 24-byte ASCII pattern", lambda p: p.write_bytes(_repetitive_bytes(MiB))),
    ("random", "1 MiB numpy default_rng(3) uniform bytes", lambda p: p.write_bytes(_random_bytes(MiB, 3))),
    ("text", "1 MiB English-like text (seed 4)", lambda p: p.write_bytes(_text_bytes(MiB, 4))),
    ("binary", "1 MiB packed little-endian 20-byte records (seed 5)", lambda p: p.write_bytes(_binary_bytes(MiB, 5))),
]


# ----------------------------------------------------------------------------------------------- helpers
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _parse_time_v(text: str) -> dict:
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Maximum resident set size"):
            out["max_rss_kib"] = int(line.rsplit(":", 1)[1])
        elif line.startswith("Elapsed (wall clock) time"):
            val = line.rsplit(": ", 1)[1]
            parts = [float(x) for x in val.split(":")]
            secs = 0.0
            for p_ in parts:
                secs = secs * 60 + p_
            out["time_v_elapsed_s"] = secs
        elif line.startswith("User time (seconds)"):
            out["user_s"] = float(line.rsplit(":", 1)[1])
        elif line.startswith("System time (seconds)"):
            out["sys_s"] = float(line.rsplit(":", 1)[1])
        elif line.startswith("Exit status"):
            out["exit_status"] = int(line.rsplit(":", 1)[1])
    return out


def timed(cmd: list[str], scratch: Path, label: str) -> dict:
    """Run one CLI command under /usr/bin/time -v; return wall (perf_counter), CPU, peak RSS and the exit code."""
    tfile = scratch / f".time-{label}.txt"
    start = time.perf_counter()
    proc = subprocess.run([TIME_BIN, "-v", "-o", str(tfile)] + cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    wall = time.perf_counter() - start
    stats = _parse_time_v(tfile.read_text()) if tfile.exists() else {}
    tfile.unlink(missing_ok=True)
    result = {"cmd": " ".join(cmd), "returncode": proc.returncode, "wall_s": round(wall, 4), **stats}
    if proc.returncode != 0:
        result["stderr_tail"] = proc.stderr[-2000:]
    return result


def fasta_stats(path: Path) -> dict:
    """Independent count of FASTA records and nucleotides (not taken from the CLI report)."""
    records = bases = 0
    lengths: set[int] = set()
    cur = 0
    with open(path, "rb") as f:
        for line in f:
            if line.startswith(b">"):
                if records:
                    lengths.add(cur)
                    bases += cur
                records += 1
                cur = 0
            else:
                cur += len(line.strip())
    if records:
        lengths.add(cur)
        bases += cur
    return {"records": records, "bases": bases, "distinct_lengths": sorted(lengths)}


def environment() -> dict:
    cpu = None
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    mem_kib = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                mem_kib = int(line.split()[1])
    except OSError:
        pass
    pretty = None
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                pretty = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    versions = {}
    for dist in ("numpy", "cryptography", "zstandard", "reedsolo", "pydantic", "typer", "vnx-dna"):
        try:
            versions[dist] = md.version(dist)
        except md.PackageNotFoundError:
            versions[dist] = None
    try:
        import vnxdna
        vnxdna_version = vnxdna.__version__
    except Exception:  # pragma: no cover
        vnxdna_version = None
    cli_version = subprocess.run(["vnx-dna", "version"], stdout=subprocess.PIPE, text=True).stdout.strip()

    def git(*args: str) -> str | None:
        try:
            return subprocess.run(["git", *args], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, check=True).stdout.strip()
        except Exception:
            return None
    u = platform.uname()
    return {
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cpu_model": cpu, "logical_cpus": os.cpu_count(),
        "ram_total_gib": round(mem_kib / 1024 / 1024, 2) if mem_kib else None,
        "os": {"uname": f"{u.system} {u.release} {u.version} {u.machine}", "pretty_name": pretty},
        "python": sys.version.split()[0], "python_executable_venv": sys.prefix != sys.base_prefix,
        "package_versions": versions, "vnxdna_version": vnxdna_version, "vnx_dna_cli_version": cli_version,
        "git_commit": git("rev-parse", "HEAD"), "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_describe": git("describe", "--tags", "--always"),
        "tracked_src_and_tests_unmodified_vs_head": git("diff", "--name-only", "HEAD", "--", "src", "tests") == "",
        "untracked_paths_under_src_or_tests": [line[3:] for line in (git("status", "--porcelain", "--", "src", "tests") or "").splitlines()
                                               if line.startswith("??")],
        "load_average_at_start": list(os.getloadavg()),
        "measurement": "/usr/bin/time -v per CLI process; max RSS = largest single process (main or worker), "
                       "not the sum over the process tree",
    }


# ----------------------------------------------------------------------------------------------- cases
def run_case(name: str, desc: str, src: Path, scratch: Path, repeats: int) -> dict:
    work = scratch / name
    work.mkdir(parents=True, exist_ok=True)
    size = src.stat().st_size
    in_sha = sha256_file(src)
    runs = []
    store_rep = encode_rep = recover_rep = None
    fasta = None
    for r in range(repeats):
        archive, strands, out = work / "archive.vxdna", work / "strands.fasta", work / "recovered.bin"
        for p in (archive, strands, Path(str(strands) + ".vxidx"), out):
            p.unlink(missing_ok=True)
        st = timed(["vnx-dna", "store", str(src), "-o", str(archive), "--report", str(work / "store.json"), "--force"],
                   scratch, f"{name}-store")
        en = timed(["vnx-dna", "encode", str(archive), "-o", str(strands), "--report", str(work / "encode.json"), "--force"],
                   scratch, f"{name}-encode")
        # recover = decode + restore in one step, from the strand file alone (no container used)
        rc = timed(["vnx-dna", "recover", str(strands), "-o", str(out), "--temp-dir", str(work), "--report",
                    str(work / "recover.json"), "--force"], scratch, f"{name}-recover")
        out_sha = sha256_file(out) if out.exists() else None
        runs.append({"store": st, "encode": en, "recover": rc, "recovered_sha256": out_sha,
                     "pass": out_sha == in_sha and all(x["returncode"] == 0 for x in (st, en, rc))})
        if r == 0:
            store_rep = json.loads((work / "store.json").read_text())
            encode_rep = json.loads((work / "encode.json").read_text())
            recover_rep = json.loads((work / "recover.json").read_text())
            fasta = fasta_stats(strands)
            stored_file_size = archive.stat().st_size
            fasta_file_size = strands.stat().st_size

    eff = encode_rep.get("efficiency", {})
    strands_total = encode_rep["strands"]
    parity = eff.get("parity_strands")
    meta = eff.get("metadata_strands")

    def med(stage: str, key: str = "wall_s") -> float:
        return round(statistics.median(r_[stage][key] for r_ in runs), 4)

    def peak(stage: str) -> int:
        return max(r_[stage].get("max_rss_kib", 0) for r_ in runs)
    enc_total = [r_["store"]["wall_s"] + r_["encode"]["wall_s"] for r_ in runs]
    return {
        "name": name, "description": desc, "input_size": size, "input_sha256": in_sha,
        "stored_size": eff.get("stored_bytes", store_rep.get("stored_size")), "container_file_size": stored_file_size,
        "stored_over_input": round(eff.get("stored_bytes", 0) / size, 4) if size else None,
        "chunks": eff.get("chunks"), "ecc_groups": eff.get("ecc_groups"), "outer_code": eff.get("outer_code"),
        "strands_total": strands_total, "data_and_parity_strands": eff.get("data_and_parity_strands"),
        "parity_strands": parity, "metadata_strands": meta, "strand_nt": eff.get("strand_nt"),
        "bases_total": encode_rep["dna_bases"], "fasta_file_size": fasta_file_size,
        "independent_fasta_count": fasta,
        "nt_per_input_byte": round(encode_rep["dna_bases"] / size, 4),
        "ecc_overhead_parity_over_total_strands": round(parity / strands_total, 4) if parity is not None else None,
        "ecc_overhead_parity_plus_metadata_over_total_strands": round((parity + meta) / strands_total, 4)
        if parity is not None and meta is not None else None,
        "time_store_s_median": med("store"), "time_encode_s_median": med("encode"),
        "time_store_plus_encode_s_median": round(statistics.median(enc_total), 4),
        "time_recover_s_median": med("recover"),
        "cpu_s_median": {s: round(statistics.median(r_[s].get("user_s", 0) + r_[s].get("sys_s", 0) for r_ in runs), 3)
                         for s in ("store", "encode", "recover")},
        "peak_rss_kib": {"store": peak("store"), "encode": peak("encode"), "recover": peak("recover"),
                         "max": max(peak("store"), peak("encode"), peak("recover"))},
        "recovered_sha256": runs[-1]["recovered_sha256"],
        "pass": all(r_["pass"] for r_ in runs), "repeats": repeats,
        "cli_internal_elapsed_s_first_run": {"store": store_rep.get("elapsed_s"), "encode": encode_rep.get("elapsed_s"),
                                             "recover": recover_rep.get("elapsed_s")},
        "store_report_profile": store_rep.get("profile"), "chunks_compressed": store_rep.get("chunks_compressed"),
        "recover_report_recovery": recover_rep.get("recovery") if isinstance(recover_rep, dict) else None,
        "runs": runs,
    }


def run_noisy(src: Path, scratch: Path) -> dict:
    work = scratch / "noisy"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    out = scratch / "noisy-recovered.bin"
    out.unlink(missing_ok=True)
    report_path = scratch / "noisy-report.json"
    params = {"coverage": 10, "substitution_rate": 0.001, "insertion_rate": 0.0001, "deletion_rate": 0.0001,
              "dropout_rate": 0.02, "seed": 42, "profile": "balanced"}
    cmd = ["vnx-dna", "pipeline", str(src), "-o", str(out), "--work-dir", str(work), "--temp-dir", str(work),
           "--coverage", "10", "--substitution-rate", "0.001", "--insertion-rate", "0.0001", "--deletion-rate", "0.0001",
           "--dropout-rate", "0.02", "--seed", "42", "--report", str(report_path), "--force"]
    t = timed(cmd, scratch, "noisy")
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    in_sha = sha256_file(src)
    out_sha = sha256_file(out) if out.exists() else None
    shutil.rmtree(work, ignore_errors=True)
    out.unlink(missing_ok=True)
    report_path.unlink(missing_ok=True)
    return {"input": "medium (1 MiB mixed, seed 42)", "input_size": src.stat().st_size, "parameters": params,
            "timing": t, "input_sha256": in_sha, "recovered_sha256": out_sha,
            "pass": out_sha == in_sha and t["returncode"] == 0, "report": report}


# ----------------------------------------------------------------------------------------------- output
def fmt_kib(kib: int) -> str:
    return f"{kib / 1024:.0f}"


def write_readme(results: dict, env: dict, path: Path) -> None:
    rows = []
    for c in results["cases"]:
        rows.append(
            f"| {c['name']} | {c['input_size']:,} | {c['stored_size']:,} | {c['strands_total']:,} | {c['bases_total']:,} | "
            f"{c['nt_per_input_byte']:.3f} | {100 * c['ecc_overhead_parity_over_total_strands']:.1f} % | "
            f"{c['time_store_s_median']:.2f} + {c['time_encode_s_median']:.2f} = {c['time_store_plus_encode_s_median']:.2f} | "
            f"{c['time_recover_s_median']:.2f} | {fmt_kib(c['peak_rss_kib']['store'])} / {fmt_kib(c['peak_rss_kib']['encode'])} / "
            f"{fmt_kib(c['peak_rss_kib']['recover'])} | {'PASS' if c['pass'] else 'FAIL'} |")
    n = results["noisy_pipeline"]
    rep = n.get("report") or {}
    lines = [
        "# VNX-DNA V3 baseline benchmark",
        "",
        "> **Software and simulation only.** No DNA was synthesised, stored or sequenced. Strands are generated",
        "> sequences; the noisy case uses VNX-DNA's seeded sequencing simulator. Nucleotide counts are information-",
        "> theoretic counts for software strands, not physical densities.",
        "",
        f"Baseline: `{env['vnx_dna_cli_version']}` at commit `{env['git_commit']}` ({env['git_describe']}), measured "
        f"{env['timestamp_utc']} on {env['cpu_model']} ({env['logical_cpus']} logical CPUs, {env['ram_total_gib']} GiB RAM), "
        f"{env['os']['pretty_name']}, Python {env['python']}. Full environment: [environment.json](environment.json); "
        "all numbers: [results.json](results.json).",
        "",
        "## Reproduce",
        "",
        "```bash",
        "git checkout v3.0.0            # or any commit whose src/ equals v3.0.0",
        "python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'",
        f"python benchmarks/baseline/v3/run_v3_baseline.py --repeats {results['repeats']}",
        "```",
        "",
        "The script generates every input deterministically (fixed seeds) under `.bench-tmp/` (git-ignored), runs the",
        "installed CLI and deletes the generated data at the end unless `--keep-data` is given.",
        "",
        "## Method",
        "",
        "* Clean DNA path per input, profile `balanced` (defaults: 1 MiB chunks, zstd level 3 kept only if smaller, "
        "Cauchy RS 64+16, 2bit mapping, P = 40 B, inner RS r = 8, 252-nt strands), no encryption, default workers:",
        "  `vnx-dna store` → `vnx-dna encode` (FASTA) → `vnx-dna recover` (decode + restore from the FASTA alone).",
        "* Each command runs under `/usr/bin/time -v`. Times are wall-clock (median of "
        f"{results['repeats']} repeats). Peak RSS is `/usr/bin/time`'s *Maximum resident set size*, i.e. the largest "
        "single process (main process or one worker), maximum over repeats — **not** the sum over the process tree "
        "used in docs/BENCHMARKS.md, so the two are not directly comparable.",
        "* Strands and nucleotides come from the `encode` report and are cross-checked by counting the FASTA "
        "records independently (`independent_fasta_count` in results.json).",
        "* **ECC overhead** = parity strands / all strands, where parity strands = ECC groups × M (from the encode "
        "report, i.e. from the manifest's `erasure_code.stripe_count` × `parity_shards`). Metadata strands (Cauchy 8+8 "
        "copies of manifest and indexes) are reported separately; `(parity + metadata) / total` is also in "
        "results.json. Because the last group of every chunk is shortened (padding-only data shards are not emitted) "
        "while it still has M parity strands, small inputs show a higher ratio than the asymptotic M/(K+M) = 20 %.",
        "* PASS = every repeat exited 0 and SHA-256(recovered) = SHA-256(input).",
        "",
        "## Results (clean DNA path)",
        "",
        "| input | bytes | stored bytes | strands | nucleotides | nt / input byte | ECC overhead (parity / strands) | "
        "encode s (store + encode) | decode s (recover) | peak RSS MiB store / encode / recover | SHA-256 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "Process wall time includes a fixed interpreter + import start-up cost: `vnx-dna version` alone takes "
        f"{results.get('startup_s_median', 0):.2f} s (median of 5) on this machine. For inputs up to ~1 MiB this "
        "start-up dominates. The time each command reports for its own work (`elapsed_s` in the CLI report, first "
        "repeat) is:",
        "",
        "| input | store s | encode s | recover s | chunks (compressed) | ECC groups | parity strands | metadata strands |",
        "|---|---|---|---|---|---|---|---|",
        *[f"| {c['name']} | {c['cli_internal_elapsed_s_first_run']['store']:.3f} | {c['cli_internal_elapsed_s_first_run']['encode']:.3f} | "
          f"{c['cli_internal_elapsed_s_first_run']['recover']:.3f} | {c['chunks']} ({c['chunks_compressed']}) | {c['ecc_groups']:,} | "
          f"{c['parity_strands']:,} | {c['metadata_strands']} |" for c in results["cases"]],
        "",
        "Inputs: " + "; ".join(f"**{c['name']}** = {c['description']}" for c in results["cases"]) + ".",
        "",
        "## Noisy pipeline (one case)",
        "",
        "`vnx-dna pipeline` on the medium input (1 MiB mixed), coverage 10 (Poisson), substitution 0.001, insertion "
        "0.0001, deletion 0.0001, dropout 0.02, seed 42, cluster + consensus (default at coverage > 1).",
        "",
        f"* result: **{'PASS' if n['pass'] else 'FAIL'}** (exit {n['timing']['returncode']}), wall "
        f"{n['timing']['wall_s']:.2f} s, CPU {n['timing'].get('user_s', 0) + n['timing'].get('sys_s', 0):.1f} s, "
        f"peak RSS (largest process) {fmt_kib(n['timing'].get('max_rss_kib', 0))} MiB",
        f"* SHA-256 input = recovered: {n['input_sha256'] == n['recovered_sha256']}",
    ]
    timings = rep.get("timings_s") if isinstance(rep, dict) else None
    if isinstance(timings, dict):
        order = ["store", "encode", "sequence", "cluster", "consensus", "decode", "restore", "verify"]
        lines.append("* per-stage wall time inside the pipeline process (report `timings_s`): " + ", ".join(
            f"{k} {timings[k]:.2f} s" for k in order if k in timings))
    steps = rep.get("steps", {}) if isinstance(rep, dict) else {}
    seq, cl, co, de = (steps.get(k, {}) for k in ("sequence", "cluster", "consensus", "decode"))
    if seq and cl and co and de:
        rec, cst = de.get("recovery", {}), cl.get("stats", {})
        lines += [
            f"* channel: {seq.get('strands'):,} designed strands → {seq.get('reads'):,} reads "
            f"({seq.get('strands_with_zero_reads'):,} strands with zero reads); observed per-base rates: "
            + ", ".join(f"{k.replace('sequencing_', '')} {v:.2e}" for k, v in seq.get('observed_rates_per_designed_base', {}).items()
                        if k.startswith("sequencing_")),
            f"* clustering: {cl.get('clusters'):,} clusters; reads verified {cst.get('reads_verified'):,}, tentative "
            f"{cst.get('reads_tentative'):,}, orphan {cst.get('reads_orphan'):,}, reassigned {cst.get('reads_reassigned_to_strong_cluster'):,}",
            f"* consensus: {co.get('stats', {}).get('consensus_crc_valid'):,} CRC-valid of {co.get('consensus_sequences'):,}",
            f"* outer decoding: {rec.get('shards_erased'):,} shards erased, {rec.get('stripes_outer_recovered')} of "
            f"{rec.get('stripes_decoded')} ECC groups needed the outer code, worst group lost {rec.get('max_erasures_in_a_group')} "
            f"strands (guarantee: 16)",
        ]
    lines += ["", "The full pipeline report is stored under `noisy_pipeline.report` in results.json.", ""]
    path.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scratch", default=str(REPO / ".bench-tmp" / "v3-baseline"))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--only", default=None, help="comma-separated case names")
    ap.add_argument("--no-noisy", action="store_true")
    ap.add_argument("--keep-data", action="store_true")
    ap.add_argument("--out", default=str(HERE))
    args = ap.parse_args()
    if shutil.which("vnx-dna") is None or not Path(TIME_BIN).exists():
        print("need `vnx-dna` on PATH and /usr/bin/time", file=sys.stderr)
        return 2
    scratch = Path(args.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out)
    env = environment()
    (out_dir / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    wanted = set(args.only.split(",")) if args.only else None
    startup = []
    for _ in range(5):
        t = time.perf_counter()
        subprocess.run(["vnx-dna", "version"], stdout=subprocess.DEVNULL, check=True)
        startup.append(time.perf_counter() - t)
    t0 = time.perf_counter()
    cases = []
    inputs: dict[str, Path] = {}
    for name, desc, gen in CASES:
        if wanted and name not in wanted:
            continue
        src = scratch / f"input-{name}.bin"
        gen(src)
        inputs[name] = src
        print(f"[{time.perf_counter() - t0:7.1f}s] {name}: {src.stat().st_size} B", flush=True)
        res = run_case(name, desc, src, scratch, args.repeats)
        print(f"            -> {'PASS' if res['pass'] else 'FAIL'} strands={res['strands_total']} "
              f"enc={res['time_store_plus_encode_s_median']}s dec={res['time_recover_s_median']}s", flush=True)
        cases.append(res)
    noisy = None
    if not args.no_noisy:
        src = inputs.get("medium")
        if src is None:
            src = scratch / "input-medium.bin"
            _cli_generate(src, "1MiB", "mixed", 42)
        print(f"[{time.perf_counter() - t0:7.1f}s] noisy pipeline", flush=True)
        noisy = run_noisy(src, scratch)
        print(f"            -> {'PASS' if noisy['pass'] else 'FAIL'} {noisy['timing']['wall_s']}s", flush=True)
    results = {"benchmark": "vnx-dna v3 baseline", "software_simulation_only": True, "repeats": args.repeats,
               "profile": "balanced", "encryption": False,
               "startup_s_median": round(statistics.median(startup), 3), "total_runtime_s": round(time.perf_counter() - t0, 1),
               "all_pass": all(c["pass"] for c in cases) and (noisy is None or noisy["pass"]),
               "cases": cases, "noisy_pipeline": noisy}
    (out_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    if not wanted and noisy is not None:
        write_readme(results, env, out_dir / "README.md")
    if not args.keep_data:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"total {results['total_runtime_s']} s; all_pass={results['all_pass']}")
    return 0 if results["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
