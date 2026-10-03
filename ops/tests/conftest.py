"""Ops-layer tests run against a throw-away VNXDNA_HOME so they never touch /opt/vnx-dna state."""
import os
import sys
import tempfile
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="vnxops-test-home-")
os.environ["VNXDNA_HOME"] = _HOME
os.environ.setdefault("VNXDNA_AGENT_CLI", "agent-cli")  # placeholder executable: agent tiers configured in tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture
def home() -> Path:
    return Path(_HOME)


@pytest.fixture
def no_systemd(monkeypatch):
    from vnxops import governor
    monkeypatch.setattr(governor, "_systemd_available", lambda: False)
