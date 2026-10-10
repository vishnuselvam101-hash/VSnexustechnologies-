"""Hybrid architecture: the vectorised ``consensus.snap_addresses`` equals ``snap_addresses_reference`` (the per-read
Python loop it replaces) on every input: reads one header byte away from one or several missing addresses, two
readings that each differ in a different byte, reads already missing, alternative readings that are missing, and chunk
boundaries. SYNTHETIC addresses."""
from __future__ import annotations

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vnxdna.recovery import consensus as cs


def _missing(rng, n: int, groups: int) -> set:
    out = set()
    while len(out) < n:
        out.add((int(rng.integers(0, 2)), 0xBEEF, int(rng.integers(0, groups)), int(rng.integers(0, 80))))
    return out


def _mutate(rng, key: tuple, nbytes: int) -> tuple:
    kind, tag, group, symbol = key
    for _ in range(nbytes):
        f = int(rng.integers(0, 4))
        if f == 0:
            kind ^= 1
        elif f == 1:
            tag ^= 1 << int(rng.integers(0, 16))
        elif f == 2:
            group ^= 1 << int(rng.integers(0, 32))
        else:
            symbol ^= 1 << int(rng.integers(0, 16))
    return kind, tag, group, symbol


def _case(seed: int, n_reads: int = 3000, n_missing: int = 200, groups: int = 6, with_alt: bool = True):
    rng = np.random.default_rng(seed)
    missing = _missing(rng, n_missing, groups)
    ml = sorted(missing)
    keys, alts = [], []
    for _ in range(n_reads):
        base = ml[int(rng.integers(0, len(ml)))] if rng.random() < 0.8 else _missing(rng, 1, groups * 4).pop()
        k = _mutate(rng, base, int(rng.integers(0, 3)))
        a = _mutate(rng, base, int(rng.integers(0, 3))) if with_alt else k
        keys.append(k)
        alts.append(a)
    return np.array(keys, dtype=np.int64), missing, (np.array(alts, dtype=np.int64) if with_alt else None)


def _same(keys, missing, alt):
    ref = cs.snap_addresses_reference(keys, missing, alt)
    got = cs.snap_addresses(keys, missing, alt)
    np.testing.assert_array_equal(got[0], ref[0])
    assert got[1] == ref[1]
    return ref[1]


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("with_alt", [True, False])
def test_vectorised_equals_reference(seed, with_alt):
    keys, missing, alt = _case(seed, with_alt=with_alt)
    assert _same(keys, missing, alt) > 0


def test_dense_symbols_many_candidates_per_key():
    """Every symbol of a group missing: the low-byte lookups return up to 80 candidates per read."""
    rng = np.random.default_rng(5)
    missing = {(0, 7, 3, s) for s in range(80)} | {(0, 7, 4, s) for s in range(0, 80, 3)}
    ml = sorted(missing)
    keys = np.array([_mutate(rng, ml[int(rng.integers(0, len(ml)))], int(rng.integers(0, 3))) for _ in range(2000)])
    alt = np.array([_mutate(rng, tuple(k), int(rng.integers(0, 2))) for k in keys.tolist()])
    _same(keys, missing, alt)


def test_chunk_boundaries(monkeypatch):
    keys, missing, alt = _case(99, n_reads=1000)
    monkeypatch.setattr(cs, "SNAP_CHUNK", 7)
    _same(keys, missing, alt)


def test_empty_and_trivial_inputs():
    keys = np.zeros((0, 4), dtype=np.int64)
    assert cs.snap_addresses(keys, {(0, 1, 2, 3)})[1] == 0
    k = np.array([[0, 1, 2, 3]])
    assert cs.snap_addresses(k, set())[1] == 0
    _same(np.array([[0, 1, 2, 3], [0, 1, 2, 4]]), {(0, 1, 2, 3)}, None)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(0, 2 ** 32 - 1), st.integers(1, 40), st.integers(1, 120), st.integers(1, 5), st.booleans())
def test_hypothesis(seed, n_missing, n_reads, groups, with_alt):
    keys, missing, alt = _case(seed, n_reads=n_reads, n_missing=min(n_missing, 2 * groups * 80), groups=groups,
                               with_alt=with_alt)
    _same(keys, missing, alt)
