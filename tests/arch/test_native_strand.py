"""Hybrid architecture: the native strand builder (``native/c/strand.c``, strand ABI 1) equals the NumPy reference of
``frame4.build_strands`` bit for bit (strands and chosen scrambler variants), and raises the same constraint error.

* every named profile and marker geometry, with loose, default and tight constraint sets (windowed GC, tandem
  repeats, forbidden motifs with reverse complements), including rows that need variants > 0 and rows that fail;
* thread-count invariance;
* the C argument checks, ABI mismatch, backend selection and the status report.

SYNTHETIC payloads.
"""
from __future__ import annotations

import ctypes

import numpy as np
import pytest

from vnxdna.codec import gf256
from vnxdna.codec.inner_parity import parity_matrix
from vnxdna.core.errors import VNXConstraintError
from vnxdna.dnaenc import frame4
from vnxdna.dnaenc.constraints import ConstraintConfig
from vnxdna.dnaenc.layout import PROFILES, Layout
from vnxdna.dnaenc.scrambler import keystreams
from vnxdna.native import strand as ns

CONFIGS = {
    "default": ConstraintConfig(),
    "loose": ConstraintConfig(gc_min_percent=0, gc_max_percent=100, max_homopolymer=0),
    "window-tandem-motifs": ConstraintConfig(gc_min_percent=35, gc_max_percent=65, gc_window_nt=20,
                                             gc_window_min_percent=20, gc_window_max_percent=80, max_homopolymer=3,
                                             max_tandem_repeat_nt=8, forbidden_motifs=["GAATTC", "GGATCC", "ACGTA"]).validate(),
    "tight": ConstraintConfig(gc_min_percent=48, gc_max_percent=52, gc_window_nt=12, gc_window_min_percent=30,
                              gc_window_max_percent=70, max_homopolymer=2, max_tandem_repeat_nt=6,
                              forbidden_motifs=["ACG"]).validate(),
}
LAYOUTS = sorted({lay for lay, _, _ in PROFILES.values()}, key=repr) + [Layout(20, 0, 8, 2), Layout(12, 2, 0, 0)]


@pytest.fixture(scope="module", autouse=True)
def _library():
    ns._reset_for_tests()
    if not ns.available():
        ns.build()
        ns._reset_for_tests()
    assert ns.available(), ns.status()
    yield
    ns._reset_for_tests()


