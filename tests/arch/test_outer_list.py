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
