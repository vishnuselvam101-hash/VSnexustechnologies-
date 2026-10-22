"""J1 (hardening O-2): the native loaders must refuse an out-of-range argument, not narrow it.

ctypes narrows a Python int to the width of the argument type without a word: a ``c_int32`` argument takes
``2 ** 32 + 4`` as ``4`` and a ``c_int64`` argument takes ``2 ** 64 + 1`` as ``1``, so the kernel answered for a
different value than the caller asked for. ``np.ascontiguousarray(a, dtype=narrower)`` wraps the values that do not
fit the dtype the same way (a code of 256 becomes 0, a mismatch cost of 40000 becomes -25536).

The loaders check the arguments before the call now, in the style of ``native.engine._checked_arrays`` (which refuses
an array the engine cannot read): an integer outside the range of the ctypes argument and an array whose values do
not fit the dtype the kernel reads are refused with a ``ValueError`` (the cluster raises its own
``NativeClusterArgumentError``, which is a ``NativeClusterError`` and a ``ValueError``) instead of being narrowed.
The NumPy reference refuses the same values (NumPy raises ``OverflowError`` for a Python integer that does not fit the
dtype it computes in), so the two paths agree on the refusal and no in-range value changes.

Every function the loaders touch is called here with a value outside the range of its argument: the native call must
raise, and the reference call must refuse the same value where the reference can be given one (the outer-list
reference works on unbounded Python integers and accepts them, so there the native path is deliberately stricter).

SYNTHETIC data.
"""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.codec import gf256
from vnxdna.codec.cauchy import CauchyErasureCode
from vnxdna.codec.inner_parity import parity_matrix
from vnxdna.dnaenc import frame4
from vnxdna.dnaenc.constraints import ConstraintConfig
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.dnaenc.scrambler import keystreams
from vnxdna.native import cluster as nc
from vnxdna.native import outerlist as nol
from vnxdna.native import orient as no
from vnxdna.native import strand as ns
from vnxdna.native._loader import INT32_MAX, INT32_MIN, INT64_MAX, INT64_MIN, checked_array, checked_int
from vnxdna.recovery import outer_list as ol
from vnxdna.recovery import pass1
from vnxdna.recovery.cluster import consensus, polish, sketch

#: a value an int32 argument narrows silently (to 4)
OVER32 = 2 ** 32 + 4
#: a value an int64 argument narrows silently (to 1)
OVER64 = 2 ** 64 + 1
#: the value below the int32 range (the reference refuses it as a negative dimension)
UNDER32 = INT32_MIN - 1

BALANCED = PROFILES["v4-balanced"][0]


@pytest.fixture(scope="module", autouse=True)
def _libraries():
    """The four native kernels: in-place build if the library is not there (skip without a compiler)."""
    for module in (nc, ns, nol, no):
        module._reset_for_tests()
        if not module.available():
            try:
                module.build()
            except Exception as error:                      # noqa: BLE001 - the build error is the skip reason
                pytest.skip(f"native {module.__name__} kernel unavailable and cannot be built here: {error}")
            module._reset_for_tests()
        assert module.available(), module.status()


# ============================================================== the checkers themselves
def test_checked_int_refuses_a_value_outside_the_range():
    """The int32 range: a value outside it is refused, not narrowed."""
    assert checked_int("k", 5, INT32_MIN, INT32_MAX) == 5
    with pytest.raises(ValueError):
        checked_int("k", OVER32, INT32_MIN, INT32_MAX)
    with pytest.raises(ValueError):
        checked_int("k", UNDER32, INT32_MIN, INT32_MAX)


def test_checked_int_refuses_a_value_outside_the_int64_range():
    assert checked_int("B", 5, INT64_MIN, INT64_MAX) == 5
    with pytest.raises(ValueError):
        checked_int("B", OVER64, INT64_MIN, INT64_MAX)


def test_checked_array_refuses_values_that_do_not_fit_the_dtype():
    """A uint8 cast wraps the values that do not fit (300 becomes 44): they are refused instead."""
    assert checked_array("raw", np.array([[3]], np.int16), np.uint8).tolist() == [[3]]
    with pytest.raises(ValueError):
        checked_array("raw", np.array([[300]], np.int16), np.uint8)
    with pytest.raises(ValueError):
        checked_array("mc", np.array([[40000]], np.int32), np.int16)


# ============================================================== cluster
def _reads(n: int = 4, width: int = 8, seed: int = 7):
    """A few synthetic reads (codes 0..3) and their lengths."""
    rng = np.random.default_rng(seed)
    raw = rng.integers(0, 4, (n, width)).astype(np.uint8)
    return raw, np.full(n, width, dtype=np.int64)


