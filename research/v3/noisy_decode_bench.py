"""Decoding throughput on noisy coverage-1 reads, for any installed ``vnx-dna`` (software simulation).

Usage::

    python research/v3/noisy_decode_bench.py --vnx .venv/bin/vnx-dna --vnx-baseline /path/to/v2/.venv/bin/vnx-dna \\
        --size 10MB --rates 0,0.002,0.005,0.01 --out research/results/v3/noisy_decode.json

For each substitution rate the *current* CLI prepares one strand pool (generate → store → encode → simulate with
fixed coverage 1, no shuffle, seed 42). Each CLI then decodes and restores the same read file; wall time, CPU time
and peak RSS of the decode process come from ``/usr/bin/time``. Recovery is checked by SHA-256 against the input.
The inner Reed–Solomon decoder dominates at high substitution rates, so this isolates V3's vectorised RS decoder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def timed(cmd: list[str]) -> dict:
    """Run under /usr/bin/time -v; return wall, user+sys CPU, peak RSS (MiB), exit code."""
    r = subprocess.run(["/usr/bin/time", "-v", *cmd], capture_output=True, text=True)
    info = {"exit": r.returncode}
    for line in r.stderr.splitlines():
        line = line.strip()
        if line.startswith("Elapsed (wall clock) time"):
            parts = line.rsplit(" ", 1)[1].split(":")
            info["wall_s"] = sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
        elif line.startswith("User time"):
            info["user_s"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("System time"):
            info["sys_s"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("Maximum resident set size"):
            info["peak_rss_mib"] = round(int(line.rsplit(" ", 1)[1]) / 1024, 1)
    info["cpu_s"] = round(info.get("user_s", 0) + info.get("sys_s", 0), 2)
    if r.returncode:
        info["stderr_tail"] = r.stderr.strip().splitlines()[-3:]
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vnx", required=True, help="vnx-dna under test (prepares the pool)")
    ap.add_argument("--vnx-baseline", help="another vnx-dna to compare (e.g. v2.0.0)")
    ap.add_argument("--size", default="10MB")
    ap.add_argument("--rates", default="0,0.002,0.005,0.01")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    clis = {"current": a.vnx, **({"baseline": a.vnx_baseline} if a.vnx_baseline else {})}
    versions = {k: subprocess.run([v, "version"], capture_output=True, text=True).stdout.strip() for k, v in clis.items()}
    work = Path(tempfile.mkdtemp(prefix="vnxdna-noisy-"))
    rows = []
    try:
        src = work / "input.bin"
        subprocess.run([a.vnx, "benchmark", "generate", "--size", a.size, "--pattern", "mixed", "--seed", "42", "-o", str(src)],
                       check=True, capture_output=True)
        subprocess.run([a.vnx, "store", str(src), "-o", str(work / "a.vxdna")], check=True, capture_output=True)
        subprocess.run([a.vnx, "encode", str(work / "a.vxdna"), "-o", str(work / "s.fasta")], check=True, capture_output=True)
        expected = sha(src)
        for rate in [float(x) for x in a.rates.split(",")]:
            reads = work / f"r{rate}.fasta"
            subprocess.run([a.vnx, "simulate", str(work / "s.fasta"), "-o", str(reads), "--coverage", "1", "--coverage-model", "fixed",
                            "--substitution-rate", str(rate), "--no-shuffle", "--seed", "42", "--force"], check=True, capture_output=True)
            row = {"substitution_rate": rate}
            for name, cli in clis.items():
                out = work / f"out-{name}-{rate}.bin"
                cmd = [cli, "restore", str(reads), "-o", str(out), "--force", "--json"]
                if a.workers:
                    cmd += ["-j", str(a.workers)]
                m = timed(cmd)
                m["exact"] = out.exists() and sha(out) == expected
                row[name] = m
                out.unlink(missing_ok=True)
            if "baseline" in row and row["current"].get("wall_s") and row["baseline"].get("wall_s"):
                row["speedup_wall"] = round(row["baseline"]["wall_s"] / row["current"]["wall_s"], 2)
            rows.append(row)
            reads.unlink(missing_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    result = {"note": "software simulation; coverage 1, fixed, no shuffle, seed 42; restore = decode + verify + write",
              "size": a.size, "versions": versions, "machine": {"platform": platform.platform(), "python": platform.python_version()},
              "rows": rows}
    text = json.dumps(result, indent=2)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
