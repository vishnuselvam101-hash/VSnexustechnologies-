"""Bounded-memory decode pieces: disk-backed V6 parity rows, open-file-limited spill buckets, memory-mapped orphans."""
from __future__ import annotations

import resource

import numpy as np
import pytest

from vnxdna.v4 import decoder as de
from vnxdna.v6.decode import _ParityRows
from vnxdna.v6.outer import Geometry


def _geo():
    return Geometry(K=64, M=16, D=4, Mc=2, P=40, container_size=10 * 64 * 40).validate()


@pytest.mark.parametrize("on_disk", [False, True])
def test_parity_rows_store_round_trip(tmp_path, on_disk):
    geo = _geo()
    store = _ParityRows(geo, tmp_path / "p.bin" if on_disk else None)
    rng = np.random.default_rng(1)
    rows = {g: rng.integers(0, 256, (geo.K, geo.P), dtype=np.uint8) for g in range(geo.G, geo.G + 3)}
    for g, v in rows.items():
        store[g] = v
    assert len(store) == 3 and geo.G + 1 in store and geo.G + 5 not in store
    for g, v in rows.items():
        assert np.array_equal(store[g], v)
    with pytest.raises(KeyError):
        store[geo.G + 5]
    store.close()
    if on_disk:
        assert (tmp_path / "p.bin").stat().st_size == 3 * geo.K * geo.P
        assert (tmp_path / "p.bin").stat().st_mode & 0o777 == 0o600


def test_bucket_count_unchanged_below_cap_and_bounded_above():
    assert de._bucket_count(1) == 1
    assert de._bucket_count(10_000_000) == 50                      # V4/V5 value
    assert de._bucket_count(51_200_000) == 256
    soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    big = de._bucket_count(10 ** 10)
    assert 256 <= big <= 4096
    if soft != resource.RLIM_INFINITY:
        assert 2 * big + 128 <= max(soft, 2 * 256 + 128)


def test_orphans_are_memory_mapped(tmp_path):
    sp = de.Spill(tmp_path, 1, 40, 300, raw_nt=330)
    rec = {"orph_raw": np.ones((5, 330), np.uint8), "orph_rawq": np.zeros((5, 330), np.uint8),
           "orph_rawlen": np.full(5, 313), "orph_hasq": np.ones(5, np.uint8),
           "acc_fields": np.zeros((0, 4), np.int64), "acc_payload": np.zeros((0, 40), np.uint8),
           "pend_fields": np.zeros((0, 4), np.int64), "pend_bases": np.zeros((0, 300), np.uint8)}
    sp.write(rec)
    sp.close()
    orph = sp.load_orphans()
    assert isinstance(orph, np.memmap) and len(orph) == 5 and int(orph["rawlen"][0]) == 313
    (tmp_path / "e").mkdir()
    empty = de.Spill(tmp_path / "e", 1, 40, 300, raw_nt=330)
    empty.close()
    assert len(empty.load_orphans()) == 0
