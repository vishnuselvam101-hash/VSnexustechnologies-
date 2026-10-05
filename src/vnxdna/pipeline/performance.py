"""Performance profiles: parallelism, batching and optional extra checks only (formerly in ``vnxdna.v4.config``).

No profile disables integrity verification: decoding always ends with the container SHA-256 + Merkle validation,
extraction always checks every chunk ID and file SHA-256.
"""
from __future__ import annotations

import os

from vnxdna.core.errors import VNXConfigurationError

PERFORMANCE_PROFILES = {
    # name: workers (None = all logical CPUs), decode batch, encode groups/task, verify-after-encode
    "safe": {"workers": 1, "batch_reads": 4096, "groups_per_task": 32, "verify_after_encode": True},
    "balanced": {"workers": max(1, min(4, (os.cpu_count() or 1))), "batch_reads": 8192, "groups_per_task": 64,
                 "verify_after_encode": False},
    "maximum-throughput": {"workers": os.cpu_count() or 1, "batch_reads": 16384, "groups_per_task": 128,
                           "verify_after_encode": False},
}


def performance(name: str | None) -> dict:
    """The named profile (default ``balanced``). An unknown name is a ``CONFIGURATION_ERROR`` (exit 7); 5.0.0 raised a
    bare ``KeyError``, reported as an internal error, exit 70 (audit §5.4)."""
    key = name or "balanced"
    if key not in PERFORMANCE_PROFILES:
        raise VNXConfigurationError(f"unknown performance profile {name!r}; available: {sorted(PERFORMANCE_PROFILES)}")
    return dict(PERFORMANCE_PROFILES[key])
