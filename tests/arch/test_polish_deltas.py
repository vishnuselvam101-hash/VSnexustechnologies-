"""Hybrid architecture: the fused polish kernel (``vnx_cl_polish_deltas``, cluster ABI 5) equals the per-candidate fold
of the reference polish edit costs (``polish.edit_costs_reference``) bit for bit.

* randomized equivalence (reference oracle, chunked exactly as consensus._polish chunks its reads) and a hypothesis
  campaign at the ctypes boundary;
* SIMD level (baseline / AVX2) and thread-count invariance;
* the C argument checks;
* consensus._polish: the fused path and the chunked fallback give identical decodes.

SYNTHETIC data; the read channel is SIMULATED.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vnxdna.native import cluster as nc
from vnxdna.recovery.cluster import polish

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "v7"))
from native_cluster_support import polish_case  # noqa: E402

INF = 1 << 28
HYP = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@pytest.fixture(scope="module", autouse=True)
def _library(tmp_path_factory):
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


def grouped(case: dict, rng: np.random.Generator) -> tuple[list, list]:
    """Split the case's reads into consecutive candidates; a candidate's reads share its first read's template."""
    n = len(case["reads"])
    cuts = sorted(set(rng.integers(1, max(n, 2), int(rng.integers(0, 6))).tolist()) - {0, n}) if n > 1 else []
    counts = np.diff([0, *cuts, n]).tolist()
    starts = np.cumsum([0, *counts[:-1]]).tolist()
    return [case["tpl"][s] for s in starts], counts


def reference(tpls, mc, reads, counts, band, c_indel, c_sub, pen, chunk):
    """consensus._polish's fold of polish.edit_costs_reference, chunk by chunk."""
    T = mc.size
    owner = np.repeat(np.arange(len(tpls)), counts)
    n = owner.size
    acc = [np.zeros((len(tpls), T, 4), np.int64), np.zeros((len(tpls), T), np.int64), np.zeros((len(tpls), T, 4), np.int64)]
    for a in range(0, n, chunk):
        sl = slice(a, a + chunk)
        k = owner[sl]
        tt = np.stack([tpls[i] for i in k]) if k.size else np.zeros((0, T), np.int16)
        opt, sub, dele, ins = polish.edit_costs_reference(tt, np.repeat(mc[None, :], k.size, axis=0), reads[sl],
                                                          band[sl], c_indel, c_sub)
        live = opt < INF
        for out, val in zip(acc, (sub, dele, ins)):
            extra = (slice(None),) + (None,) * (val.ndim - 1)
            np.add.at(out, k[live], np.minimum(val[live], pen) - opt[live][extra])
    return acc


def fused(tpls, mc, reads, counts, band, c_indel, c_sub, pen, chunk, threads=1, level=0):
    buf, off, lens = nc.pack(list(reads))
    n = len(reads)
    starts = np.arange(0, n, chunk)
    cb = np.repeat(np.maximum.reduceat(band, starts), np.diff(np.append(starts, n))) if n else band
    return nc.polish_deltas(np.stack(tpls), mc, buf, off, lens, np.concatenate([[0], np.cumsum(counts)]), band,
                            band < cb, c_indel, c_sub, pen, nthreads=threads, level=level)


def args_of(case, rng, chunk=None):
    tpls, counts = grouped(case, rng)
    mc = np.asarray(case["mc"])[0] if len(case["reads"]) else np.zeros(case["tpl"].shape[1], np.int32)
    T = mc.size
    pen = 2 * case["c_indel"] * T if rng.random() < 0.8 else int(rng.integers(0, 5000))
    return (tpls, mc.astype(np.int32), list(case["reads"]), counts, np.asarray(case["band"], np.int64), case["c_indel"],
            case["c_sub"], pen, chunk or int(rng.choice([1, 3, 7, 512])))


def equal(x, y) -> bool:
    return all(a.shape == b.shape and np.array_equal(a, b) for a, b in zip(x, y))


def levels() -> list[int]:
    return [1, 2] if nc._load().vnx_cl_fb_level() >= 2 else [1]


def test_randomized_equivalence_with_the_reference():
    rng = np.random.default_rng(20261010)
    reads = 0
    while reads < 20_000:
        case = polish_case(rng, n=int(rng.integers(1, 120)))
        a = args_of(case, rng)
        want = reference(*a)
        for level in levels():
            assert equal(fused(*a, level=level), want)
        reads += len(case["reads"])


def test_dip_cells_with_wide_chunk_band():
    """A narrow read in a chunk with a wide band sees the cells just outside its band (dip); in a chunk of its own
    it does not. Both must match the reference."""
    rng = np.random.default_rng(7)
    for _ in range(60):
        case = polish_case(rng, n=int(rng.integers(2, 30)), T=int(rng.choice([1, 5, 40, 120])))
        case["band"] = rng.integers(0, 4, len(case["reads"])).astype(np.int64)
        case["band"][0] = 40
        for chunk in (1, 512):
            a = args_of(case, rng, chunk)
            assert equal(fused(*a), reference(*a))


@pytest.mark.parametrize("threads", [1, 2, 3, 8])
def test_thread_count_does_not_change_results(threads):
    rng = np.random.default_rng(11)
    for _ in range(8):
        case = polish_case(rng, n=int(rng.integers(20, 90)), T=60)
        a = args_of(case, rng)
        assert equal(fused(*a, threads=threads), fused(*a, threads=1))


