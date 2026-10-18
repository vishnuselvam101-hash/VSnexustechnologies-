"""V12: the native orientation scan (``native/c/orient.c``, orient ABI 1; ABI 2, 3 and 4 since V13 add the retry bound, the speculative projection and the frame packing) equals the NumPy reference
``pass1._orientation_reference`` bit for bit, on every layout, for clean, substituted, indel, reverse-complemented,
truncated, N-carrying and random junk reads; golden hashes pin the decisions of fixed batches for both backends; the C
argument checks, ABI mismatch, backend selection and the status report are exercised.

SIMULATED reads (synthetic strands of each layout's template with random payload bases, mutated in NumPy).
"""
from __future__ import annotations

import ctypes
import hashlib

import numpy as np
import pytest

from vnxdna.dnaenc.layout import PROFILES, Layout
from vnxdna.native import orient as no
from vnxdna.recovery import pass1
from vnxdna.recovery.options import DecodeOptions

LAYOUTS = sorted({lay for lay, _, _ in PROFILES.values()}, key=repr) + [Layout(20, 0, 8, 2), Layout(12, 2, 0, 0)]
BALANCED = PROFILES["v4-balanced"][0]
CHANNELS = {"clean": (0.0, 0.0, 0.5), "sub": (0.02, 0.0, 0.5), "indel": (0.01, 0.004, 0.5),
            "indel-heavy": (0.02, 0.03, 0.5), "forward-only": (0.01, 0.004, 0.0), "rc-only": (0.01, 0.004, 1.0)}
#: sha256 of the flip vector of ``_batch(BALANCED, seed, 2000)`` for both backends (pinned at the first native build)
GOLDEN = {
    101: "077bb28b3c8c38a90a9a38f65c707714c414bf8c8fa30b6cf84e13769b001fa1",
    102: "13977d9dc9a97b37a8fbf5c4356d406dd53bd49ef247974d8c66dcb341d6dbff",
    103: "718ca9906d70e2f1d60e373f77ab0b30932de4d1497dc8b60595c81d6f92b2e2",
}


@pytest.fixture(scope="module", autouse=True)
def _library():
    no._reset_for_tests()
    if not no.available():
        no.build()
        no._reset_for_tests()
    assert no.available(), no.status()
    yield
    no._reset_for_tests()


def _init(lay: Layout) -> None:
    opt = DecodeOptions()
    pass1._p_init(lay, opt.band, opt.sync_costs, 0, True)


def _mutate(rng: np.random.Generator, r: np.ndarray, sub: float, ind: float) -> np.ndarray:
    r = r[rng.random(r.size) >= ind / 2]                                                   # deletions
    r = np.where(rng.random(r.size) < sub, rng.integers(0, 4, r.size), r).astype(np.uint8)  # substitutions
    ins = np.flatnonzero(rng.random(r.size) < ind / 2)
    return np.insert(r, ins, rng.integers(0, 4, ins.size).astype(np.uint8))               # insertions


def _batch(lay: Layout, seed: int, n: int, sub: float = 0.01, ind: float = 0.004, rc: float = 0.5,
           junk: float = 0.1) -> tuple[np.ndarray, np.ndarray]:
    """Flat codes and lengths of ``n`` SIMULATED reads of ``lay``: template markers + random payload, mutated, half
    reverse complemented, some truncated, some with N (4), some random junk of random length (including 0)."""
    rng = np.random.default_rng(seed)
    tpl, _ = lay.template()
    marker = tpl >= 0
    reads = []
    for _ in range(n):
        u = rng.random()
        if u < junk:
            r = rng.integers(0, 5, rng.integers(0, 2 * tpl.size + 1)).astype(np.uint8)
        else:
            r = rng.integers(0, 4, tpl.size).astype(np.uint8)
            r[marker] = tpl[marker]
            r = _mutate(rng, r, sub, ind)
            if rng.random() < rc:
                r = pass1._RC[r[::-1]]
            if rng.random() < 0.05:
                r = r[: rng.integers(0, r.size + 1)]
            if rng.random() < 0.05 and r.size:
                r[rng.integers(0, r.size, 3)] = 4
        reads.append(r)
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    return (np.concatenate(reads) if n else np.zeros(0, dtype=np.uint8)), lengths


