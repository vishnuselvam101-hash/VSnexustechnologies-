"""V10 opt: consensus batches in worker processes give exactly the in-process result -- the same verified frames
(every field, including payload bytes), in the same order, and the same counters. SYNTHETIC data; the read channel is
SIMULATED."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from vnxdna.dnaenc.layout import PROFILES
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster import stage as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v7"))
from cluster_support import make_strands, noisy  # noqa: E402

LAY = PROFILES["v4-balanced"][0]


def _canon(frames):
    return [{k: (v.tobytes() if isinstance(v, np.ndarray) else v) for k, v in sorted(f.items())} for f in frames]


@pytest.fixture(scope="module")
def reads(tmp_path_factory):
    _, strands = make_strands(tmp_path_factory.mktemp("cons_proc"), 1500)
    rng = np.random.default_rng(10_004)
    return [noisy(rng, s) for s in strands[:24] for _ in range(6)]


def _run(reads, workers, monkeypatch, batch):
    monkeypatch.delenv("VNXDNA_CLUSTER_THREADS", raising=False)
    monkeypatch.setattr(st, "CONSENSUS_BATCH", batch)
    counts, cons, info = Counter(), Counter(), {}
    token = st._INFO.set(info)
    try:
        frames, budget = st.cluster_frames(reads, LAY, ClusterConfig(), counts=counts, cons=cons, workers=workers)
    finally:
        st._INFO.reset(token)
    return _canon(frames), budget, counts, cons, info["consensus_processes"]


def test_processes_equal_in_process(reads, monkeypatch):
    f1, b1, c1, k1, p1 = _run(reads, 1, monkeypatch, 5)
    f3, b3, c3, k3, p3 = _run(reads, 3, monkeypatch, 5)
    assert (p1, p3) == (1, 3)
    assert f1 and f1 == f3
    assert b1 == b3 and c1 == c3 and k1 == k3


def test_batch_size_does_not_matter(reads, monkeypatch):
    assert _run(reads, 2, monkeypatch, 3)[0] == _run(reads, 1, monkeypatch, 256)[0]


def test_consensus_processes_rule(monkeypatch):
    monkeypatch.delenv("VNXDNA_CLUSTER_THREADS", raising=False)
    assert st.consensus_processes(1, 10) == 1
    assert st.consensus_processes(4, 1) == 1
    assert st.consensus_processes(4, 2) == 2
    assert st.consensus_processes(4, 10) == 4
    monkeypatch.setenv("VNXDNA_CLUSTER_THREADS", "4")       # an explicit kernel-thread choice keeps the in-process path
    assert st.consensus_processes(4, 10) == 1


def test_observer_keeps_in_process_path(reads, monkeypatch):
    """A replaced cluster_consensus (observability wrappers, e.g. the nanopore funnel's trace) must see every batch."""
    seen = []

    def observer(*a, **k):
        seen.append(len(a[1]))
        return st._CONSENSUS(*a, **k)
    monkeypatch.setattr(st, "cluster_consensus", observer)
    frames, _, _, _, procs = _run(reads, 3, monkeypatch, 5)
    assert procs == 1 and len(seen) >= 2          # every batch went through the observer, in-process
    assert frames == _run_plain(reads, monkeypatch)


def _run_plain(reads, monkeypatch):
    monkeypatch.setattr(st, "cluster_consensus", st._CONSENSUS)
    return _run(reads, 1, monkeypatch, 5)[0]


def test_default_workers_follow_thread_scope(reads, monkeypatch):
    from vnxdna.native import cluster as nc
    monkeypatch.delenv("VNXDNA_CLUSTER_THREADS", raising=False)
    monkeypatch.setattr(st, "CONSENSUS_BATCH", 5)
    info: dict = {}
    token = st._INFO.set(info)
    try:
        st.cluster_frames(reads, LAY, ClusterConfig())                 # no scope: 1 thread -> in-process
        assert info["consensus_processes"] == 1
        with nc.thread_scope(2):
            st.cluster_frames(reads, LAY, ClusterConfig())
        assert info["consensus_processes"] == 2
    finally:
        st._INFO.reset(token)


def test_checkpoint_stop_propagates(reads, monkeypatch):
    monkeypatch.delenv("VNXDNA_CLUSTER_THREADS", raising=False)
    monkeypatch.setattr(st, "CONSENSUS_BATCH", 5)
    calls = []

    def stop():
        calls.append(1)
        if len(calls) == 2:
            raise TimeoutError("budget")
    with pytest.raises(TimeoutError):
        st.cluster_frames(reads, LAY, ClusterConfig(), checkpoint=stop, workers=2)
