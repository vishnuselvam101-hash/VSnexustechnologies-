"""V13 B1: marker-anchored speculative projection of the sync path.

- the native kernel (``native.orient.spec_project``, orient ABI 3) equals ``pass1._spec_project_reference`` bit for bit
  on every layout, with and without qualities, for clean, substituted, indel, reverse-complemented, truncated,
  N-carrying and random junk reads; golden hashes pin fixed batches for both backends;
- the projection is a specification of its own (shift choice, mismatch tolerance, region erasures, quality erasures),
  checked on hand-built reads;
- in pass 1 the speculative path only adds acceptances: on simulated reads of a real archive every read accepted with
  ``sync_speculation="off"`` (the DP for every read) is accepted with ``"markers"`` with the same fields and payload,
  and the pending records, container bytes and every stat except the sync/speculative/pending/orphan split agree;
- the option is validated, reaches the workers through ``decode_reads`` and is reported; the C argument checks hold.

SIMULATED reads (synthetic strands of each layout's template with random payload bases, mutated in NumPy, or the
channel simulator on an encoded archive).
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.layout import PROFILES, Layout
from vnxdna.native import orient as no
from vnxdna.native.reads import iter_reads
from vnxdna.recovery import pass1
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.probe import detect_layout
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en

LAYOUTS = sorted({lay for lay, _, _ in PROFILES.values()}, key=repr) + [Layout(20, 0, 8, 2), Layout(12, 2, 0, 0)]
BALANCED = PROFILES["v4-balanced"][0]
INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002}
pytestmark = pytest.mark.skipif(not no.available(), reason="native orientation kernel not built")
#: sha256 over (ok, bases, erased, mm, indels) of ``_batch(BALANCED, seed, 1500)`` at band 6 without qualities, for
#: both backends (pinned at the first native build of ABI 3)
GOLDEN = {
    201: "2b9304390c8123f50b38f93dc43cf21dc655c5474262644e3beb1fd21d0e21a4",
    202: "99956dc4c51c7ce9aa92f83b2062b1faecad7129c8adff8d2a97a7b22e27f6fb",
}


def _template(lay):
    tpl, fpos = lay.template()
    mpos = np.flatnonzero(tpl >= 0)
    want = tpl[mpos].astype(np.uint8)
    starts = np.flatnonzero(np.diff(mpos, prepend=-2) > 1)
    return tpl, fpos, mpos, want, starts


def _batch(lay: Layout, seed: int, n: int, sub: float = 0.01, ind: float = 0.004, rc: float = 0.3, junk: float = 0.1):
    rng = np.random.default_rng(seed)
    tpl, _ = lay.template()
    marker = tpl >= 0
    reads = []
    for _ in range(n):
        if rng.random() < junk:
            r = rng.integers(0, 5, rng.integers(0, 2 * tpl.size + 1)).astype(np.uint8)
        else:
            r = rng.integers(0, 4, tpl.size).astype(np.uint8)
            r[marker] = tpl[marker]
            r = r[rng.random(r.size) >= ind / 2]
            r = np.where(rng.random(r.size) < sub, rng.integers(0, 4, r.size), r).astype(np.uint8)
            ins = np.flatnonzero(rng.random(r.size) < ind / 2)
            r = np.insert(r, ins, rng.integers(0, 4, ins.size).astype(np.uint8))
            if rng.random() < rc:
                r = pass1._RC[r[::-1]]
            if rng.random() < 0.05:
                r = r[: rng.integers(0, r.size + 1)]
            if rng.random() < 0.05 and r.size:
                r[rng.integers(0, r.size, 3)] = 4
        reads.append(r)
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    offs = np.concatenate([[0], np.cumsum(lengths)])[:-1].astype(np.int64)
    return (np.concatenate(reads) if n else np.zeros(0, dtype=np.uint8)), lengths, offs


def _both(lay, codes, lengths, offs, q, mq, band):
    tpl, fpos, mpos, want, starts = _template(lay)
    ref = pass1._spec_project_reference(codes, lengths, offs, q, mq, tpl.size, mpos, want, starts, fpos, band)
    nat = no.spec_project(codes, lengths, offs, q, mq, tpl.size, mpos, want, starts, fpos, band)
    return ref, nat


def _assert_equal(ref, nat):
    assert len(ref) == len(nat) == 5
    for name, a, b in zip(("ok", "bases", "erased", "mm", "indels"), ref, nat):
        assert a.dtype == b.dtype and a.shape == b.shape, name
        np.testing.assert_array_equal(a, b, err_msg=name)
    assert ref[0].dtype == bool and ref[2].dtype == bool and ref[1].dtype == np.uint8 and ref[3].dtype == np.int64


@pytest.mark.parametrize("layout", LAYOUTS, ids=repr)
@pytest.mark.parametrize("quals", [False, True])
def test_native_equals_reference_on_every_layout(layout, quals):
    rng = np.random.default_rng(hash((repr(layout), quals)) & 0xFFFF)
    codes, lengths, offs = _batch(layout, int(rng.integers(0, 1 << 30)), 400, sub=0.02, ind=0.01)
    q = rng.integers(0, 60, codes.size).astype(np.uint8) if quals else None
    sel = rng.permutation(400)[: int(rng.integers(1, 401))]
    for band in (0, 2, 6, 9):
        ref, nat = _both(layout, codes, lengths[sel], offs[sel], q, 20 if quals else 0, band)
        _assert_equal(ref, nat)
    if layout.marker_period and layout.marker_len:
        ref, _ = _both(layout, codes, lengths, offs, None, 0, 6)
        assert 0 < ref[0].sum() < lengths.size, "the batch must contain projected and unprojected reads"


@pytest.mark.parametrize("seed", range(12))
def test_random_junk_batches_agree(seed):
    rng = np.random.default_rng(900 + seed)
    codes, lengths, offs = _batch(BALANCED, 7000 + seed, 300, sub=0.05, ind=0.05, rc=0.5, junk=0.5)
    q = rng.integers(0, 60, codes.size).astype(np.uint8)
    _assert_equal(*_both(BALANCED, codes, lengths, offs, q, int(rng.integers(0, 50)), int(rng.integers(0, 13))))
    _assert_equal(*_both(BALANCED, codes, lengths, offs, None, 0, 6))


@pytest.mark.parametrize("seed", sorted(GOLDEN))
def test_golden_hashes(seed):
    codes, lengths, offs = _batch(BALANCED, seed, 1500)
    ref, nat = _both(BALANCED, codes, lengths, offs, None, 0, 6)
    for out in (ref, nat):
        h = hashlib.sha256()
        for a in out:
            h.update(np.ascontiguousarray(a).tobytes())
        assert h.hexdigest() == GOLDEN[seed]


def test_empty_selection_and_marker_free_layout():
    empty = np.zeros(0, dtype=np.int64)
    codes = np.zeros(10, dtype=np.uint8)
    ref, nat = _both(BALANCED, codes, empty, empty, None, 0, 6)
    _assert_equal(ref, nat)
    assert ref[0].size == 0 and ref[1].shape == (0, BALANCED.frame_nt)
    lay = Layout(12, 2, 0, 0)        # no markers: one region, erased wherever the length differs from the template
    tpl, fpos = lay.template()
    T = tpl.size
    one = np.array([T], dtype=np.int64)
    two = np.array([T - 1], dtype=np.int64)
    zero = np.zeros(1, dtype=np.int64)
    codes = np.arange(T, dtype=np.uint8) % 4
    ref, nat = _both(lay, codes, one, zero, None, 0, 6)
    _assert_equal(ref, nat)
    assert ref[0][0] and not ref[2][0].any() and ref[4][0] == 0 and np.array_equal(ref[1][0], codes[fpos])
    ref, nat = _both(lay, codes, two, zero, None, 0, 6)
    _assert_equal(ref, nat)
    assert ref[0][0] and ref[2][0].all() and ref[4][0] == 1


def test_projection_rules_on_hand_built_reads():
    lay = BALANCED
    tpl, fpos, mpos, want, starts = _template(lay)
    T = tpl.size
    rng = np.random.default_rng(3)
    clean = np.where(tpl >= 0, tpl, rng.integers(0, 4, T)).astype(np.uint8)
    # 1. the exact strand: shift 0 everywhere, nothing erased, every frame base copied
    ok, bases, er, mm, ind = pass1._spec_project_reference(clean, np.array([T]), np.array([0]), None, 0, T, mpos, want,
                                                           starts, fpos, 6)
    assert ok[0] and not er[0].any() and mm[0] == 0 and ind[0] == 0 and np.array_equal(bases[0], clean[fpos])
    # 2. one deletion inside region 3 (between marker blocks 2 and 3): that region is erased, nothing else; one indel
    pos = int(mpos[starts[2]] + 2) + 5
    r = np.delete(clean, pos)
    ok, bases, er, mm, ind = pass1._spec_project_reference(r, np.array([T - 1]), np.array([0]), None, 0, T, mpos, want,
                                                           starts, fpos, 6)
    region = np.searchsorted(mpos[starts], fpos, side="right")
    assert ok[0] and mm[0] == 0 and ind[0] == 1
    assert er[0][region == 3].all() and not er[0][region != 3].any()
    assert np.array_equal(bases[0][region != 3], clean[fpos][region != 3])
    # 3. one insertion in region 0 and one deletion in region 5: both regions erased, two indels, the other bases right
    r = np.insert(clean, 3, np.uint8(1))
    p5 = int(mpos[starts[4]] + 2) + 4 + 1
    r = np.delete(r, p5)
    ok, bases, er, mm, ind = pass1._spec_project_reference(r, np.array([T]), np.array([0]), None, 0, T, mpos, want,
                                                           starts, fpos, 6)
    assert ok[0] and ind[0] == 2 and set(np.unique(region[er[0]])) == {0, 5}
    assert np.array_equal(bases[0][~er[0]], clean[fpos][~er[0]])
    # 4. a single marker mismatch keeps the shift and is counted; two in one block refuse the read
    r = clean.copy()
    r[mpos[starts[6]]] = (r[mpos[starts[6]]] + 1) % 4
    ok, bases, er, mm, ind = pass1._spec_project_reference(r, np.array([T]), np.array([0]), None, 0, T, mpos, want,
                                                           starts, fpos, 6)
    assert ok[0] and mm[0] == 1 and not er[0].any()
    r[mpos[starts[6] + 1]] = (r[mpos[starts[6] + 1]] + 1) % 4
    ok, *_ = pass1._spec_project_reference(r, np.array([T]), np.array([0]), None, 0, T, mpos, want, starts, fpos, 6)
    assert not ok[0]
    # 5. N bases and low qualities are erased where they land; a length outside the band is not projected
    r = clean.copy()
    r[fpos[10]] = 4
    q = np.full(T, 30, dtype=np.uint8)
    q[fpos[11]] = 5
    ok, bases, er, mm, ind = pass1._spec_project_reference(r, np.array([T]), np.array([0]), q, 10, T, mpos, want,
                                                           starts, fpos, 6)
    assert ok[0] and bases[0][10] == 4 and er[0][10] and er[0][11] and er[0].sum() == 2
    ok, bases, er, mm, ind = pass1._spec_project_reference(clean[: T - 7], np.array([T - 7]), np.array([0]), None, 0, T,
                                                           mpos, want, starts, fpos, 6)
    assert not ok[0] and er[0].all() and (bases[0] == 4).all() and mm[0] == 0 and ind[0] == 0


@pytest.fixture(scope="module")
def archive_reads(tmp_path_factory):
    d = tmp_path_factory.mktemp("v13spec")
    datagen.generate(d / "in.bin", 100_000, "random", 13102)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    out = {"layout": detect_layout(d / "s.fasta", DecodeOptions()), "archive": d / "a.vnx"}
    for name, cfg in (("indel", INDEL), ("indel_rc", {**INDEL, "reverse_complement_rate": 0.5}),
                      ("heavy", {"substitution_rate": 0.03, "insertion_rate": 0.01, "deletion_rate": 0.01})):
        path = d / f"{name}.fastq"
        ch.simulate_file(d / "s.fasta", path, ch.ChannelConfig(seed=11, coverage=2, **cfg))
        out[name] = path
    return out


def _run(path, lay, spec, diag=False):
    opt = DecodeOptions()
    pass1._p_init(lay, opt.band, opt.sync_costs, 0, True, diag=diag, spec=spec)
    return [pass1._process(b.codes, b.lengths, None) for b in iter_reads(path, 2048)]


@pytest.mark.parametrize("channel", ["indel", "indel_rc", "heavy"])
def test_speculation_only_adds_acceptances(archive_reads, channel):
    lay = archive_reads["layout"]
    spec = _run(archive_reads[channel], lay, "markers")
    dp = _run(archive_reads[channel], lay, "off")
    n_spec = sum(r["stats"]["speculative"] for r in spec)
    assert n_spec > 0 and all(r["stats"]["speculative"] == 0 for r in dp)
    for a, b in zip(spec, dp):
        ia, ib = a["acc_index"], b["acc_index"]
        assert np.isin(ib, ia).all(), "a read the DP path accepted is accepted on the speculative path"
        common = np.isin(ia, ib)
        pos_b = np.searchsorted(ib, ia[common])
        np.testing.assert_array_equal(a["acc_fields"][common], b["acc_fields"][pos_b])
        np.testing.assert_array_equal(a["acc_payload"][common], b["acc_payload"][pos_b])
        # the reads only the speculative path accepted are no longer pending (same address fields were tentative)
        assert a["stats"]["pending"] <= b["stats"]["pending"]
        assert a["stats"]["fast"] == b["stats"]["fast"] and a["stats"]["reads"] == b["stats"]["reads"]
        assert a["stats"]["sync"] >= b["stats"]["sync"] and a["stats"]["sync"] >= a["stats"]["speculative"]
        assert a["stats"]["unaligned"] <= b["stats"]["unaligned"]


def test_off_never_calls_the_projection_and_other_configurations_do_not_use_it(archive_reads, monkeypatch):
    lay = archive_reads["layout"]
    calls = []
    orig = pass1._spec_project
    monkeypatch.setattr(pass1, "_spec_project", lambda *a: calls.append(1) or orig(*a))
    _run(archive_reads["indel"], lay, "off")
    assert not calls
    _run(archive_reads["indel"], lay, "markers")
    assert calls
    calls.clear()
    monkeypatch.setattr(pass1, "_FLAT_PATH", False)             # the list path (smart/soft, quality weighting) never
    _run(archive_reads["indel"], lay, "markers")
    assert not calls


def test_stage_counters_account_for_speculative_reads(archive_reads):
    lay = archive_reads["layout"]
    out = _run(archive_reads["indel"], lay, "markers", diag=True)
    verified = sum(r["diag"]["counts"]["inner_ecc.verified_speculative"] for r in out)
    assert verified == sum(r["stats"]["speculative"] for r in out) > 0
    for r in out:
        c = r["diag"]["counts"]
        assert (c["inner_ecc.verified_fast"] + c["inner_ecc.verified_sync"] + c["inner_ecc.verified_speculative"]
                == r["acc_index"].size)


def test_option_validation_and_end_to_end(archive_reads, tmp_path):
    with pytest.raises(VNXConfigurationError, match="sync_speculation"):
        DecodeOptions(sync_speculation="dp").validate()
    outs = {}
    # the per-read invariants below are those of the V13 path, which attempts every read (the V13 streaming engine
    # attempts only the reads its address scheduler selects, so its counts differ between the two modes)
    for mode in ("markers", "off"):
        res = de.decode_reads(archive_reads["indel_rc"], tmp_path / f"{mode}.vnx",
                              de.DecodeOptions(workers=2, sync_speculation=mode, engine="v13"))
        assert res.status == "SUCCESS" and res.report["engine"]["rung"] == "v13"
        outs[mode] = ((tmp_path / f"{mode}.vnx").read_bytes(), res.report["reads"])
    assert outs["markers"][0] == outs["off"][0] == archive_reads["archive"].read_bytes()
    a, b = outs["markers"][1], outs["off"][1]
    assert a["speculative"] > 0 and b["speculative"] == 0
    assert a["sync"] >= b["sync"] and a["pending"] <= b["pending"] and a["reads"] == b["reads"] and a["fast"] == b["fast"]
    # the default ladder (the engine when it is built) decodes both modes to the same bytes
    for mode in ("markers", "off"):
        res = de.decode_reads(archive_reads["indel_rc"], tmp_path / f"auto-{mode}.vnx",
                              de.DecodeOptions(workers=2, sync_speculation=mode))
        assert res.status == "SUCCESS" and (tmp_path / f"auto-{mode}.vnx").read_bytes() == outs["off"][0]


def test_kernel_argument_checks():
    tpl, fpos, mpos, want, starts = _template(BALANCED)
    T = tpl.size
    codes = np.zeros(T, dtype=np.uint8)
    one = np.array([T], dtype=np.int64)
    zero = np.zeros(1, dtype=np.int64)
    ok, bases, er, mm, ind = no.spec_project(codes, one, zero, None, 0, T, mpos, want, starts, fpos, 6)
    assert ok.shape == (1,) and bases.shape == (1, fpos.size)
    with pytest.raises(ValueError):
        no.spec_project(codes, one, zero + 1, None, 0, T, mpos, want, starts, fpos, 6)      # read past the buffer
    with pytest.raises(ValueError):
        no.spec_project(codes, one, zero, np.zeros(3, dtype=np.uint8), 0, T, mpos, want, starts, fpos, 6)
    with pytest.raises(ValueError):
        no.spec_project(codes, one, zero, None, 0, T, mpos, want, starts, fpos, -1)
    with pytest.raises(ValueError):
        no.spec_project(codes, one, zero, None, 0, T, mpos, want, starts, fpos + T, 6)
    with pytest.raises(ValueError):
        no.spec_project(codes, one, zero, None, 0, T - 1, mpos, want, starts, fpos, 6)      # markers beyond T
    with pytest.raises(ValueError):
        no.spec_project(codes, one, np.zeros(2, dtype=np.int64), None, 0, T, mpos, want, starts, fpos, 6)