def _rows(seed: int, n: int, payload: int):
    rng = np.random.default_rng(seed)
    pays = rng.integers(0, 256, (n, payload), dtype=np.uint8)
    pays[: n // 4] = 0                               # low-entropy rows push the screen to later variants
    return rng.integers(0, 1 << 20, n), rng.integers(0, 1 << 16, n), pays


def _both(monkeypatch, *args, **kw):
    res = {}
    for backend in ("reference", "native"):
        monkeypatch.setenv("VNXDNA_STRAND_BACKEND", backend)
        try:
            res[backend] = frame4.build_strands(*args, **kw)
        except VNXConstraintError as error:
            res[backend] = (str(error), error.details)
    return res


@pytest.mark.parametrize("cfg_name", sorted(CONFIGS))
@pytest.mark.parametrize("layout", LAYOUTS, ids=repr)
def test_native_equals_reference(monkeypatch, layout, cfg_name):
    groups, syms, pays = _rows(hash((repr(layout), cfg_name)) & 0xFFFF, 300, layout.payload_bytes)
    res = _both(monkeypatch, layout, CONFIGS[cfg_name], 0xBEEF, 0, groups, syms, pays)
    ref, nat = res["reference"], res["native"]
    assert type(ref[0]) is type(nat[0])
    if isinstance(ref[0], str):
        assert ref == nat
    else:
        np.testing.assert_array_equal(ref[0], nat[0])
        np.testing.assert_array_equal(ref[1], nat[1])


def test_variants_beyond_zero_and_failures_are_exercised(monkeypatch):
    lay = PROFILES["v4-balanced"][0]
    groups, syms, pays = _rows(5, 400, lay.payload_bytes)
    monkeypatch.setenv("VNXDNA_STRAND_BACKEND", "native")
    _, chosen = frame4.build_strands(lay, CONFIGS["default"], 7, 0, groups, syms, pays)
    assert (chosen == 0).any() and (chosen >= 5).any()
    res = _both(monkeypatch, lay, CONFIGS["tight"], 7, 0, groups, syms, pays, max_variants=2)
    assert isinstance(res["native"][0], str) and res["native"] == res["reference"]


def test_length_rule_fails_every_row_like_the_reference(monkeypatch):
    lay = PROFILES["v4-dense"][0]
    cfg = ConstraintConfig(max_length=lay.strand_nt - 1)
    groups, syms, pays = _rows(9, 20, lay.payload_bytes)
    res = _both(monkeypatch, lay, cfg, 1, 0, groups, syms, pays)
    assert isinstance(res["native"][0], str) and res["native"] == res["reference"]


def test_thread_count_does_not_change_the_result():
    lay = PROFILES["v4-indel"][0]
    plain = frame4.plain_rows(3, 0, *_rows(11, 257, lay.payload_bytes))
    ks = keystreams(plain.shape[1])
    tpl, _ = lay.template()
    rules = dict(gc_min=40, gc_max=60, win=0, wmin=25, wmax=75, hmax=3, tandem=0)
    args = (plain, ks, parity_matrix(1 + plain.shape[1], lay.inner_parity), gf256.MUL, tpl, rules, [])
    one = ns.build_rows(*args, nthreads=1)
    for t in (2, 3, 8, 1000):
        many = ns.build_rows(*args, nthreads=t)
        np.testing.assert_array_equal(one[0], many[0])
        np.testing.assert_array_equal(one[1], many[1])


def test_empty_input(monkeypatch):
    lay = PROFILES["v4-balanced"][0]
    monkeypatch.setenv("VNXDNA_STRAND_BACKEND", "native")
    out, chosen = frame4.build_strands(lay, ConstraintConfig(), 1, 0, np.zeros(0, int), np.zeros(0, int),
                                       np.zeros((0, lay.payload_bytes), np.uint8))
    assert out.shape == (0, lay.strand_nt) and chosen.shape == (0,)


def test_c_rejects_inconsistent_arguments():
    lib = ns.KERNEL.lib()
    lay = Layout(12, 2, 0, 0)
    span = 9 + 12 + 4
    plain = np.zeros((4, span), np.uint8)
    ks = keystreams(span)
    mat = parity_matrix(1 + span, 2)
    tpl = lay.template()[0].astype(np.int16)
    out = np.empty((4, tpl.size), np.uint8)
    chosen = np.empty(4, np.int16)
    mbuf = np.array([0, 1, 9], np.uint8)
    moff = np.zeros(1, np.int64)

    def call(n=4, c0=0, c1=4, span_=span, nvar=4, r=2, nt=tpl.size, tpl_=tpl, gc=(0, 100), tandem=0, nmot=0,
             mlen=np.ones(1, np.int64), mbytes=3):
        return lib.vnx_st_build(n, c0, c1, span_, plain.ctypes.data, nvar, ks.ctypes.data, r, mat.ctypes.data,
                                gf256.MUL.ctypes.data, nt, tpl_.ctypes.data, gc[0], gc[1], 0, 0, 100, 0, tandem, nmot,
                                mbuf.ctypes.data, mbytes, moff.ctypes.data, mlen.ctypes.data, out.ctypes.data,
                                chosen.ctypes.data)

    assert call() == 0 and (chosen == 0).all()
    bad_tpl = tpl.copy()
    bad_tpl[0] = 4
    for kw in (dict(c1=5), dict(c0=3, c1=2), dict(n=-1), dict(span_=5000), dict(nvar=257), dict(r=4097),
               dict(nt=tpl.size - 1), dict(tpl_=bad_tpl), dict(gc=(60, 40)), dict(gc=(0, 101)), dict(tandem=5),
               dict(nmot=1, mlen=np.array([65], np.int64)), dict(nmot=1, mlen=np.array([3], np.int64)),
               dict(nmot=1, mlen=np.array([2], np.int64), mbytes=1)):
        assert call(**kw) == -1, kw
    assert call(c0=2, c1=2) == 0


def test_abi_mismatch_is_ignored(tmp_path, monkeypatch):
    lib = ns.build(tmp_path / "libvnx_strand_abi99.so", extra_flags=["-DVNX_ST_ABI=99"])
    monkeypatch.setenv("VNXDNA_STRAND_LIB", str(lib))
    monkeypatch.setattr(ns, "_INPLACE", tmp_path / "missing.so")
    # only the mismatched library: an installed build (``_vnx_strand*.so`` next to the package, as after
    # ``pip install -e .`` in CI) would otherwise be the next candidate and load
    monkeypatch.setattr(ns, "_candidates", lambda: [lib])
    ns._reset_for_tests()
    try:
        assert not ns.available()
        assert "ABI 99 != 1" in ns.status()["load_error"]
        monkeypatch.setenv("VNXDNA_STRAND_BACKEND", "auto")
        assert ns.resolve_backend() == "reference"
        monkeypatch.setenv("VNXDNA_STRAND_BACKEND", "native")
        with pytest.raises(ns.NativeStrandError):
            ns.resolve_backend()
        assert ns.status()["active_backend"] is None
    finally:
        ns._reset_for_tests()


def test_backend_variable_is_validated(monkeypatch):
    monkeypatch.setenv("VNXDNA_STRAND_BACKEND", "fast")
    with pytest.raises(ValueError):
        ns.resolve_backend()
    assert "must be one of" in ns.status()["error"]
    monkeypatch.setenv("VNXDNA_STRAND_BACKEND", "reference")
    assert ns.resolve_backend() == "reference" and ns.status()["active_backend"] == "reference"


def test_symbols_are_exported_with_c_types():
    assert isinstance(ns.KERNEL.lib().vnx_st_abi_version, ctypes._CFuncPtr)
