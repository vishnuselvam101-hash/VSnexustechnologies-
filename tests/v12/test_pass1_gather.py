"""V12 pass-1 bookkeeping: the flat gathers that replaced per-read Python loops give the same values as the loops, and
the orientation pre-pass with early exit decides exactly as scoring every shift for every read."""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.dnaenc.layout import PROFILES
from vnxdna.recovery import pass1
from vnxdna.recovery.options import DecodeOptions


def _gather_reference(flat, offs, lengths, sel, cols, clip=None):
    out = np.zeros((sel.size, cols.size), dtype=np.uint8)
    valid = np.zeros((sel.size, cols.size), dtype=bool)
    for j, i in enumerate(sel.tolist()):
        r = flat[offs[i]:offs[i] + lengths[i]]
        for c, pos in enumerate(cols.tolist()):
            if pos < r.size:
                out[j, c] = min(int(r[pos]), clip) if clip is not None else r[pos]
                valid[j, c] = True
    return out, valid


def _batch(rng, n, lo, hi):
    lengths = rng.integers(lo, hi + 1, size=n).astype(np.int64)
    offs = np.concatenate([[0], np.cumsum(lengths)])
    flat = rng.integers(0, 5, size=int(offs[-1])).astype(np.uint8)
    return flat, offs, lengths


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_gather_rows_matches_the_loop(seed):
    rng = np.random.default_rng(seed)
    flat, offs, lengths = _batch(rng, 40, 0, 30)
    sel = rng.permutation(40)[:25]
    for cols, clip in ((np.arange(24), None), (np.array([0, 3, 5, 8, 13, 21, 29, 33]), 3), (np.arange(0), None)):
        got = pass1._gather_rows(flat, offs, lengths, sel, cols, clip=clip)
        want = _gather_reference(flat, offs, lengths, sel, cols, clip)
        assert np.array_equal(got[0], want[0]) and np.array_equal(got[1], want[1])
    empty = np.zeros(0, dtype=np.int64)
    assert pass1._gather_rows(flat, offs, lengths, empty, np.arange(5))[0].shape == (0, 5)
    # a batch of empty reads has an empty flat buffer
    lens0 = np.zeros(3, dtype=np.int64)
    raw, valid = pass1._gather_rows(np.zeros(0, dtype=np.uint8), np.zeros(4, dtype=np.int64), lens0, np.arange(3),
                                    np.arange(4))
    assert raw.shape == (3, 4) and not raw.any() and not valid.any()


def _orientation_reference(codes, lengths):
    """Every shift for every read (the V12 scan before the early exit)."""
    lay = pass1._P["lay"]
    n = lengths.size
    tpl, _ = lay.template()
    mpos = np.flatnonzero(tpl >= 0)
    offs = np.concatenate([[0], np.cumsum(lengths)])[:-1]
    want = tpl[mpos].astype(np.uint8)
    starts = np.flatnonzero(np.diff(mpos, prepend=-2) > 1)
    top = max(0, codes.size - 1)
    sf = np.zeros((n, starts.size), dtype=np.int64)
    sr = np.zeros((n, starts.size), dtype=np.int64)
    for s in range(-pass1._ORIENT_SHIFT, pass1._ORIENT_SHIFT + 1):
        p = mpos[None, :] + s
        inside = (p >= 0) & (p < lengths[:, None])
        fidx = np.clip(offs[:, None] + p, 0, top)
        hf = (codes[fidx] == want[None, :]) & inside
        ridx = np.clip(offs[:, None] + lengths[:, None] - 1 - p, 0, top)
        hr = (pass1._RC[codes[ridx]] == want[None, :]) & inside
        np.maximum(sf, np.add.reduceat(hf, starts, axis=1, dtype=np.int64), out=sf)
        np.maximum(sr, np.add.reduceat(hr, starts, axis=1, dtype=np.int64), out=sr)
    return sr.sum(axis=1) > sf.sum(axis=1) + max(2, mpos.size // 8)


def _strand_reads(rng, lay, n):
    """Template strands with random frame bases, then random substitutions, indels, truncation and reverse complement:
    a mix the pre-pass must sort out, plus some random junk."""
    tpl, _ = lay.template()
    reads = []
    for _ in range(n):
        r = np.where(tpl >= 0, tpl, rng.integers(0, 4, size=tpl.size)).astype(np.uint8)
        kind = rng.integers(0, 6)
        if kind >= 1:                                               # substitutions
            k = rng.integers(0, 8)
            pos = rng.integers(0, r.size, size=k)
            r[pos] = rng.integers(0, 5, size=k)
        if kind >= 2:                                               # one to three indels
            for _k in range(rng.integers(1, 4)):
                pos = rng.integers(0, r.size)
                r = np.delete(r, pos) if rng.random() < 0.5 else np.insert(r, pos, rng.integers(0, 4))
        if kind == 4:                                               # truncated
            r = r[: rng.integers(5, r.size)]
        if kind == 5:                                               # junk
            r = rng.integers(0, 5, size=rng.integers(0, 2 * tpl.size)).astype(np.uint8)
        if rng.random() < 0.4:
            r = pass1._RC[r[::-1]]
        reads.append(r)
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    return np.concatenate(reads) if n else np.zeros(0, dtype=np.uint8), lengths


@pytest.mark.parametrize("seed", [11, 12, 13, 14])
def test_orientation_early_exit_decides_like_the_full_scan(seed):
    lay = PROFILES["v4-balanced"][0]
    opt = DecodeOptions()
    pass1._p_init(lay, opt.band, opt.sync_costs, 0, True)
    rng = np.random.default_rng(seed)
    codes, lengths = _strand_reads(rng, lay, 400)
    got = pass1._orientation(codes, lengths)
    want = _orientation_reference(codes, lengths)
    assert np.array_equal(got, want)
    assert 0 < want.sum() < want.size, "the batch must contain flipped and unflipped reads"
    assert pass1._orientation(np.zeros(0, dtype=np.uint8), np.zeros(0, dtype=np.int64)).size == 0
