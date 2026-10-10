"""V10 W2: the windowed polish trial-cost kernel (``vnx_cl_trial_costs`` in ``native/c/cluster.c``) returns exactly the
V9 scoring (``consensus.trial_costs_reference``: one full forward-backward pass per trial and read).

* randomized equivalence over more than 100,000 (trial, read) rows of polish-like cases, and against the pure NumPy
  forward-backward on a smaller set;
* the per-row "dip" flags (read band below its chunk's shared band) are needed: without them some costs differ;
* chunk boundaries (more than 512 rows), thread-count invariance, empty inputs, out-of-domain routing;
* the C argument checks;
* a whole decode with the full-template polish: identical outcome, container and report with the kernel and with the
  V9 scoring.

SYNTHETIC data; the read channel is SIMULATED.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from vnxdna.native import cluster as nc
from vnxdna.recovery.cluster import consensus as C
from vnxdna.recovery.cluster import polish as _pl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nanopore"))
sys.path.insert(0, str(HERE.parent / "v7"))
import nanofunnel as nf                                   # noqa: E402
from native_cluster_support import backend                # noqa: E402


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


def _mutate(rng, t: np.ndarray, p_sub: float, p_ins: float, p_del: float) -> np.ndarray:
    out = []
    for b in np.maximum(t, 0).tolist():
        if rng.random() < p_ins:
            out.append(int(rng.integers(0, 4)))
        if rng.random() < p_del:
            continue
        out.append(int(rng.integers(0, 4)) if rng.random() < p_sub else int(b))
    return np.asarray(out, dtype=np.uint8)


def case(rng, ncand: int | None = None, T: int | None = None, realistic: bool = True) -> dict:
    """Candidates with a base template, reads around it, and polish-like trials (segment substitutions and shifts)."""
    T = int(T if T is not None else rng.choice([1, 7, 40, 120, 313]))
    ncand = int(ncand if ncand is not None else rng.integers(1, 8))
    period = int(rng.integers(4, 30))
    tpl0 = np.full(T, -1, dtype=np.int64)
    tpl0[::period] = rng.integers(0, 4, tpl0[::period].size)
    seg = _pl.segments(tpl0)
    mc = np.where(tpl0 >= 0, int(rng.choice([1, 4, 9])), int(rng.choice([0, 1, 3]))).astype(np.int64)
    tpls, reads, owner, band = [], [], [], []
    trial_tpl, trial_owner = [], []
    cband = rng.integers(0, 40, ncand) if rng.random() < 0.7 else np.full(ncand, int(rng.integers(0, 40)))
    for k in range(ncand):
        t = tpl0.copy()
        t[tpl0 < 0] = rng.integers(0, 4, int((tpl0 < 0).sum()))
        if rng.random() < 0.1:
            t[rng.random(T) < 0.1] = -1
        tpls.append(t)
        for _ in range(int(rng.integers(1, 12))):
            if realistic:
                reads.append(_mutate(rng, t, *rng.choice([[0.01, 0.01, 0.01], [0.04, 0.03, 0.05], [0.1, 0.1, 0.1]])))
            else:
                reads.append(rng.integers(0, 5, max(0, T + int(rng.integers(-8, 9)))).astype(np.uint8))
            owner.append(k)
            band.append(int(cband[k]))
        for _ in range(int(rng.integers(0, 10))):
            u = t.copy()
            if rng.random() < 0.5 and T > 1:
                g = int(rng.integers(0, int(seg.max(initial=-1)) + 1)) if seg.max(initial=-1) >= 0 else -1
                ps = np.flatnonzero(seg == g) if g >= 0 else np.arange(T)
                if ps.size >= 1:
                    p = int(rng.choice(ps))
                    q = int(rng.integers(max(0, p - 6), min(T, p + 7) + 1))
                    if q not in (p, p + 1) and q <= T:
                        u = _pl.shift(t, p, min(q, T), int(rng.integers(0, 4)))
            else:
                a = int(rng.integers(0, T))
                e = min(T, a + int(rng.integers(1, 6)))
                u[a:e] = rng.integers(0, 4, e - a)
            trial_tpl.append(u)
            trial_owner.append(k)
    c_indel = int(rng.choice([0, 1, 1, 2, 3]))
    return {"tpls": tpls, "mc": mc, "reads": reads, "owner": np.asarray(owner, dtype=np.int64),
            "band": np.asarray(band, dtype=np.int64), "c_indel": c_indel, "pen": 2 * max(1, c_indel) * T,
            "trial_tpl": trial_tpl, "trial_owner": trial_owner}


def args(c: dict) -> tuple:
    return (c["tpls"], c["mc"], c["reads"], c["owner"], c["band"], c["c_indel"], c["pen"], c["trial_tpl"],
            c["trial_owner"])


def native(c: dict):
    out = C._trial_costs_native(*args(c))
    assert out is not None
    return out


def same(x, y) -> bool:
    return all(a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b) for a, b in zip(x, y))


def test_100k_rows_equal_the_v9_scoring():
    rng = np.random.default_rng(20261010)
    rows = 0
    while rows < 100_000:
        c = case(rng, ncand=int(rng.integers(4, 30)), realistic=rng.random() < 0.85)
        assert same(C.trial_costs_reference(*args(c)), native(c))
        counts = np.bincount(c["owner"], minlength=len(c["tpls"]))
        rows += int(counts[c["trial_owner"]].sum()) + len(c["reads"])


def test_equal_to_the_numpy_forward_backward():
    rng = np.random.default_rng(11)
    with backend("reference"):
        for _ in range(25):
            c = case(rng, ncand=int(rng.integers(1, 5)), T=int(rng.choice([1, 7, 40, 90])))
            assert same(C.trial_costs_reference(*args(c)), native(c))


@pytest.mark.parametrize("T", [1, 2, 3, 9, 40])
def test_shift_equals_the_v9_list_implementation(T):
    rng = np.random.default_rng(T)
    for dtype in (np.int64, np.int16):
        t = rng.integers(-1, 4, T).astype(dtype)
        for p in range(T):
            for q in range(T + 1):
                for x in (0, 3):
                    a, b = _pl.shift(t, p, q, x), _pl.shift_reference(t, p, q, x)
                    assert a.dtype == b.dtype and np.array_equal(a, b), (p, q, x)


def test_dip_flags_are_needed_and_match():
    """With per-row dips forced to 0 some costs differ: the band-edge path of the reference is reproduced, not absent."""
    rng = np.random.default_rng(5)
    differs = 0
    for _ in range(300):
        c = case(rng, ncand=6, T=int(rng.choice([7, 40])), realistic=False)
        c["c_indel"], c["mc"] = 1, np.full_like(c["mc"], 9)
        c["band"] = np.where(c["owner"] == 0, 30, rng.integers(0, 3, c["owner"].size)).astype(np.int64)
        ref = C.trial_costs_reference(*args(c))
        assert same(ref, native(c))
        orig = C._chunk_dips
        try:
            C._chunk_dips = lambda b, chunk=C.FB_CHUNK: np.zeros(b.size, dtype=np.uint8)   # noqa: E731
            differs += not same(ref, native(c))
        finally:
            C._chunk_dips = orig
    assert differs > 0


def test_chunk_boundaries():
    """More than 512 rows: each row's dip follows its own fb_calls chunk."""
    rng = np.random.default_rng(3)
    for _ in range(4):
        c = case(rng, ncand=40, T=40)
        rows = int(np.bincount(c["owner"], minlength=40)[c["trial_owner"]].sum())
        assert rows > 512 or len(c["reads"]) > 100
        assert same(C.trial_costs_reference(*args(c)), native(c))


