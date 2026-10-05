"""V7 A2: the native read-clustering kernels (``vnxdna.native.cluster``, ``native/c/cluster.c``) equal the NumPy
reference in ``vnxdna.recovery.cluster`` bit for bit (V7_ARCHITECTURE §5.7, §8 "Native = reference"; protocol P6).

* golden hashes (``native_cluster_golden.json``, written from the reference by
  ``benchmarks/v7/native_cluster/make_golden.py``): both backends must reproduce them;
* randomized equivalence per kernel (sketch, candidate pairs, banded distance, verification/clustering,
  forward-backward), at least 100,000 reads for the sketch, the distance and the forward-backward kernel, plus
  hypothesis campaigns at the ctypes boundary;
* every forward-backward variant (scalar / AVX2, int16 / int32 lanes, 1-4 threads) gives the same calls;
* backend selection (auto / native / reference), fallback when the library is missing or has another ABI, and the C
  argument checks (sizes from Python are never trusted);
* a whole decode with read clustering on: identical outcome, container and report on both backends.

SYNTHETIC data; the read channel is SIMULATED.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vnxdna.native import cluster as nc
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster import consensus, editdist, graph, sketch

from .native_cluster_support import (CASES, backend, case_for, fb_case, mutate, pad, pairs_case, run_kernel, same,
                                     sketch_case)

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "native_cluster_golden.json"
HYP = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@pytest.fixture(scope="module", autouse=True)
def _library(tmp_path_factory):
    """The native library: installed / in-place / VNXDNA_CLUSTER_LIB, else built here (skip without a compiler)."""
    saved = os.environ.get(nc.ENV_LIB)
    nc._reset_for_tests()
    if not nc.available():
        try:
            lib = nc.build(tmp_path_factory.mktemp("cluster") / "libvnx_cluster.so")
        except nc.NativeClusterError as error:
            pytest.skip(f"native cluster kernel unavailable and cannot be built here: {error}")
        os.environ[nc.ENV_LIB] = str(lib)
        nc._reset_for_tests()
    assert nc.available()
    yield
    if saved is None:
        os.environ.pop(nc.ENV_LIB, None)
    else:
        os.environ[nc.ENV_LIB] = saved
    nc._reset_for_tests()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(nc.ENV, raising=False)
    monkeypatch.delenv("VNXDNA_CLUSTER_THREADS", raising=False)


def _both(kind: str, case):
    ref = run_kernel(kind, case, "reference")
    nat = run_kernel(kind, case, "native")
    assert same(ref, nat), kind
    return ref


# ------------------------------------------------------------------------------------------------ golden hashes
def test_golden_hashes_on_both_backends():
    from .native_cluster_support import digest
    want = json.loads(GOLDEN.read_text())
    assert want["schema"] == "vnx.native-cluster-golden/1" and len(want["cases"]) >= 90
    for rec in want["cases"]:
        case = case_for(rec["kernel"], rec["seed"])
        for name in ("reference", "native"):
            got = digest(*run_kernel(rec["kernel"], case, name))
            assert got == rec["sha256"], (rec, name)


# ------------------------------------------------------------------------------------------------ randomized equivalence
@pytest.mark.parametrize("kind", sorted(CASES))
def test_random_cases_equal(kind):
    for seed in range(1000, 1000 + (40 if kind == "pool" else 150)):
        _both(kind, case_for(kind, seed))


def test_sketch_100k_reads():
    rng = np.random.default_rng(71)
    total = 0
    while total < 100_000:
        case = sketch_case(rng, n=5000)
        case["s"] = min(case["s"], 32)
        _both("sketch", case)
        total += case["raw"].shape[0]
    assert total >= 100_000


def test_banded_distance_100k_pairs():
    rng = np.random.default_rng(72)
    total = 0
    while total < 100_000:
        case = pairs_case(rng, p=4000, lmax=int(rng.choice([1, 20, 40, 64, 65])))
        _both("banded", case)
        total += len(case["a"])
    assert total >= 100_000


def test_forward_backward_100k_reads():
    rng = np.random.default_rng(73)
    total = 0
    while total < 100_000:
        case = fb_case(rng, n=int(rng.choice([1, 15, 16, 17, 513, 2500])), T=int(rng.choice([0, 1, 9, 30])))
        _both("fb", case)
        total += len(case["reads"])
    assert total >= 100_000


def test_banded_distance_of_long_reads_and_wide_bands():
    """Several 64-bit words per column (Myers blocks), the exact band program where the band matters, slack 0."""
    rng = np.random.default_rng(74)
    for L in (63, 64, 65, 127, 128, 129, 500, 1100):
        x = [rng.integers(0, 4, L).astype(np.uint8) for _ in range(30)]
        y = [mutate(rng, s, r) for s, r in zip(x, np.linspace(0, 0.9, 30))]
        for slack in (0, 1, 7, 32, 2000):
            _both("banded", {"a": x, "b": y, "slack": slack})
            _both("banded", {"a": y, "b": x, "slack": slack})


def test_clustering_of_strand_pools_with_refinement_and_budgets():
    from .native_cluster_support import pool_case
    rng = np.random.default_rng(75)
    seen = Counter()
    for t in range(30):
        # every third pool: few strands with many reads, so a verification chunk (512 pairs) skips connected pairs
        reads, cfg = pool_case(rng, 3, 45) if t % 3 == 0 else pool_case(rng)
        out = _both("pool", (reads, cfg))
        counts = out[5]
        seen.update(k for k, v in counts.items() if v)
    # the cases reach the refinement, both budgets, skipped pairs and length rejections
    for key in ("components_refined", "pairs_skipped_same_component", "pairs_verified", "edges_rejected",
                "pairs_rejected_length", "candidate_pairs_below_min_shared", "buckets_over_cap"):
        assert seen[key] > 0, key


def test_counters_created_by_the_reference_are_created_natively():
    """A Counter keeps keys incremented by 0 and reports list them: the native path creates exactly the same keys."""
    for seed in range(40):
        reads, cfg = case_for("pool", 5000 + seed)
        ref = run_kernel("pool", (reads, cfg), "reference")[5]
        nat = run_kernel("pool", (reads, cfg), "native")[5]
        assert list(ref) == list(nat) and ref == nat


def test_native_path_really_runs_in_domain(monkeypatch):
    """The dispatchers must not silently fall back to the reference for normal inputs."""
    def boom(*a, **k):
        raise AssertionError("reference called")
    monkeypatch.setenv(nc.ENV, "native")
    monkeypatch.setattr(sketch, "sketch_reads_reference", boom)
    monkeypatch.setattr(graph, "candidate_pairs_reference", boom)
    monkeypatch.setattr(graph, "_verify_reference", boom)
    monkeypatch.setattr(editdist, "banded_distance_reference", boom)
    monkeypatch.setattr(consensus, "fb_calls_reference", boom)
    for seed in range(10):
        run_kernel("pool", case_for("pool", 7000 + seed), "native")
        run_kernel("fb", case_for("fb", 7000 + seed), "native")


def test_out_of_domain_inputs_route_to_the_reference():
    rng = np.random.default_rng(76)
    case = fb_case(rng, n=20, T=24)
    case["mc"] = case["mc"] + 2000                         # costs above the kernel's domain
    _both("fb", case)
    reads = [rng.integers(0, 4, 50).astype(np.uint8) for _ in range(6)]
    reads[2][3] = 9                                         # the reference cannot reverse-complement code 9
    raw, lens = pad(reads)
    _both("sketch", {"raw": raw, "lengths": lens, "k": 12, "s": 8})
    _both("banded", {"a": reads[:3], "b": reads[3:], "slack": 5})     # no reverse complement: in the native domain
    for name in ("reference", "native"):
        with backend(name), pytest.raises(IndexError):
            # s = 257 is outside the native domain: the dispatcher gives the reference's answer, here its error
            sketch.sketch_reads(raw, lens, 12, 257)


# ------------------------------------------------------------------------------------------------ forward-backward variants
def _fits16(case) -> bool:
    lens = [r.size for r in case["reads"]]
    T = case["tpl"].shape[1]
    step = max(int(case["mc"].max(initial=0)), case["c_indel"], 1)
    return (T + max(lens, default=0) + 2) * step + case["slack"] < 15000


def test_forward_backward_every_level_width_and_thread_count():
    levels = [1] + ([2] if nc.fb_level() == "avx2" else [])
    for seed in range(120):
        case = case_for("fb", 9000 + seed)
        ref = consensus.fb_calls_reference(case["tpl"], case["mc"], case["reads"], case["band"], case["c_indel"],
                                           case["slack"])
        buf, off, lens = nc.pack(case["reads"])
        B = int(case["band"].max(initial=0))
        widths = [0, 32] + ([16] if _fits16(case) else [])
        for level in levels:
            for width in widths:
                for threads in (1, 3):
                    got = nc.fb(case["tpl"], case["mc"], buf, off, lens, case["band"], B, case["c_indel"],
                                case["slack"], level=level, nthreads=threads, width=width)
                    assert same(ref, got), (seed, level, width, threads)


def test_forward_backward_threads_env_does_not_change_results(monkeypatch):
    rng = np.random.default_rng(77)
    case = fb_case(rng, n=700, T=40)
    one = run_kernel("fb", case, "native")
    monkeypatch.setenv("VNXDNA_CLUSTER_THREADS", "4")
    assert nc.threads() == 4
    assert same(one, run_kernel("fb", case, "native"))


def test_int16_lanes_refused_when_the_bound_does_not_hold():
    rng = np.random.default_rng(78)
    case = fb_case(rng, n=4, T=30)
    case["mc"][:] = 1000
    buf, off, lens = nc.pack(case["reads"])
    with pytest.raises(nc.NativeClusterError):
        nc.fb(case["tpl"], case["mc"], buf, off, lens, case["band"], int(case["band"].max()), 1000, 0, width=16)


# ------------------------------------------------------------------------------------------------ hypothesis (ctypes boundary)
codes = st.lists(st.integers(0, 7), max_size=90).map(lambda x: np.array(x, dtype=np.uint8))


@HYP
@given(st.lists(codes, min_size=1, max_size=12), st.integers(1, 31), st.integers(1, 40))
def test_hyp_sketch(reads, k, s):
    raw, lens = pad(reads)
    _both("sketch", {"raw": raw, "lengths": lens, "k": k, "s": s})


@HYP
@given(st.lists(st.tuples(codes, codes), min_size=1, max_size=10), st.integers(0, 70))
def test_hyp_banded(pairs, slack):
    _both("banded", {"a": [p[0] for p in pairs], "b": [p[1] for p in pairs], "slack": slack})


@HYP
@given(st.integers(0, 40), st.integers(1, 6), st.integers(1, 6), st.integers(2, 12), st.integers(0, 60),
       st.integers(1, 3), st.randoms(use_true_random=False))
def test_hyp_candidates(n, s, alphabet, cap, max_pairs, min_shared, rnd):
    rng = np.random.default_rng(rnd.getrandbits(32))
    case = {"hashes": rng.integers(0, alphabet, (n, s)).astype(np.uint32),
            "orient": rng.integers(0, 3, (n, s)).astype(np.uint8), "bucket_cap": cap, "max_pairs": max_pairs,
            "min_shared": min_shared}
    _both("candidates", case)


@HYP
@given(st.integers(0, 30), st.integers(0, 12), st.lists(codes, min_size=1, max_size=8), st.integers(0, 30),
       st.integers(0, 4), st.randoms(use_true_random=False))
def test_hyp_forward_backward(T, B, reads, c_indel, slack, rnd):
    rng = np.random.default_rng(rnd.getrandbits(32))
    n = len(reads)
    tpl = rng.integers(-1, 6, (n, T)).astype(np.int16)
    mc = rng.integers(0, 12, (n, T)).astype(np.int32)
    band = rng.integers(0, B + 1, n).astype(np.int64)
    _both("fb", {"tpl": tpl, "mc": mc, "reads": reads, "band": band, "c_indel": c_indel, "slack": slack})


@HYP
@given(st.integers(0, 1 << 30), st.integers(1, 4))
def test_hyp_strand_pools(seed, strands):
    from .native_cluster_support import pool_case
    _both("pool", pool_case(np.random.default_rng(seed), strands))


# ------------------------------------------------------------------------------------------------ backend selection
def test_backend_names_and_status():
    assert nc.requested_backend() == "auto" and nc.resolve_backend() == "native"
    assert nc.resolve_backend("reference") == "reference" and nc.resolve_backend("NATIVE") == "native"
    with pytest.raises(ValueError):
        nc.requested_backend("bogus")
    st_ = nc.status()
    assert st_["active_backend"] == "native" and st_["native_available"] and st_["abi_version"] == nc.ABI_VERSION
    assert st_["fb_level"] in ("scalar", "avx2") and st_["error"] is None


def test_invalid_backend_variable_is_reported_and_raised(monkeypatch):
    monkeypatch.setenv(nc.ENV, "bogus")
    assert "bogus" in nc.status()["error"] and nc.status()["active_backend"] is None
    with pytest.raises(ValueError):
        editdist.banded_distance([np.zeros(3, np.uint8)], [np.zeros(3, np.uint8)])


def test_missing_library_falls_back_with_one_warning(monkeypatch, caplog):
    monkeypatch.setattr(nc, "_candidates", lambda: [])
    nc._reset_for_tests()
    try:
        case = case_for("pool", 3)
        with caplog.at_level(logging.WARNING, logger=nc.__name__):
            assert nc.resolve_backend() == "reference"
            out = run_kernel("pool", case, "auto")
            assert nc.resolve_backend() == "reference"
        warnings = [r for r in caplog.records if "unavailable" in r.getMessage()]
        assert len(warnings) == 1
        with backend("native"), pytest.raises(nc.NativeClusterError):
            nc.resolve_backend()
        st_ = nc.status()
        assert st_["native_available"] is False and st_["library"] is None and st_["load_error"]
        with pytest.raises(nc.NativeClusterError):
            nc.sketch(np.zeros((1, 5), np.uint8), np.array([5]), 3, 2)
    finally:
        monkeypatch.undo()
        nc._reset_for_tests()
    assert same(out, run_kernel("pool", case, "native"))


def test_library_with_another_abi_is_skipped(tmp_path, monkeypatch):
    lib = nc.build(tmp_path / "abi99.so", extra_flags=["-DVNX_CL_ABI=99"])
    monkeypatch.setenv(nc.ENV_LIB, str(lib))
    nc._reset_for_tests()
    try:
        assert nc._candidates()[0] == lib                  # the explicit path is tried first ...
        assert nc.status()["library"] != str(lib)          # ... and skipped (a later candidate or the reference runs)
        monkeypatch.setattr(nc, "_candidates", lambda: [lib])
        nc._reset_for_tests()
        st_ = nc.status()
        assert not nc.available() and st_["active_backend"] == "reference" and "ABI 99 != 1" in st_["load_error"]
    finally:
        monkeypatch.undo()
        nc._reset_for_tests()


def test_native_status_lists_the_cluster_kernel():
    import vnxdna
    k = vnxdna.native_status()["kernels"]["cluster"]
    assert k["backend"] == "native" and k["abi_version"] == nc.ABI_VERSION and k["simd_level"] in ("scalar", "avx2")
    assert k["reference"] == "vnxdna.recovery.cluster"


# ------------------------------------------------------------------------------------------------ C argument checks
def _p(a):
    return a.ctypes.data


def test_c_rejects_inconsistent_sizes():
    lib = nc._load()
    buf = np.zeros(10, np.uint8)
    off = np.array([0, 8], np.int64)
    ln = np.array([5, 5], np.int64)                      # the second sequence ends beyond the 10-byte buffer
    ia = np.array([0], np.int64)
    ib = np.array([1], np.int64)
    out = np.zeros(1, np.int64)
    assert lib.vnx_cl_banded(2, _p(buf), buf.size, _p(off), _p(ln), 1, _p(ia), _p(ib), None, 4, _p(out)) == -1
    ln[1] = 2
    assert lib.vnx_cl_banded(2, _p(buf), buf.size, _p(off), _p(ln), 1, _p(ia), _p(ib), None, 4, _p(out)) == 0
    ib[0] = 2                                             # pair index out of range
    assert lib.vnx_cl_banded(2, _p(buf), buf.size, _p(off), _p(ln), 1, _p(ia), _p(ib), None, 4, _p(out)) == -1
    ib[0] = 1
    ln[0] = -1
    assert lib.vnx_cl_banded(2, _p(buf), buf.size, _p(off), _p(ln), 1, _p(ia), _p(ib), None, 4, _p(out)) == -1
    ln[0] = 5
    assert lib.vnx_cl_banded(2, _p(buf), buf.size, _p(off), _p(ln), 1, _p(ia), _p(ib), None, -1, _p(out)) == -1
    # verification: pmax < 1, a pair index out of range, a sequence outside the buffer
    root = np.zeros(2, np.int64)
    ori = np.zeros(2, np.uint8)
    best = np.zeros(2)
    cnt = np.zeros(5, np.int64)
    rel = np.zeros(1, np.uint8)
    for pmax, b_idx, nbuf in ((0, 1, 10), (5, 2, 10), (5, 1, 9)):
        pm = np.array([pmax], np.int64)
        ib[0] = b_idx
        assert lib.vnx_cl_verify(2, _p(buf), nbuf, _p(off), _p(ln), 1, _p(ia), _p(ib), _p(rel), _p(pm), 0.3, 4, 512,
                                 _p(root), _p(ori), _p(best), _p(cnt)) == -1
    # sketch: more rows than the raw buffer holds; k and s outside the domain
    raw = np.zeros((3, 8), np.uint8)
    lens = np.full(3, 8, np.int64)
    h = np.zeros((4, 2), np.uint32)
    o = np.zeros((4, 2), np.uint8)
    assert lib.vnx_cl_sketch(4, 8, _p(raw), raw.size, _p(lens), 3, 2, _p(h), _p(o)) == -1
    assert lib.vnx_cl_sketch(3, 8, _p(raw), raw.size, _p(lens), 32, 2, _p(h), _p(o)) == -1
    assert lib.vnx_cl_sketch(3, 8, _p(raw), raw.size, _p(lens), 3, 257, _p(h), _p(o)) == -1
    assert lib.vnx_cl_sketch(3, 8, _p(raw), raw.size, _p(lens), 3, 2, _p(h), _p(o)) == 0
    # forward-backward: band above B, B above 512, costs above 1024, unknown level/width, read outside the buffer
    T = 4
    tpl = np.zeros((2, T), np.int16)
    mc = np.ones((2, T), np.int32)
    calls = np.zeros((2, T), np.uint8)
    opt = np.zeros(2, np.int64)
    off2 = np.array([0, 4], np.int64)
    ln2 = np.array([4, 4], np.int64)

    def fb(B=2, band=(1, 1), cost=1, level=0, width=0, nbuf=10, c_indel=6):
        mc[:] = cost
        bd = np.array(band, np.int64)
        return lib.vnx_cl_fb(2, T, B, _p(tpl), _p(mc), _p(buf), nbuf, _p(off2), _p(ln2), _p(bd), c_indel, 0,
                             _p(calls), _p(opt), level, width)
    assert fb() == 0
    assert fb(band=(1, 3)) == -1
    assert fb(B=513, band=(1, 1)) == -1
    assert fb(cost=1025) == -1
    assert fb(c_indel=-1) == -1
    assert fb(level=3) == -1
    assert fb(width=8) == -1
    assert fb(nbuf=7) == -1
    # candidates: more than 2^31 reads (key overflow) and s < 1
    st_ = np.zeros(8, np.int64)
    hh = np.zeros((2, 1), np.uint32)
    oo = np.zeros((2, 1), np.uint8)
    assert lib.vnx_cl_candidates((1 << 31) + 1, 1, _p(hh), _p(oo), 4, 10, 1, None, None, None, 0, _p(st_)) == -1
    assert lib.vnx_cl_candidates(2, 0, _p(hh), _p(oo), 4, 10, 1, None, None, None, 0, _p(st_)) == -1
    assert lib.vnx_cl_abi_version() == nc.ABI_VERSION and ctypes.sizeof(ctypes.c_int64) == 8


def test_python_wrappers_check_shapes():
    with pytest.raises(ValueError):
        nc.sketch(np.zeros((3, 4), np.uint8), np.zeros(2, np.int64), 3, 2)
    buf, off, lens = nc.pack([np.zeros(3, np.uint8), np.zeros(2, np.uint8)])
    with pytest.raises(ValueError):
        nc.banded(buf, off, lens, np.array([0]), np.array([1, 0]), None, 3)
    with pytest.raises(ValueError):
        nc.verify(buf, off, lens[:1], np.array([0]), np.array([1]), np.array([0]), np.array([3]), 0.3, 3, 512)


def test_empty_inputs():
    for name in ("reference", "native"):
        with backend(name):
            h, o = sketch.sketch_reads(np.zeros((0, 5), np.uint8), np.zeros(0, np.int64), 12, 32)
            assert h.shape == (0, 32) and o.shape == (0, 32)
            assert editdist.banded_distance([], [], 32).size == 0
            cl = graph.cluster_reads([], np.zeros((0, 32), np.uint32), np.zeros((0, 32), np.uint8), ClusterConfig())
            assert cl.labels.size == 0
    _both("banded", {"a": [np.zeros(0, np.uint8)], "b": [np.zeros(0, np.uint8)], "slack": 0})
    _both("fb", {"tpl": np.zeros((2, 0), np.int16), "mc": np.zeros((2, 0), np.int32),
                 "reads": [np.zeros(0, np.uint8), np.zeros(3, np.uint8)], "band": np.array([0, 3]), "c_indel": 6,
                 "slack": 0})


# ------------------------------------------------------------------------------------------------ whole decode
VOLATILE = {"seconds", "stage_seconds", "peak_rss_bytes", "elapsed", "elapsed_seconds", "wall_seconds", "native_backends"}


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def test_decode_with_read_clustering_is_identical_on_both_backends(tmp_path):
    """A SIMULATED indel-heavy pool that 6.0 leaves undecodable: same outcome, container and report (only the
    ``native_backends`` provenance differs) with the native kernels and with the reference; 1 and 4 workers."""
    from vnxdna.simulation.channel import ChannelConfig, simulate_file
    from vnxdna.v4 import decoder as de

    from .cluster_support import make_strands
    container, _ = make_strands(tmp_path, 1200)
    simulate_file(tmp_path / "s.fasta", tmp_path / "r.fastq",
                  ChannelConfig(substitution_rate=0.01, insertion_rate=0.005, deletion_rate=0.015, coverage=10.0,
                                seed=1, coverage_model="negative-binomial", coverage_dispersion=4.0,
                                reverse_complement_rate=0.5, n_rate=0.001))
    runs = {}
    for name, workers in (("reference", 1), ("native", 1), ("native", 4)):
        out = tmp_path / f"{name}-{workers}.vnx"
        with backend(name):
            res = de.decode_reads(tmp_path / "r.fastq", out, de.DecodeOptions(read_clustering="fallback",
                                                                             stage_counters=True, workers=workers))
        assert res.report["native_backends"]["cluster"]["backend"] == name
        runs[(name, workers)] = (res.status, out.read_bytes() if out.exists() else None,
                                 json.loads(json.dumps(_strip(res.report), default=str)))
    ref = runs[("reference", 1)]
    assert ref[0] == "SUCCESS" and ref[1] == container and ref[2]["clustering"]["status"] == "ran"
    assert runs[("native", 1)] == ref and runs[("native", 4)] == ref
