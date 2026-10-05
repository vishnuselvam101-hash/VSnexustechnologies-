"""V1-suite fixtures. Helpers live in ``tests/v1_support.py`` (import them from there, not from ``conftest``)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Subprocess tests (``python -m vnxdna…``) must run the code of this tree, like the in-process tests do through the
# pyproject ``pythonpath = ["src", ...]``; otherwise an editable install of another checkout would answer them.
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if os.environ.get("PYTHONPATH", "").split(os.pathsep)[0] != _SRC:
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (_SRC, os.environ.get("PYTHONPATH")) if p)

from v1_support import FAST, OTHER_KEY, TEST_KEY, mixed_bytes  # noqa: F401  (re-exported for older imports)
from vnxdna.container.builder import StoreOptions


@pytest.fixture
def fast_options() -> StoreOptions:
    return FAST


@pytest.fixture
def work(tmp_path):
    return tmp_path


@pytest.fixture(scope="session")
def fixtures_dir():
    return os.path.join(os.path.dirname(__file__), "fixtures", "v0_1")
