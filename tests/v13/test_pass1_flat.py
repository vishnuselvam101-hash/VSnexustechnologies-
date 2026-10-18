"""V13 C1/C2: the flat pass-1 data path (no per-read view lists) is pinned to the V12 list path it replaces.

- ``native.align.align_usable_flat`` (ABI 4 selected-reads entry) returns the arrays of ``align_usable`` on the same
  reads as lists, in any selection order, with and without qualities;
- ``TemplateAligner.project_flat`` equals ``project`` on the native and the reference backend, with the retry band;
- ``pass1._rc_flat`` equals the per-read reverse-complement loop;
- ``pass1._process`` returns identical results with ``_FLAT_PATH`` on and off on simulated reads of a real archive
  (clean, indel, reverse complement, with a quality threshold and with stage counters);
- the flat entry refuses reads outside the buffer (Python domain check and the kernel's own check);
- the flat-only ``_try`` refuses configurations the flat path does not cover.
"""
from __future__ import annotations

import ctypes

import numpy as np
import pytest

from v5.native_support import LAYOUTS, strand
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.native import align as na
from vnxdna.native.reads import iter_reads
from vnxdna.recovery import pass1
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.probe import detect_layout
from vnxdna.sync.template import SyncCosts, TemplateAligner
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import encoder as en

FIELDS = ("bases", "erased", "ok", "insertions", "deletions", "marker_mismatches", "cost")
INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002}
pytestmark = pytest.mark.skipif(not na.available(), reason="native aligner not built")


def _reads(layout, rng, n):
    """Strands with substitutions, indels (0–12), junk, reverse complements and N codes, as the aligner fuzz does."""
    T = layout.strand_nt
    out = []
    for _ in range(n):
        s = strand(layout, rng)
        kind = int(rng.integers(0, 8))
        if kind == 0:
            out.append(rng.integers(0, 4, max(0, T + int(rng.integers(-30, 31)))).astype(np.uint8))
            continue
        for _ in range(int(rng.integers(0, 3 + 10 * (kind >= 4)))):
            op = int(rng.integers(0, 3))
            if op == 0 and s.size:
                p = int(rng.integers(0, s.size))
                s = s.copy()
                s[p] = (s[p] + int(rng.integers(1, 4))) % 4
            elif op == 1:
                s = np.insert(s, int(rng.integers(0, s.size + 1)), np.uint8(rng.integers(0, 4)))
            elif s.size:
                s = np.delete(s, int(rng.integers(0, s.size)))
        if kind == 5:
            s = (3 - s[::-1]).astype(np.uint8)
        if kind == 6 and s.size:
            s = s.copy()
            s[rng.integers(0, s.size, 3)] = 4
        out.append(s.astype(np.uint8))
    return out


def _flat(reads, quals):
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=len(reads))
    offs = np.zeros(len(reads) + 1, dtype=np.int64)
    np.cumsum(lengths, out=offs[1:])
    codes = np.concatenate(reads) if reads else np.zeros(0, dtype=np.uint8)
    qflat = None if quals is None else (np.concatenate(quals) if quals else np.zeros(0, dtype=np.uint8))
    return codes, offs, lengths, qflat


def _same(got, want):
    assert len(got) == len(want)
    for a, b in zip(got, want):
        a, b = np.asarray(a), np.asarray(b)
        assert a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b)