@pytest.mark.parametrize("threads", [1, 2, 3, 8])
def test_thread_count_does_not_change_results(threads):
    rng = np.random.default_rng(99)
    c = case(rng, ncand=25, T=120)
    with nc.thread_scope(threads):
        assert same(C.trial_costs_reference(*args(c)), native(c))


def test_empty_trials_and_zero_length_reads():
    rng = np.random.default_rng(1)
    c = case(rng, ncand=3, T=40)
    c["trial_tpl"], c["trial_owner"] = [], []
    assert same(C.trial_costs_reference(*args(c)), native(c))
    c = case(rng, ncand=3, T=40)
    c["reads"][0] = np.zeros(0, dtype=np.uint8)
    assert same(C.trial_costs_reference(*args(c)), native(c))
    c = case(rng, ncand=2, T=1)
    assert same(C.trial_costs_reference(*args(c)), native(c))


def test_dispatch_and_out_of_domain_routing(monkeypatch):
    rng = np.random.default_rng(2)
    c = case(rng, ncand=3, T=40)
    calls = []
    real = nc.trial_costs
    monkeypatch.setattr(nc, "trial_costs", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert same(C.trial_costs(*args(c)), C.trial_costs_reference(*args(c))) and calls
    big = dict(c, band=np.full_like(c["band"], 600))                         # band above the native maximum
    assert C._trial_costs_native(*args(big)) is None
    assert C._trial_costs_native(*args(dict(c, mc=np.full_like(c["mc"], 2000)))) is None
    unsorted = dict(c, trial_owner=list(reversed(c["trial_owner"])), trial_tpl=list(reversed(c["trial_tpl"])))
    if len(set(c["trial_owner"])) > 1:
        assert C._trial_costs_native(*args(unsorted)) is None
    assert same(C.trial_costs(*args(unsorted)), C.trial_costs_reference(*args(unsorted)))
    with backend("reference"):
        calls.clear()
        C.trial_costs(*args(c))
        assert not calls


def test_c_rejects_inconsistent_arguments():
    lib = nc._lib_or_raise()
    T = 3
    base = np.zeros((1, T), np.int16)
    mc = np.ones(T, np.int32)
    buf = np.zeros(6, np.uint8)
    off = np.array([0, 3], np.int64)
    lens = np.array([3, 3], np.int64)
    band = np.array([1, 2], np.int64)
    rs = np.array([0, 2], np.int64)
    dip = np.zeros(2, np.uint8)
    tt = np.ones((1, T), np.int16)
    ts = np.array([0, 1], np.int64)
    tr = np.array([0], np.int64)
    tdip = np.zeros(2, np.uint8)
    cost = np.empty(1, np.int64)
    basec = np.empty(1, np.int64)

    def call(**kw):
        a = dict(c0=0, c1=1, ncand=1, T=T, base=base, mc=mc, buf=buf, nbuf=buf.size, off=off, lens=lens, band=band,
                 rs=rs, dip=dip, ci=1, pen=10, tt=tt, ts=ts, tr=tr, nrows=2, tdip=tdip)
        a.update(kw)
        p = lambda x: None if x is None else x.ctypes.data     # noqa: E731
        return lib.vnx_cl_trial_costs(a["c0"], a["c1"], a["ncand"], a["T"], p(a["base"]), p(a["mc"]), p(a["buf"]),
                                      a["nbuf"], p(a["off"]), p(a["lens"]), p(a["band"]), p(a["rs"]), p(a["dip"]),
                                      a["ci"], a["pen"], p(a["tt"]), p(a["ts"]), p(a["tr"]), a["nrows"], p(a["tdip"]),
                                      cost.ctypes.data, basec.ctypes.data)

    assert call() == 0
    for bad in ({"c0": -1}, {"c1": 2}, {"c0": 1, "c1": 0}, {"T": -1}, {"T": 8193}, {"ci": -1}, {"ci": 1025},
                {"pen": -1}, {"nbuf": 5}, {"off": np.array([0, 4], np.int64)}, {"lens": np.array([3, -1], np.int64)},
                {"band": np.array([1, 513], np.int64)}, {"band": np.array([-1, 0], np.int64)},
                {"mc": np.full(T, 1025, np.int32)}, {"base": None}, {"buf": None}, {"rs": np.array([1, 2], np.int64)},
                {"rs": np.array([0, -1], np.int64)}, {"tr": np.array([1], np.int64)}, {"nrows": 1}, {"tt": None}):
        assert call(**bad) == -1, bad
    with pytest.raises(ValueError):
        nc.trial_costs(base, mc[:2], buf, off, lens, band, rs, dip, 1, 10, tt, ts, tr, tdip)
    assert ctypes.CDLL(nc.status()["library"]).vnx_cl_abi_version() == nc.ABI_VERSION == 4


def test_decode_with_full_template_polish_is_identical_to_the_v9_scoring(tmp_path, monkeypatch):
    from vnxdna.simulation import engine, registry

    arc = nf.build_archive(tmp_path / "archive", 4000, 90123, "v7-lowcov")
    model = registry.load_model("insertion-heavy").with_parameters(
        {"sequencing.coverage": {"model": "negative-binomial", "mean": 4.0, "dispersion": 4.0}}, label="v10 test")
    engine.simulate_file_with_truth(arc["strands_path"], tmp_path / "reads.fastq", model, 90123)
    opts = nf.decoder_options({"decoder": {"read_clustering": "fallback",
                                           "cluster_config": {"consensus_template": "full"}}})
    out = {}
    used = []
    real = C._trial_costs_native
    for name in ("v9", "w2"):
        if name == "v9":
            monkeypatch.setattr(C, "_trial_costs_native", lambda *a: None)
        else:
            monkeypatch.setattr(C, "_trial_costs_native", lambda *a: used.append(1) or real(*a))
        dec, cap = nf.run_decode(tmp_path / "reads.fastq", tmp_path / f"{name}.vnx", arc["container_sha256"], opts)
        dec.pop("decode_seconds")
        dec.pop("peak_rss_bytes")
        out[name] = dec
    assert used, "the decode never reached the polish scoring"
    assert out["v9"] == out["w2"]


def test_add_sorted_equals_add_at():
    rng = np.random.default_rng(8)
    for shape in ((0,), (5,), (40, 7), (30, 9, 4)):
        idx = np.sort(rng.integers(0, 6, shape[0]))
        vals = rng.integers(-10**9, 10**9, shape).astype(np.int64)
        a = np.zeros((6,) + shape[1:], dtype=np.int64)
        b = a.copy()
        np.add.at(a, idx, vals)
        C._add_sorted(b, idx, vals)
        assert np.array_equal(a, b)
