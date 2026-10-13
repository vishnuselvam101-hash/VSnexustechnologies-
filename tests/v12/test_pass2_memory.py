"""V12 session 5 memory work: the lower-memory forms of pass-1 gathers, duplicate resolution and the bucket
consensus packing give exactly the values of the forms they replace (SIMULATED inputs; no channel claims)."""
from __future__ import annotations

from collections import Counter

import numpy as np
import pytest

from vnxdna.native.heap import trim_heap
from vnxdna.recovery import pass1
from vnxdna.recovery.consensus import resolve_duplicates


def _resolve_duplicates_v11(acc: np.ndarray) -> tuple[dict, int]:
    """The v11.0.0 form (key matrix, first-record gather), kept here as the reference."""
    out: dict = {}
    conflicts = 0
    if not acc.size:
        return out, 0
    order = np.lexsort((acc["symbol"], acc["group"], acc["tag"], acc["kind"]))
    acc = acc[order]
    keys = np.stack([acc["kind"].astype(np.int64), acc["tag"].astype(np.int64), acc["group"].astype(np.int64),
                     acc["symbol"].astype(np.int64)], axis=1)
    change = np.flatnonzero((np.diff(keys, axis=0) != 0).any(axis=1)) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(acc)]])
    pay = acc["payload"]
    first = np.repeat(starts, ends - starts)
    same = (pay == pay[first]).all(axis=1)
    uniform = np.logical_and.reduceat(same, starts)
    klist = keys[starts].tolist()
    for gi in np.flatnonzero(uniform).tolist():
        out[tuple(klist[gi])] = pay[starts[gi]]
    for gi in np.flatnonzero(~uniform).tolist():
        s, e = int(starts[gi]), int(ends[gi])
        key = tuple(klist[gi])
        c = Counter(acc["payload"][i].tobytes() for i in range(s, e))
        (top, n1), *rest = c.most_common(2) + [(None, 0)]
        n2 = rest[0][1] if rest else 0
        if len(c) > 1:
            conflicts += 1
        if n1 > n2:
            out[key] = np.frombuffer(top, dtype=np.uint8)
    return out, conflicts


def _acc(rng, n, payload=6, groups=9, symbols=5, conflict_rate=0.2):
    dt = np.dtype([("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("payload", "u1", (payload,))])
    acc = np.zeros(n, dtype=dt)
    acc["kind"] = rng.integers(0, 2, n)
    acc["tag"] = rng.integers(0, 2, n)
    acc["group"] = rng.integers(0, groups, n)
    acc["symbol"] = rng.integers(0, symbols, n)
    # the address decides the payload, so duplicates agree; a fraction carries a different copy (conflicts and ties)
    base = rng.integers(0, 256, size=(2, 2, groups, symbols, payload), dtype=np.uint8)
    acc["payload"] = base[acc["kind"], acc["tag"], acc["group"], acc["symbol"]]
    bad = rng.random(n) < conflict_rate
    acc["payload"][bad] = rng.integers(0, 3, size=(int(bad.sum()), payload), dtype=np.uint8)
    return acc


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("n", [0, 1, 2, 7, 300, 2000])
def test_resolve_duplicates_equals_the_v11_form(seed, n):
    acc = _acc(np.random.default_rng(seed), n)
    got, c_got = resolve_duplicates(acc)
    want, c_want = _resolve_duplicates_v11(acc)
    assert c_got == c_want
    assert list(got.keys()) == list(want.keys())            # same addresses, same (sorted) order
    for k in want:
        assert got[k].dtype == np.uint8 and np.array_equal(got[k], want[k])


def test_resolve_duplicates_crosses_the_comparison_chunk_boundary():
    # one address run spanning the 2^14-row chunk edge, with its only disagreeing record right after the edge
    rng = np.random.default_rng(7)
    n = (1 << 14) + 40
    acc = _acc(rng, n, groups=1, symbols=1, conflict_rate=0.0)
    acc["kind"], acc["tag"] = 1, 1
    assert len(resolve_duplicates(acc)[0]) == 1
    acc["payload"][(1 << 14) + 1] ^= 1
    got, conflicts = resolve_duplicates(acc)
    want, c_want = _resolve_duplicates_v11(acc)
    assert conflicts == c_want == 1 and list(got) == list(want)
    assert np.array_equal(got[(1, 1, 0, 0)], want[(1, 1, 0, 0)])


@pytest.mark.parametrize("n", [0, 1, 511, 512, 513, 1500, 8192])
def test_gather_exact_equals_stacked_views(n):
    rng = np.random.default_rng(n)
    width = 23
    lengths = np.full(n, width, dtype=np.int64)
    lengths[rng.random(n) < 0.3] += 2                        # some reads are not exact-length: they are skipped
    offs = np.concatenate([[0], np.cumsum(lengths)])
    flat = rng.integers(0, 5, size=int(offs[-1])).astype(np.uint8)
    exact = np.flatnonzero(lengths == width)
    got = pass1._gather_exact(flat, offs[exact], width)
    assert got.shape == (exact.size, width) and got.dtype == np.uint8
    if exact.size:
        assert np.array_equal(got, np.stack([flat[offs[i]:offs[i] + width] for i in exact.tolist()]))


@pytest.mark.parametrize("n", [0, 1, 512, 1300])
def test_gather_rows_chunks_equal_the_loop(n):
    rng = np.random.default_rng(100 + n)
    lengths = rng.integers(0, 31, size=n).astype(np.int64)
    offs = np.concatenate([[0], np.cumsum(lengths)])
    flat = rng.integers(0, 5, size=int(offs[-1])).astype(np.uint8)
    sel = rng.permutation(n)
    cols = np.array([0, 2, 3, 7, 11, 19, 29])
    got, valid = pass1._gather_rows(flat, offs, lengths, sel, cols, clip=3)
    want = np.zeros((n, cols.size), dtype=np.uint8)
    wvalid = np.zeros((n, cols.size), dtype=bool)
    for j, i in enumerate(sel.tolist()):
        r = flat[offs[i]:offs[i + 1]]
        for c, pos in enumerate(cols.tolist()):
            if pos < r.size:
                want[j, c] = min(int(r[pos]), 3)
                wvalid[j, c] = True
    assert np.array_equal(got, want) and np.array_equal(valid, wvalid)


def test_trim_heap_is_safe_to_call():
    r = trim_heap()
    assert isinstance(r, bool)
    assert isinstance(trim_heap(), bool)        # idempotent; a no-op where malloc_trim does not exist
