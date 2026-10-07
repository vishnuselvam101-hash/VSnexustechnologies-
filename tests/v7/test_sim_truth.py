"""V7 diagnostics: the channel simulator's opt-in per-read ground truth (``simulate_batch(..., truth=True)``).

The truth (source strand, reverse complement) must not change a single read, and must be correct. SYNTHETIC strands;
SIMULATED channel.
"""
from __future__ import annotations

import numpy as np

from vnxdna.simulation.channel import ChannelConfig, simulate_batch

RC = np.array([3, 2, 1, 0, 4], dtype=np.uint8)


def _strands(n=40, L=120, seed=1):
    return np.random.default_rng(seed).integers(0, 4, (n, L)).astype(np.uint8)


def _split(res):
    offs = np.concatenate([[0], np.cumsum(res["lengths"])])
    return [res["codes"][offs[i]:offs[i + 1]] for i in range(res["lengths"].size)]


def test_truth_changes_no_read():
    codes = _strands()
    cfg = ChannelConfig(substitution_rate=0.02, insertion_rate=0.01, deletion_rate=0.03, coverage=6.0,
                        coverage_model="negative-binomial", coverage_dispersion=4.0, duplication_rate=0.1,
                        burst_rate=0.05, burst_max_len=6, n_rate=0.01, reverse_complement_rate=0.5,
                        homopolymer_indel_multiplier=3.0, quality_informative=0.5, dropout_rate=0.1, seed=99)
    for idx in (0, 3):
        a = simulate_batch(codes, cfg, idx)
        b = simulate_batch(codes, cfg, idx, truth=True)
        for k in ("codes", "lengths", "quals"):
            assert np.array_equal(a[k], b[k])
        assert a["stats"] == b["stats"]
        assert "source" not in a and b["source"].size == b["lengths"].size == b["reverse_complement"].size
        assert int(b["reverse_complement"].sum()) >= a["stats"]["reverse_complement"]   # duplicates repeat a flag


def test_truth_is_correct_on_an_error_free_channel():
    codes = _strands(seed=2)
    cfg = ChannelConfig(coverage=5.0, coverage_model="poisson", reverse_complement_rate=0.5, duplication_rate=0.2, seed=7)
    res = simulate_batch(codes, cfg, 1, truth=True)
    reads = _split(res)
    assert len(reads) > 100
    for r, s, rc in zip(reads, res["source"].tolist(), res["reverse_complement"].tolist()):
        want = RC[codes[s][::-1]] if rc else codes[s]
        assert np.array_equal(r, want)


def test_truth_of_an_empty_batch():
    res = simulate_batch(_strands(n=3), ChannelConfig(coverage=0.0, coverage_model="fixed", seed=1), 0, truth=True)
    assert res["source"].size == 0 and res["reverse_complement"].size == 0
