"""V13 E1: ``consensus._row_ids`` partitions rows exactly as ``np.unique(axis=0, return_inverse=True)`` does."""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.recovery.consensus import _row_ids


def _partition(inv: np.ndarray) -> list:
    first = {}
    return [first.setdefault(int(v), len(first)) for v in inv.tolist()]      # relabel in order of first appearance


@pytest.mark.parametrize("seed", range(6))
def test_same_partition_as_np_unique(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(0, 3000))
    rows = rng.integers(0, 6, size=(n, 4)).astype(np.int64)                    # many duplicates
    rows[:, 2] = rng.integers(0, 1 << 32, size=n) if seed % 2 else rows[:, 2]  # wide group field or small
    rows[rng.random(n) < 0.1] = 0
    got = _row_ids(rows)
    _, want = np.unique(rows, axis=0, return_inverse=True)
    want = want.ravel()
    assert got.dtype == np.int64 and got.shape == (n,)
    assert _partition(got) == _partition(want)
    if n:
        assert got.min() == 0 and got.max() + 1 == len(np.unique(rows, axis=0))


def test_edges():
    assert _row_ids(np.zeros((0, 4), dtype=np.int64)).tolist() == []
    assert _row_ids(np.array([[1, 2, 3, 4]])).tolist() == [0]
    assert _row_ids(np.array([[1, 2, 3, 4], [1, 2, 3, 4]])).tolist() == [0, 0]
    ids = _row_ids(np.array([[2, 0], [1, 0], [2, 0], [1, 1]]))
    assert ids[0] == ids[2] and len(set(ids.tolist())) == 3
    neg = np.array([[-1, 5], [-1, 5], [1 << 40, -3]], dtype=np.int64)
    assert _row_ids(neg).tolist()[:2] == [_row_ids(neg)[0]] * 2
