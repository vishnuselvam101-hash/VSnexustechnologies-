"""Central environment configuration: /opt/vnx-dna/config/system.yaml.

`vnxdna system init` writes it once (it is never overwritten without --force); everything else reads it with
`load()`, which falls back to the same defaults so a missing file never breaks a command.
"""
from __future__ import annotations

import copy
import os
import datetime as dt
from pathlib import Path
from typing import Any

import yaml

from . import paths

DEFAULTS: dict[str, Any] = {
    "schema": "vnxdna.system/1",
    "environment": {"name": "vnx-dna-vps", "role": "development (shares host with VNX Weather production)", "gpu": None},
    "paths": {"home": str(paths.HOME), "repo": str(paths.REPO), "venv": str(paths.VENV),
              "laya_config": str(paths.LAYA_CONFIG), "hardware_profile": str(paths.CONFIG / "hardware-capability.yaml"),
              "artifacts": str(paths.ARTIFACTS), "reports": str(paths.REPORTS), "logs": str(paths.LOGS),
              "backups": str(paths.BACKUPS), "workspaces": str(paths.HOME / "workspaces"),
              "datasets": str(paths.HOME / "datasets"), "experiments": str(paths.HOME / "experiments")},
    "repository": {"canonical_remote": "https://github.com/vishnuselvam101-hash/VSnexustechnologies-.git",
                   "baseline_tag": "v3.0.0", "baseline_commit": "9b5123ceb86805b9d0eabd7a5f74592c3d97eb58",
                   "integration_branch": "development", "protected": ["main", "development", "v*"]},
    "governor": {"slice": "vnxdna.slice", "watchdog_unit": "vnxdna-governor.service",
                 "policy": str(paths.LAYA_CONFIG / "resources.yaml")},
    "models": {"ollama_url": "http://127.0.0.1:11434", "registry": str(paths.LAYA_CONFIG / "models.yaml"),
               "benchmarks": str(paths.REPORTS / "model-benchmarks.json"), "remote_enabled": True},
    "queue": {"backend": "local", "fallback_to_local": True,
              "redis": {"host": "127.0.0.1", "port": 6379, "db": 7, "prefix": "vnxdna:q"},
              "sqs": {"enabled": False, "queue_url": None, "region": None}},
    "harness": {"default_timeout_s": 3600, "keep_workspaces_days": 14},
    "observability": {"status_cache_s": 10},
    "mcp": {"registry": str(paths.REPO / "docs" / "MCP_REGISTRY.md"), "agent_servers": []},
    # External coding-agent CLI used for tiers 3/4 (machine-specific; not named in the repository).
    "agent": {"cli": None},
}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def path() -> Path:
    return paths.CONFIG / "system.yaml"


def load() -> dict[str, Any]:
    p = path()
    return _merge(DEFAULTS, yaml.safe_load(p.read_text()) or {}) if p.exists() else copy.deepcopy(DEFAULTS)


def agent_cli() -> str | None:
    """Executable of the external coding-agent CLI (tiers 3/4): $VNXDNA_AGENT_CLI, else system.yaml `agent.cli`."""
    return os.environ.get("VNXDNA_AGENT_CLI") or (load().get("agent") or {}).get("cli") or None


def agent_home() -> Path | None:
    """The agent CLI's per-user state directory (~/.<executable name>), checked for credential permissions."""
    cli = agent_cli()
    return Path.home() / ("." + Path(cli).name) if cli else None


def init(force: bool = False) -> Path:
    p = path()
    if p.exists() and not force:
        return p
    paths.ensure(p.parent)
    header = (f"# VNX-DNA central environment configuration (written {dt.date.today()} by `vnxdna system init`).\n"
              "# Policy (agents, models, gates) lives in the repo under config/laya; this file is machine-specific.\n"
              "# No secrets here: credentials stay in the tools' own stores (agent CLI login, gh auth, AWS chain).\n"
              "# Set agent.cli to the external coding-agent executable to enable tiers 3/4.\n")
    p.write_text(header + yaml.safe_dump(DEFAULTS, sort_keys=False, width=120))
    return p
