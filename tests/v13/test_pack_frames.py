"""V13 S3: native frame packing of the pass-1 paths (orient ABI 4).

- ``native.orient.pack_projection`` equals ``pass1._pack_projection_reference`` (min-3 hard bases, erased bases = 4,
  ``nt_to_bytes`` byte packing, per-byte erasure flags) on random bases/flags of every layout, flags of any non-zero value;
- ``native.orient.pack_exact`` equals ``pass1._pack_exact_reference`` (gather of the exact-length reads, marker mismatch
  count, N and quality erasures) with and without qualities; golden hashes pin fixed batches for both backends;
- the pass-1 worker gives the same results with the kernel and with the NumPy reference (``VNXDNA_ORIENT_BACKEND``);
- the C argument checks hold.

SIMULATED reads (synthetic strands mutated in NumPy)."""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from vnxdna.dnaenc.layout import PROFILES, Layout
from vnxdna.native import orient as no
from vnxdna.recovery import pass1
from vnxdna.recovery.options import DecodeOptions

LAYOUTS = sorted({lay for lay, _, _ in PROFILES.values()}, key=repr) + [Layout(20, 0, 8, 2), Layout(12, 2, 0, 0)]
BALANCED = PROFILES["v4-balanced"][0]
pytestmark = pytest.mark.skipif(not no.available(), reason="native orientation kernel not built")
#: sha256 over (hard, proj, frames, erased bytes, mm) of ``_exact_batch(BALANCED, seed, 1200)`` with qualities and
#: min_q 20, for both backends (pinned at the first native build of ABI 4)
GOLDEN = {
    301: "f6d3483355ed4b5eceb801d2b1509e206d5bb801ae08ae67c49767fe7e67850f",
    302: "1da17fd7aab0fd4e3aa112d5eec4e9ab0618b4fe01b472f83d0177d98c34bb98",
}


def _template(lay):
    tpl, fpos = lay.template()
    mpos = np.flatnonzero(tpl >= 0)
    return tpl, fpos, mpos, tpl[mpos].astype(np.uint8)


def _exact_batch(lay: Layout, seed: int, n: int, sub: float = 0.03, nrate: float = 0.02):
    """n reads of exactly strand_nt bases (markers of the template, random payload bases, substitutions, N) inside a
    flat buffer with junk between the reads; returns (codes, offs, quals)."""
    rng = np.random.default_rng(seed)
    tpl, _ = lay.template()
    marker = tpl >= 0
    pieces, offs, pos = [], [], 0
    for _ in range(n):
        gap = rng.integers(0, 5, rng.integers(0, 7)).astype(np.uint8)
        r = rng.integers(0, 4, tpl.size).astype(np.uint8)
        r[marker] = tpl[marker]
        r = np.where(rng.random(r.size) < sub, rng.integers(0, 4, r.size), r).astype(np.uint8)
        r[rng.random(r.size) < nrate] = 4
        pieces += [gap, r]
        pos += gap.size
        offs.append(pos)
        pos += r.size
    codes = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.uint8)
    return codes, np.asarray(offs, dtype=np.int64), rng.integers(0, 60, codes.size).astype(np.uint8)


def _same(ref, nat, names):
    assert len(ref) == len(nat) == len(names)
    for name, a, b in zip(names, ref, nat):
        assert a.dtype == b.dtype and a.shape == b.shape, name
        np.testing.assert_array_equal(a, b, err_msg=name)


PACK = ("hard", "proj", "frames", "erased_bytes")


