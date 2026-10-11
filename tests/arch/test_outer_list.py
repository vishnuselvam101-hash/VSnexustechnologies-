"""Hybrid architecture: outer-assisted inner decoding of short rows (``vnxdna.recovery.outer_list``, C++ kernel
``vnxdna.native.outerlist``, ABI 1).

* the C++ kernel equals the Python reference (assignment with exact-byte masks; per-column list decoding including
  the best-solution proposals, agreement counts and the subset cap) on randomized cases;
* the C ABI rejects inconsistent arguments;
* a short row is recovered exactly from corrupted candidate frames (header, payload and tail errors), each new symbol
  verified by inner RS + CRC-32 or by its re-encoded tail;
* candidates from unrelated data never recover a row;
* the setting is opt-in and validated.

SYNTHETIC data.
"""
from __future__ import annotations

import ctypes

import numpy as np
import pytest

from vnxdna.codec import gf256
from vnxdna.codec.cauchy import CauchyErasureCode
from vnxdna.codec.codecs import CauchyRSCodec
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.layout import Layout
from vnxdna.dnaenc.scrambler import keystreams
from vnxdna.native import outerlist as nol
from vnxdna.recovery import outer_list as ol
from vnxdna.recovery.cluster import ClusterConfig


@pytest.fixture(scope="module", autouse=True)
def _library():
    nol._reset_for_tests()
    if not nol.available():
        try:
            nol.build()
        except nol.NativeOuterListError as error:
            pytest.skip(f"C++ outer-list kernel unavailable and cannot be built here: {error}")
        nol._reset_for_tests()
    assert nol.available(), nol.status()
    yield
    nol._reset_for_tests()


def _codeword(rng, K, M, P):
    data = rng.integers(0, 256, (1, K, P), dtype=np.uint8)
    code = CauchyErasureCode(K, M)
    return np.concatenate([data[0], code.encode(data)[0]]), np.asarray(code._parity_matrix, dtype=np.uint8)


def _row_case(seed, K=10, M=6, P=12, f=2, n_cands=6, err=0.15, dup=1):
    rng = np.random.default_rng(seed)
    cw, parity = _codeword(rng, K, M, P)
    n = K + M
    unknown = rng.choice(n, size=M + f, replace=False)
    known = np.ones(n, bool)
    known[unknown] = False
    sym = np.where(known[:, None], cw, 0).astype(np.uint8)
    pos = rng.choice(unknown, size=min(n_cands, unknown.size), replace=False)
    cpos, cval, cok = [], [], []
    for p in pos:
        for _ in range(dup):
            v = cw[p].copy()
            bad = rng.random(P) < err
            v[bad] = rng.integers(0, 256, int(bad.sum()))
            cpos.append(p)
            cval.append(v)
            cok.append(rng.random(P) > 0.05)
    return cw, parity, known, sym, np.array(cpos, np.int32), np.stack(cval), np.stack(cok).astype(np.uint8)


@pytest.mark.parametrize("seed", range(40))
def test_decode_row_native_equals_reference(seed):
    f = 1 + seed % 4
    cw, parity, known, sym, cpos, cval, cok = _row_case(seed, f=f, n_cands=f + 1 + seed % 5, dup=1 + seed % 2,
                                                        err=0.05 + 0.05 * (seed % 4))
    args = (parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, cpos, cval, cok, 1, 1, 50 if seed % 3 else 3)
    nat = nol.decode_row(*args)
    ref = ol.decode_row_reference(*args)
    np.testing.assert_array_equal(nat[0], ref[0])
    assert nat[1] == ref[1]
    np.testing.assert_array_equal(nat[2], ref[2])
    np.testing.assert_array_equal(nat[3], ref[3])


def test_decode_row_recovers_with_clean_redundant_candidates():
    cw, parity, known, sym, cpos, cval, cok = _row_case(3, f=3, n_cands=8, err=0.0)
    cok[:] = 1
    out, acc, best, second = nol.decode_row(parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, cpos, cval, cok,
                                            2, 2, 10_000)
    assert acc == cw.shape[1]
    np.testing.assert_array_equal(out, cw)
    assert (best == 8).all() and (second < best).all()


def test_decode_row_without_enough_candidates_accepts_nothing():
    cw, parity, known, sym, cpos, cval, cok = _row_case(4, f=3, n_cands=3, err=0.0)
    out, acc, best, _ = nol.decode_row(parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, cpos, cval, cok, 1, 1,
                                       10_000)
    assert acc == 0 and (best == 3).all()           # the f candidates determine a solution but confirm nothing


