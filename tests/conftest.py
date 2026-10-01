"""V1-suite fixtures. Helpers live in ``tests/v1_support.py`` (import them from there, not from ``conftest``)."""
from __future__ import annotations

import os

import pytest

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
