"""Deterministic test-data generation (streamed; never holds the whole input in memory).

Patterns: ``random`` (incompressible), ``text`` (English-like words), ``repetitive``
(short repeated pattern), ``binary`` (structured little-endian records), ``mixed``
(1 MiB blocks cycling through the others). Output depends only on (pattern, size, seed).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

from .errors import VNXConfigurationError

PATTERNS = ("random", "text", "repetitive", "binary", "mixed")
_WORDS = (b"the of and to in is that for it as with was on be by this are or at from have an they which one you were all "
          b"we can her has there been if more when will would who so no data storage archive molecule strand sequence "
          b"error code redundancy synthesis read write bit base pair memory system").split()
BLOCK = 1 << 20


def _block(pattern: str, rng: np.random.Generator, n: int, index: int) -> bytes:
    if pattern == "mixed":
        pattern = ("text", "random", "binary", "repetitive")[index % 4]
    if pattern == "random":
        return rng.integers(0, 256, n, dtype=np.uint8).tobytes()
    if pattern == "repetitive":
        unit = b"VNX-DNA repetitive pattern 0123456789\n"
        return (unit * (n // len(unit) + 1))[:n]
    if pattern == "text":
        idx = rng.integers(0, len(_WORDS), n // 4 + 16)
        out = bytearray()
        for k, i in enumerate(idx.tolist()):
            out += _WORDS[i] + (b".\n" if k % 13 == 12 else b" ")
            if len(out) >= n:
                break
        return bytes(out[:n])
    if pattern == "binary":
        m = n // 16 + 1
        rec = np.zeros(m, dtype=[("id", "<u4"), ("t", "<u4"), ("v", "<f4"), ("flags", "<u2"), ("pad", "<u2")])
        rec["id"] = np.arange(index * m, index * m + m)
        rec["t"] = 1_700_000_000 + rec["id"] * 7
        rec["v"] = np.sin(rec["id"] / 100.0).astype(np.float32)
        rec["flags"] = rng.integers(0, 4, m)
        return rec.tobytes()[:n]
    raise VNXConfigurationError(f"unknown pattern {pattern!r}; choose from {PATTERNS}")


def generate(path: str | os.PathLike, size: int, pattern: str = "mixed", seed: int = 42) -> str:
    """Write ``size`` deterministic bytes; returns their SHA-256."""
    if pattern not in PATTERNS:
        raise VNXConfigurationError(f"unknown pattern {pattern!r}; choose from {PATTERNS}")
    if size < 0:
        raise VNXConfigurationError("size must be >= 0")
    h = hashlib.sha256()
    rng = np.random.default_rng([seed, PATTERNS.index(pattern)])
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        done = 0
        index = 0
        while done < size:
            n = min(BLOCK, size - done)
            b = _block(pattern, rng, n, index)
            f.write(b)
            h.update(b)
            done += n
            index += 1
    return h.hexdigest()


def parse_size(text: str | int) -> int:
    if isinstance(text, int):
        return text
    t = text.strip().upper().replace("IB", "B")
    units = {"KB": 1 << 10, "MB": 1 << 20, "GB": 1 << 30, "TB": 1 << 40, "B": 1}
    for u in ("KB", "MB", "GB", "TB", "B"):
        if t.endswith(u):
            return int(float(t[: -len(u)]) * units[u])
    return int(t)