@HYP
@given(seed=st.integers(0, 2**32 - 1), n=st.integers(0, 40), T=st.sampled_from([0, 1, 2, 9, 33]))
def test_hypothesis_boundary(seed, n, T):
    rng = np.random.default_rng(seed)
    case = polish_case(rng, n=max(n, 1), T=T)
    if n == 0:
        case["reads"], case["band"] = [], np.zeros(0, np.int64)
        case["mc"] = np.zeros((0, T), np.int32)
        tpls, counts = [case["tpl"][0]], [0]
        a = (tpls, np.zeros(T, np.int32), [], counts, case["band"], case["c_indel"], case["c_sub"], 10, 512)
    else:
        a = args_of(case, rng)
    want = reference(*a)
    for level in levels():
        assert equal(fused(*a, level=level), want)


def test_polish_wrapper_routes_out_of_domain_inputs_to_none(monkeypatch):
    rng = np.random.default_rng(3)
    case = polish_case(rng, n=10, T=20)
    tpls, mc, reads, counts, band, ci, cs, pen, chunk = args_of(case, rng)
    assert polish.polish_deltas(tpls, mc, reads, counts, band, ci, cs, pen, chunk) is not None
    assert polish.polish_deltas(tpls, mc, reads, counts, band, 2000, cs, pen, chunk) is None    # cost > 1024
    bad = [r.copy() for r in reads]
    bad[0] = np.full(5, 9, np.uint8)                                                              # code >= 8
    assert polish.polish_deltas(tpls, mc, bad, counts, band, ci, cs, pen, chunk) is None
    monkeypatch.setenv(nc.ENV, "reference")
    assert polish.polish_deltas(tpls, mc, reads, counts, band, ci, cs, pen, chunk) is None


def test_c_rejects_inconsistent_arguments():
    lib = nc._lib_or_raise()
    T, ncand = 3, 2
    tpl = np.zeros((ncand, T), np.int16)
    mc = np.ones(T, np.int32)
    buf = np.zeros(6, np.uint8)
    off = np.array([0, 3], np.int64)
    lens = np.array([3, 3], np.int64)
    rs = np.array([0, 1, 2], np.int64)
    band = np.array([1, 2], np.int64)
    dip = np.array([1, 0], np.uint8)
    out = [np.empty((ncand, T, 4), np.int64), np.empty((ncand, T), np.int64), np.empty((ncand, T, 4), np.int64),
           np.empty(ncand, np.int64)]

    def call(**kw):
        a = dict(ncand=ncand, c0=0, c1=ncand, T=T, tpl=tpl, mc=mc, buf=buf, nbuf=buf.size, off=off, lens=lens, rs=rs,
                 band=band, dip=dip, ci=1, cs=1, pen=10, level=0)
        a.update(kw)
        ptr = (lambda x: None if x is None else x.ctypes.data)
        return lib.vnx_cl_polish_deltas(a["ncand"], a["c0"], a["c1"], a["T"], ptr(a["tpl"]), ptr(a["mc"]), ptr(a["buf"]),
                                        a["nbuf"], ptr(a["off"]), ptr(a["lens"]), ptr(a["rs"]), ptr(a["band"]),
                                        ptr(a["dip"]), a["ci"], a["cs"], a["pen"], *(o.ctypes.data for o in out),
                                        a["level"])

    assert call() == 0
    for bad in (dict(c1=3), dict(c0=2, c1=1), dict(T=8193), dict(ci=1025), dict(cs=-1), dict(pen=-1), dict(level=3),
                dict(nbuf=5), dict(mc=np.array([1, 2000, 1], np.int32)), dict(band=np.array([1, 513], np.int64)),
                dict(dip=np.array([2, 0], np.uint8)), dict(rs=np.array([0, 2, 1], np.int64)),
                dict(off=np.array([0, 4], np.int64)), dict(lens=np.array([-1, 3], np.int64)), dict(buf=None)):
        assert call(**bad) == -1, bad
    assert call(c0=1, c1=1) == 0                                       # an empty range touches nothing
    with pytest.raises(ValueError):
        nc.polish_deltas(tpl, np.ones(T + 1, np.int32), buf, off, lens, rs, band, dip, 1, 1, 10)
    with pytest.raises(ValueError):
        nc.polish_deltas(tpl, mc, buf, off, lens, np.array([0, 1, 1]), band, dip, 1, 1, 10)   # does not end at n
    assert ctypes.CDLL(nc.status()["library"]).vnx_cl_abi_version() == nc.ABI_VERSION == 6


def test_polish_uses_the_fused_kernel_and_matches_the_fallback(monkeypatch, tmp_path):
    """Whole cluster_consensus runs with consensus_template="full" (noisy clusters, both orientations): the fused
    path and the chunked edit_costs fold give the same frames and counters."""
    from collections import Counter

    from cluster_support import make_strands, noisy, revcomp
    from vnxdna.dnaenc.layout import PROFILES
    from vnxdna.recovery.cluster import ClusterConfig
    from vnxdna.recovery.cluster.consensus import cluster_consensus

    lay = PROFILES["v4-balanced"][0]
    _, strands = make_strands(tmp_path, 1200)
    rng = np.random.default_rng(31)
    clusters = []
    for i in range(16):
        reads = [noisy(rng, strands[i], 0.04, 0.03, 0.05) for _ in range(int(rng.integers(2, 9)))]
        clusters.append((i, [revcomp(r) for r in reads] if i % 2 else reads))
    full = ClusterConfig(consensus_template="full")
    calls = {"n": 0}
    real = polish.polish_deltas

    def spy(*a, **k):
        r = real(*a, **k)
        calls["n"] += r is not None
        return r

    def run():
        c = Counter()
        return repr(cluster_consensus(lay, clusters, full, counts=c)), dict(c)

    monkeypatch.setattr(polish, "polish_deltas", spy)
    got = run()
    assert calls["n"] > 0 and got[1].get("cluster_polish_rounds", 0) >= 1
    monkeypatch.setattr(polish, "polish_deltas", lambda *a, **k: None)
    assert run() == got
