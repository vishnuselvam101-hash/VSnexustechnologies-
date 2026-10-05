"""V6 outer code, unit level: geometry, product-code structure, iterative decoding, analytic bounds, planner."""
from __future__ import annotations

import math

import numpy as np
import pytest

from vnxdna.v6 import outer as ou
from vnxdna.v6.errors import V6ConfigurationError
from vnxdna.v6.loss import LossConfig, loss_mask


def geo(K=8, M=3, D=4, Mc=2, P=5, size=None, order="sequential", groups=10.5):
    size = size or int(groups * K * P)
    return ou.Geometry(K, M, D, Mc, P, size, order).validate()


# ---------------------------------------------------------------------------------------------------------------- geometry
@pytest.mark.parametrize("order", ou.ORDERS)
@pytest.mark.parametrize("shape", [(8, 3, 4, 2, 10.5), (8, 3, 4, 0, 3.2), (6, 2, 3, 1, 7.0), (5, 0, 2, 1, 1.0),
                                   (4, 4, 16, 3, 2.9)])
def test_order_covers_every_strand_once(order, shape):
    K, M, D, Mc, groups = shape
    g = geo(K, M, D, Mc, groups=groups, order=order)
    keys = g.order_keys()
    assert len(keys) == g.strands()
    expected = {(r, t) for r in range(g.total_groups) for t in range(g.symbols_of(r))}
    assert {tuple(k) for k in keys.tolist()} == expected
    assert sum(len(g.stripe_order(s)) for s in range(g.stripes)) == g.strands()


def _written_file(g, n_super):
    """Record keys of the strand file in the order the V6 encoder writes them (superblock strands at their slots)."""
    keys = [tuple(k) for k in g.order_keys().tolist()]
    slots, out, i = g.superblock_slots(n_super, len(keys)), [], 0
    for j, k in enumerate(keys):
        while i < n_super and slots[i] <= j:
            out.append(("sb", i))
            i += 1
        out.append(k)
    out += [("sb", x) for x in range(i, n_super)]
    return {k: n for n, k in enumerate(out)}


@pytest.mark.parametrize("order", ou.ORDERS)
@pytest.mark.parametrize("shape", [(8, 3, 4, 2, 10.5), (8, 3, 4, 0, 3.2), (6, 2, 3, 1, 7.0), (5, 0, 2, 1, 1.0),
                                   (4, 4, 16, 3, 2.9), (3, 2, 5, 0, 11.4), (1, 1, 1, 1, 3.0)])
@pytest.mark.parametrize("n_super", [0, 1, 7])
def test_closed_form_record_index_matches_written_order(order, shape, n_super):
    """Geometry.data_index/file_index/superblock_index (used by locate) equal the encoder's strand order (job #62)."""
    K, M, D, Mc, groups = shape
    g = geo(K, M, D, Mc, groups=groups, order=order)
    where = _written_file(g, n_super)
    for r in range(g.total_groups):
        t = np.arange(g.symbols_of(r))
        assert g.file_index(g.data_index(r, t), n_super).tolist() == [where[(r, x)] for x in t.tolist()]
    assert g.superblock_index(n_super).tolist() == [where[("sb", i)] for i in range(n_super)]


def test_balanced_stripes_and_inverse():
    g = geo(K=4, M=1, D=7, Mc=1, groups=30.3)          # 31 groups → 5 stripes of 6 or 7 rows
    sizes = [len(g.stripe_rows(s)[0]) for s in range(g.stripes)]
    assert g.stripes == 5 and sum(sizes) == g.G and max(sizes) - min(sizes) <= 1 and max(sizes) <= g.D
    for s in range(g.stripes):
        data, par = g.stripe_rows(s)
        assert all(g.stripe_of(x) == s for x in data + par)
        assert len(par) == g.Mc


def test_positions_roundtrip_and_short_last_group():
    g = geo(K=8, M=3, D=4, Mc=1, groups=2.25)           # last group: 2 source symbols
    last = g.G - 1
    assert g.k_of(last) == 2 and g.k_of(g.G) == g.K
    for r in (0, last, g.G):
        for t in range(g.symbols_of(r)):
            assert g.transmitted(r, g.position(r, t)) == t
    assert [g.transmitted(last, p) for p in range(2, 8)] == [None] * 6
    assert g.G == 3 and len(g.stripe_rows(0)[0]) == 3
    pad = ou.stripe_padding(g, 0)
    assert pad[3].all()                                  # absent data row 3 of a 3-row stripe (D = 4) is padding
    assert pad[2, 2:8].all() and not pad[2, :2].any() and not pad[2, 8:].any()     # short last group
    assert not pad[[0, 1, 4]].any()


