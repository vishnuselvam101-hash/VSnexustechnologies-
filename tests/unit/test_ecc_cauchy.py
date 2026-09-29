"""Outer erasure code: field arithmetic, MDS property, exhaustive loss patterns."""
import itertools
import random

import numpy as np
import pytest

from vnxdna.ecc import gf256
from vnxdna.ecc.cauchy import CauchyErasureCode, reference_encode
from vnxdna.errors import ConfigurationError, InsufficientRedundancyError


def test_field_axioms_exhaustive():
    for a in range(1, 256):
        assert gf256.mul(a, gf256.inv(a)) == 1
        assert gf256.MUL[a, gf256.inv(a)] == 1
    for a in range(256):
        for b in range(256):
            assert gf256.MUL[a, b] == gf256.mul(a, b) == gf256.mul(b, a)


def test_generator_is_primitive():
    assert len({gf256.power(gf256.GENERATOR, i) for i in range(255)}) == 255


@pytest.mark.parametrize("k,m", [(1, 0), (1, 1), (1, 5), (2, 2), (4, 2), (5, 3), (8, 4), (10, 4), (6, 6)])
def test_every_kxk_submatrix_invertible(k, m):
    """MDS ⇔ every K-row subset of the generator is invertible. Exhaustive."""
    code = CauchyErasureCode(k, m)
    for rows in itertools.combinations(range(k + m), k):
        gf256.matrix_invert([code.generator_row(r) for r in rows])  # raises if singular


def _roundtrip_all_patterns(k, m, length=7, seed=0):
    code = CauchyErasureCode(k, m)
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 256, size=(1, k, length), dtype=np.uint8)
    full = np.concatenate([data, code.encode(data)], axis=1)
    checked = 0
    for lost in range(0, m + 1):
        for pattern in itertools.combinations(range(k + m), lost):
            present = np.ones((1, k + m), dtype=bool)
            present[0, list(pattern)] = False
            damaged = full.copy()
            damaged[0, list(pattern)] = 0xA5  # garbage in erased positions must be ignored
            assert np.array_equal(code.decode(damaged, present), data), (k, m, pattern)
            checked += 1
    return checked


def test_8_plus_4_every_loss_pattern_up_to_4():
    # 1 + 12 + 66 + 220 + 495 patterns, including the V0.1 failing pattern {4,5,7,11}
    assert _roundtrip_all_patterns(8, 4) == 794


def test_v0_1_counterexample_pattern_recovers():
    code = CauchyErasureCode(8, 4)
    data = np.arange(8 * 5, dtype=np.uint8).reshape(1, 8, 5)
    full = np.concatenate([data, code.encode(data)], axis=1)
    present = np.ones((1, 12), dtype=bool)
    present[0, [4, 5, 7, 11]] = False
    assert np.array_equal(code.decode(full, present), data)


@pytest.mark.parametrize("k,m", [(1, 1), (1, 3), (2, 1), (3, 2), (4, 2), (4, 4), (5, 3), (6, 2), (10, 4), (12, 4), (16, 4)])
def test_small_configurations_exhaustive(k, m):
    assert _roundtrip_all_patterns(k, m) == sum(1 for t in range(m + 1) for _ in itertools.combinations(range(k + m), t))


@pytest.mark.parametrize("k,m", [(32, 8), (64, 16), (100, 28), (200, 56)])
def test_large_configurations_deterministic_sampling(k, m):
    code = CauchyErasureCode(k, m)
    rng = random.Random(f"{k}/{m}")
    data = np.random.default_rng(k).integers(0, 256, size=(1, k, 3), dtype=np.uint8)
    full = np.concatenate([data, code.encode(data)], axis=1)
    for _ in range(40):
        lost = rng.sample(range(k + m), rng.randint(1, m))
        present = np.ones((1, k + m), dtype=bool)
        present[0, lost] = False
        assert np.array_equal(code.decode(full, present), data)


