"""Every ``vnxdna`` module path and attribute used outside ``src/`` keeps resolving (V6_ARCHITECTURE §7.1 M0).

``public_paths.json`` was generated once, from the pre-refactor tree, by ``generate_public_paths.py`` (tests/,
experiments/, benchmarks/, plus the ``python -m`` entry points named in README, Dockerfile, docs/ and CI). The V6 Phase 2
module moves leave the old paths as aliases or façades; this test is what keeps them honest.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DATA = json.loads((Path(__file__).with_name("public_paths.json")).read_text())
MODULES = [m["module"] for m in DATA["modules"]]
RUNNABLE = [m["module"] for m in DATA["modules"] if m["runnable"]]
SRC = Path(__file__).resolve().parents[2] / "src"


def test_the_contract_is_not_empty():
    assert len(MODULES) >= 90 and len(DATA["attributes"]) >= 400
    assert "vnxdna.v4.decoder" in MODULES and ["vnxdna.v4.decoder", "Spill"] in DATA["attributes"]


@pytest.mark.parametrize("name", MODULES)
def test_module_path_imports(name):
    importlib.import_module(name)


def test_every_attribute_resolves():
    missing = [f"{m}.{a}" for m, a in DATA["attributes"] if not hasattr(importlib.import_module(m), a)]
    assert not missing, f"{len(missing)} public names no longer resolve: {missing[:30]}"


@pytest.mark.parametrize("name", RUNNABLE)
def test_documented_python_dash_m_entry_points_still_run(name):
    env = dict(os.environ, PYTHONPATH=str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    res = subprocess.run([sys.executable, "-m", name, "--help"], capture_output=True, text=True, env=env, timeout=120)
    assert res.returncode == 0, res.stderr[-2000:]
