"""V10 candidate step: bounded memory (range-partitioned slot pairs), workload-aware budget, explicit stop when memory
cannot be allocated. The partitioned kernel must equal the reference and the single-buffer kernel byte for byte."""
import ctypes
import subprocess
import sys
import textwrap
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from vnxdna.native import cluster as nc
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster import graph

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v7"))
from native_cluster_support import candidates_case  # noqa: E402

pytestmark = pytest.mark.skipif(not nc.available(), reason="native cluster kernels not built")
ROOT = Path(__file__).resolve().parents[2]


def _single_buffer(case):
    """The ABI-3 single-call form (one buffer for every slot pair) as the in-kernel reference."""
    lib = ctypes.CDLL(nc.status()["library"])
    nc._bind(lib)
    h = np.ascontiguousarray(case["hashes"], np.uint32)
    o = np.ascontiguousarray(case["orient"], np.uint8)
    n, s = h.shape
    st = np.zeros(7, np.int64)
    cap = 16
    while True:
        a, b, r = np.empty(cap, np.int64), np.empty(cap, np.int64), np.empty(cap, np.uint8)
        rc = lib.vnx_cl_candidates(n, s, h.ctypes.data, o.ctypes.data, case["bucket_cap"], case["max_pairs"],
                                   case["min_shared"], a.ctypes.data, b.ctypes.data, r.ctypes.data, cap, st.ctypes.data)
        assert rc >= 0
        if rc == 1:
            cap = int(st[3])
            continue
        k = 0 if st[2] else int(st[3])
        return a[:k], b[:k], r[:k], st


