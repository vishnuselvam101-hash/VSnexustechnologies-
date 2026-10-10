"""CPU budget of the execution model (hybrid architecture).

Concurrency policy (docs/HYBRID_ARCHITECTURE.md §Concurrency):

* one level of parallelism at a time: worker *processes* (encode groups, decode batches, consensus batches) run their
  native kernels single-threaded (``vnxdna.native.cluster.thread_scope(1)``, the strand builder's default of one
  thread); kernel *threads* are used only when the caller runs in-process;
* the default worker count of every performance profile is bounded by :func:`available_cpus`, which respects the
  process's CPU affinity mask and a cgroup v2 CPU quota (containers, ``taskset``, systemd slices), where
  ``os.cpu_count()`` reports every CPU of the host;
* results never depend on the worker or thread count (deterministic partitioning; see the per-stage docstrings).
"""
from __future__ import annotations

import math
import os
from pathlib import Path

CGROUP_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")


def _cgroup_quota(path: Path = CGROUP_CPU_MAX) -> int | None:
    """CPUs allowed by a cgroup v2 ``cpu.max`` quota (``"<quota> <period>"``, or ``"max <period>"`` = unlimited)."""
    try:
        quota, period = path.read_text().split()[:2]
    except (OSError, ValueError):
        return None
    if quota == "max":
        return None
    try:
        q, p = int(quota), int(period)
    except ValueError:
        return None
    return max(1, math.ceil(q / p)) if q > 0 and p > 0 else None


def available_cpus() -> int:
    """CPUs this process may actually use: the affinity mask, capped by a cgroup v2 quota; at least 1."""
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    quota = _cgroup_quota()
    return max(1, min(n, quota) if quota else n)