@pytest.mark.parametrize("seed", range(6))
def test_align_usable_flat_matches_the_list_form(seed):
    rng = np.random.default_rng(1300 + seed)
    name = sorted(LAYOUTS)[seed % len(LAYOUTS)]
    layout = LAYOUTS[name]
    band = int(rng.choice([0, 2, 6, 12]))
    costs = SyncCosts(*(int(v) for v in rng.choice([0, 1, 4, 6, 100], 4)), int(rng.integers(0, 3)))
    al = TemplateAligner(layout, band, costs, backend="native")
    reads = _reads(layout, rng, 300)
    with_q = seed % 2 == 1
    quals = [rng.integers(0, 60, r.size).astype(np.uint8) for r in reads] if with_q else None
    minq = int(rng.integers(0, 40)) if with_q else 0
    codes, offs, lengths, qflat = _flat(reads, quals)
    usable = np.flatnonzero(np.abs(lengths - layout.strand_nt) <= band)
    sel = rng.permutation(usable)                          # any order, any subset
    want = na.align_usable(al, [reads[i] for i in sel], None if quals is None else [quals[i] for i in sel], minq)
    got = na.align_usable_flat(al, codes, offs[sel], lengths[sel], qflat, minq)
    assert want is not None and got is not None
    _same(got, want)
    # a single read, and no read at all
    one = sel[:1]
    _same(na.align_usable_flat(al, codes, offs[one], lengths[one], qflat, minq),
          na.align_usable(al, [reads[i] for i in one], None if quals is None else [quals[i] for i in one], minq))
    empty = np.zeros(0, dtype=np.int64)
    _same(na.align_usable_flat(al, codes, empty, empty, qflat, minq), na.align_usable(al, [], None if quals is None else [], minq))


@pytest.mark.parametrize("backend", ["native", "reference"])
@pytest.mark.parametrize("retry_band", [0, 12])
def test_project_flat_matches_project(backend, retry_band):
    rng = np.random.default_rng(77 + retry_band)
    layout = PROFILES["v4-balanced"][0]
    al = TemplateAligner(layout, 6, SyncCosts(), backend=backend, retry_band=retry_band)
    reads = _reads(layout, rng, 200)
    quals = [rng.integers(0, 60, r.size).astype(np.uint8) for r in reads]
    codes, offs, lengths, qflat = _flat(reads, quals)
    sel = rng.permutation(len(reads))[:150]
    for q, qf, minq in ((None, None, 0), (quals, qflat, 20)):
        want = al.project([reads[i] for i in sel], None if q is None else [q[i] for i in sel], minq)
        got = al.project_flat(codes, offs, lengths, sel, qf, minq)
        _same([getattr(got, f) for f in FIELDS], [getattr(want, f) for f in FIELDS])
    got = al.project_flat(codes, offs, lengths, np.zeros(0, dtype=np.int64), qflat, 0)
    assert got.bases.shape == (0, layout.frame_nt) and got.cost.size == 0


@pytest.mark.parametrize("seed", range(3))
def test_rc_flat_matches_the_loop(seed):
    rng = np.random.default_rng(500 + seed)
    reads = [rng.integers(0, 5, int(rng.integers(0, 40))).astype(np.uint8) for _ in range(50)]
    quals = [rng.integers(0, 60, r.size).astype(np.uint8) for r in reads]
    codes, offs, lengths, qflat = _flat(reads, quals)
    sel = rng.permutation(50)[:30]
    rcodes, rq, roffs, rl = pass1._rc_flat(codes, qflat, offs, lengths, sel)
    want = [pass1._RC[reads[i][::-1]] for i in sel]
    wq = [quals[i][::-1] for i in sel]
    assert np.array_equal(rl, lengths[sel]) and roffs[0] == 0 and np.array_equal(np.diff(roffs), rl)
    for j in range(sel.size):
        assert np.array_equal(rcodes[roffs[j]:roffs[j + 1]], want[j])
        assert np.array_equal(rq[roffs[j]:roffs[j + 1]], wq[j])
    assert pass1._rc_flat(codes, None, offs, lengths, sel)[1] is None
    empty = pass1._rc_flat(codes, qflat, offs, lengths, np.zeros(0, dtype=np.int64))
    assert empty[0].size == 0 and empty[1].size == 0 and empty[2].tolist() == [0]