def _both(monkeypatch, codes, lengths) -> dict:
    out = {}
    for backend in ("reference", "native"):
        monkeypatch.setenv("VNXDNA_ORIENT_BACKEND", backend)
        out[backend] = pass1._orientation(codes, lengths)
    return out


@pytest.mark.parametrize("channel", sorted(CHANNELS))
@pytest.mark.parametrize("layout", LAYOUTS, ids=repr)
def test_native_equals_reference(monkeypatch, layout, channel):
    _init(layout)
    sub, ind, rc = CHANNELS[channel]
    codes, lengths = _batch(layout, hash((repr(layout), channel)) & 0xFFFF, 400, sub, ind, rc)
    res = _both(monkeypatch, codes, lengths)
    assert res["reference"].dtype == bool and res["native"].dtype == bool
    np.testing.assert_array_equal(res["reference"], res["native"])
    if layout.marker_period and rc == 0.5 and channel != "indel-heavy":
        assert 0 < res["native"].sum() < lengths.size, "the batch must contain flipped and unflipped reads"


@pytest.mark.parametrize("seed", range(20))
def test_random_junk_batches_agree(monkeypatch, seed):
    _init(BALANCED)
    codes, lengths = _batch(BALANCED, 5000 + seed, 300, sub=0.05, ind=0.05, rc=0.5, junk=0.5)
    res = _both(monkeypatch, codes, lengths)
    np.testing.assert_array_equal(res["reference"], res["native"])


@pytest.mark.parametrize("seed", sorted(GOLDEN))
def test_golden_hashes(monkeypatch, seed):
    _init(BALANCED)
    codes, lengths = _batch(BALANCED, seed, 2000)
    res = _both(monkeypatch, codes, lengths)
    for backend, flip in res.items():
        assert hashlib.sha256(np.packbits(flip).tobytes()).hexdigest() == GOLDEN[seed], backend


def test_decisions_do_not_depend_on_batching(monkeypatch):
    _init(BALANCED)
    monkeypatch.setenv("VNXDNA_ORIENT_BACKEND", "native")
    codes, lengths = _batch(BALANCED, 77, 500)
    whole = pass1._orientation(codes, lengths)
    offs = np.concatenate([[0], np.cumsum(lengths)])
    parts = [pass1._orientation(codes[offs[a]:offs[b]], lengths[a:b]) for a, b in ((0, 1), (1, 7), (7, 500))]
    np.testing.assert_array_equal(whole, np.concatenate(parts))


def test_empty_batch_and_marker_free_layout(monkeypatch):
    monkeypatch.setenv("VNXDNA_ORIENT_BACKEND", "native")
    _init(BALANCED)
    assert pass1._orientation(np.zeros(0, dtype=np.uint8), np.zeros(0, dtype=np.int64)).size == 0
    _init(Layout(12, 2, 0, 0))
    codes, lengths = _batch(Layout(12, 2, 0, 0), 3, 50)
    assert not pass1._orientation(codes, lengths).any()
    # zero-length reads inside a batch
    assert no.scan(np.zeros(0, np.uint8), np.zeros(5, np.int64), np.array([0, 1, 4]), np.array([1, 2, 3], np.uint8),
                   np.array([0, 2]), 2, 2).tolist() == [False] * 5


def test_python_argument_checks():
    mpos, want, starts = np.array([0, 1, 4]), np.array([1, 2, 3], np.uint8), np.array([0, 2])
    codes, lengths = np.zeros(10, np.uint8), np.array([4, 6], np.int64)
    with pytest.raises(ValueError):
        no.scan(codes, np.array([4, 7]), mpos, want, starts, 2, 2)             # lengths exceed codes
    with pytest.raises(ValueError):
        no.scan(codes, np.array([-1, 6]), mpos, want, starts, 2, 2)            # negative length
    with pytest.raises(ValueError):
        no.scan(codes, lengths, mpos, want[:2], starts, 2, 2)                  # want shorter than mpos
    with pytest.raises(ValueError):
        no.scan(codes, lengths, mpos, want, np.array([1, 2]), 2, 2)            # starts must begin at 0
    with pytest.raises(ValueError):
        no.scan(codes, lengths, mpos, want, starts, -1, 2)                     # negative margin
    with pytest.raises(ValueError):
        no.scan(codes.reshape(2, 5), lengths, mpos, want, starts, 2, 2)        # not one-dimensional


