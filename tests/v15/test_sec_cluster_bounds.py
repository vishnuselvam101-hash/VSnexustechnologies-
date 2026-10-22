"""F5: the native cluster wrapper must bound its kernel arguments before it allocates or narrows them.

``native/cluster.py`` sized the sketch arrays with the caller's ``s`` before the kernel had refused the value, and it
passed ``k`` and ``s`` to a ctypes ``c_int32`` argument: a value that does not fit is narrowed without a word, so
``2 ** 32 + 12`` reached the kernel as ``12`` and the caller got the sketch of a different k. The arguments are
checked before anything is allocated now, and a value that does not fit the kernel's int32 is refused instead of
being narrowed.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from vnxdna.native import cluster as nc
from vnxdna.recovery.cluster import consensus, sketch


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


def _reads(n: int = 4, width: int = 8, seed: int = 7):
    """A few synthetic reads (codes 0..3) and their lengths."""
    rng = np.random.default_rng(seed)
    raw = rng.integers(0, 4, (n, width)).astype(np.uint8)
    return raw, np.full(n, width, dtype=np.int64)


def _fb_case():
    """One read that matches its template exactly: (tpl, mc, buf, offsets, lengths, band)."""
    tpl = np.array([[0, 1]], dtype=np.int16)
    mc = np.array([[3, 3]], dtype=np.int32)
    buf = np.array([0, 1], dtype=np.uint8)
    off = np.array([0], dtype=np.int64)
    lens = np.array([2], dtype=np.int64)
    band = np.array([0], dtype=np.int64)
    return tpl, mc, buf, off, lens, band


def test_a_k_that_does_not_fit_the_kernel_argument_is_refused():
    """``2 ** 32 + 12`` would reach the int32 argument as ``12``: the sketch of a different k."""
    raw, lengths = _reads()
    with pytest.raises(nc.NativeClusterError):
        nc.sketch(raw, lengths, 2 ** 32 + 12, 8)


def test_an_s_that_does_not_fit_the_kernel_argument_is_refused():
    raw, lengths = _reads()
    with pytest.raises(nc.NativeClusterError):
        nc.sketch(raw, lengths, 3, 2 ** 32 + 8)


def test_nothing_is_allocated_for_an_s_above_the_kernel_limit():
    """The sketch arrays are sized by ``s``: the value is refused before the allocation, not after it."""
    raw, lengths = _reads()
    attempts: list = []
    real_empty = np.empty

    def spy(shape, *args, **kwargs):
        attempts.append(shape)
        return real_empty(shape, *args, **kwargs)

    np.empty = spy
    try:
        with pytest.raises(nc.NativeClusterError):
            nc.sketch(raw, lengths, 3, 1 << 20)
    finally:
        np.empty = real_empty
    assert not attempts, f"the wrapper allocated {attempts} before the kernel refused the value"


def test_a_c_indel_that_does_not_fit_the_kernel_argument_is_refused():
    """The forward-backward costs are int32 arguments too: a value that does not fit is refused, not narrowed."""
    tpl, mc, buf, off, lens, band = _fb_case()
    with pytest.raises(nc.NativeClusterError):
        nc.fb(tpl, mc, buf, off, lens, band, 0, 2 ** 32 + 6, 0)


def test_a_valid_sketch_is_unchanged():
    raw, lengths = _reads()
    hashes, orient = nc.sketch(raw, lengths, 5, 8)
    want_hashes, want_orient = sketch.sketch_reads_reference(raw, lengths, 5, 8)
    assert np.array_equal(hashes, want_hashes)
    assert np.array_equal(orient, want_orient)


def test_a_valid_forward_backward_is_unchanged():
    tpl, mc, buf, off, lens, band = _fb_case()
    calls, opt = nc.fb(tpl, mc, buf, off, lens, band, 0, 6, 0)
    want_calls, want_opt = consensus.fb_calls_reference(tpl, mc, [np.array([0, 1], dtype=np.uint8)],
                                                        np.array([0], dtype=np.int64), 6, 0)
    assert np.array_equal(calls, want_calls)
    assert np.array_equal(opt, want_opt)