def _fb_case():
    """One read that matches its template exactly: (tpl, mc, reads, band)."""
    tpl = np.array([[0, 1]], dtype=np.int16)
    mc = np.array([[3, 3]], dtype=np.int32)
    reads = [np.array([0, 1], dtype=np.uint8)]
    band = np.array([0], dtype=np.int64)
    return tpl, mc, reads, band


def test_pack_refuses_codes_that_do_not_fit_the_uint8_buffer():
    """A code of 300 would be narrowed to 44 in the packed buffer, so the kernel would read another read."""
    with pytest.raises(ValueError):
        nc.pack([np.array([0, 300], dtype=np.int16)])


def test_sketch_refuses_an_s_that_does_not_fit_the_int32_argument():
    """``2 ** 32 + 4`` would reach the int32 argument as ``4``: the sketch of a different s."""
    raw, lengths = _reads()
    with pytest.raises(ValueError):
        nc.sketch(raw, lengths, 3, OVER32)


def test_sketch_refuses_the_same_out_of_range_s_as_the_reference():
    """Below the int32 range the reference refuses the value too (a negative dimension)."""
    raw, lengths = _reads()
    with pytest.raises(ValueError):
        nc.sketch(raw, lengths, 3, UNDER32)
    with pytest.raises((ValueError, OverflowError)):
        sketch.sketch_reads_reference(raw, lengths, 3, UNDER32)


def test_sketch_refuses_codes_that_do_not_fit_the_uint8_kernel_argument():
    """A code of 300 would be narrowed to 44 and the kernel would sketch a different read."""
    raw, lengths = _reads()
    with pytest.raises(ValueError):
        nc.sketch(raw.astype(np.int16) * 100, lengths, 3, 8)


def test_fb_refuses_a_band_that_does_not_fit_the_int64_argument():
    """The band is an int64 argument: ``2 ** 64 + 1`` would reach the kernel as ``1``."""
    tpl, mc, reads, band = _fb_case()
    buf, off, lens = nc.pack(reads)
    with pytest.raises(ValueError):
        nc.fb(tpl, mc, buf, off, lens, band, OVER64, 6, 0)


def test_fb_refuses_the_same_c_indel_as_the_reference():
    """The reference computes with the value, so it refuses the one that does not fit its dtype."""
    tpl, mc, reads, band = _fb_case()
    buf, off, lens = nc.pack(reads)
    with pytest.raises(ValueError):
        nc.fb(tpl, mc, buf, off, lens, band, 0, OVER32, 0)
    with pytest.raises((ValueError, OverflowError)):
        consensus.fb_calls_reference(tpl, mc, reads, band, OVER32, 0)


def test_edit_costs_refuses_a_band_that_does_not_fit_the_int64_argument():
    tpl, mc, reads, band = _fb_case()
    buf, off, lens = nc.pack(reads)
    with pytest.raises(ValueError):
        nc.edit_costs(tpl, mc, buf, off, lens, band, OVER64, 6, 4)


def test_edit_costs_refuses_the_same_c_sub_as_the_reference():
    tpl, mc, reads, band = _fb_case()
    buf, off, lens = nc.pack(reads)
    with pytest.raises(ValueError):
        nc.edit_costs(tpl, mc, buf, off, lens, band, 0, 6, OVER64)
    with pytest.raises((ValueError, OverflowError)):
        polish.edit_costs_reference(tpl, mc, reads, band, 6, OVER64)


def _trial_case():
    """One candidate, two reads and one trial: the arguments of ``nc.trial_costs``."""
    T = 3
    base = np.zeros((1, T), np.int16)
    mc = np.ones(T, np.int32)
    buf = np.zeros(6, np.uint8)
    off = np.array([0, 3], np.int64)
    lens = np.array([3, 3], np.int64)
    band = np.array([1, 2], np.int64)
    read_start = np.array([0, 2], np.int64)
    base_dip = np.zeros(2, np.uint8)
    trial_tpl = np.ones((1, T), np.int16)
    trial_start = np.array([0, 1], np.int64)
    trial_row = np.array([0], np.int64)
    trial_dip = np.zeros(2, np.uint8)
    return (base, mc, buf, off, lens, band, read_start, base_dip, trial_tpl, trial_start, trial_row, trial_dip)


def test_trial_costs_refuses_a_pen_that_does_not_fit_the_int64_argument():
    """The penalty is an int64 argument: ``2 ** 64 + 1`` would reach the kernel as ``1``."""
    (base, mc, buf, off, lens, band, read_start, base_dip, trial_tpl, trial_start, trial_row,
     trial_dip) = _trial_case()
    with pytest.raises(ValueError):
        nc.trial_costs(base, mc, buf, off, lens, band, read_start, base_dip, 1, OVER64, trial_tpl, trial_start,
                       trial_row, trial_dip)


