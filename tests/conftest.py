"""Shared helpers. Fast test profiles keep the suite quick without changing semantics."""
from __future__ import annotations

import hashlib
import os
import random

import pytest

from vnxdna.container.builder import StoreOptions
from vnxdna.dna.constraints import ConstraintSpec

# 8+4 outer code, small strands and chunks: every code path, small inputs.
FAST = StoreOptions(chunk_size=4096, data_shards=8, parity_shards=4, payload_bytes=24, inner_parity_bytes=8)
TEST_KEY = hashlib.sha256(b"VNX-DNA test-only key; protects nothing").digest()
OTHER_KEY = hashlib.sha256(b"a different test-only key").digest()


def mixed_bytes(size: int, seed: int = 0) -> bytes:
    """Random + repeated + low-entropy content."""
    r = random.Random(seed)
    third = size // 3
    return (r.randbytes(third) + (b"VNX-DNA repeated block " * (third // 23 + 1))[:third]
            + bytes(r.choice((0, 1, 2, 255)) for _ in range(size - 2 * third)))


@pytest.fixture
def fast_options() -> StoreOptions:
    return FAST


@pytest.fixture
def work(tmp_path):
    return tmp_path


@pytest.fixture(scope="session")
def fixtures_dir():
    return os.path.join(os.path.dirname(__file__), "fixtures", "v0_1")