@pytest.fixture(scope="module")
def archive_reads(tmp_path_factory):
    d = tmp_path_factory.mktemp("v13flat")
    datagen.generate(d / "in.bin", 120_000, "random", 13001)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    out = {"layout": detect_layout(d / "s.fasta", DecodeOptions())}
    for name, cfg in (("clean", {}), ("indel", INDEL), ("indel_rc", {**INDEL, "reverse_complement_rate": 0.5}),
                      ("heavy", {"substitution_rate": 0.03, "insertion_rate": 0.01, "deletion_rate": 0.01})):
        path = d / f"{name}.fastq"
        ch.simulate_file(d / "s.fasta", path, ch.ChannelConfig(seed=7, coverage=2, **cfg))
        out[name] = path
    return out


def _run(path, quals_wanted: bool):
    res = []
    for b in iter_reads(path, 2048):
        res.append(pass1._process(b.codes, b.lengths, b.quals if quals_wanted else None))
    return res


def _assert_same_outputs(a, b):
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert set(x) == set(y)
        for k in x:
            if k == "cpu_seconds":
                continue
            if isinstance(x[k], dict):
                for kk in set(x[k]) | set(y[k]):
                    np.testing.assert_array_equal(np.asarray(x[k][kk]), np.asarray(y[k][kk]), err_msg=f"{k}.{kk}")
            else:
                assert np.asarray(x[k]).dtype == np.asarray(y[k]).dtype, k
                np.testing.assert_array_equal(np.asarray(x[k]), np.asarray(y[k]), err_msg=k)


@pytest.mark.parametrize("channel", ["clean", "indel", "indel_rc", "heavy"])
@pytest.mark.parametrize("min_q,diag", [(0, False), (15, False), (0, True)])
def test_process_flat_path_equals_the_list_path(archive_reads, channel, min_q, diag, monkeypatch):
    opt = DecodeOptions()
    lay = archive_reads["layout"]
    monkeypatch.setattr(pass1, "_FLAT_PATH", True)
    # C1/C2 only: the V12 retry rule and the DP for every read on both paths (the bounded retry and the speculative
    # projection are flat-path features, pinned by test_rc_retry_bound.py and test_spec_projection.py)
    pass1._p_init(lay, opt.band, opt.sync_costs, min_q, True, diag=diag, rc_retry="always", spec="off")
    flat = _run(archive_reads[channel], min_q > 0)
    monkeypatch.setattr(pass1, "_FLAT_PATH", False)
    pass1._p_init(lay, opt.band, opt.sync_costs, min_q, True, diag=diag, rc_retry="always", spec="off")
    lists = _run(archive_reads[channel], min_q > 0)
    _assert_same_outputs(flat, lists)
    stats = sum((x["stats"]["fast"] + x["stats"]["sync"] for x in flat), 0)
    assert stats > 0, "the batches must contain accepted reads"
    if channel == "indel_rc":
        assert sum(x["stats"]["reverse_complement"] for x in flat) > 0


def test_process_without_reverse_complement_and_with_smart_recovery_still_agree(archive_reads, monkeypatch):
    """rc=False (no orientation pass, no retry) on the flat path; smart recovery keeps the list path whatever the flag."""
    from vnxdna.sync.smart.recovery import IndelRecoveryConfig
    opt = DecodeOptions()
    lay = archive_reads["layout"]
    for flag in (True, False):
        monkeypatch.setattr(pass1, "_FLAT_PATH", flag)
        pass1._p_init(lay, opt.band, opt.sync_costs, 0, False, spec="off")
        res = _run(archive_reads["indel"], False)
        if flag:
            keep = res
        else:
            _assert_same_outputs(keep, res)
    smart = []
    for flag in (True, False):
        monkeypatch.setattr(pass1, "_FLAT_PATH", flag)
        pass1._p_init(lay, opt.band, opt.sync_costs, 0, True, IndelRecoveryConfig())
        smart.append(_run(archive_reads["indel"], True))
    _assert_same_outputs(smart[0], smart[1])