def _clustered(rng, n, s, group):
    """n reads in true clusters of ``group`` (shared hashes per slot) plus noise: a dense, realistic bucket layout."""
    cid = rng.permutation(np.arange(n) // group)
    base = rng.integers(0, 1 << 32, (n // group + 1, s), dtype=np.uint64)
    h = base[cid].astype(np.uint32)
    noise = rng.random((n, s)) < 0.1
    h[noise] = rng.integers(0, 1 << 32, int(noise.sum()), dtype=np.uint64).astype(np.uint32)
    o = rng.integers(0, 2, (n, s)).astype(np.uint8)
    o[rng.random((n, s)) < 0.02] = 2
    return h, o


@pytest.mark.parametrize("seed", range(40))
def test_any_chunk_size_equals_reference(seed):
    rng = np.random.default_rng(1000 + seed)
    case = candidates_case(rng)
    ref_counts: Counter = Counter()
    ra, rb, rr, rbud = graph.candidate_pairs_reference(case["hashes"], case["orient"], case["bucket_cap"],
                                                       case["max_pairs"], ref_counts, case["min_shared"])
    for chunk in (1, 2, 7, 64, nc.DEFAULT_CHUNK_PAIRS):
        counts: Counter = Counter()
        a, b, r, bud = graph.candidate_pairs(case["hashes"], case["orient"], case["bucket_cap"], case["max_pairs"],
                                             counts, case["min_shared"], chunk)
        assert np.array_equal(a, ra) and np.array_equal(b, rb) and np.array_equal(r, rr), chunk
        assert bud == rbud and counts == ref_counts, chunk


def test_partition_boundaries_and_buffer_bound():
    rng = np.random.default_rng(7)
    h, o = _clustered(rng, 20_000, 16, 10)
    case = {"hashes": h, "orient": o, "bucket_cap": 256, "max_pairs": 1 << 40, "min_shared": 2}
    sa, sb, sr, sst = _single_buffer(case)
    for chunk in (1, 1000, 50_000, 1 << 40):
        a, b, r, st = nc.candidates(h, o, 256, 1 << 40, 2, chunk)
        assert np.array_equal(a, sa) and np.array_equal(b, sb) and np.array_equal(r, sr)
        assert np.array_equal(st[:7], sst)
        assert st[7] >= 1 and st[8] >= 1
        if chunk == 1:  # one read per range: every read with pairs is its own partition
            assert st[7] > 1000
        if chunk >= sst[1]:
            assert st[7] == 1 and st[8] == sst[1]      # single range: the whole buffer, as before
        else:
            # a range holds at most `chunk` slot pairs, or one read's pairs (<= s * (bucket_cap - 1)) if more
            assert st[8] <= max(chunk, 16 * 255)


def test_budget_stop_matches_reference_and_holds_no_pairs():
    rng = np.random.default_rng(3)
    h, o = _clustered(rng, 5_000, 8, 10)
    for limit in (0, 1, 999, 10_000):
        counts: Counter = Counter()
        a, b, r, bud = graph.candidate_pairs(h, o, 256, limit, counts, 2, 64)
        ref: Counter = Counter()
        _, _, _, rbud = graph.candidate_pairs_reference(h, o, 256, limit, ref, 2)
        assert bud == rbud and bud["limit"] == "max_candidate_pairs" and a.size == 0 and counts == ref


def test_invalid_arguments_are_rejected():
    lib = ctypes.CDLL(nc.status()["library"])
    nc._bind(lib)
    h = np.zeros((4, 2), np.uint32)
    o = np.zeros((4, 2), np.uint8)
    st = np.zeros(9, np.int64)
    hd = ctypes.c_void_p()
    run = lambda n, cap, mp, chunk: lib.vnx_cl_cand_run(n, 2, h.ctypes.data, o.ctypes.data, cap, mp, 1, chunk,  # noqa: E731
                                                        st.ctypes.data, ctypes.byref(hd))
    assert run(4, 256, 10, 0) == -1                   # chunk < 1
    assert run(4, 256, -1, 1) == -1                   # negative budget
    assert run(4, -1, 10, 1) == -1                    # negative bucket cap
    assert run((1 << 31) + 1, 256, 10, 1) == -1       # index would not fit 31 bits / key 62 bits (checked before reading)
    assert lib.vnx_cl_cand_run(4, 2, h.ctypes.data, o.ctypes.data, 256, 10, 1, 1, None, ctypes.byref(hd)) == -1
    assert run(4, 256, 1 << 40, 1) == 0 and hd.value
    try:
        assert st[3] == 6                             # 4 equal reads, 2 slots: C(4,2) pairs
        a = np.empty(6, np.int64)
        assert lib.vnx_cl_cand_take(hd, 4, a.ctypes.data, a.ctypes.data, a.ctypes.data, 5) == -1   # cap < kept
        assert lib.vnx_cl_cand_take(None, 4, None, None, None, 0) == -1
    finally:
        lib.vnx_cl_cand_free(hd)
    lib.vnx_cl_cand_free(None)                        # no-op


def test_workload_aware_budget():
    c = ClusterConfig()
    assert c.candidate_budget(0) == c.max_candidate_pairs
    assert c.candidate_budget(10) == c.max_candidate_pairs                       # floor: never below the absolute budget
    big = c.max_candidate_pairs // c.max_candidate_pairs_per_read * 3
    assert c.candidate_budget(big) == c.max_candidate_pairs_per_read * big       # scales with the reads
    assert c.candidate_budget(10**12) == c.max_candidate_pairs_ceiling           # bounded: no overflow, ceiling holds
    off = ClusterConfig(max_candidate_pairs_per_read=0)
    assert off.candidate_budget(10**9) == off.max_candidate_pairs                # 0 per read = V9 behaviour
    low = ClusterConfig(max_candidate_pairs=100, max_candidate_pairs_ceiling=10)
    assert low.candidate_budget(10**6) == 100                                    # a ceiling below the floor never lowers it
    from vnxdna.core.errors import VNXConfigurationError
    for bad in ({"max_candidate_pairs_per_read": -1}, {"max_candidate_pairs_ceiling": -1}, {"candidate_chunk_pairs": 0}):
        with pytest.raises(VNXConfigurationError):
            ClusterConfig(**bad).validate()


def test_cluster_reads_uses_the_scaled_budget():
    rng = np.random.default_rng(11)
    h, o = _clustered(rng, 2_000, 8, 10)
    need = int(nc.candidates(h, o, 256, 1 << 40, 2)[3][1])
    reads = [np.zeros(1, np.uint8)] * 2_000
    stop = graph.cluster_reads(reads, h, o, ClusterConfig(max_candidate_pairs=need - 1, max_candidate_pairs_per_read=0))
    assert stop.budget and stop.budget["limit"] == "max_candidate_pairs"
    per_read = -(-need // 2_000)
    go = graph.cluster_reads(reads, h, o, ClusterConfig(max_candidate_pairs=need - 1, max_candidate_pairs_per_read=per_read))
    assert go.budget is None or go.budget["limit"] != "max_candidate_pairs"
    assert go.counts["candidate_pairs"] > 0                                     # the pairs were produced


def test_allocation_failure_is_an_explicit_stop_not_a_crash(tmp_path):
    """Address-space limit just above the inputs: the kernel's allocation fails, the step reports `candidate_memory`
    and returns no pairs; the process survives."""
    code = textwrap.dedent(f"""
        import resource, sys
        from collections import Counter
        import numpy as np
        sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
        from test_candidates_chunked import _clustered
        from vnxdna.recovery.cluster import graph
        h, o = _clustered(np.random.default_rng(5), 400_000, 32, 10)
        graph.candidate_pairs(h[:50], o[:50], 256, 1 << 40, Counter(), 2)        # load the library first
        with open('/proc/self/statm') as f:
            vm = int(f.read().split()[0]) * resource.getpagesize()
        resource.setrlimit(resource.RLIMIT_AS, (vm + (24 << 20), resource.RLIM_INFINITY))
        counts = Counter()
        a, b, r, bud = graph.candidate_pairs(h, o, 256, 1 << 40, counts, 2)
        print(bud["limit"], a.size, counts["candidate_memory_exhausted"])
    """)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
                       env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert p.returncode == 0, p.stderr[-2000:]
    assert p.stdout.split() == ["candidate_memory", "0", "1"]


def test_memory_stop_fails_the_stage_explicitly(monkeypatch):
    def boom(*a, **k):
        raise nc.NativeClusterMemoryError("native candidates failed (-2: out of memory)")
    monkeypatch.setattr(nc, "candidates", boom)
    rng = np.random.default_rng(2)
    h, o = _clustered(rng, 300, 8, 10)
    cl = graph.cluster_reads([np.zeros(1, np.uint8)] * 300, h, o, ClusterConfig())
    assert cl.budget["limit"] == "candidate_memory" and (cl.labels == -1).all() and cl.counts["unassigned"] == 300
    from vnxdna.recovery.cluster.stage import CANDIDATE_STOPS
    assert "candidate_memory" in CANDIDATE_STOPS


@pytest.mark.slow
def test_large_workload_bounded_buffer():
    """~2 M reads' worth of slot pairs in small ranges: identical to the single buffer, buffer bounded by the chunk."""
    rng = np.random.default_rng(9)
    h, o = _clustered(rng, 200_000, 32, 10)
    case = {"hashes": h, "orient": o, "bucket_cap": 256, "max_pairs": 1 << 40, "min_shared": 2}
    sa, sb, sr, sst = _single_buffer(case)
    a, b, r, st = nc.candidates(h, o, 256, 1 << 40, 2, 1 << 22)
    assert np.array_equal(a, sa) and np.array_equal(b, sb) and np.array_equal(r, sr)
    assert np.array_equal(st[:7], sst) and st[7] > 1 and st[8] <= 1 << 22 and sst[1] > 4 * (1 << 22)