@pytest.mark.parametrize("bad", [dict(K=0), dict(K=200, M=57), dict(D=0), dict(D=250, Mc=7), dict(order="random")])
def test_invalid_geometry_rejected(bad):
    args = dict(K=8, M=3, D=4, Mc=2, P=5, container_size=100, order="sequential")
    args.update(bad)
    with pytest.raises(V6ConfigurationError):
        ou.Geometry(**args).validate()


def test_overhead_matches_counts():
    g = geo(K=64, M=16, D=32, Mc=2, P=40, size=1 << 20)
    assert g.G == 410 and g.stripes == 13 and g.parity_groups == 26
    assert g.strands() == g.data_strands() + 410 * 16 + 26 * 80
    assert math.isclose(g.overhead(), g.strands() / g.data_strands() - 1)


# ---------------------------------------------------------------------------------------------------------------- product structure
def _stripe(g, s, rng):
    data_g, _ = g.stripe_rows(s)
    blocks = np.zeros((len(data_g), g.K, g.P), dtype=np.uint8)
    for r, grp in enumerate(data_g):
        blocks[r, : g.k_of(grp)] = rng.integers(0, 256, (g.k_of(grp), g.P))
    cw = ou.row_codewords(blocks, g.K, g.M)
    full = np.zeros((g.D + g.Mc, g.n, g.P), dtype=np.uint8)
    full[: len(data_g)] = cw
    if g.Mc:
        full[g.D:] = ou.row_codewords(ou.column_parity_rows(blocks, g.D, g.Mc), g.K, g.M)
    return full


def test_column_parity_rows_are_row_codewords_and_columns_are_codewords():
    g = geo(K=8, M=3, D=4, Mc=2, groups=3.5)
    rng = np.random.default_rng(1)
    full = _stripe(g, 0, rng)
    # every row (incl. column-parity rows) is a row codeword
    assert np.array_equal(ou.row_codewords(full[:, : g.K], g.K, g.M), full)
    # every position (incl. row-parity positions) is a column codeword
    cols = full.transpose(1, 0, 2)                                   # (n, D + Mc, P)
    par = ou.cauchy_parity(ou.code(g.D, g.Mc), np.ascontiguousarray(cols[:, : g.D]))
    assert np.array_equal(par, cols[:, g.D:])


@pytest.mark.parametrize("seed", range(40))
def test_iterative_decoder_matches_structural_prediction(seed):
    rng = np.random.default_rng(seed)
    K, M, D, Mc = int(rng.integers(3, 9)), int(rng.integers(1, 4)), int(rng.integers(2, 6)), int(rng.integers(1, 3))
    g = ou.Geometry(K, M, D, Mc, 4, int(D * K * 4 - rng.integers(0, 4 * K)), "sequential").validate()
    full = _stripe(g, 0, rng)
    pad = ou.stripe_padding(g, 0)
    p = rng.uniform(0.05, 0.6)
    known = (rng.random(pad.shape) >= p) | pad
    garbage = full.copy()
    garbage[~known] = rng.integers(0, 256, (int((~known).sum()), g.P))      # unknown cells must be ignored
    out_cw, out_known = ou.decode_stripe(garbage, known, K, M, D, Mc)
    pred = ou.structural_decode(known[None], K, M, D, Mc)[0]
    assert np.array_equal(out_known, pred)
    assert np.array_equal(out_cw[out_known], full[out_known])          # whatever becomes known is correct


def test_columns_recover_whole_lost_rows_rows_alone_cannot():
    g = geo(K=6, M=2, D=5, Mc=2, groups=5)
    full = _stripe(g, 0, np.random.default_rng(3))
    known = np.ones((g.D + g.Mc, g.n), dtype=bool)
    known[[1, 3]] = False                                            # two whole rows lost
    cw, kn = ou.decode_stripe(np.where(known[..., None], full, 0), known, g.K, g.M, g.D, g.Mc)
    assert kn.all() and np.array_equal(cw, full)
    known[4] = False                                                 # a third whole row: beyond Mc = 2
    _, kn = ou.decode_stripe(np.where(known[..., None], full, 0), known, g.K, g.M, g.D, g.Mc)
    assert not kn[:g.D].all()


def test_iteration_beats_single_pass():
    """A pattern that neither rows nor columns decode alone but alternating does."""
    K, M, D, Mc = 4, 1, 4, 1
    g = ou.Geometry(K, M, D, Mc, 3, D * K * 3).validate()
    full = _stripe(g, 0, np.random.default_rng(5))
    known = np.ones((5, 5), dtype=bool)
    known[0, [0, 1]] = False      # row 0 loses 2 > M
    known[1, [0, 2]] = False      # row 1 loses 2 > M; column 0 loses 2 > Mc
    known[2, [3]] = False
    st = ou.StripeStats()
    cw, kn = ou.decode_stripe(np.where(known[..., None], full, 0), known, K, M, D, Mc, st)
    assert kn.all() and np.array_equal(cw, full) and st.iterations >= 2


