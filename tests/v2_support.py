"""Shared V2 test helpers (importable as ``v2_support``): a small profile (8+4 outer code, 4 KiB chunks) exercises every code path on small inputs."""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

from vnxdna.v2.profiles import options_for

FAST = options_for("balanced", chunk_size=4096, data_shards=8, parity_shards=4, payload_bytes=24, inner_parity_bytes=8)
KEY = hashlib.sha256(b"VNX-DNA V2 test-only key; protects nothing").digest()
OTHER_KEY = hashlib.sha256(b"another V2 test-only key").digest()


def mixed_bytes(size: int, seed: int = 0) -> bytes:
    r = random.Random(seed)
    third = size // 3
    return (r.randbytes(third) + (b"VNX-DNA V2 repeated block " * (third // 26 + 1))[:third]
            + bytes(r.choice((0, 1, 2, 255)) for _ in range(size - 2 * third)))



def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path
