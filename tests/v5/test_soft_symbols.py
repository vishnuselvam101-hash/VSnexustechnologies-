"""Phase 4 soft-symbol mathematics: representation, conversions, byte factorisation, numerical robustness."""
from __future__ import annotations

import math

import numpy as np
import pytest

from vnxdna.v5.soft import symbols as ss


def _rand_L(n, seed, scale=3.0):
    return ss.normalise(np.random.default_rng(seed).normal(0, scale, (n, 4)))


def test_distribution_shape_is_preserved():
    """0.51/0.49/0/0 and 0.51/0.163/0.163/0.163 are different objects, not 'A with confidence 0.51'."""
    a = ss.from_probabilities(np.array([[0.51, 0.49, 0.0, 0.0]]))
    b = ss.from_probabilities(np.array([[0.51, 0.49 / 3, 0.49 / 3, 0.49 / 3]]))
    assert not np.allclose(np.exp(a), np.exp(b))
    assert np.isneginf(a[0, 2]) and np.isfinite(b).all()
    assert ss.entropy_bits(a)[0] < ss.entropy_bits(b)[0]


def test_conversions():
    L = ss.from_read(np.array([0, 1, 4]), np.array([20, 20, 20]), 0.01)
    p = np.exp(L)
    assert p[0, 0] == pytest.approx(0.99) and p[0, 1] == pytest.approx(0.01 / 3)
    assert np.allclose(p[2], 0.25)                          # N → uniform
    L2 = ss.from_read(np.array([2]), None, 0.005)          # no quality: the documented default error
    assert np.exp(L2)[0, 2] == pytest.approx(0.995)
    assert np.allclose(np.exp(ss.uniform(3)), 0.25)


def test_combine_is_independent_evidence():
    e1 = ss.observation_loglik(np.array([0]), np.array([10]), 0.01)
    e2 = ss.observation_loglik(np.array([0]), np.array([10]), 0.01)
    e3 = ss.observation_loglik(np.array([1]), np.array([10]), 0.01)
    two = np.exp(ss.combine(e1, e2))[0]
    one_each = np.exp(ss.combine(e1, e3))[0]
    assert two[0] > np.exp(ss.combine(e1))[0, 0]           # agreement sharpens
    assert one_each[0] == pytest.approx(one_each[1])         # disagreement of equal strength cancels


@pytest.mark.parametrize("seed", range(5))
def test_byte_factorisation_is_exact(seed):
    L = _rand_L(280, seed)
    full = ss.byte_log_posterior(L)
    assert np.allclose(np.logaddexp.reduce(full, axis=1), 0.0)          # each byte posterior sums to 1
    hard, _ = ss.byte_hard(L)
    assert np.array_equal(full.argmax(axis=1), hard)
    s = np.sort(full, axis=1)
    assert np.allclose(s[:, -1] - s[:, -2], ss.byte_reliability(L))
    for j in range(0, 70, 7):
        top = ss.byte_topk(L[4 * j:4 * j + 4], 8)
        assert np.allclose([x for x, _ in top], np.sort(full[j])[::-1][:8])
        assert all(full[j, v] == pytest.approx(x) for x, v in top)
    w = np.random.default_rng(seed).integers(0, 256, 70)
    assert ss.word_loglik(L, w) == pytest.approx(full[np.arange(70), w].sum())


@pytest.mark.parametrize("bad", [
    np.array([[np.nan, 0, 0, 0]]), np.array([[np.inf, 0, 0, 0]]), np.array([[-np.inf] * 4]),
    np.array([[0.1, -1, -1, -1]]),                       # log-probability > 0
    np.log(np.array([[0.5, 0.5, 0.5, 0.5]])),           # sums to 2
    np.zeros((3, 3)), np.zeros(4), np.zeros((2, 4), dtype=np.int64)])
def test_malformed_log_probabilities_rejected(bad):
    with pytest.raises(ss.SoftInputError):
        ss.validate_logp(bad)


@pytest.mark.parametrize("bad", [np.array([[np.nan, 1, 0, 0]]), np.array([[np.inf, 0, 0, 0]]), np.array([[-0.1, 0.5, 0.3, 0.3]]),
                                 np.array([[0.3, 0.3, 0.3, 0.3]]), np.array([[0.0, 0.0, 0.0, 0.0]]), np.ones((2, 3)) / 3])
def test_malformed_probabilities_rejected(bad):
    with pytest.raises(ss.SoftInputError):
        ss.from_probabilities(bad)


def test_length_mismatch_rejected():
    with pytest.raises(ss.SoftInputError):
        ss.validate_logp(ss.uniform(279), 280)
    with pytest.raises(ss.SoftInputError):
        ss.observation_loglik(np.zeros(5, np.uint8), np.zeros(4, np.uint8), 0.01)


def test_numerical_extremes():
    # underflow / overflow / denormals: normalisation in the log domain stays finite and exact
    huge = ss.normalise(np.array([[1e300, 0.0, -1e300, 5.0], [-745.0, -746.0, -800.0, -1e4], [1e-310, 0, 0, 0]]))
    assert np.isfinite(huge[:, :2]).all() or np.isneginf(huge).any()
    assert np.allclose(np.logaddexp.reduce(huge, axis=1), 0.0)
    assert huge[0, 0] == 0.0                                 # one dominant term is exactly certain
    one_hot = ss.from_probabilities(np.array([[1.0, 0.0, 0.0, 0.0]]))
    assert one_hot[0, 0] == 0.0 and np.isneginf(one_hot[0, 1:]).all()
    assert ss.byte_reliability(np.repeat(one_hot, 4, axis=0))[0] > 1e299   # certain byte
    tiny = ss.normalise(np.array([[-1e-15, -2e-15, 0.0, -1e-16]]))       # nearly identical: no NaN, deterministic
    assert np.isfinite(tiny).all()
    assert ss.byte_hard(np.repeat(tiny, 4, axis=0))[0][0] == ss.byte_hard(np.repeat(tiny, 4, axis=0))[0][0]
    with pytest.raises(ss.SoftInputError):
        ss.normalise(np.array([[-np.inf] * 4]))
    with pytest.raises(ss.SoftInputError):
        ss.normalise(np.array([[np.nan, 0, 0, 0]]))


def test_nan_never_becomes_confidence():
    L = ss.uniform(280)
    L[17, 2] = np.nan
    with pytest.raises(ss.SoftInputError):
        ss.validate_logp(L)
    assert math.isnan(L[17, 2])


def test_topk_with_impossible_values():
    L = ss.from_probabilities(np.array([[1.0, 0, 0, 0], [0.5, 0.5, 0, 0], [1.0, 0, 0, 0], [1.0, 0, 0, 0]]))
    top = ss.byte_topk(L, 10)
    assert len(top) == 2 and {v for _, v in top} == {0b00000000, 0b00010000}