# ---------------------------------------------------------------------------------------------------------------- bounds and planner
def test_failure_bound_row_only_is_exact_binomial():
    g = geo(K=20, M=5, D=8, Mc=0, P=4, size=20 * 4 * 8)
    p = 0.1
    q = sum(math.comb(25, i) * p ** i * (1 - p) ** (25 - i) for i in range(6))
    assert math.isclose(ou.failure_bound(g, p), 1 - q ** 8, rel_tol=1e-9)


def test_failure_bound_monotone_and_column_code_helps():
    a = geo(K=40, M=10, D=16, Mc=0, P=4, size=40 * 4 * 64)
    b = geo(K=40, M=10, D=16, Mc=2, P=4, size=40 * 4 * 64)
    ps = [0.05, 0.1, 0.15, 0.2, 0.25]
    fa = [ou.failure_bound(a, p) for p in ps]
    assert fa == sorted(fa)
    assert all(ou.failure_bound(b, p) <= ou.failure_bound(a, p) + 1e-15 for p in ps)
    assert ou.dropout_threshold(b) > ou.dropout_threshold(a)


def _brute_burst_ok(g, L):
    order = g.order_keys()
    for st in range(0, len(order) - L + 1):
        lost = order[st:st + L]
        for s in {g.stripe_of(int(x)) for x in lost[:, 0]}:
            data, par = g.stripe_rows(s)
            idx = {r: i for i, r in enumerate(data)}
            idx.update({r: g.D + i for i, r in enumerate(par)})
            known = np.ones((g.D + g.Mc, g.n), dtype=bool)
            for grp, t in lost.tolist():
                if g.stripe_of(grp) == s:
                    known[idx[grp], g.position(grp, t)] = False
            known |= ou.stripe_padding(g, s)
            if not ou.structural_decode(known[None], g.K, g.M, g.D, g.Mc)[0][: g.D].all():
                return False
    return True


@pytest.mark.parametrize("order", ou.ORDERS)
@pytest.mark.parametrize("shape", [(6, 2, 3, 1, 7.4), (5, 2, 4, 0, 9.0), (4, 1, 3, 2, 6.6)])
def test_burst_tolerance_matches_brute_force(order, shape):
    K, M, D, Mc, groups = shape
    g = geo(K, M, D, Mc, P=2, groups=groups, order=order)
    tol = ou.burst_tolerance(g)
    assert _brute_burst_ok(g, tol)
    assert not _brute_burst_ok(g, tol + 1)


def test_interleaving_raises_burst_tolerance():
    seq = geo(K=64, M=16, D=64, Mc=0, P=40, size=200_000)
    inter = geo(K=64, M=16, D=64, Mc=0, P=40, size=200_000, order="interleaved")
    assert ou.burst_tolerance(seq) == 16
    # 79 groups → two balanced stripes of 40 and 39 rows; position-major order spreads a burst over every row of a
    # stripe, each row tolerating M = 16 losses
    assert inter.stripes == 2 and min(len(inter.stripe_rows(s)[0]) for s in range(2)) == 39
    assert ou.burst_tolerance(inter) >= 16 * 39 - 16


def test_planner_respects_budget_and_is_deterministic():
    a = ou.plan(1 << 20, 40, 0.25)
    b = ou.plan(1 << 20, 40, 0.25)
    assert a == b
    assert a.geometry.overhead() <= 0.25 + 1e-12
    v5 = geo(K=64, M=16, D=64, Mc=0, P=40, size=1 << 20)
    assert a.threshold > ou.dropout_threshold(v5)
    with pytest.raises(V6ConfigurationError):
        ou.plan(1 << 20, 40, 0.0)


# ---------------------------------------------------------------------------------------------------------------- loss model
def test_loss_mask_deterministic_and_bursts_contiguous():
    a = loss_mask(10_000, LossConfig(dropout=0.1, seed=4))
    assert np.array_equal(a, loss_mask(10_000, LossConfig(dropout=0.1, seed=4)))
    assert 0.08 < 1 - a.mean() < 0.12
    b = loss_mask(1000, LossConfig(burst_count=1, burst_length=50, seed=9))
    lost = np.flatnonzero(~b)
    assert len(lost) == 50 and lost[-1] - lost[0] == 49
    with pytest.raises(V6ConfigurationError):
        loss_mask(10, LossConfig(dropout=1.0))