@pytest.mark.parametrize("seed", range(10))
def test_assign_native_equals_reference(seed):
    rng = np.random.default_rng(100 + seed)
    ks = keystreams(9 + 20 + 4)
    t = rng.integers(0, 256, (30, 9), dtype=np.uint8)
    frames = rng.integers(0, 256, (200, 40), dtype=np.uint8)
    for i in range(0, 200, 3):                      # frames that are near some target with some variant
        v = int(rng.integers(0, 8))
        x = int(rng.integers(0, 30))
        frames[i, 0] = v
        frames[i, 1:10] = t[x] ^ ks[v][:9]
        for b in rng.choice(10, size=int(rng.integers(0, 4)), replace=False):
            frames[i, b] ^= int(rng.integers(1, 256))
    variants = np.arange(8, dtype=np.int16)
    for mask in (0, ol.EXACT_MASK, 1):
        nat = nol.assign(frames, ks, variants, t, 3, mask)
        ref = ol.assign_reference(frames, ks, variants, t, 3, mask)
        for a, b in zip(nat, ref):
            np.testing.assert_array_equal(np.asarray(a, np.int64), np.asarray(b, np.int64))
        assert (nat[0] >= 0).sum() > 0


def test_c_abi_rejects_inconsistent_arguments():
    lib = nol.KERNEL.lib()
    cw, parity, known, sym, cpos, cval, cok = _row_case(9, f=2)
    K, M, P = 10, 6, 12
    best, second = np.zeros(P, np.int32), np.zeros(P, np.int32)
    kn = known.astype(np.uint8)

    def call(k=K, m=M, p=P, nc=len(cpos), cp=cpos, kn_=kn, min_extra=1, margin=1, subsets=100):
        return lib.vnx_ol_decode_row(k, m, p, parity.ctypes.data, gf256.MUL.ctypes.data, ol.INV.ctypes.data,
                                     kn_.ctypes.data, sym.copy().ctypes.data, nc, cp.ctypes.data, cval.ctypes.data,
                                     cok.ctypes.data, min_extra, margin, subsets, best.ctypes.data, second.ctypes.data)

    assert call() >= 0
    bad_pos = cpos.copy()
    bad_pos[0] = int(np.flatnonzero(known)[0])          # a candidate at a known position
    all_known = np.ones_like(kn)
    for kw in (dict(k=0), dict(m=0), dict(k=200, m=100), dict(p=0), dict(nc=-1), dict(cp=bad_pos), dict(kn_=all_known),
               dict(margin=0), dict(subsets=0), dict(min_extra=-1)):
        assert call(**kw) == -1, kw
    out = np.zeros(1, np.int32)
    assert lib.vnx_ol_assign(1, 5, cval.ctypes.data, 1, 33, cval.ctypes.data, 1, np.zeros(1, np.int16).ctypes.data, 0,
                             None, 3, 0, out.ctypes.data, out.ctypes.data, out.ctypes.data, out.ctypes.data) == -1


def _archive_row(seed, lay, tag, g, k, M):
    """True payloads of one row and their transmitted frames."""
    rng = np.random.default_rng(seed)
    P = lay.payload_bytes
    codec = CauchyRSCodec(k, M)
    data = rng.integers(0, 256, (k, P), dtype=np.uint8)
    cw = codec.encode(data)
    frames = np.concatenate([ol.encode_frames(lay, tag, g, s, cw[s][None, :], int(rng.integers(0, 4)))
                             for s in range(k + M)])
    return codec, data, cw, frames


def _corrupt(rng, frame, n_err, P):
    f = frame.copy()
    where = rng.choice(np.arange(1, f.size), size=n_err, replace=False)
    f[where] ^= rng.integers(1, 256, n_err).astype(np.uint8)
    return f


@pytest.mark.parametrize("seed", range(6))
def test_short_row_recovered_from_corrupted_frames(seed):
    lay = Layout(20, 8, 0, 0)
    tag, g, k, M = 0x1234, 7, 16, 6
    codec, data, cw, frames = _archive_row(seed, lay, tag, g, k, M)
    rng = np.random.default_rng(1000 + seed)
    missing = rng.choice(k + M, size=M + 2, replace=False)            # 2 symbols short
    syms = {s: cw[s] for s in range(k + M) if s not in missing}
    cand, er = [], []
    for s in missing:                                                 # corrupted beyond the inner code (t = 4)
        cand.append(_corrupt(rng, frames[s], int(rng.integers(5, 9)), lay.payload_bytes))
        er.append(np.zeros(lay.frame_bytes, bool))
    noise = rng.integers(0, 256, (50, lay.frame_bytes), dtype=np.uint8)
    F = np.concatenate([np.stack(cand), noise])
    E = np.zeros(F.shape, bool)
    cfg = ClusterConfig(outer_list="on").validate()
    dec = ol.OuterListDecoder(lay, cfg, codec, tag)
    out = dec.recover({g: (k, syms)}, F, E)
    assert g in out, dict(dec.counts)
    np.testing.assert_array_equal(out[g], data)


