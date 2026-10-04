"""Provenance block recorded with every V5 benchmark and experiment (mission §0 and §19).

Extends the V4 ``environment()`` record with what a V4-vs-V5 comparison needs:
the V4 control tag/commit, the commit under test, native toolchain versions,
the CPU flags that matter for vectorised kernels, and a hash of the exact
configuration. Seeds, input hashes, channel parameters, coverage and worker
counts are recorded by each benchmark next to its results.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from vnxdna.v4.util import environment

V4_TAG = "v4.0.0"
REPO = Path(__file__).resolve().parents[2]


def _run(*cmd: str) -> str | None:
    try:
        out = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _first_line(*cmd: str) -> str | None:
    out = _run(*cmd)
    return out.splitlines()[0] if out else None


def cpu_flags() -> list[str]:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("flags"):
                have = set(line.split(":", 1)[1].split())
                return sorted(have & {"sse4_2", "avx", "avx2", "avx512f", "avx512bw", "bmi2", "popcnt"})
    except OSError:
        pass
    return []


def config_sha256(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def provenance(config: dict | None = None) -> dict:
    env = environment()
    env.update({
        "v4_control_tag": V4_TAG,
        "v4_control_commit": _run("git", "rev-parse", f"{V4_TAG}^{{commit}}"),
        "commit_under_test": _run("git", "rev-parse", "HEAD"),
        "worktree_dirty": bool(_run("git", "status", "--porcelain", "--untracked-files=no")),
        "native_toolchain": {"gcc": _first_line("gcc", "--version"), "rustc": _first_line("rustc", "--version"),
                             "cargo": _first_line("cargo", "--version")},
        "cpu_flags": cpu_flags(),
    })
    if config is not None:
        env["config_sha256"] = config_sha256(config)
    return env
