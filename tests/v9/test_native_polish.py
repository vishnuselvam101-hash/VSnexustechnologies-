"""V9 Phase 1: the native polish kernel (``vnx_cl_edit_costs`` in ``native/c/cluster.c``) equals the NumPy reference
``polish.edit_costs_reference`` bit for bit (docs/V9_PREREGISTRATION.md §7).

* golden hashes (``native_polish_golden.json``, written from the reference by ``make_polish_golden.py``) on both backends;
* randomized equivalence over at least 100,000 reads, plus a hypothesis campaign at the ctypes boundary;
* thread-count invariance, backend selection (``VNXDNA_CLUSTER_BACKEND``) and routing of out-of-domain inputs;
* the C argument checks (sizes from Python are never trusted);
* a whole decode with the full-template polish: identical outcome, container and report on both backends.

SYNTHETIC data; the read channel is SIMULATED.
"""
from __future__ import annotations

import ctypes
import json
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
sys.path.insert(0, str(HERE.parent / "nanopore"))
sys.path.insert(0, str(HERE.parent / "v7"))
import nanofunnel as nf                                   # noqa: E402
from native_cluster_support import backend, digest, polish_case  # noqa: E402

GOLDEN = HERE / "native_polish_golden.json"
HYP = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


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


def run(case: dict, name: str, threads: int | None = None):
    args = (case["tpl"], case["mc"], case["reads"], case["band"], case["c_indel"], case["c_sub"])
    if name == "reference":
        return polish.edit_costs_reference(*args)
    buf, off, lens = nc.pack(list(case["reads"]))
    B = int(np.asarray(case["band"]).max(initial=0))
    return nc.edit_costs(case["tpl"], case["mc"], buf, off, lens, case["band"], B, case["c_indel"], case["c_sub"],
                         nthreads=threads)


def same(x, y) -> bool:
    return all(a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b) for a, b in zip(x, y))


def test_golden_hashes_on_both_backends():
    golden = json.loads(GOLDEN.read_text())
    assert len(golden["cases"]) >= 40
    for seed, want in golden["cases"].items():
        case = polish_case(np.random.default_rng(int(seed)))
        assert digest(*run(case, "reference")) == want, seed
        assert digest(*run(case, "native")) == want, seed


def test_100k_reads_equal():
    rng = np.random.default_rng(20261030)
    reads = 0
    while reads < 100_000:
        case = polish_case(rng, n=int(rng.integers(40, 200)))
        assert same(run(case, "reference"), run(case, "native"))
        reads += len(case["reads"])


def test_wide_shared_band_with_narrow_read_bands():
    """The kernel evaluates only each read's band (plus one edge cell); cells of the shared band outside it must not
    matter, in particular the cells just outside the band that take part in the insertion closures."""
    rng = np.random.default_rng(7)
    for _ in range(60):
        case = polish_case(rng, n=int(rng.integers(2, 30)), T=int(rng.choice([1, 5, 40, 120])))
        case["band"] = rng.integers(0, 4, len(case["reads"])).astype(np.int64)
        case["band"][0] = 40
        assert same(run(case, "reference"), run(case, "native"))


@pytest.mark.parametrize("threads", [1, 2, 3, 4, 7])
def test_thread_count_does_not_change_results(threads):
    rng = np.random.default_rng(11)
    for _ in range(10):
        case = polish_case(rng, n=int(rng.integers(1, 90)))
        assert same(run(case, "native", 1), run(case, "native", threads))


@given(T=st.integers(0, 50), B=st.integers(0, 20), seed=st.integers(0, 2**32 - 1), c_indel=st.integers(0, 1024),
       c_sub=st.integers(0, 1024), n=st.integers(1, 12))
@HYP
def test_hyp_edit_costs(T, B, seed, c_indel, c_sub, n):
    rng = np.random.default_rng(seed)
    tpl = rng.integers(-1, 8, (n, T)).astype(np.int16)
    mc = rng.integers(0, 1025, (n, T)).astype(np.int32)
    reads = [rng.integers(0, 8, int(rng.integers(0, T + B + 4))).astype(np.uint8) for _ in range(n)]
    band = rng.integers(0, B + 1, n).astype(np.int64)
    case = {"tpl": tpl, "mc": mc, "reads": reads, "band": band, "c_indel": c_indel, "c_sub": c_sub}
    assert same(run(case, "reference"), run(case, "native"))


def test_dispatch_uses_the_native_kernel_in_domain(monkeypatch):
    case = polish_case(np.random.default_rng(3), n=20, T=40)
    calls = []
    real = nc.edit_costs
    monkeypatch.setattr(nc, "edit_costs", lambda *a, **k: calls.append(1) or real(*a, **k))
    args = (case["tpl"], case["mc"], case["reads"], case["band"], case["c_indel"], case["c_sub"])
    with backend("native"):
        out = polish.edit_costs(*args)
    assert calls and same(out, polish.edit_costs_reference(*args))
    calls.clear()
    with backend("reference"):
        out = polish.edit_costs(*args)
    assert not calls and same(out, polish.edit_costs_reference(*args))


