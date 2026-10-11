"""Hybrid architecture: the Rust integrity kernel (``native/rust/integrity``, ABI 1) equals the NumPy reference
``core.crc.crc32_rows`` and ``zlib.crc32`` bit for bit; the C ABI rejects inconsistent arguments; backends are
selectable and observable; frame encoding/decoding gives identical results with either backend. SYNTHETIC data."""
from __future__ import annotations

import zlib

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vnxdna.core.crc import crc32_rows as reference
from vnxdna.native import integrity as ni


@pytest.fixture(scope="module", autouse=True)
def _library():
    ni._reset_for_tests()
    if not ni.available():
        try:
            ni.build()
        except ni.NativeIntegrityError as error:
            pytest.skip(f"Rust integrity kernel unavailable and cannot be built here: {error}")
        ni._reset_for_tests()
    assert ni.available(), ni.status()
    yield
    ni._reset_for_tests()


@pytest.mark.parametrize("shape", [(0, 5), (1, 0), (3, 0), (1, 1), (7, 13), (1000, 63), (257, 8), (64, 200)])
def test_equals_reference_and_zlib(shape):
    rng = np.random.default_rng(sum(shape))
    rows = rng.integers(0, 256, shape, dtype=np.uint8)
    got = ni.crc32_rows_native(rows)
    np.testing.assert_array_equal(got, reference(rows))
    assert got.dtype == np.uint32 and got.shape == (shape[0],)
    assert got.tolist() == [zlib.crc32(r.tobytes()) for r in rows]


def test_strided_views_and_other_dtypes():
    rng = np.random.default_rng(3)
    big = rng.integers(0, 256, (300, 80), dtype=np.uint8)
    for view in (big[:, 5:60], big[::3, 10:], big[::-1, :40], big[:, ::2], big.T[:20, :50], big[7:8, :]):
        np.testing.assert_array_equal(ni.crc32_rows_native(view), reference(view))
    ints = rng.integers(0, 256, (20, 9)).astype(np.int64)
    np.testing.assert_array_equal(ni.crc32_rows_native(ints), reference(ints))


def test_thread_count_does_not_change_results():
    rows = np.random.default_rng(4).integers(0, 256, (1001, 57), dtype=np.uint8)
    one = ni.crc32_rows_native(rows, threads=1)
    for t in (2, 3, 8, 64, 1000):
        np.testing.assert_array_equal(ni.crc32_rows_native(rows, threads=t), one)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(0, 40), st.integers(0, 70), st.integers(0, 2 ** 32 - 1), st.integers(1, 9))
def test_hypothesis(n, w, seed, threads):
    rows = np.random.default_rng(seed).integers(0, 256, (n, w), dtype=np.uint8)
    np.testing.assert_array_equal(ni.crc32_rows_native(rows, threads=threads), reference(rows))


def test_rejects_non_2d():
    with pytest.raises(ValueError):
        ni.crc32_rows_native(np.zeros(5, np.uint8))


def test_c_abi_rejects_inconsistent_arguments():
    lib = ni.KERNEL.lib()
    data = np.zeros(64, np.uint8)
    out = np.zeros(8, np.uint32)
    d, o = data.ctypes.data, out.ctypes.data

    def call(n=8, w=8, stride=8, dp=d, dlen=64, op=o, olen=8, t=1):
        return lib.vnx_ri_crc32_rows(n, w, stride, dp, dlen, op, olen, t)

    assert call() == 0
    for kw in (dict(dlen=63), dict(olen=7), dict(stride=7), dict(n=-1), dict(w=-1), dict(t=0), dict(t=65),
               dict(dp=None), dict(op=None), dict(n=2 ** 62, stride=2 ** 2)):
        assert call(**kw) == -1, kw
    assert call(n=0, dp=None, dlen=0, op=None, olen=0) == 0


def test_backend_selection_and_status(monkeypatch):
    rows = np.random.default_rng(8).integers(0, 256, (50, 30), dtype=np.uint8)
    for backend in ("native", "reference", "auto"):
        monkeypatch.setenv("VNXDNA_INTEGRITY_BACKEND", backend)
        np.testing.assert_array_equal(ni.crc32_rows(rows), reference(rows))
    monkeypatch.setenv("VNXDNA_INTEGRITY_BACKEND", "reference")
    assert ni.resolve_backend() == "reference" and ni.status()["active_backend"] == "reference"
    monkeypatch.setenv("VNXDNA_INTEGRITY_BACKEND", "bogus")
    with pytest.raises(ValueError):
        ni.resolve_backend()
    assert "must be one of" in ni.status()["error"]


def test_missing_library_falls_back_or_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(ni, "_candidates", lambda: [tmp_path / "missing.so"])
    ni._reset_for_tests()
    try:
        monkeypatch.setenv("VNXDNA_INTEGRITY_BACKEND", "auto")
        assert ni.resolve_backend() == "reference"
        monkeypatch.setenv("VNXDNA_INTEGRITY_BACKEND", "native")
        with pytest.raises(ni.NativeIntegrityError):
            ni.resolve_backend()
    finally:
        ni._reset_for_tests()


def test_frames_identical_with_either_backend(monkeypatch):
    from vnxdna.codec.codecs import InnerRS
    from vnxdna.dnaenc import frame4
    from vnxdna.dnaenc.layout import PROFILES
    from vnxdna.dnaenc.scrambler import keystreams
    lay = PROFILES["v4-balanced"][0]
    rng = np.random.default_rng(12)
    pays = rng.integers(0, 256, (200, lay.payload_bytes), dtype=np.uint8)
    res = {}
    for backend in ("reference", "native"):
        monkeypatch.setenv("VNXDNA_INTEGRITY_BACKEND", backend)
        plain = frame4.plain_rows(9, 0, np.arange(200), np.arange(200) % 7, pays)
        ks = keystreams(plain.shape[1])
        msg = np.concatenate([np.zeros((200, 1), np.uint8), plain ^ ks[0]], axis=1)
        frames = np.concatenate([msg, InnerRS(lay.inner_parity).parity(msg)], axis=1)
        bad = np.random.default_rng(13)
        frames[bad.integers(0, 200, 40), bad.integers(1, lay.frame_bytes, 40)] ^= 0x5A    # some rows corrupted
        frames[:5] = bad.integers(0, 256, (5, lay.frame_bytes))                           # some unrecoverable
        parsed = frame4.decode_frames(lay, frames)
        assert parsed.ok.sum() >= 150 and not parsed.ok[:5].any()
        res[backend] = (plain, parsed.ok, parsed.payload)
    for a, b in zip(res["reference"], res["native"]):
        np.testing.assert_array_equal(a, b)