def test_unrelated_candidates_never_recover_a_row():
    lay = Layout(20, 8, 0, 0)
    tag, g, k, M = 0x1234, 7, 16, 6
    codec, data, cw, frames = _archive_row(77, lay, tag, g, k, M)
    _, _, _, other = _archive_row(78, lay, tag, g, k, M)             # same addresses, different data
    rng = np.random.default_rng(5)
    missing = rng.choice(k + M, size=M + 2, replace=False)
    syms = {s: cw[s] for s in range(k + M) if s not in missing}
    F = np.concatenate([_corrupt(rng, other[s], 6, lay.payload_bytes)[None] for s in missing])
    dec = ol.OuterListDecoder(lay, ClusterConfig(outer_list="on").validate(), codec, tag)
    assert dec.recover({g: (k, syms)}, F, np.zeros(F.shape, bool)) == {}


def test_reference_backend_gives_the_same_recovery(monkeypatch):
    lay = Layout(20, 8, 0, 0)
    tag, g, k, M = 0x1234, 3, 16, 6
    codec, data, cw, frames = _archive_row(11, lay, tag, g, k, M)
    rng = np.random.default_rng(12)
    missing = rng.choice(k + M, size=M + 1, replace=False)
    syms = {s: cw[s] for s in range(k + M) if s not in missing}
    F = np.stack([_corrupt(rng, frames[s], 6, lay.payload_bytes) for s in missing])
    res = {}
    for backend in ("native", "reference"):
        monkeypatch.setenv("VNXDNA_OUTERLIST_BACKEND", backend)
        dec = ol.OuterListDecoder(lay, ClusterConfig(outer_list="on").validate(), codec, tag)
        res[backend] = (dec.recover({g: (k, dict(syms))}, F, np.zeros(F.shape, bool)), dict(dec.counts))
    assert res["native"][0].keys() == res["reference"][0].keys() == {g}
    np.testing.assert_array_equal(res["native"][0][g], res["reference"][0][g])
    assert res["native"][1] == res["reference"][1]


def test_setting_is_opt_in_and_validated():
    assert ClusterConfig().outer_list == "off"
    with pytest.raises(VNXConfigurationError):
        ClusterConfig(outer_list="yes").validate()
    with pytest.raises(VNXConfigurationError):
        ClusterConfig(list_variants=300).validate()
    assert not ol.OuterListDecoder(Layout(), ClusterConfig(), CauchyRSCodec(4, 2), 1).applicable()


def test_symbols_are_exported():
    assert isinstance(nol.KERNEL.lib().vnx_ol_decode_row, ctypes._CFuncPtr)


# ---------------------------------------------------------------------------------------------------- frame-level RANSAC
def _ransac_case(seed, K=12, M=6, P=24, f=2, good=5, junk=20, err=0.25):
    rng = np.random.default_rng(seed)
    cw, parity = _codeword(rng, K, M, P)
    n = K + M
    unknown = rng.choice(n, size=M + f, replace=False)
    known = np.ones(n, bool)
    known[unknown] = False
    sym = np.where(known[:, None], cw, 0).astype(np.uint8)
    hpos, hval, truth = [], [], []
    for p in rng.choice(unknown, size=good, replace=False):
        v = cw[p].copy()
        bad = rng.random(P) < err
        v[bad] ^= rng.integers(1, 256, int(bad.sum())).astype(np.uint8)
        hpos.append(p), hval.append(v), truth.append(True)
    for _ in range(junk):
        hpos.append(int(rng.choice(unknown))), hval.append(rng.integers(0, 256, P, dtype=np.uint8)), truth.append(False)
    order = rng.permutation(len(hpos))
    return (parity, known, sym, np.array(hpos, np.int32)[order], np.stack(hval)[order], np.array(truth)[order])


@pytest.mark.parametrize("seed", range(24))
def test_ransac_native_equals_reference(seed):
    f = 1 + seed % 3
    parity, known, sym, hpos, hval, _ = _ransac_case(seed, f=f, good=f + 2, junk=6 + seed % 5)
    for tau, cap in ((6, 10_000), (3, 7)):
        nat = nol.ransac(parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, hpos, hval, tau, cap)
        ref = ol.ransac_reference(parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, hpos, hval, tau, cap)
        assert nat[0] == ref[0]
        np.testing.assert_array_equal(nat[1], ref[1])


