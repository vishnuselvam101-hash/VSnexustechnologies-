"""V8.14 optimisation of ``polish.edit_costs`` (the measured bottleneck of the Phase 1 full-template polish): the vectorised
version is bit-identical to the V7 implementation (``_edit_costs_reference``) on randomised inputs, including reads that do
not fit their band, different read lengths, bands and costs, per-read templates and marker-like mismatch costs."""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.recovery.cluster.polish import _edit_costs_reference, edit_costs


def _noisy(rng, s: np.ndarray, p: float) -> np.ndarray:
    out = []
    for b in s.tolist():
        u = rng.random()
        if u < p / 3:
            continue
        if u < 2 * p / 3:
            out.append(int(rng.integers(0, 4)))
        out.append(int(rng.integers(0, 4)) if u < p else b)
    return np.array(out, dtype=np.uint8)


@pytest.mark.parametrize("seed", range(40))
def test_vectorised_edit_costs_equal_the_reference(seed):
    rng = np.random.default_rng(1000 + seed)
    T = int(rng.integers(5, 60))
    n = int(rng.integers(1, 12))
    band = int(rng.integers(2, 14))
    cin, csub = int(rng.integers(2, 9)), int(rng.integers(1, 6))
    tpl = np.stack([rng.integers(0, 4, T) for _ in range(n)]).astype(np.int16)
    if seed % 3 == 0:
        tpl[:] = tpl[0]
    mc = rng.integers(1, 7, (n, T)).astype(np.int32)
    reads = [_noisy(rng, tpl[k].astype(np.uint8), float(rng.uniform(0, 0.35))) for k in range(n)]
    if seed % 5 == 0:
        reads[0] = rng.integers(0, 4, T + band + 5).astype(np.uint8)          # does not fit its band
    bands = rng.integers(max(1, band - 2), band + 3, n)
    a = edit_costs(tpl, mc, reads, bands, cin, csub)
    b = _edit_costs_reference(tpl, mc, reads, bands, cin, csub)
    for x, y in zip(a, b):
        assert x.dtype == y.dtype and x.shape == y.shape and np.array_equal(x, y)
