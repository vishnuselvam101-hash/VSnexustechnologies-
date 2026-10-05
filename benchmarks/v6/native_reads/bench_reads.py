"""Micro-benchmark: reference (vnxdna.v4.reads) vs native (vnxdna.v6.native_reads) read parsing.

Generates a synthetic FASTQ (default 256 MiB, read lengths 100-299 nt, 8% N-free random bases, Phred+33) into a
temporary directory, then parses it with each backend in a fresh subprocess, ``--repeats`` times each
(alternating), recording wall time of the full iteration (open, read, parse, build the batch arrays) and peak RSS
(ru_maxrss of the subprocess). A separate untimed pass per backend hashes every batch array in order (SHA-256);
the hashes must be identical. The file is read from the page cache after the first pass: this measures parsing, not the disk.
Writes one JSON with the git commit, CPU model, compiler and library provenance.

usage: PYTHONPATH=src python benchmarks/v6/native_reads/bench_reads.py [--mib 256] [--repeats 3] [--out PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]

CHILD = r"""
import hashlib, json, resource, sys, time
backend, path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
from vnxdna.v4 import reads as ref
from vnxdna.v6 import native_reads as nr
if backend == "native":
    assert nr.available(), nr.status()
    it = lambda: nr.iter_reads_native(path, 8192)
else:
    it = lambda: ref.iter_reads(path, 8192)
base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
h = hashlib.sha256()
reads = nt = batches = 0
t0 = time.perf_counter()
for b in it():
    batches += 1
    reads += b.lengths.size
    if mode == "hash":
        nt += int(b.lengths.sum())
        for a in (b.codes, b.lengths, b.quals, b.invalid):
            h.update(a.tobytes())
dt = time.perf_counter() - t0
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({"seconds": dt, "reads": reads, "nt": nt, "batches": batches,
                  "sha256": h.hexdigest() if mode == "hash" else None,
                  "maxrss_base_kb": base, "maxrss_peak_kb": peak}))
"""


def write_fastq(path: Path, mib: int, seed: int = 2026) -> None:
    rng = np.random.default_rng(seed)
    alphabet = np.frombuffer(b"ACGT", np.uint8)
    target = mib << 20
    written, i = 0, 0
    with open(path, "wb") as f:
        while written < target:
            n = 4096
            lens = rng.integers(100, 300, n)
            parts = []
            for k in range(n):
                L = int(lens[k])
                seq = alphabet[rng.integers(0, 4, L)].tobytes()
                qual = rng.integers(35, 74, L).astype(np.uint8).tobytes()
                parts.append(b"@bench_%d\n%s\n+\n%s\n" % (i, seq, qual))
                i += 1
            blob = b"".join(parts)
            f.write(blob)
            written += len(blob)


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT).stdout.strip()
    except OSError:
        return ""


def cpu_model() -> str:
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mib", type=int, default=256)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", type=Path, default=ROOT / "benchmarks" / "v6" / "native_reads" / "results" / "bench_reads.json")
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    from vnxdna.v6 import native_reads as nr

    assert nr.available(), nr.status()
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    cc = os.environ.get("CC") or "cc"
    load_start = os.getloadavg()
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "bench.fastq"
        write_fastq(path, a.mib)
        size = path.stat().st_size
        runs: dict[str, list[dict]] = {"reference": [], "native": []}
        subprocess.run(["cat", str(path)], stdout=subprocess.DEVNULL, check=True)   # warm the page cache
        def child(backend: str, mode: str) -> dict:
            out = subprocess.run([sys.executable, "-c", CHILD, backend, str(path), mode], capture_output=True,
                                 text=True, env=env, check=True)
            return json.loads(out.stdout.strip().splitlines()[-1])

        hashes = {backend: child(backend, "hash") for backend in runs}      # untimed: output identity
        for _ in range(a.repeats):                                           # timed: iteration only
            for backend in ("reference", "native"):
                runs[backend].append(child(backend, "time"))
    summary = {}
    for backend, rs in runs.items():
        secs = [r["seconds"] for r in rs]
        med = statistics.median(secs)
        summary[backend] = {"seconds": secs, "median_seconds": round(med, 4),
                            "mb_per_s": round(size / 1e6 / med, 1), "mib_per_s": round(size / 2**20 / med, 1),
                            "reads_per_s": round(rs[0]["reads"] / med), "peak_rss_mib": round(max(r["maxrss_peak_kb"] for r in rs) / 1024, 1),
                            "rss_at_start_mib": round(rs[0]["maxrss_base_kb"] / 1024, 1),
                            "reads": rs[0]["reads"], "nt": hashes[backend]["nt"], "batches": rs[0]["batches"],
                            "output_sha256": hashes[backend]["sha256"]}
    same = len({h["sha256"] for h in hashes.values()}) == 1 and len({r["reads"] for rs in runs.values() for r in rs}) == 1
    lib = nr.status()["library"]
    res = {
        "benchmark": "v6 native streaming read parser vs v4 reference (synthetic FASTQ, page-cache warm)",
        "git_commit": sh(["git", "rev-parse", "HEAD"]), "git_dirty": bool(sh(["git", "status", "--porcelain", "--", "src/vnxdna/v6/native", "src/vnxdna/v6/native_reads.py"])),
        "cpu_model": cpu_model(), "cpu_count": os.cpu_count(), "compiler": sh([cc, "--version"]).splitlines()[0] if sh([cc, "--version"]) else None,
        "cflags": nr.CFLAGS, "python": platform.python_version(), "numpy": np.__version__, "platform": platform.platform(),
        "library": lib, "library_sha256": hashlib.sha256(Path(lib).read_bytes()).hexdigest(),
        "kernel_source_sha256": hashlib.sha256((ROOT / "src/vnxdna/v6/native/reads.c").read_bytes()).hexdigest(),
        "file_bytes": size, "file_mib": round(size / 2**20, 1), "batch": 8192, "repeats": a.repeats,
        "outputs_identical": same, "results": summary,
        "speedup_median": round(summary["reference"]["median_seconds"] / summary["native"]["median_seconds"], 2),
        "loadavg_1_5_15_start": [round(x, 2) for x in load_start], "loadavg_1_5_15_end": [round(x, 2) for x in os.getloadavg()],
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps({k: res[k] for k in ("file_mib", "outputs_identical", "speedup_median")}))
    for b, s in summary.items():
        print(b, {k: s[k] for k in ("median_seconds", "mb_per_s", "reads_per_s", "peak_rss_mib")})
    print("wrote", a.out)
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