@pytest.mark.parametrize("seed", range(8))
def test_ransac_separates_true_frames_from_junk(seed):
    f = 1 + seed % 3
    parity, known, sym, hpos, hval, truth = _ransac_case(100 + seed, f=f, good=f + 3, junk=30, err=0.15)
    support, agree = nol.ransac(parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, hpos, hval, 8, 100_000)
    assert support >= f + 1
    chosen = agree >= 8
    assert not (chosen & ~truth).any()             # no junk frame is selected
    assert chosen[truth].mean() >= 0.5


def test_ransac_rejects_bad_arguments():
    lib = nol.KERNEL.lib()
    parity, known, sym, hpos, hval, _ = _ransac_case(1)
    agree = np.zeros(hpos.size, np.int32)
    kn = known.astype(np.uint8)

    def call(tau=4, cap=100, hp=hpos, kn_=kn, P=sym.shape[1]):
        return lib.vnx_ol_ransac(12, 6, P, parity.ctypes.data, gf256.MUL.ctypes.data, ol.INV.ctypes.data,
                                 kn_.ctypes.data, sym.ctypes.data, hp.size, hp.ctypes.data, hval.ctypes.data, tau, cap,
                                 agree.ctypes.data)
    assert call() >= 0
    bad = hpos.copy()
    bad[0] = int(np.flatnonzero(known)[0])
    for kw in (dict(tau=0), dict(tau=sym.shape[1] + 1), dict(cap=0), dict(hp=bad), dict(kn_=np.ones_like(kn)), dict(P=0)):
        assert call(**kw) == -1, kw


@pytest.mark.parametrize("seed", range(6))
def test_short_row_recovered_when_headers_are_damaged(seed):
    """Candidates whose group/symbol header bytes are corrupted (so the exact header rule cannot place them) are found
    by the frame-level RANSAC and the row is recovered exactly."""
    lay = Layout(20, 8, 0, 0)
    tag, g, k, M = 0x1234, 7, 16, 6
    codec, data, cw, frames = _archive_row(300 + seed, lay, tag, g, k, M)
    rng = np.random.default_rng(400 + seed)
    missing = rng.choice(k + M, size=M + 2, replace=False)
    syms = {s: cw[s] for s in range(k + M) if s not in missing}
    cand = []
    for s in missing:
        f_ = _corrupt(rng, frames[s], 6, lay.payload_bytes)
        f_[8 + int(rng.integers(0, 2))] ^= 0x41                       # one symbol byte of the header wrong
        cand.append(f_)
    F = np.concatenate([np.stack(cand), rng.integers(0, 256, (40, lay.frame_bytes), dtype=np.uint8)])
    dec = ol.OuterListDecoder(lay, ClusterConfig(outer_list="on").validate(), codec, tag, n_groups=20)
    out = dec.recover({g: (k, syms)}, F, np.zeros(F.shape, bool))
    assert g in out, dict(dec.counts)
    np.testing.assert_array_equal(out[g], data)
    assert dec.counts["ransac_candidates"] > 0


def test_rounds_continue_when_a_round_only_finds_candidates(monkeypatch):
    """Regression (seeds 93022/93027/93028, SIMULATED): a single short row whose first round finds payload-consistency
    candidates but no new symbol must get another round (it used to stop, so the candidates were never used)."""
    lay = Layout(20, 8, 0, 0)
    codec, data, cw, _ = _archive_row(5, lay, 0x1234, 7, 16, 6)
    syms = {s: cw[s] for s in range(16)}                              # k symbols: needs one check symbol
    syms.pop(0)                                                       # one short
    calls = []

    def fake_round(self, rows, frames, erased, ks):
        calls.append(len(calls))
        if len(calls) == 1:                                           # round 1: a new candidate only
            self._extra.setdefault(7, {})[(0, 0)] = 0
            return 0
        k_, s_ = rows[7]
        s_[0], s_[16] = cw[0], cw[16]                                 # round 2: symbols verified
        return 2

    monkeypatch.setattr(ol.OuterListDecoder, "_round", fake_round)
    dec = ol.OuterListDecoder(lay, ClusterConfig(outer_list="on").validate(), codec, 0x1234)
    out = dec.recover({7: (16, syms)}, np.zeros((1, lay.frame_bytes), np.uint8), np.zeros((1, lay.frame_bytes), bool))
    assert len(calls) == 2 and 7 in out
    np.testing.assert_array_equal(out[7], data)


