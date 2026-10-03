"""Filesystem locations. Machine state lives under VNXDNA_HOME (default /opt/vnx-dna); policy lives in the repo."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("VNXDNA_HOME", "/opt/vnx-dna"))
CONFIG = HOME / "config"
REPO_CONFIG = REPO / "config"
LAYA_CONFIG = REPO_CONFIG / "laya"
AUDIT = HOME / "environment-audit"
REPORTS = HOME / "reports"
LOGS = HOME / "logs"
ARTIFACTS = HOME / "artifacts"
BACKUPS = HOME / "backups"
RUN = HOME / "run"
VENV = HOME / "venv"


def ensure(*dirs: Path) -> None:
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def run_dir() -> Path:
    """Scratch directory for temporary work (created on demand, e.g. on a fresh VNXDNA_HOME in CI)."""
    ensure(RUN)
    return RUN