def test_trial_costs_refuses_the_same_pen_as_the_reference():
    """The reference clips the cost with the penalty, so it refuses the value that does not fit."""
    (base, mc, buf, off, lens, band, read_start, base_dip, trial_tpl, trial_start, trial_row,
     trial_dip) = _trial_case()
    with pytest.raises(ValueError):
        nc.trial_costs(base, mc, buf, off, lens, band, read_start, base_dip, 1, OVER64, trial_tpl, trial_start,
                       trial_row, trial_dip)
    reads = [np.zeros(3, np.uint8), np.zeros(3, np.uint8)]
    with pytest.raises((ValueError, OverflowError)):
        consensus.trial_costs_reference([np.zeros(3, np.int64)], np.ones(3, np.int64), reads, np.array([0, 0]),
                                         np.array([1, 2], np.int64), 1, OVER64, [np.ones(3, np.int64)], np.array([0]))


# ============================================================== strand
def _strand_case():
    """The arguments of ``ns.build_rows`` for the balanced layout."""
    lay = BALANCED
    rng = np.random.default_rng(11)
    n, payload = 8, lay.payload_bytes
    groups = rng.integers(0, 1 << 20, n)
    syms = rng.integers(0, 1 << 16, n)
    pays = rng.integers(0, 256, (n, payload), dtype=np.uint8)
    plain = frame4.plain_rows(3, 0, groups, syms, pays)
    ks = keystreams(plain.shape[1])
    tpl, _ = lay.template()
    rules = dict(gc_min=40, gc_max=60, win=0, wmin=25, wmax=75, hmax=3, tandem=0)
    return lay, groups, syms, pays, (plain, ks, parity_matrix(1 + plain.shape[1], lay.inner_parity), gf256.MUL,
                                     tpl, rules, [])


def test_build_rows_refuses_a_rule_that_does_not_fit_the_int64_argument():
    """The constraint rules are int64 arguments: ``2 ** 64 + 1`` would reach the kernel as ``1``."""
    _, _, _, _, args = _strand_case()
    args = list(args)
    args[5] = dict(args[5], gc_min=OVER64)
    with pytest.raises(ValueError):
        ns.build_rows(*args, nthreads=1)


def test_build_rows_refuses_the_same_rule_as_the_reference():
    """The reference screens the GC rule with the value, so it refuses the one that does not fit."""
    lay, groups, syms, pays, _ = _strand_case()
    cfg = ConstraintConfig(gc_min_percent=OVER64)
    with pytest.raises((ValueError, OverflowError)):
        frame4.build_strands(lay, cfg, 3, 0, groups, syms, pays)


# ============================================================== outer list
def _codeword(rng, K, M, P):
    data = rng.integers(0, 256, (1, K, P), dtype=np.uint8)
    code = CauchyErasureCode(K, M)
    return np.concatenate([data[0], code.encode(data)[0]]), np.asarray(code._parity_matrix, dtype=np.uint8)


def _row_case(seed=3, K=4, M=2, P=4, f=2, n_cands=3):
    """One short row with known and unknown symbols, plus a few candidate rows."""
    rng = np.random.default_rng(seed)
    cw, parity = _codeword(rng, K, M, P)
    n = K + M
    unknown = rng.choice(n, size=M + f, replace=False)
    known = np.ones(n, bool)
    known[unknown] = False
    sym = np.where(known[:, None], cw, 0).astype(np.uint8)
    pos = rng.choice(unknown, size=n_cands, replace=False)
    cpos, cval, cok = [], [], []
    for p in pos:
        v = cw[p].copy()
        bad = rng.random(P) < 0.2
        v[bad] = rng.integers(0, 256, int(bad.sum()))
        cpos.append(p)
        cval.append(v)
        cok.append(rng.random(P) > 0.05)
    return cw, parity, known, sym, np.array(cpos, np.int32), np.stack(cval), np.stack(cok).astype(np.uint8)


def test_assign_refuses_a_max_dist_that_does_not_fit_the_int64_argument():
    """The reference works on unbounded Python integers and accepts the value; the native path refuses it."""
    rng = np.random.default_rng(100)
    ks = keystreams(9 + 20 + 4)
    targets = rng.integers(0, 256, (30, 9), dtype=np.uint8)
    frames = rng.integers(0, 256, (10, 40), dtype=np.uint8)
    variants = np.arange(8, dtype=np.int16)
    with pytest.raises(ValueError):
        nol.assign(frames, ks, variants, targets, OVER64, 0)


def test_assign_refuses_variants_that_do_not_fit_the_int16_kernel_argument():
    """A variant of 40000 would be narrowed to -25536 and the kernel would look for another variant; the reference
    refuses the value too (it is out of range of the keystreams)."""
    rng = np.random.default_rng(101)
    ks = keystreams(9 + 20 + 4)
    targets = rng.integers(0, 256, (30, 9), dtype=np.uint8)
    frames = rng.integers(0, 256, (10, 40), dtype=np.uint8)
    variants = np.array([40000], dtype=np.int32)
    with pytest.raises(ValueError):
        nol.assign(frames, ks, variants, targets, 3, 0)
    with pytest.raises((ValueError, OverflowError, IndexError)):
        ol.assign_reference(frames, ks, variants, targets, 3, 0)