# ---------------------------------------------------------------------- hypothesis selection (development seeds 9305x)
def _tuple_order(n, f):
    return [tuple(S) for S in ol._tuples(n, f)]


def test_ransac_tuples_are_enumerated_best_first():
    """Every f-tuple within the first m hypotheses precedes any tuple that uses hypothesis m + 1, so the tuple cap is
    spent on the best-ranked hypotheses instead of on every partner of hypothesis 0."""
    order = _tuple_order(6, 3)
    assert len(order) == 20 and len(set(order)) == 20
    assert all(max(t) <= max(u) for t, u in zip(order, order[1:]))
    assert order[:4] == [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
    assert _tuple_order(4, 1) == [(0,), (1,), (2,), (3,)]
    assert _tuple_order(2, 3) == []


def test_ransac_cap_is_spent_best_first_in_both_backends():
    """f = 3: hypothesis 0 is junk, 1..4 are true. With a cap of 10 tuples the first-index-fixed order never leaves
    junk 0 behind; best-first order tries (1, 2, 3) within the cap, so the true frames support each other."""
    rng = np.random.default_rng(7)
    K, M, P, f = 12, 6, 24, 3
    cw, parity = _codeword(rng, K, M, P)
    unknown = rng.choice(K + M, size=M + f, replace=False)
    known = np.ones(K + M, bool)
    known[unknown] = False
    sym = np.where(known[:, None], cw, 0).astype(np.uint8)
    pos = list(unknown[:5])
    hpos = np.array(pos + [int(rng.choice(unknown)) for _ in range(8)], np.int32)
    hval = np.stack([rng.integers(0, 256, P, dtype=np.uint8)] + [cw[p] for p in pos[1:]] +
                    [rng.integers(0, 256, P, dtype=np.uint8) for _ in range(8)])
    args = (parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, hpos, hval, 8, 10)
    nat, ref = nol.ransac(*args), ol.ransac_reference(*args)
    assert nat[0] == ref[0] == 4 and (nat[1][1:5] == P).all()
    np.testing.assert_array_equal(nat[1], ref[1])


def test_group_ambiguity_counts_rows_that_explain_the_header_as_well():
    lay = Layout(20, 8, 0, 0)
    dec = ol.OuterListDecoder(lay, ClusterConfig(outer_list="on").validate(), CauchyRSCodec(16, 6), 0x1234, n_groups=300)
    g = 290                                                           # bytes (0, 0, 1, 34)
    gb = np.array([[0, 0, 1, 34],                                     # exact: only this row
                   [9, 0, 1, 34],                                     # byte 3 wrong: only row 290 is one byte away
                   [0, 0, 1, 60],                                     # byte 6 wrong (316 does not exist): 256..299 + 60
                   [0, 0, 0, 34]], np.uint8)                          # row 34 itself: 0..255 and 290 within one byte
    np.testing.assert_array_equal(dec._group_ambiguity(gb, g), [1, 1, 45, 257])
    huge = ol.OuterListDecoder(lay, ClusterConfig(outer_list="on").validate(), CauchyRSCodec(16, 6), 0x1234)
    assert huge._group_ambiguity(gb, g).tolist() == [1, 1 << 32, 1 << 32, 1 << 32]


def _corrupt_body(rng, frame, n_err):
    """``n_err`` byte errors outside the variant byte and the header (bytes 0..9)."""
    f = frame.copy()
    where = rng.choice(np.arange(10, f.size), size=n_err, replace=False)
    f[where] ^= rng.integers(1, 256, n_err).astype(np.uint8)
    return f


def _ransac_setup(seed, n_groups=300, g=290, k=16, M=6, f=2):
    lay = Layout(20, 8, 0, 0)
    tag = 0x1234
    codec, data, cw, frames = _archive_row(seed, lay, tag, g, k, M)
    rng = np.random.default_rng(seed + 1)
    missing = sorted(rng.choice(k + M, size=M + f, replace=False).tolist())
    syms = {s: cw[s] for s in range(k + M) if s not in missing}
    return lay, tag, codec, data, cw, frames, rng, missing, syms


def test_ransac_hypotheses_include_header_candidates_with_their_variant():
    """Two true frames whose variant byte is wrong are header-assigned candidates (the assignment tries every variant)
    but, descrambled with their own variant byte, are nowhere near the row: without them the RANSAC has only the two
    frames with a damaged symbol byte (tied between every missing symbol), exactly f true placements, and can verify
    nothing."""
    lay, tag, codec, data, cw, frames, rng, missing, syms = _ransac_setup(21)
    F = []
    for s in missing[:2]:                                             # variant byte wrong, 4 body errors
        x = _corrupt_body(rng, frames[s], 4)
        x[0] ^= 1
        F.append(x)
    for s in missing[2:4]:                                            # low symbol byte wrong (tied), 4 errors
        x = _corrupt_body(rng, frames[s], 4)
        x[9] ^= 0x55
        F.append(x)
    F = np.stack(F + [rng.integers(0, 256, (30, lay.frame_bytes), dtype=np.uint8)[i] for i in range(30)])
    cfg = ClusterConfig(outer_list="on").validate()
    dec = ol.OuterListDecoder(lay, cfg, codec, tag, n_groups=300)
    ks = keystreams(9 + lay.payload_bytes + 4)
    variants = np.arange(cfg.list_variants, dtype=np.int16)
    T = np.stack([ol.header_bytes(tag, 290, s) for s in missing])
    tgt, var, _, _ = ol.assign(F, ks, variants, T, cfg.list_max_header_distance, ol.EXACT_MASK)
    cands = {290: {(missing[int(tgt[i])], i): int(var[i]) for i in np.flatnonzero(tgt >= 0).tolist()}}
    assert set(cands[290]) == {(missing[0], 0), (missing[1], 1)} and all(v != F[i, 0] for (_, i), v in cands[290].items())
    dec._ransac_assign({290: (16, syms)}, F, ks, cands)
    assert {(missing[2], 2), (missing[3], 3)} <= set(cands[290]), dict(dec.counts)
    assert dec.counts["ransac_candidates"] == 2


@pytest.mark.parametrize("scan", [8, 0])
def test_ransac_scan_places_frames_whose_header_is_unusable(scan):
    """Three header-assigned frames determine the codeword (f = 2, support 3). Two more true frames have a damaged
    group byte and both symbol bytes damaged, so no header test places them; the scan of every failed frame against
    the winning codeword finds them by payload (and only with the scan on). Junk frames stay out."""
    lay, tag, codec, data, cw, frames, rng, missing, syms = _ransac_setup(33)
    F = [_corrupt_body(rng, frames[s], 3) for s in missing[:3]]
    for s in missing[3:5]:
        x = _corrupt_body(rng, frames[s], 3)
        x[4] ^= 0x40
        x[8] ^= 0x21
        x[9] ^= 0x12
        F.append(x)
    F = np.stack(F + [rng.integers(0, 256, (40, lay.frame_bytes), dtype=np.uint8)[i] for i in range(40)])
    cfg = ClusterConfig(outer_list="on", list_ransac_scan_variants=scan).validate()
    dec = ol.OuterListDecoder(lay, cfg, codec, tag, n_groups=300)
    ks = keystreams(9 + lay.payload_bytes + 4)
    cands = {290: {(s, i): int(F[i, 0]) for i, s in enumerate(missing[:3])}}
    dec._ransac_assign({290: (16, syms)}, F, ks, cands)
    found = {(missing[3], 3), (missing[4], 4)} <= set(cands[290])
    assert found == bool(scan), dict(dec.counts)
    assert all(i < 5 for _, i in cands[290])
    assert dec.counts["ransac_scan_candidates"] == (2 if scan else 0)
    assert dec.counts["ransac_scan_mismatch"] == 0


def test_ransac_ranks_unambiguous_headers_before_ambiguous_ones():
    """70 frames of neighbouring rows whose low group byte is damaged (an invalid row number one byte from row 290 and
    from 43 others) used to fill the 64-hypothesis budget before the row's own frames, whose damaged header only row
    290 explains."""
    lay, tag, codec, data, cw, frames, rng, missing, syms = _ransac_setup(22)
    ks = keystreams(9 + lay.payload_bytes + 4)
    junk = []
    for j in range(70):                                               # row 256 + j % 30, byte 6 -> 60 (row 316: none)
        _, _, cw2, fr2 = _archive_row(500 + j, lay, tag, 256 + j % 30, 16, 6)
        x = fr2[missing[j % 4]].copy()
        v = int(x[0])
        x[7] = 60 ^ ks[v][6]
        junk.append(x)
    good = []
    for s in missing[:4]:                                             # byte 3 damaged: unique fix, 4 body errors
        x = _corrupt_body(rng, frames[s], 4)
        x[4] ^= 0x55
        good.append(x)
    F = np.stack(junk + good)
    cfg = ClusterConfig(outer_list="on").validate()
    dec = ol.OuterListDecoder(lay, cfg, codec, tag, n_groups=300)
    cands: dict = {}
    dec._ransac_assign({290: (16, syms)}, F, ks, cands)
    assert set(cands.get(290, {})) == {(s, 70 + j) for j, s in enumerate(missing[:4])}, dict(dec.counts)


def test_rs_hypotheses_try_header_candidates_with_their_variant():
    """A header-assigned candidate whose variant byte is wrong is inner-decoded with the assigned variant (its own byte
    would descramble the payload with the wrong keystream)."""
    lay, tag, codec, data, cw, frames, rng, missing, syms = _ransac_setup(23, f=1)
    s = missing[0]
    x = _corrupt_body(rng, frames[s], 2)
    x[0] ^= 2
    F = x[None, :]
    cfg = ClusterConfig(outer_list="on").validate()
    dec = ol.OuterListDecoder(lay, cfg, codec, tag, n_groups=300)
    ks = keystreams(9 + lay.payload_bytes + 4)
    T = np.stack([ol.header_bytes(tag, 290, m) for m in missing])
    owner = [(290, m) for m in missing]
    rows = {290: (16, dict(syms))}
    cands = {290: {(s, 0): int(frames[s, 0])}}
    added = dec._rs_hypotheses(rows, {}, F, np.zeros(F.shape, bool), ks, T, owner, cands)
    assert added == 1 and s in rows[290][1]
    np.testing.assert_array_equal(rows[290][1][s], cw[s])


@pytest.mark.parametrize("seed", range(4))
def test_short_row_recovered_when_variant_bytes_are_wrong(seed):
    """End to end: every candidate frame of the row carries a wrong scrambler-variant byte, half of them also a damaged
    group byte; the row is recovered exactly."""
    lay, tag, codec, data, cw, frames, rng, missing, syms = _ransac_setup(30 + seed)
    cand = []
    for j, s in enumerate(missing):
        x = _corrupt_body(rng, frames[s], int(rng.integers(4, 7)))
        x[0] ^= 1 + int(rng.integers(0, 3))
        if j % 2:
            x[4] ^= 0x55
        cand.append(x)
    F = np.concatenate([np.stack(cand), rng.integers(0, 256, (40, lay.frame_bytes), dtype=np.uint8)])
    dec = ol.OuterListDecoder(lay, ClusterConfig(outer_list="on").validate(), codec, tag, n_groups=300)
    out = dec.recover({290: (16, syms)}, F, np.zeros(F.shape, bool))
    assert 290 in out, dict(dec.counts)
    np.testing.assert_array_equal(out[290], data)


def test_rs_hypotheses_try_the_errors_only_variant():
    """A candidate with 9 wrong payload bytes (inner code: 4 errors, or e + 2f <= 8) and a right proposal confirmed
    in 5 of its columns: writing them leaves 4 errors, which decode only when nothing is erased. Erasing the 4
    disagreeing unconfirmed columns plus the consensus's 2 flags (6 erasures, 12 > 8) fails."""
    lay, tag, codec, data, cw, frames, rng, missing, syms = _ransac_setup(24, f=1)
    s = missing[0]
    P = lay.payload_bytes
    x = frames[s].copy()
    for c in range(10, 19):                                           # payload columns 0..8 wrong
        x[c] ^= 0x5A
    F = x[None, :]
    E = np.zeros(F.shape, bool)
    E[0, [20, 21]] = True                                             # consensus flags on right bytes
    pos = ol._pos(16, 16)
    res = np.zeros((22, P), np.uint8)
    res[pos(s)] = cw[s]                                               # proposal right everywhere
    ok = np.zeros(P, bool)
    ok[:5] = True                                                     # columns 0..4 confirmed (fixes 5 of 9 errors)
    proposal = {290: (res, ok, np.ones(P, bool), pos)}
    dec = ol.OuterListDecoder(lay, ClusterConfig(outer_list="on").validate(), codec, tag, n_groups=300)
    ks = keystreams(9 + P + 4)
    T = np.stack([ol.header_bytes(tag, 290, m) for m in missing])
    owner = [(290, m) for m in missing]
    rows = {290: (16, dict(syms))}
    added = dec._rs_hypotheses(rows, proposal, F, E, ks, T, owner, {})
    assert added == 1 and s in rows[290][1]
    np.testing.assert_array_equal(rows[290][1][s], cw[s])


def test_unique_header_fix_frames_become_candidates_without_ransac_support():
    """f = 3 and exactly three header-assigned candidates: the RANSAC can verify nothing (a tuple always agrees with
    itself). Two more frames whose damaged group byte only this row explains, each naming one missing symbol, are
    candidates on the strength of their header, like a candidate with a damaged tag byte."""
    lay, tag, codec, data, cw, frames, rng, missing, syms = _ransac_setup(25, f=3)
    F = [_corrupt_body(rng, frames[s], 4) for s in missing[:3]]
    for s in missing[3:5]:
        x = _corrupt_body(rng, frames[s], 4)
        x[4] ^= 0x55
        F.append(x)
    F = np.stack(F + [rng.integers(0, 256, lay.frame_bytes, dtype=np.uint8) for _ in range(30)])
    cfg = ClusterConfig(outer_list="on").validate()
    dec = ol.OuterListDecoder(lay, cfg, codec, tag, n_groups=300)
    ks = keystreams(9 + lay.payload_bytes + 4)
    cands = {290: {(missing[j], j): int(F[j, 0]) for j in range(3)}}
    dec._ransac_assign({290: (16, syms)}, F, ks, cands)
    assert {(missing[3], 3), (missing[4], 4)} <= set(cands[290]), dict(dec.counts)
    assert dec.counts["unique_candidates"] == 2 and not dec.counts["ransac_candidates"]


def test_consistency_assignment_tries_the_common_variants():
    """A failed frame with a garbage header and a wrong variant byte agrees with the row's proposal only when
    descrambled with its true variant; it is assigned with that variant."""
    lay, tag, codec, data, cw, frames, rng, missing, syms = _ransac_setup(26, f=1)
    P = lay.payload_bytes
    s = missing[0]
    x = _corrupt_body(rng, frames[s], 2)
    x[1:10] = rng.integers(0, 256, 9)                                 # header garbage
    x[0] = 7                                                          # variant byte wrong (true variant < 4)
    F = np.stack([x] + [rng.integers(0, 256, lay.frame_bytes, dtype=np.uint8) for _ in range(20)])
    F[1:, 0] = rng.integers(0, 4, 20)                                 # the archive's variants
    pos = ol._pos(16, 16)
    res = np.zeros((22, P), np.uint8)
    res[pos(s)] = cw[s]
    proposal = {290: (res, np.ones(P, bool), np.ones(P, bool), pos)}
    dec = ol.OuterListDecoder(lay, ClusterConfig(outer_list="on").validate(), codec, tag, n_groups=300)
    ks = keystreams(9 + P + 4)
    owner = [(290, m) for m in missing]
    dec._consistency_assign({290: (16, dict(syms))}, proposal, F, ks, owner)
    assert dec._extra == {290: {(s, 0): int(frames[s, 0])}}, dict(dec.counts)


def test_rs_hypotheses_try_proposal_disagreements_without_the_consensus_flags():
    """A candidate with 10 wrong payload bytes (inner code: 2e + f <= 16), 5 consensus flags on right bytes and an
    unconfirmed proposal that is right in 4 of the wrong columns: with the flags 6 errors + 9 erasures (21) fail,
    errors only (20) fail, and the 4 disagreeing columns erased on their own (12 + 4) decode."""
    lay = Layout(40, 16, 0, 0)
    tag, g, k, M = 0x1234, 7, 16, 6
    codec, data, cw, frames = _archive_row(44, lay, tag, g, k, M)
    rng = np.random.default_rng(45)
    missing = sorted(rng.choice(k + M, size=M + 1, replace=False).tolist())
    syms = {s: cw[s] for s in range(k + M) if s not in missing}
    s = missing[0]
    P = lay.payload_bytes
    x = frames[s].copy()
    for c in range(10, 20):                                           # payload columns 0..9 wrong
        x[c] ^= 0x5A
    F = x[None, :]
    E = np.zeros(F.shape, bool)
    E[0, 30:35] = True                                                # flags on right payload bytes
    pos = ol._pos(k, k)
    res = np.zeros((k + M, P), np.uint8)
    res[pos(s)] = cw[s] ^ 1                                           # proposal wrong everywhere ...
    res[pos(s), 0:4] = cw[s][0:4]                                     # ... except in four of the wrong columns
    ok = np.zeros(P, bool)
    has = np.zeros(P, bool)
    has[0:4] = True                                                   # the only columns with a solution
    proposal = {g: (res, ok, has, pos)}
    dec = ol.OuterListDecoder(lay, ClusterConfig(outer_list="on").validate(), codec, tag, n_groups=20)
    ks = keystreams(9 + P + 4)
    T = np.stack([ol.header_bytes(tag, g, m) for m in missing])
    owner = [(g, m) for m in missing]
    rows = {g: (k, dict(syms))}
    added = dec._rs_hypotheses(rows, proposal, F, E, ks, T, owner, {})
    assert added == 1 and s in rows[g][1]
    np.testing.assert_array_equal(rows[g][1][s], cw[s])
