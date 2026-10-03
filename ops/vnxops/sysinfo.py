"""Read-only system probes. Everything here reads /proc, /sys or runs a fixed argv; nothing changes the system."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def run(argv: list[str], timeout: float = 20.0) -> tuple[int, str]:
    """Run a fixed argv (never a shell string) and return (exit code, stdout+stderr). Missing tools give 127."""
    if not shutil.which(argv[0]):
        return 127, ""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as exc:
        return 126, str(exc)


def meminfo() -> dict[str, int]:
    """/proc/meminfo in bytes."""
    out: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            out[key] = int(parts[0]) * (1024 if len(parts) > 1 else 1)
    return out


def pressure(resource: str) -> dict[str, float]:
    """Pressure-stall information (PSI) 'some' averages for cpu/memory/io, or {} if unsupported."""
    try:
        line = Path(f"/proc/pressure/{resource}").read_text().splitlines()[0]
    except OSError:
        return {}
    return {k: float(v) for k, v in (kv.split("=") for kv in line.split()[1:]) if k.startswith("avg")}


def cpu_times() -> tuple[int, int]:
    """(idle, total) jiffies from /proc/stat."""
    vals = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    return vals[3] + vals[4], sum(vals)


def cpu_percent(interval: float = 0.5) -> float:
    import time
    i1, t1 = cpu_times()
    time.sleep(interval)
    i2, t2 = cpu_times()
    return 0.0 if t2 == t1 else round(100.0 * (1 - (i2 - i1) / (t2 - t1)), 1)


def disk(path: str = "/") -> dict[str, int]:
    u = shutil.disk_usage(path)
    return {"total": u.total, "used": u.used, "free": u.free}


def cpu_info() -> dict[str, Any]:
    model = ""
    flags: list[str] = []
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.startswith("model name") and not model:
            model = line.split(":", 1)[1].strip()
        if line.startswith("flags") and not flags:
            flags = line.split(":", 1)[1].split()
    simd = [f for f in ("sse4_2", "avx", "avx2", "avx512f", "avx512bw", "avx512_vnni", "aes", "sha_ni") if f in flags]
    return {"model": model, "logical_cpus": os.cpu_count(), "simd": simd, "loadavg": os.getloadavg()}


def os_release() -> dict[str, str]:
    out = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        k, _, v = line.partition("=")
        out[k] = v.strip('"')
    return out


def tool_version(name: str, argv: list[str] | None = None) -> str | None:
    """First line of `<tool> --version`, or None if absent."""
    path = shutil.which(name)
    if not path:
        return None
    code, out = run(argv or [name, "--version"], timeout=10)
    first = out.splitlines()[0] if out else ""
    return first[:160] or path


def systemd_active(unit: str) -> str:
    _, out = run(["systemctl", "is-active", unit], timeout=5)
    return out or "unknown"


def docker_containers() -> list[dict[str, str]]:
    code, out = run(["docker", "ps", "--format", "{{json .}}"], timeout=15)
    if code != 0:
        return []
    rows = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
            rows.append({"name": d.get("Names", ""), "image": d.get("Image", ""), "status": d.get("Status", "")})
        except json.JSONDecodeError:
            continue
    return rows


def listening_ports() -> list[dict[str, str]]:
    code, out = run(["ss", "-Hltnp"], timeout=10)
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            proc = parts[5].split('"')[1] if len(parts) > 5 and '"' in parts[5] else ""
            rows.append({"listen": parts[3], "process": proc})
    return rows
