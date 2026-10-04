"""Seeded differential fuzz of the native inner RS decoder against vnxdna.v4.rs_fast (every usable dispatch level).

Thousands of words per seed: valid codewords with errors/erasures around the bound, garbage, constant words, bursts,
dense random erasure flags, codes from (2, 1) to (255, 254). The longer run (>= 100k words per level) is
benchmarks/v6/native_rs/stress_fuzz.py; this module keeps the same generator at test-suite size.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import numpy as np
import pytest

from vnxdna.v4 import rs_fast
from vnxdna.v6 import native_rs as nr

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("native_rs_stress_fuzz", REPO / "benchmarks" / "v6" / "native_rs" / "stress_fuzz.py")
fuzz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fuzz)


@pytest.fixture(scope="module", autouse=True)
def _native(tmp_path_factory):
    if not nr.available():
        try:
            lib = nr.build(tmp_path_factory.mktemp("native_rs") / "libvnx_rs.so")
        except nr.NativeRSError as error:
            pytest.skip(f"native RS decoder unavailable and cannot be built here: {error}")
        os.environ[nr.ENV_LIB] = str(lib)
        nr._reset_for_tests()
    assert nr.available(), nr.status()


@pytest.mark.parametrize("seed", range(6))
def test_seeded_differential_fuzz(seed):
    stats = fuzz.run(rounds=60, seed=1000 + seed, levels=nr.supported_levels())
    assert stats["mismatches"] == 0, stats
    assert stats["words"] >= 2000
    assert 0 < stats["reference_ok"] < stats["words"]       # both outcomes are exercised


@pytest.mark.parametrize("nsym", [12, 16, 20])
def test_profile_codes_near_the_bound(nsym):
    """The production shapes (70, 12/16/20): every e, f split from inside the bound to 3 symbols beyond it."""
    rng = np.random.default_rng(nsym)
    n = 70
    rows, masks = [], []
    for f in range(0, nsym + 2):
        for e in range(0, (nsym - min(f, nsym)) // 2 + 4):
            cw = fuzz.valid_codewords(rng, 40, n, nsym)
            er = np.zeros(cw.shape, dtype=bool)
            for i in range(cw.shape[0]):
                pos = rng.permutation(n)
                cw[i, pos[:e]] ^= rng.integers(1, 256, e, dtype=np.uint8)
                er[i, pos[e:e + f]] = True
                cw[i, pos[e:e + f]] = rng.integers(0, 256, f, dtype=np.uint8)
            rows.append(cw)
            masks.append(er)
    cw, er = np.concatenate(rows), np.concatenate(masks)
    ref = rs_fast.decode_batch(cw, nsym, er)
    for lv in nr.supported_levels():
        got = nr.decode_batch(cw, nsym, er, backend=lv)
        for a, b in zip(got, ref):
            assert a.dtype == b.dtype and np.array_equal(a, b), lv


@pytest.mark.parametrize("first", [2, 130, 185, 225])
def test_pure_garbage_every_code_length(first):
    """Uniformly random words for every n (and three nsym values each): mostly failures, occasional miscorrections.
    Split into four n ranges of similar reference cost so that parallel runs balance."""
    rng = np.random.default_rng(99 + first)
    last = {2: 130, 130: 185, 185: 225, 225: 256}[first]
    for n in range(first, last):
        for nsym in sorted({1, n // 2 or 1, n - 1}):
            cw = rng.integers(0, 256, (12, n), dtype=np.uint8)
            er = rng.random(cw.shape) < rng.random() * 0.3
            ref = rs_fast.decode_batch(cw, nsym, er)
            for lv in nr.supported_levels():
                got = nr.decode_batch(cw, nsym, er, backend=lv)
                for a, b in zip(got, ref):
                    assert np.array_equal(a, b), (lv, n, nsym)
