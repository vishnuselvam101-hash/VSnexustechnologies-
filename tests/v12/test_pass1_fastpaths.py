"""V12 session 5 pass-1 fast paths: the one-pass byte-erasure form equals the axis reduction it replaces, on every
input shape and layout the decoder produces (contiguous bool or uint8 flags) and on the ones it does not."""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.sync.template import frame_erasures_to_bytes


def _reference(erased_nt: np.ndarray) -> np.ndarray:
    return erased_nt.reshape(erased_nt.shape[0], -1, 4).any(axis=2)


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("shape", [(1, 4), (5, 8), (37, 1248), (2000, 312)])
def test_frame_erasures_to_bytes_equals_the_reduction(seed, shape):
    rng = np.random.default_rng(seed)
    e = rng.random(shape) < 0.08
    got = frame_erasures_to_bytes(e)
    assert got.dtype == bool and np.array_equal(got, _reference(e))
    u = (e.view(np.uint8) * rng.integers(1, 256, size=shape)).astype(np.uint8)       # any non-zero flag counts
    assert np.array_equal(frame_erasures_to_bytes(u), _reference(u))
    assert np.array_equal(frame_erasures_to_bytes(e[:, ::-1]), _reference(e[:, ::-1]))      # non-contiguous: fallback
    assert np.array_equal(frame_erasures_to_bytes(e[::2]), _reference(e[::2]))
    assert np.array_equal(frame_erasures_to_bytes(np.ascontiguousarray(e[::2])), _reference(e[::2]))


def test_frame_erasures_to_bytes_single_flag_positions():
    for col in range(8):
        e = np.zeros((1, 8), dtype=bool)
        e[0, col] = True
        assert frame_erasures_to_bytes(e).tolist() == [[col < 4, col >= 4]]


def test_frame_erasures_to_bytes_empty():
    assert frame_erasures_to_bytes(np.zeros((0, 8), dtype=bool)).shape == (0, 2)
