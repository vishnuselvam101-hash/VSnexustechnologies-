"""Environment/provenance capture for experiments, simulations and benchmarks."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from vnxdna._version import __version__


def git_state(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Return {'commit', 'dirty'} of the repository containing ``path`` (default: this package)."""
    where = Path(path) if path is not None else Path(__file__).resolve().parent
    try:
        commit = subprocess.run(["git", "-C", str(where), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if commit.returncode != 0:
            return {"commit": None, "dirty": None}
        status = subprocess.run(["git", "-C", str(where), "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True, timeout=10)
        return {"commit": commit.stdout.strip(), "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def environment() -> dict[str, Any]:
    import numpy
    import reedsolo
    import zstandard
    import cryptography
    return {
        "vnxdna_version": __version__,
        "git": git_state(),
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "libraries": {"numpy": numpy.__version__, "reedsolo": getattr(reedsolo, "__version__", "unknown"),
                      "zstandard": zstandard.__version__, "cryptography": cryptography.__version__},
    }