def test_c_rejects_inconsistent_arguments():
    lib = no.KERNEL.lib()
    codes = np.zeros(10, np.uint8)
    lengths, offs = np.array([4, 6], np.int64), np.array([0, 4], np.int64)
    mpos, want, block = np.array([0, 1, 4], np.int64), np.array([1, 2, 3], np.uint8), np.array([0, 0, 1], np.int64)
    flip = np.zeros(2, np.uint8)

    def call(**kw):
        a = dict(n=2, codes=codes.ctypes.data, ncodes=10, lengths=lengths.ctypes.data, offs=offs.ctypes.data, nm=3,
                 mpos=mpos.ctypes.data, want=want.ctypes.data, block=block.ctypes.data, nblocks=2, margin=2,
                 max_shift=2, flip=flip.ctypes.data)
        a.update(kw)
        return lib.vnx_or_scan(*a.values())

    assert call() == 0
    assert call(n=-1) == -1 and call(nblocks=4) == -1 and call(margin=-1) == -1 and call(max_shift=-1) == -1
    assert call(ncodes=9) == -1                                                            # read 2 overruns codes
    assert call(offs=np.array([0, 5], np.int64).ctypes.data) == -1
    assert call(lengths=np.array([4, -1], np.int64).ctypes.data) == -1
    assert call(block=np.array([0, 1, 0], np.int64).ctypes.data) == -1                      # blocks not sorted
    assert call(block=np.array([0, 0, 2], np.int64).ctypes.data) == -1                      # block out of range
    assert call(mpos=np.array([0, 4, 1], np.int64).ctypes.data) == -1                       # mpos not ascending
    assert call(codes=None) == -1 and call(flip=None) == -1 and call(mpos=None) == -1
    assert call(n=0, lengths=None, offs=None, flip=None) == 0
    assert call(nm=0, nblocks=0, mpos=None, want=None, block=None) == 0 and not flip.any()


def test_abi_mismatch_is_ignored(tmp_path, monkeypatch):
    lib = no.build(tmp_path / "libvnx_orient_abi99.so", extra_flags=["-DVNX_OR_ABI=99"])
    monkeypatch.setenv("VNXDNA_ORIENT_LIB", str(lib))
    monkeypatch.setattr(no, "_INPLACE", tmp_path / "missing.so")
    monkeypatch.setattr(no, "_candidates", lambda: [lib])
    no._reset_for_tests()
    try:
        assert not no.available()
        assert "ABI 99 != 4" in no.status()["load_error"]
        monkeypatch.setenv("VNXDNA_ORIENT_BACKEND", "auto")
        assert no.resolve_backend() == "reference"
        _init(BALANCED)
        codes, lengths = _batch(BALANCED, 9, 100)
        assert pass1._orientation(codes, lengths).dtype == bool             # the decoder falls back to the reference
        monkeypatch.setenv("VNXDNA_ORIENT_BACKEND", "native")
        with pytest.raises(no.NativeOrientError):
            no.resolve_backend()
        assert no.status()["active_backend"] is None
    finally:
        no._reset_for_tests()


def test_backend_variable_is_validated(monkeypatch):
    monkeypatch.setenv("VNXDNA_ORIENT_BACKEND", "fast")
    with pytest.raises(ValueError):
        no.resolve_backend()
    assert "must be one of" in no.status()["error"]
    monkeypatch.setenv("VNXDNA_ORIENT_BACKEND", "reference")
    assert no.resolve_backend() == "reference" and no.status()["active_backend"] == "reference"


def test_symbols_are_exported_with_c_types():
    assert isinstance(no.KERNEL.lib().vnx_or_abi_version, ctypes._CFuncPtr)
    assert no.KERNEL.lib().vnx_or_abi_version() == no.ABI_VERSION == 4