def test_one_more_than_m_losses_is_rejected_not_miscorrected():
    code = CauchyErasureCode(8, 4)
    data = np.zeros((2, 8, 4), dtype=np.uint8)
    full = np.concatenate([data, code.encode(data)], axis=1)
    present = np.ones((2, 12), dtype=bool)
    present[1, :5] = False
    with pytest.raises(InsufficientRedundancyError) as info:
        code.decode(full, present)
    assert info.value.details["unrecoverable_stripes"] == [1]


def test_vectorized_encoder_matches_scalar_reference():
    code = CauchyErasureCode(7, 5)
    rng = np.random.default_rng(3)
    data = rng.integers(0, 256, size=(1, 7, 33), dtype=np.uint8)
    expected = reference_encode(code, [bytes(r) for r in data[0]])
    got = np.concatenate([data, code.encode(data)], axis=1)[0]
    assert [bytes(r) for r in got] == expected


def test_many_stripes_with_mixed_patterns():
    code = CauchyErasureCode(6, 3)
    rng = np.random.default_rng(9)
    data = rng.integers(0, 256, size=(50, 6, 11), dtype=np.uint8)
    full = np.concatenate([data, code.encode(data)], axis=1)
    present = np.ones((50, 9), dtype=bool)
    for s in range(50):
        present[s, rng.choice(9, size=rng.integers(0, 4), replace=False)] = False
    assert np.array_equal(code.decode(full, present), data)


def test_zero_parity_code_is_identity():
    code = CauchyErasureCode(3, 0)
    data = np.ones((1, 3, 2), dtype=np.uint8)
    assert code.encode(data).shape == (1, 0, 2)
    assert np.array_equal(code.decode(data, np.ones((1, 3), bool)), data)


@pytest.mark.parametrize("k,m", [(0, 1), (-1, 2), (200, 57), (True, 1), (2.0, 1), (1, -1)])
def test_invalid_configurations(k, m):
    with pytest.raises(ConfigurationError):
        CauchyErasureCode(k, m)


def test_numpy_inverse_matches_reference_inverse():
    rng = random.Random(5)
    for n in (1, 2, 5, 17, 40):
        code = CauchyErasureCode(n, n)
        rows = sorted(rng.sample(range(2 * n), n))
        matrix = [code.generator_row(r) for r in rows]
        assert gf256.matrix_invert_np(np.array(matrix)).tolist() == gf256.matrix_invert(matrix)
    with pytest.raises(ValueError):
        gf256.matrix_invert_np(np.array([[1, 1], [1, 1]]))


@pytest.mark.parametrize("shape", [(1, 3, 5, 4), (7, 4, 6, 9), (300, 32, 96, 32)])
def test_matmul_paths_agree_with_scalar(shape):
    s, r, c, length = shape
    rng = np.random.default_rng(sum(shape))
    coeff = rng.integers(0, 256, size=(r, c), dtype=np.uint8)
    rows = rng.integers(0, 256, size=(s, c, length), dtype=np.uint8)
    got = gf256.matmul_rows(coeff, rows)
    for stripe in (0, s - 1):
        for i in (0, r - 1):
            expect = np.zeros(length, dtype=np.uint8)
            for j in range(c):
                expect ^= np.array([gf256.mul(int(coeff[i, j]), int(v)) for v in rows[stripe, j]], dtype=np.uint8)
            assert np.array_equal(got[stripe, i], expect)


def test_max_size_code_recovers_parity_count_erasures():
    code = CauchyErasureCode(192, 64)
    rng = np.random.default_rng(11)
    data = rng.integers(0, 256, size=(2, 192, 8), dtype=np.uint8)
    full = np.concatenate([data, code.encode(data)], axis=1)
    present = np.ones((2, 256), dtype=bool)
    present[0, rng.choice(256, 64, replace=False)] = False
    present[1, :64] = False  # all losses on data shards: worst case for the solver
    assert np.array_equal(code.decode(full, present), data)
