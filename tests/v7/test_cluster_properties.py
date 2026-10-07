"""V7 item A property tests (hypothesis; V7_ARCHITECTURE §8 "Property", directive §20): the clustering stage's verified
frames do not depend on read order (P-3) or read orientation, exact duplicates change nothing, truncated or corrupted
reads can only lose frames (explicit refusal), and every frame it ever returns equals a true frame (P-2 "never
wrong"). SYNTHETIC test data; the read channel is SIMULATED."""
from __future__ import annotations

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vnxdna.dnaenc.layout import PROFILES
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster.consensus import cluster_consensus
from vnxdna.recovery.cluster.stage import cluster_frames

from .cluster_support import frame_key, make_strands, noisy, revcomp, truth_frames

LAY = PROFILES["v4-balanced"][0]
SETTINGS = settings(max_examples=12, deadline=None, derandomize=True,
                    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])


@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    _, strands = make_strands(tmp_path_factory.mktemp("cl_prop"), 1200)
    strands = strands[:14]
    rng = np.random.default_rng(31)
    reads = [noisy(rng, s) for s in strands for _ in range(7)]
    truth = truth_frames(LAY, strands)
    base = {frame_key(f) for f in cluster_frames(reads, LAY, ClusterConfig())[0]}
    assert base and base <= truth
    return strands, reads, truth, base


def _frames(reads):
    return {frame_key(f) for f in cluster_frames(reads, LAY, ClusterConfig())[0]}


@SETTINGS
@given(seed=st.integers(0, 2**32 - 1))
def test_any_read_order_and_orientation_gives_the_same_frames(pool, seed):
    strands, reads, truth, base = pool
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(reads))
    flip = rng.random(len(reads)) < 0.5
    got = _frames([revcomp(reads[i]) if f else reads[i] for i, f in zip(order.tolist(), flip.tolist())])
    assert got == base


@SETTINGS
@given(seed=st.integers(0, 2**32 - 1), dups=st.integers(1, 30))
def test_exact_duplicates_change_nothing(pool, seed, dups):
    strands, reads, truth, base = pool
    rng = np.random.default_rng(seed)
    extra = [reads[i].copy() for i in rng.integers(0, len(reads), dups).tolist()]
    allr = reads + extra
    order = rng.permutation(len(allr))
    assert _frames([allr[i] for i in order.tolist()]) == base


@SETTINGS
@given(seed=st.integers(0, 2**32 - 1), n=st.integers(1, 40), keep=st.floats(0.05, 0.95))
def test_truncated_and_garbage_reads_never_produce_a_wrong_frame(pool, seed, n, keep):
    strands, reads, truth, base = pool
    rng = np.random.default_rng(seed)
    reads = list(reads)
    for i in rng.choice(len(reads), size=min(n, len(reads)), replace=False).tolist():
        r = reads[i]
        cut = max(1, int(keep * r.size))
        reads[i] = r[:cut] if rng.random() < 0.5 else r[-cut:]
    reads += [rng.integers(0, 5, int(rng.integers(1, 500))).astype(np.uint8) for _ in range(5)]
    got = _frames(reads)
    assert got <= truth                                   # same frames or fewer (explicit refusal), never wrong


@SETTINGS
@given(seed=st.integers(0, 2**32 - 1), k=st.integers(2, 4), per=st.integers(2, 8))
def test_mixed_strand_clusters_yield_nothing_or_a_constituent(pool, seed, k, per):
    """P-2: a cluster forced to hold reads of 2-4 different strands."""
    strands, reads, truth, base = pool
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(strands), size=k, replace=False).tolist()
    mixed = [noisy(rng, strands[i]) for i in pick for _ in range(per)]
    mixed = [mixed[i] for i in rng.permutation(len(mixed)).tolist()]
    fr = cluster_consensus(LAY, [(0, mixed)], ClusterConfig())
    allowed = truth_frames(LAY, [strands[i] for i in pick])
    assert all(frame_key(f) in allowed for f in fr)
