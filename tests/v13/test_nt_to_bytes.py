"""V13 C3: the uint32 multiply form of ``nt_to_bytes`` equals the shift-and-or reference on every input."""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.dnaenc.mapping import bytes_to_nt, nt_to_bytes, nt_to_bytes_reference


@pytest.mark.parametrize("seed", range(8))
def test_fast_form_equals_the_reference(seed):
    rng = np.random.default_rng(seed)
    n, b = int(rng.integers(0, 300)), int(rng.integers(1, 90))
    for hi in (4, 5, 256):                               # codes 0..3, with N (4), and arbitrary bytes
        nt = rng.integers(0, hi, size=(n, 4 * b)).astype(np.uint8)
        want = nt_to_bytes_reference(nt)
        got = nt_to_bytes(nt)
        assert got.dtype == want.dtype == np.uint8 and got.shape == want.shape == (n, b)
        assert np.array_equal(got, want)
        assert np.array_equal(nt_to_bytes(np.asfortranarray(nt)), want)      # non-contiguous input: the reference runs
        assert np.array_equal(nt_to_bytes(nt[:, ::-1][:, ::-1]), want)
        assert np.array_equal(nt_to_bytes(nt.astype(np.int64)), want)


def test_round_trip_and_edges():
    rng = np.random.default_rng(99)
    frames = rng.integers(0, 256, size=(50, 37)).astype(np.uint8)
    assert np.array_equal(nt_to_bytes(bytes_to_nt(frames)), frames)
    assert nt_to_bytes(np.zeros((0, 8), dtype=np.uint8)).shape == (0, 2)
    assert nt_to_bytes(np.zeros((3, 0), dtype=np.uint8)).shape == (3, 0)
    one = np.array([[3, 2, 1, 0]], dtype=np.uint8)
    assert nt_to_bytes(one).tolist() == [[0b11100100]]
    # a view with an offset into a larger buffer is still aligned and contiguous: the fast form applies
    buf = rng.integers(0, 4, size=(10, 16)).astype(np.uint8)
    assert np.array_equal(nt_to_bytes(buf[2:7]), nt_to_bytes_reference(buf[2:7]))