def test_flat_entry_refuses_reads_outside_the_buffer():
    layout = PROFILES["v4-balanced"][0]
    al = TemplateAligner(layout, 6, SyncCosts(), backend="native")
    rng = np.random.default_rng(3)
    reads = [strand(layout, rng) for _ in range(4)]
    codes, offs, lengths, _ = _flat(reads, None)
    assert na.align_usable_flat(al, codes, offs[:4], lengths, None, 0) is not None
    bad_start = offs[:4].copy()
    bad_start[3] += 1                                       # the last read would run past the buffer
    assert na.align_usable_flat(al, codes, bad_start, lengths, None, 0) is None
    assert na.align_usable_flat(al, codes, offs[:4] - 1, lengths, None, 0) is None          # negative start
    assert na.align_usable_flat(al, codes.astype(np.int16), offs[:4], lengths, None, 0) is None   # wrong dtype
    assert na.align_usable_flat(al, codes, offs[:4], lengths, np.zeros(3, dtype=np.uint8), 0) is None  # quals size
    # the kernel's own check, bypassing the Python domain check: a read past total_len is VNX_E_READ (-3)
    lib = na._load()
    g = na.geometry(al)
    n, T = 1, layout.strand_nt
    fn = lib.vnx_align_batch_sel_lv
    out = [np.zeros((n, layout.frame_nt), dtype=np.uint8), np.zeros((n, layout.frame_nt), dtype=np.uint8),
           np.zeros(n, dtype=np.uint8)] + [np.zeros(n, dtype=np.int64) for _ in range(4)]
    c = al.costs
    starts = np.array([1], dtype=np.int64)
    lens = np.array([T], dtype=np.int64)
    ptr = na._ptr
    args = (n, ptr(codes[:T]), ptr(starts), ptr(lens), T, None, None, al.T, ptr(g["tpl"]), ptr(g["seg_of"]),
            ptr(g["prev_seg"]), ptr(g["next_seg"]), al.n_segments, layout.frame_nt, ptr(g["frame_pos"]), ptr(g["seg_frame"]),
            al.band, c.marker_mismatch, c.insertion, c.deletion, c.marker_deletion_extra, c.guard_segments, 0,
            *(ptr(o) for o in out), 4)
    assert fn(*args) == -3
    starts[0] = 0
    assert fn(*args) == 0
    assert fn(0, None, None, None, 0, *args[5:]) == 0        # ABI 4: n == 0 needs no lens ...
    assert fn(1, ptr(codes[:T]), ptr(starts), None, T, *args[5:]) == -1    # ... n > 0 without lens is VNX_E_ARG


def test_flat_only_try_refuses_other_configurations():
    from vnxdna.sync.smart.recovery import IndelRecoveryConfig
    layout = PROFILES["v4-balanced"][0]
    opt = DecodeOptions()
    pass1._p_init(layout, opt.band, opt.sync_costs, 0, True, IndelRecoveryConfig())
    codes = np.zeros(layout.strand_nt, dtype=np.uint8)
    flat = (codes, None, np.array([0, codes.size], dtype=np.int64), np.array([codes.size], dtype=np.int64))
    with pytest.raises(ValueError, match="default recovery configuration"):
        pass1._try(None, None, flat=flat)
    pass1._p_init(layout, opt.band, opt.sync_costs, 0, True)
    assert len(pass1._try(None, None, flat=flat)) == 8


def test_abi4_entry_is_bound_and_the_list_entry_still_runs():
    lib = na._load()
    assert lib.vnx_align_abi_version() == 4 == na.ABI_VERSION
    assert isinstance(lib.vnx_align_batch_sel_lv.argtypes[3], type(ctypes.c_void_p))
    layout = PROFILES["v4-balanced"][0]
    al = TemplateAligner(layout, 6, SyncCosts(), backend="native")
    rng = np.random.default_rng(9)
    reads = [strand(layout, rng) for _ in range(3)]
    got = na.align_usable(al, reads, None, 0)
    assert got is not None and got[2].all()
