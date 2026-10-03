"""PHASE 2: measure the machine and derive a safe operating envelope (hardware-capability.yaml).

Every number in the profile is measured on this host by `vnxdna profile`; derived limits record the formula that
produced them. Benchmarks run niced (and DNA/ECC ones in a child process so peak RSS is measured per benchmark).
The production workloads that share the host are protected by a RAM reserve and by leaving CPUs free.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .sysinfo import cpu_info, disk, meminfo, run

GiB = 2**30
MiB = 2**20
NICE = ["nice", "-n", "10", "ionice", "-c2", "-n7"]


def _sysbench(args: list[str], pattern: str) -> float | None:
    code, out = run([*NICE, "sysbench", *args], timeout=120)
    m = re.search(pattern, out)
    return float(m.group(1)) if code == 0 and m else None


def _child(name: str, size: int) -> dict[str, Any]:
    """Run one in-process benchmark in a niced child; returns its JSON plus peak RSS from wait4()."""
    proc = subprocess.Popen([*NICE, sys.executable, "-m", "vnxops.profile_worker", name, str(size)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=str(paths.REPO / "ops"), env={**os.environ, "PYTHONHASHSEED": "0"})
    out, err = proc.communicate(timeout=900)
    # Popen already reaped the child; RUSAGE_CHILDREN max is cumulative, so we report the worker's self-measured peak.
    if proc.returncode != 0:
        return {"error": err.strip().splitlines()[-1] if err.strip() else f"exit {proc.returncode}"}
    return json.loads(out)


def _dd(work: Path) -> dict[str, float | None]:
    f = work / "dd.bin"
    res: dict[str, float | None] = {}
    for label, argv in (("seq_write_MBps", ["dd", "if=/dev/zero", f"of={f}", "bs=4M", "count=256", "oflag=direct", "conv=fsync"]),
                        ("seq_read_MBps", ["dd", f"if={f}", "of=/dev/null", "bs=4M", "iflag=direct"])):
        t0 = time.perf_counter()
        code, _ = run([*NICE, *argv], timeout=300)
        dtm = time.perf_counter() - t0
        res[label] = round(1024 / dtm, 1) if code == 0 else None
    f.unlink(missing_ok=True)
    return res


def _fileio(work: Path) -> dict[str, float | None]:
    base = ["fileio", "--file-total-size=1G", "--file-num=4"]
    cwd = os.getcwd()
    os.chdir(work)
    try:
        run(["sysbench", *base, "prepare"], timeout=300)
        code, out = run([*NICE, "sysbench", *base, "--file-test-mode=rndrw", "--file-extra-flags=direct", "--time=15",
                         "--threads=4", "run"], timeout=120)
        run(["sysbench", *base, "cleanup"], timeout=120)
    finally:
        os.chdir(cwd)
    r = re.search(r"reads/s:\s+([\d.]+)", out)
    w = re.search(r"writes/s:\s+([\d.]+)", out)
    return {"rand_read_iops_16k": float(r.group(1)) if r else None, "rand_write_iops_16k": float(w.group(1)) if w else None}


def _compile_c(work: Path) -> dict[str, float | None]:
    src = work / "bench.c"
    src.write_text("\n".join(f"int f{i}(int x){{int s=0;for(int j=0;j<x;j++)s+=j*{i}%7;return s;}}" for i in range(3000))
                   + "\nint main(void){return f1(3);}\n")
    out: dict[str, float | None] = {}
    for label, cc in (("c_compile_3000fn_O2_s", "gcc"), ("cxx_compile_3000fn_O2_s", "g++")):
        if not shutil.which(cc):
            out[label] = None
            continue
        t0 = time.perf_counter()
        code, _ = run([*NICE, cc, "-x", "c" if cc == "gcc" else "c++", "-O2", "-c", str(src), "-o", str(work / "b.o")], timeout=300)
        out[label] = round(time.perf_counter() - t0, 2) if code == 0 else None
    return out


def _compile_rust(work: Path) -> dict[str, float | None]:
    if not shutil.which("cargo"):
        return {"rust_compile_hello_release_s": None}
    proj = work / "rb"
    run(["cargo", "new", "-q", "--vcs", "none", str(proj)], timeout=60)
    (proj / "src" / "main.rs").write_text("fn main(){let v:Vec<u64>=(0..1000).map(|x|x*x).collect();println!(\"{}\",v.iter().sum::<u64>());}\n")
    t0 = time.perf_counter()
    code, _ = run([*NICE, "cargo", "build", "-q", "--release", "--offline", "--manifest-path", str(proj / "Cargo.toml")], timeout=600)
    return {"rust_compile_hello_release_s": round(time.perf_counter() - t0, 2) if code == 0 else None}


def measure(quick: bool = False) -> dict[str, Any]:
    paths.ensure(paths.RUN)
    work = Path(tempfile.mkdtemp(prefix="profile-", dir=paths.run_dir()))
    threads = os.cpu_count() or 1
    t = 5 if quick else 10
    m: dict[str, Any] = {}
    try:
        m["cpu_single_events_s"] = _sysbench(["cpu", "--threads=1", f"--time={t}", "run"], r"events per second:\s+([\d.]+)")
        m["cpu_multi_events_s"] = _sysbench(["cpu", f"--threads={threads}", f"--time={t}", "run"], r"events per second:\s+([\d.]+)")
        m["mem_bandwidth_1t_MiBps"] = _sysbench(["memory", "--memory-block-size=1M", "--memory-total-size=8G", "--threads=1", "run"],
                                                r"\(([\d.]+) MiB/sec\)")
        m["mem_bandwidth_4t_MiBps"] = _sysbench(["memory", "--memory-block-size=1M", "--memory-total-size=16G", "--threads=4", "run"],
                                                r"\(([\d.]+) MiB/sec\)")
        m.update(_dd(work))
        if not quick:
            m.update(_fileio(work))
        m.update(_compile_c(work))
        m.update(_compile_rust(work))
        for name, size in (("python_loop", 0), ("sha256", 256 * MiB), ("zstd", 64 * MiB), ("ecc_outer", 32 * MiB),
                           ("ecc_inner", 4 * MiB), ("dna_roundtrip", (256 if quick else 1024) * 1024),
                           ("stream_container", (64 if quick else 256) * MiB)):
            m[name] = _child(name, size)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return m


def derive(measured: dict[str, Any]) -> dict[str, Any]:
    """Safe limits. Formulas are recorded next to each value so the profile can be audited and re-derived."""
    mem = meminfo()
    total, avail = mem["MemTotal"], mem["MemAvailable"]
    used_outside = total - avail  # production containers, Android emulator, dev servers, page-cache pinned
    threads = os.cpu_count() or 1
    prod_reserve = max(8 * GiB, int(1.5 * used_outside))
    safe_ram = max(2 * GiB, total - prod_reserve - 2 * GiB)
    d = disk(str(paths.HOME))
    disk_floor = max(15 * GiB, d["total"] // 10)
    disk_budget = max(0, min(40 * GiB, d["free"] - disk_floor))
    # Largest standard benchmark size whose working set (≈ 8× input on disk for container+FASTA+reads) fits the budget.
    sizes = [1024 * 10**k for k in range(7)]  # 1 KB … 1 GB
    max_bench = max([s for s in sizes if 8 * s <= disk_budget and s <= safe_ram // 4] or [sizes[0]])
    safe_threads = max(1, threads - 2)
    return {
        "prod_reserve_bytes": {"value": prod_reserve, "formula": "max(8 GiB, 1.5 × (MemTotal − MemAvailable) at profile time)"},
        "SAFE_RAM_LIMIT": {"value": safe_ram, "formula": "MemTotal − prod_reserve − 2 GiB kernel/page-cache floor"},
        "SAFE_RAM_HIGH": {"value": int(safe_ram * 0.85), "formula": "0.85 × SAFE_RAM_LIMIT (soft throttle point)"},
        "SAFE_SWAP_LIMIT": {"value": min(2 * GiB, mem.get("SwapTotal", 0) // 4), "formula": "min(2 GiB, SwapTotal / 4)"},
        "MIN_FREE_RAM": {"value": 6 * GiB, "formula": "admission floor: MemAvailable must stay ≥ 6 GiB after any new job"},
        "SAFE_CPU_THREADS": {"value": safe_threads, "formula": "logical CPUs − 2 (two kept for production)"},
        "SAFE_PARALLEL_BUILDS": {"value": max(1, min(safe_threads // 2, safe_ram // (2 * GiB))), "formula": "min(threads/2, RAM/2 GiB)"},
        "SAFE_TEST_PARALLELISM": {"value": max(1, min(4, safe_threads // 2)), "formula": "min(4, threads/2)"},
        "SAFE_LLM_MEMORY": {"value": max(0, avail - 6 * GiB - 2 * GiB), "formula": "MemAvailable − MIN_FREE_RAM − 2 GiB for one concurrent VNX job"},
        "SAFE_LLM_CONCURRENCY": {"value": 1, "formula": "one loaded local model at a time (CPU inference saturates cores)"},
        "SAFE_DOCKER_MEMORY": {"value": min(4 * GiB, safe_ram // 4), "formula": "min(4 GiB, SAFE_RAM_LIMIT / 4) per VNX-DNA container"},
        "DISK_FREE_FLOOR": {"value": disk_floor, "formula": "max(15 GiB, 10% of disk) must stay free"},
        "DISK_WORK_BUDGET": {"value": disk_budget, "formula": "min(40 GiB, free − floor)"},
        "MAX_BENCHMARK_INPUT": {"value": max_bench, "formula": "largest 10^k KB with 8× ≤ disk budget and ≤ RAM limit / 4"},
        "MAX_PROCESSES": {"value": 256, "formula": "TasksMax for vnxdna.slice"},
    }


def build_profile(quick: bool = False) -> dict[str, Any]:
    mem = meminfo()
    measured = measure(quick=quick)
    return {
        "schema": "vnxdna.hardware-capability/1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "quick": quick,
        "host": {"cpu": cpu_info(), "mem_total": mem["MemTotal"], "mem_available_at_profile": mem["MemAvailable"],
                 "swap_total": mem.get("SwapTotal"), "disk": disk(str(paths.HOME)), "gpu": None},
        "measured": measured,
        "limits": derive(measured),
    }


def write(profile: dict[str, Any], path: Path | None = None) -> Path:
    path = path or paths.CONFIG / "hardware-capability.yaml"
    paths.ensure(path.parent)
    header = ("# VNX-DNA hardware capability profile. Generated by `vnxdna profile`; do not hand-edit measured values.\n"
              "# The resource governor and benchmark scaler read `limits.*.value`.\n")
    path.write_text(header + yaml.safe_dump(profile, sort_keys=False, width=120))
    return path


def load(path: Path | None = None) -> dict[str, Any]:
    path = path or paths.CONFIG / "hardware-capability.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def limit(name: str, default: Any = None, profile: dict[str, Any] | None = None) -> Any:
    prof = profile if profile is not None else load()
    return prof.get("limits", {}).get(name, {}).get("value", default)
