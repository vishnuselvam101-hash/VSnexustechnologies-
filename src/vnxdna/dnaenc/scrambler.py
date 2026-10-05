"""The frame-family scrambler: bytes 1 … 13+P are XORed with SHAKE-128("VNX4 scrambler" ‖ v) (spec §3.1 I-2).
Split from ``vnxdna.v4.frame`` (V6 Phase 2, M3)."""
from __future__ import annotations

import hashlib

import numpy as np

VARIANTS = 256
DOMAIN = b"VNX4 scrambler"

_KEYSTREAM: dict[int, np.ndarray] = {}


def keystreams(span: int) -> np.ndarray:
    ks = _KEYSTREAM.get(span)
    if ks is None:
        ks = np.stack([np.frombuffer(hashlib.shake_128(DOMAIN + bytes([v])).digest(span), dtype=np.uint8)
                       for v in range(VARIANTS)])
        _KEYSTREAM[span] = ks
    return ks