def test_out_of_domain_inputs_route_to_the_reference(monkeypatch):
    rng = np.random.default_rng(5)
    case = polish_case(rng, n=6, T=10)
    monkeypatch.setattr(nc, "edit_costs", lambda *a, **k: pytest.fail("native kernel called out of domain"))
    for change in ({"c_sub": 1025}, {"c_indel": 2000}, {"band": np.full(6, 600, dtype=np.int64)},
                   {"reads": [np.full(5, 9, dtype=np.uint8)] * 6}):
        c = dict(case, **change)
        args = (c["tpl"], c["mc"], c["reads"], c["band"], c["c_indel"], c["c_sub"])
        with backend("native"):
            assert same(polish.edit_costs(*args), polish.edit_costs_reference(*args))


def test_empty_and_zero_length_inputs():
    case = polish_case(np.random.default_rng(1), n=5, T=0)
    assert same(run(case, "reference"), run(case, "native"))
    case = polish_case(np.random.default_rng(2), n=5, T=12)
    case["reads"] = [np.zeros(0, dtype=np.uint8)] * 5
    assert same(run(case, "reference"), run(case, "native"))
    z = np.zeros((0, 4), dtype=np.int16)
    opt, sub, dele, ins = nc.edit_costs(z, z.astype(np.int32), np.zeros(0, np.uint8), np.zeros(0, np.int64),
                                        np.zeros(0, np.int64), np.zeros(0, np.int64), 0, 1, 1)
    assert opt.shape == (0,) and sub.shape == (0, 4, 4) and dele.shape == (0, 4) and ins.shape == (0, 4, 4)


def test_c_rejects_inconsistent_arguments():
    lib = nc._lib_or_raise()
    n, T, B = 2, 3, 2
    tpl = np.zeros((n, T), np.int16)
    mc = np.ones((n, T), np.int32)
    buf = np.zeros(6, np.uint8)
    off = np.array([0, 3], np.int64)
    lens = np.array([3, 3], np.int64)
    band = np.array([1, 2], np.int64)
    opt = np.empty(n, np.int64)
    sub = np.empty((n, T, 4), np.int64)
    dele = np.empty((n, T), np.int64)
    ins = np.empty((n, T, 4), np.int64)

    def call(**kw):
        a = dict(n=n, T=T, B=B, tpl=tpl, mc=mc, buf=buf, nbuf=buf.size, off=off, lens=lens, band=band, ci=1, cs=1)
        a.update(kw)
        p = lambda x: None if x is None else x.ctypes.data     # noqa: E731
        return lib.vnx_cl_edit_costs(a["n"], a["T"], a["B"], p(a["tpl"]), p(a["mc"]), p(a["buf"]), a["nbuf"],
                                     p(a["off"]), p(a["lens"]), p(a["band"]), a["ci"], a["cs"], opt.ctypes.data,
                                     sub.ctypes.data, dele.ctypes.data, ins.ctypes.data)

    assert call() == 0
    for bad in ({"n": -1}, {"T": -1}, {"T": 8193}, {"B": -1}, {"B": 513}, {"ci": -1}, {"ci": 1025}, {"cs": -1},
                {"cs": 1025}, {"nbuf": 5}, {"off": np.array([0, 4], np.int64)}, {"lens": np.array([3, -1], np.int64)},
                {"band": np.array([1, 3], np.int64)}, {"band": np.array([-1, 0], np.int64)},
                {"mc": np.full((n, T), 1025, np.int32)}, {"tpl": None}, {"buf": None}):
        assert call(**bad) == -1, bad
    with pytest.raises(ValueError):
        nc.edit_costs(tpl, mc[:, :2], buf, off, lens, band, B, 1, 1)
    assert ctypes.CDLL(nc.status()["library"]).vnx_cl_abi_version() == nc.ABI_VERSION == 2


def test_decode_with_full_template_polish_is_identical_on_both_backends(tmp_path):
    from vnxdna.simulation import engine, registry

    arc = nf.build_archive(tmp_path / "archive", 4000, 90123, "v7-lowcov")
    model = registry.load_model("insertion-heavy").with_parameters(
        {"sequencing.coverage": {"model": "negative-binomial", "mean": 4.0, "dispersion": 4.0}}, label="v9 test")
    engine.simulate_file_with_truth(arc["strands_path"], tmp_path / "reads.fastq", model, 90123)
    opts = nf.decoder_options({"decoder": {"read_clustering": "fallback",
                                           "cluster_config": {"consensus_template": "full"}}})
    out = {}
    for name in ("reference", "native"):
        with backend(name):
            dec, cap = nf.run_decode(tmp_path / "reads.fastq", tmp_path / f"{name}.vnx", arc["container_sha256"], opts)
        dec.pop("decode_seconds")
        dec.pop("peak_rss_bytes")
        out[name] = dec
    assert out["reference"] == out["native"]