@pytest.mark.parametrize("layout", LAYOUTS, ids=repr)
@pytest.mark.parametrize("seed", range(3))
def test_pack_projection_equals_the_reference(layout, seed):
    rng = np.random.default_rng(seed * 7 + layout.strand_nt)
    n, F = int(rng.integers(0, 300)), layout.frame_nt
    for hi, flag in ((4, 2), (5, 2), (256, 256)):          # codes 0..3, with N, arbitrary bytes; flags 0/1 or any value
        bases = rng.integers(0, hi, size=(n, F)).astype(np.uint8)
        erased = (rng.random((n, F)) < 0.2).astype(np.uint8) * rng.integers(1, flag, size=(n, F)).astype(np.uint8)
        ref = pass1._pack_projection_reference(bases, erased)
        _same(ref, no.pack_projection(bases, erased), PACK)
        _same(ref, no.pack_projection(bases, erased.view(bool)), PACK)
        assert ref[2].shape == (n, F // 4) and ref[3].dtype == bool


@pytest.mark.parametrize("layout", LAYOUTS, ids=repr)
@pytest.mark.parametrize("quals", [False, True])
def test_pack_exact_equals_the_reference(layout, quals):
    codes, offs, q = _exact_batch(layout, 40 + layout.strand_nt + int(quals), 250)
    tpl, fpos, mpos, want = _template(layout)
    for min_q in (0, 15, 40):
        ref = pass1._pack_exact_reference(layout, codes, offs, q if quals else None, min_q)
        nat = no.pack_exact(codes, offs, q if (quals and min_q) else None, min_q, tpl.size, mpos, want, fpos)
        _same(ref, nat, PACK + ("mm",))
        assert nat[4].dtype == np.int64 and (nat[4] >= 0).all()
    # a selection of the reads, in any order
    sel = np.random.default_rng(1).permutation(offs.size)[:100]
    ref = pass1._pack_exact_reference(layout, codes, offs[sel], q, 20)
    _same(ref, no.pack_exact(codes, offs[sel], q, 20, tpl.size, mpos, want, fpos), PACK + ("mm",))


def test_pack_exact_rules_on_hand_built_reads():
    lay = Layout(12, 2, 0, 0)                        # no markers: strand = frame of 4 * frame_bytes bases
    tpl, fpos, mpos, want = _template(lay)
    T = tpl.size
    codes = np.arange(T, dtype=np.uint8) % 4
    codes[5] = 4
    q = np.full(T, 30, dtype=np.uint8)
    q[2] = 3
    hard, proj, frames, eb, mm = no.pack_exact(codes, np.zeros(1, dtype=np.int64), q, 10, T, mpos, want, fpos)
    assert mm.tolist() == [0]
    assert hard[0, 5] == 3 and proj[0, 5] == 4 and proj[0, 2] == 4 and hard[0, 2] == codes[2]
    assert eb[0, 0] and eb[0, 1] and not eb[0, 2:].any()           # bytes 0 (quality) and 1 (N) are erased
    assert frames[0, 2] == (codes[8] << 6) | (codes[9] << 4) | (codes[10] << 2) | codes[11]
    lay = BALANCED
    tpl, fpos, mpos, want = _template(lay)
    r = np.zeros(tpl.size, dtype=np.uint8)
    r[mpos] = want
    r[mpos[0]] = (want[0] + 1) % 4
    r[mpos[-1]] = (want[-1] + 1) % 4
    _, _, _, _, mm = no.pack_exact(r, np.zeros(1, dtype=np.int64), None, 0, tpl.size, mpos, want, fpos)
    assert mm.tolist() == [2]


@pytest.mark.parametrize("seed", sorted(GOLDEN))
def test_golden_hashes(seed):
    codes, offs, q = _exact_batch(BALANCED, seed, 1200)
    tpl, fpos, mpos, want = _template(BALANCED)
    ref = pass1._pack_exact_reference(BALANCED, codes, offs, q, 20)
    nat = no.pack_exact(codes, offs, q, 20, tpl.size, mpos, want, fpos)
    for out in (ref, nat):
        h = hashlib.sha256()
        for a in out:
            h.update(np.ascontiguousarray(a).tobytes())
        assert h.hexdigest() == GOLDEN[seed]


def test_pass1_results_do_not_depend_on_the_backend(monkeypatch):
    from tests.v13.test_spec_projection import _batch
    lay = BALANCED
    opt = DecodeOptions()
    codes, lengths, offs = _batch(lay, 4242, 600, sub=0.02, ind=0.01, rc=0.3, junk=0.1)
    q = np.random.default_rng(5).integers(0, 60, codes.size).astype(np.uint8)
    outs = {}
    for backend in ("native", "reference"):
        monkeypatch.setenv("VNXDNA_ORIENT_BACKEND", backend)
        pass1._p_init(lay, opt.band, opt.sync_costs, 20, True)
        outs[backend] = pass1._process(codes, lengths, q)
    for k in outs["native"]:
        if k == "cpu_seconds":
            continue
        a, b = outs["native"][k], outs["reference"][k]
        if isinstance(a, np.ndarray):
            np.testing.assert_array_equal(a, b, err_msg=k)
        else:
            assert a == b, k
    # synthetic strands carry no valid inner code: the exact and sync paths both run (their packing is compared above)
    # and every aligned read ends pending
    assert (lengths == lay.strand_nt).sum() > 0 and outs["native"]["stats"]["pending"] > 0


def test_python_argument_checks():
    lay = BALANCED
    tpl, fpos, mpos, want = _template(lay)
    T = tpl.size
    codes = np.zeros(T, dtype=np.uint8)
    zero = np.zeros(1, dtype=np.int64)
    assert no.pack_exact(codes, zero, None, 0, T, mpos, want, fpos)[2].shape == (1, lay.frame_bytes)
    assert no.pack_exact(codes, zero[:0], None, 0, T, mpos, want, fpos)[2].shape == (0, lay.frame_bytes)
    with pytest.raises(ValueError):
        no.pack_exact(codes, zero + 1, None, 0, T, mpos, want, fpos)               # read past the buffer
    with pytest.raises(ValueError):
        no.pack_exact(codes, zero, np.zeros(T - 1, dtype=np.uint8), 5, T, mpos, want, fpos)   # qualities too short
    with pytest.raises(ValueError):
        no.pack_exact(codes, zero, None, 0, T, mpos, want, fpos[:-1])             # frame not a multiple of 4
    with pytest.raises(ValueError):
        no.pack_exact(codes, zero, None, 0, T, mpos, want[:-1], fpos)             # one code per marker
    with pytest.raises(ValueError):
        no.pack_projection(np.zeros((2, 6), dtype=np.uint8), np.zeros((2, 6), dtype=np.uint8))
    with pytest.raises(ValueError):
        no.pack_projection(np.zeros((2, 8), dtype=np.uint8), np.zeros((3, 8), dtype=np.uint8))
    assert no.pack_projection(np.zeros((0, 8), dtype=np.uint8), np.zeros((0, 8), dtype=np.uint8))[2].shape == (0, 2)


def test_c_rejects_inconsistent_arguments():
    import ctypes
    lib = no.KERNEL.lib()
    z = np.zeros(64, dtype=np.uint8)
    o = np.zeros(64, dtype=np.int64)
    fp = np.arange(8, dtype=np.int64)
    p = lambda a: a.ctypes.data  # noqa: E731
    args = lambda **kw: [kw.get("n", 1), p(z), 64, p(o), None, 0, kw.get("T", 16), 0, None, None, kw.get("F", 8),  # noqa: E731
                         p(fp), p(z), p(z), p(z), p(z), p(o)]
    assert lib.vnx_or_pack_exact(*args()) == 0
    assert lib.vnx_or_pack_exact(*args(n=-1)) == -1
    assert lib.vnx_or_pack_exact(*args(T=0)) == -1
    assert lib.vnx_or_pack_exact(*args(F=6)) == -1
    assert lib.vnx_or_pack_exact(*args(F=24)) == -1                           # F > T
    bad = np.array([60], dtype=np.int64)                                      # read past the buffer
    a = args(); a[3] = p(bad)
    assert lib.vnx_or_pack_exact(*a) == -1
    assert lib.vnx_or_pack_projection(1, p(z), p(z), 8, p(z), p(z), p(z), p(z)) == 0
    assert lib.vnx_or_pack_projection(1, p(z), p(z), 6, p(z), p(z), p(z), p(z)) == -1
    assert lib.vnx_or_pack_projection(-1, p(z), p(z), 8, p(z), p(z), p(z), p(z)) == -1
    assert lib.vnx_or_pack_projection(1, None, p(z), 8, p(z), p(z), p(z), p(z)) == -1
    assert isinstance(lib.vnx_or_pack_exact, ctypes._CFuncPtr)
