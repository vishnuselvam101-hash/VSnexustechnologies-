"""Candidate step over its slot-pair budget (job 86): instead of dropping the whole clustering stage, the bucket cap is
lowered to the largest c >= bucket_cap_floor whose slot pairs fit, the larger buckets are ignored and counted, and the
stage continues. Native and reference agree byte for byte; without a fitting cap the stop is the floor-less stop."""
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.native import cluster as nc
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster import graph

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v10"))
from test_candidates_chunked import _clustered  # noqa: E402

native = pytest.mark.skipif(not nc.available(), reason="native cluster kernels not built")


def _pool(seed, n=3_000, s=8):
    """Clusters of mixed sizes (2..80 reads) so that buckets span many sizes."""
    rng = np.random.default_rng(seed)
    parts = [_clustered(rng, n // 3, s, g) for g in (3, 25, 80)]
    return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])


def _slots(h, o, cap):
    """Slot pairs at bucket cap ``cap`` (no budget)."""
    total = 0
    for i in range(h.shape[1]):
        _, c = np.unique(h[o[:, i] < 2, i], return_counts=True)
        c = c[(c >= 2) & (c <= cap)]
        total += int((c * (c - 1) // 2).sum())
    return total


def test_reference_lowers_to_the_largest_fitting_cap_and_equals_a_run_at_that_cap():
    h, o = _pool(1)
    need = _slots(h, o, 256)
    budget = need - 1
    counts: Counter = Counter()
    a, b, r, rec = graph.candidate_pairs_reference(h, o, 256, budget, counts, 2, cap_floor=2)
    cap = rec["bucket_cap"]
    assert rec["stopped"] is False and rec["limit"] == "max_candidate_pairs" and rec["requested"] == need
    assert 2 <= cap < 256 and _slots(h, o, cap) <= budget < _slots(h, o, cap + 1)    # the largest cap that fits
    assert rec["used"] == _slots(h, o, cap) == counts["candidate_pairs_slots"]
    plain: Counter = Counter()
    pa, pb, pr, prec = graph.candidate_pairs_reference(h, o, cap, 1 << 62, plain, 2)
    assert prec is None and np.array_equal(a, pa) and np.array_equal(b, pb) and np.array_equal(r, pr)
    assert counts["candidate_pairs"] == plain["candidate_pairs"] > 0
    assert counts["buckets_over_budget_cap"] == rec["buckets_dropped"] > 0
    assert counts["candidate_bucket_cap"] == cap


def test_no_fitting_cap_above_the_floor_is_the_floorless_stop():
    h, o = _pool(2)
    for floor in (None, 255, 256, 10_000):
        for budget in (0, 5, 1_000):
            got: Counter = Counter()
            ref: Counter = Counter()
            g = graph.candidate_pairs_reference(h, o, 256, budget, got, 2, cap_floor=floor)
            f = graph.candidate_pairs_reference(h, o, 256, budget, ref, 2)
            assert g[3] == f[3] and g[3]["limit"] == "max_candidate_pairs" and g[0].size == 0 and got == ref
            assert graph.is_stop(g[3])


@native
@pytest.mark.parametrize("seed", range(12))
def test_native_equals_reference_with_a_floor(seed):
    rng = np.random.default_rng(500 + seed)
    h, o = _pool(seed, n=int(rng.integers(300, 3_000)), s=int(rng.choice([4, 8, 16])))
    need = _slots(h, o, 256)
    for budget in (0, need // 50, need // 3, need - 1, need):
        for floor in (None, 2, 8, 64, 300):
            for chunk in (1, 97, 1 << 25):
                got: Counter = Counter()
                ref: Counter = Counter()
                a, b, r, rec = graph.candidate_pairs(h, o, 256, budget, got, 2, chunk, floor)
                ra, rb, rr, rrec = graph.candidate_pairs_reference(h, o, 256, budget, ref, 2, floor)
                assert rec == rrec and got == ref
                assert np.array_equal(a, ra) and np.array_equal(b, rb) and np.array_equal(r, rr)


@native
def test_native_stats_report_the_lowered_cap():
    h, o = _pool(3)
    need = _slots(h, o, 256)
    st = nc.candidates(h, o, 256, need - 1, 2, 1 << 25, 2)[3]
    assert st[2] == 0 and 2 <= st[9] < 256 and st[10] > 0 and st[11] == need and st[1] <= need - 1
    st0 = nc.candidates(h, o, 256, need - 1, 2)[3]                  # no floor: the stop, nothing lowered
    assert st0[2] == 1 and st0[9] == st0[10] == st0[11] == 0


def test_cluster_reads_keeps_clustering_and_reports_the_record():
    h, o = _pool(4)
    need = _slots(h, o, 256)
    reads = [np.zeros(1, np.uint8)] * h.shape[0]
    cfg = ClusterConfig(max_candidate_pairs=need - 1, max_candidate_pairs_per_read=0, bucket_cap_floor=2)
    cl = graph.cluster_reads(reads, h, o, cfg)
    assert not graph.is_stop(cl.budget) and cl.budget["stopped"] is False
    assert cl.counts["clustered_reads"] > 0 and cl.counts["candidate_bucket_cap"] == cl.budget["bucket_cap"]
    off = graph.cluster_reads(reads, h, o, ClusterConfig(max_candidate_pairs=need - 1, max_candidate_pairs_per_read=0,
                                                         bucket_cap_floor=256))
    assert graph.is_stop(off.budget) and (off.labels == -1).all()   # a floor at the cap: the old stop


def test_config_default_and_validation():
    assert ClusterConfig().bucket_cap_floor == 2 and ClusterConfig().to_dict()["bucket_cap_floor"] == 2
    with pytest.raises(VNXConfigurationError):
        ClusterConfig(bucket_cap_floor=-1).validate()
    assert graph.is_stop([{"limit": "max_candidate_pairs", "stopped": False}, {"limit": "candidate_memory"}])
    assert not graph.is_stop([{"limit": "max_candidate_pairs", "stopped": False}, {"limit": "max_refine_pairs"}])
    assert not graph.is_stop(None)