def test_decode_row_refuses_a_max_subsets_that_does_not_fit_the_int64_argument():
    """The reference accepts the value (unbounded Python integers); the native path refuses it."""
    _, parity, known, sym, cpos, cval, cok = _row_case()
    with pytest.raises(ValueError):
        nol.decode_row(parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, cpos, cval, cok, 1, 1, OVER64)


def test_ransac_refuses_a_max_tuples_that_does_not_fit_the_int64_argument():
    """The reference accepts the value (unbounded Python integers); the native path refuses it."""
    _, parity, known, sym, cpos, cval, _ = _row_case()
    with pytest.raises(ValueError):
        nol.ransac(parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, cpos, cval, 8, OVER64)


# ============================================================== orient
def _template(lay):
    """The template arguments of the orientation kernels: (tpl, frame_pos, mpos, want, starts)."""
    tpl, fpos = lay.template()
    mpos = np.flatnonzero(tpl >= 0)
    want = tpl[mpos].astype(np.uint8)
    starts = np.flatnonzero(np.diff(mpos, prepend=-2) > 1)
    return tpl, fpos, mpos, want, starts


def _orient_batch(lay, seed=7, n=4):
    """A few reads of the template: (codes, lengths, offs)."""
    rng = np.random.default_rng(seed)
    tpl, _ = lay.template()
    reads = []
    for _ in range(n):
        r = rng.integers(0, 4, tpl.size).astype(np.uint8)
        r[tpl >= 0] = tpl[tpl >= 0]
        reads.append(r)
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    offs = np.concatenate([[0], np.cumsum(lengths)])[:-1].astype(np.int64)
    return np.concatenate(reads), lengths, offs


def test_scan_refuses_a_margin_that_does_not_fit_the_int64_argument():
    """The reference computes with the margin, so it refuses the one that does not fit its dtype."""
    lay = BALANCED
    codes, lengths, _ = _orient_batch(lay)
    _, _, mpos, want, starts = _template(lay)
    with pytest.raises(ValueError):
        no.scan(codes, lengths, mpos, want, starts, OVER64, 2)
    with pytest.raises((ValueError, OverflowError)):
        pass1._orientation_reference(codes, lengths, mpos, want, starts, OVER64)


def test_rc_bound_refuses_a_cost_that_does_not_fit_the_int64_argument():
    lay = BALANCED
    codes, lengths, offs = _orient_batch(lay)
    _, _, mpos, want, starts = _template(lay)
    with pytest.raises(ValueError):
        no.rc_bound(codes, lengths, offs, mpos, want, starts, 6, OVER64, 2, 2)
    with pytest.raises((ValueError, OverflowError)):
        pass1._rc_bound_reference(codes, lengths, offs, mpos, want, starts, 6, OVER64, 2, 2)


def test_spec_project_refuses_a_template_length_that_does_not_fit_the_int64_argument():
    lay = BALANCED
    codes, lengths, offs = _orient_batch(lay)
    tpl, fpos, mpos, want, starts = _template(lay)
    with pytest.raises(ValueError):
        no.spec_project(codes, lengths, offs, None, 0, OVER64, mpos, want, starts, fpos, 6)
    with pytest.raises((ValueError, OverflowError)):
        pass1._spec_project_reference(codes, lengths, offs, None, 0, OVER64, mpos, want, starts, fpos, 6)


def test_pack_projection_refuses_bases_that_do_not_fit_the_uint8_kernel_argument():
    """A base of 300 does not fit a uint8: the reference would narrow it to 44 (and clamp the hard base to 3), the
    native path refuses the value instead of narrowing it."""
    bases = np.array([[0, 1, 2, 3]], np.int16)
    erased = np.zeros((1, 4), np.uint8)
    with pytest.raises(ValueError):
        no.pack_projection(bases * 100, erased)


def test_pack_exact_refuses_a_min_q_that_does_not_fit_the_int64_argument():
    """The reference compares the qualities with an unbounded Python integer and accepts the value; the native path
    refuses it instead of narrowing it to 1."""
    lay = BALANCED
    codes, _, offs = _orient_batch(lay)
    tpl, fpos, mpos, want, _ = _template(lay)
    qflat = np.full(codes.size, 30, np.uint8)
    with pytest.raises(ValueError):
        no.pack_exact(codes, offs, qflat, OVER64, tpl.size, mpos, want, fpos)
    out = pass1._pack_exact_reference(lay, codes, offs, qflat, OVER64)
    assert out[0].shape == (offs.size, fpos.size)
