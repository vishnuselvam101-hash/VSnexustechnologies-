"""V13 D1: the bounded reverse-complement retry.

- the native bound (``native.orient.rc_bound``, orient ABI 2) equals ``pass1._rc_bound_reference`` on random reads;
- the bound is sound: it never exceeds the cost the marker-template DP returns for the reverse complement of a read
  (native and reference aligner, several layouts, bands and cost tables);
- ``_process`` with ``rc_retry="bounded"`` returns exactly the results of ``"always"`` (the V12 rule) on simulated
  reads of a real archive, and no read whose retry was skipped would have been adopted under the V12 rule;
- the option is validated, reaches the workers through ``decode_reads`` and is reported.
"""
from __future__ import annotations

import numpy as np
import pytest

from v5.native_support import LAYOUTS
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.native import orient as no
from vnxdna.native.reads import iter_reads
from vnxdna.recovery import pass1
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.probe import detect_layout
from vnxdna.sync.template import SyncCosts, TemplateAligner
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en

INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002}
pytestmark = pytest.mark.skipif(not no.available(), reason="native orientation kernel not built")


def _template(lay):
    tpl, _ = lay.template()
    mpos = np.flatnonzero(tpl >= 0)
    want = tpl[mpos].astype(np.uint8)
    starts = np.flatnonzero(np.diff(mpos, prepend=-2) > 1)
    return mpos, want, starts


def _reads(rng, lay, n, rc_rate=0.5):
    tpl, _ = lay.template()
    out = []
    for _ in range(n):
        r = np.where(tpl >= 0, tpl, rng.integers(0, 4, size=tpl.size)).astype(np.uint8)
        k = int(rng.integers(0, 10))
        pos = rng.integers(0, r.size, size=k)
        r[pos] = rng.integers(0, 5, size=k)
        for _k in range(int(rng.integers(0, 4))):
            p = int(rng.integers(0, r.size))
            r = np.delete(r, p) if rng.random() < 0.5 else np.insert(r, p, np.uint8(rng.integers(0, 4)))
        if rng.random() < 0.1:
            r = r[: int(rng.integers(0, r.size + 1))]
        if rng.random() < rc_rate:
            r = pass1._RC[r[::-1]]
        out.append(r)
    lengths = np.fromiter((r.size for r in out), dtype=np.int64, count=n)
    offs = np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64)
    return (np.concatenate(out) if n else np.zeros(0, dtype=np.uint8)), lengths, offs, out


@pytest.mark.parametrize("seed", range(8))
def test_native_bound_equals_the_reference(seed):
    rng = np.random.default_rng(1400 + seed)
    lay = LAYOUTS[sorted(LAYOUTS)[seed % len(LAYOUTS)]]
    mpos, want, starts = _template(lay)
    codes, lengths, offs, _ = _reads(rng, lay, 150)
    sel = rng.permutation(150)[: int(rng.integers(0, 151))]
    band = int(rng.integers(0, 9))
    c = [int(v) for v in rng.choice([0, 1, 4, 6, 7, 100, 65535], 3)]
    want_b = pass1._rc_bound_reference(codes, lengths[sel], offs[sel], mpos, want, starts, band, *c)
    got = no.rc_bound(codes, lengths[sel], offs[sel], mpos, want, starts, band, *c)
    assert got.dtype == want_b.dtype == np.int64 and np.array_equal(got, want_b)
    empty = np.zeros(0, dtype=np.int64)
    assert no.rc_bound(codes, empty, empty, mpos, want, starts, band, *c).size == 0
    if mpos.size == 0:
        assert not got.any()


@pytest.mark.parametrize("backend", ["native", "reference"])
@pytest.mark.parametrize("name", ["default-313nt", "short-178nt", "indel-profile", "dense-markers-8/6", "tiny-8/1"])
def test_bound_never_exceeds_the_dp_cost_of_the_reverse_complement(backend, name):
    lay = LAYOUTS[name]
    mpos, want, starts = _template(lay)
    rng = np.random.default_rng(hash(name) % 1000)
    for band, costs in ((6, SyncCosts()), (2, SyncCosts(1, 1, 1, 0, 0)), (8, SyncCosts(7, 3, 5, 2, 1)),
                        (3, SyncCosts(100, 6, 6, 1, 0))):
        al = TemplateAligner(lay, band, costs, backend=backend)
        codes, lengths, offs, reads = _reads(rng, lay, 120)
        bound = pass1._rc_bound_reference(codes, lengths, offs[:-1], mpos, want, starts, band, costs.marker_mismatch,
                                          costs.insertion, costs.deletion)
        pr = al.project([pass1._RC[r[::-1]] for r in reads])
        ok = pr.ok
        assert ok.any()
        assert (bound[ok] <= pr.cost[ok]).all(), f"{name} band {band} {costs}: bound above the DP cost"
        assert (bound >= 0).all()


@pytest.fixture(scope="module")
def archive_reads(tmp_path_factory):
    d = tmp_path_factory.mktemp("v13rc")
    datagen.generate(d / "in.bin", 100_000, "random", 13101)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    out = {"layout": detect_layout(d / "s.fasta", DecodeOptions()), "archive": d / "a.vnx"}
    for name, cfg in (("indel", INDEL), ("indel_rc", {**INDEL, "reverse_complement_rate": 0.5}),
                      ("heavy_rc", {"substitution_rate": 0.03, "insertion_rate": 0.01, "deletion_rate": 0.01,
                                    "reverse_complement_rate": 0.5})):
        path = d / f"{name}.fastq"
        ch.simulate_file(d / "s.fasta", path, ch.ChannelConfig(seed=9, coverage=2, **cfg))
        out[name] = path
    return out


def _run(path, lay, rc_retry, diag):
    opt = DecodeOptions()
    pass1._p_init(lay, opt.band, opt.sync_costs, 0, True, diag=diag, rc_retry=rc_retry)
    return [pass1._process(b.codes, b.lengths, None) for b in iter_reads(path, 2048)]


# the only values allowed to differ between the two retry rules: the skip count and the retry-attempt counter
_RETRY_ONLY = {"rc_retry_skipped", "orientation.rc_retry_attempted", "cpu_seconds"}


def _assert_same(a, b, path=""):
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) - _RETRY_ONLY == set(b) - _RETRY_ONLY, path
        for k in a:
            if k not in _RETRY_ONLY:
                _assert_same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _assert_same(x, y, f"{path}[{i}]")
    else:
        np.testing.assert_array_equal(np.asarray(a), np.asarray(b), err_msg=path)


@pytest.mark.parametrize("channel", ["indel", "indel_rc", "heavy_rc"])
def test_bounded_retry_decides_like_the_full_retry(archive_reads, channel):
    lay = archive_reads["layout"]
    bounded = _run(archive_reads[channel], lay, "bounded", True)
    always = _run(archive_reads[channel], lay, "always", True)
    skipped = sum(r["stats"]["rc_retry_skipped"] for r in bounded)
    assert sum(r["stats"]["rc_retry_skipped"] for r in always) == 0
    assert skipped > 0, "the channel must produce reads whose retry the bound settles"
    # the retry of a skipped read could only have been adopted through a verified reverse-complement frame: none was,
    # so the adopted orientations (diag rc_used), accepted frames, pending records and every counter but the number of
    # retries attempted are identical
    for b, a in zip(bounded, always):
        assert np.array_equal(a["diag"]["rc_used"], b["diag"]["rc_used"])
        assert a["diag"]["counts"]["orientation.rc_retry_adopted"] == b["diag"]["counts"]["orientation.rc_retry_adopted"]
        assert (a["diag"]["counts"].get("orientation.rc_retry_attempted", 0)
                - b["diag"]["counts"].get("orientation.rc_retry_attempted", 0) == b["stats"]["rc_retry_skipped"])
    _assert_same(bounded, always)
    if channel != "indel":
        assert sum(r["stats"]["reverse_complement"] for r in bounded) > 0


def test_always_is_the_v12_rule_and_other_configurations_do_not_use_the_bound(archive_reads, monkeypatch):
    lay = archive_reads["layout"]
    calls = []
    orig = pass1._rc_bound
    monkeypatch.setattr(pass1, "_rc_bound", lambda *a: calls.append(1) or orig(*a))
    _run(archive_reads["indel"], lay, "always", False)
    assert not calls
    _run(archive_reads["indel"], lay, "bounded", False)
    assert calls
    calls.clear()
    monkeypatch.setattr(pass1, "_FLAT_PATH", False)             # the list path (smart/soft, quality weighting) never
    _run(archive_reads["indel"], lay, "bounded", False)
    assert not calls


def test_option_validation_and_end_to_end(archive_reads, tmp_path):
    with pytest.raises(VNXConfigurationError, match="orientation_retry"):
        DecodeOptions(orientation_retry="never").validate()
    outs = {}
    for mode in ("bounded", "always"):
        res = de.decode_reads(archive_reads["indel_rc"], tmp_path / f"{mode}.vnx",
                              de.DecodeOptions(workers=2, orientation_retry=mode))
        assert res.status == "SUCCESS"
        outs[mode] = ((tmp_path / f"{mode}.vnx").read_bytes(), res.report["reads"])
    assert outs["bounded"][0] == outs["always"][0] == archive_reads["archive"].read_bytes()
    assert outs["bounded"][1]["rc_retry_skipped"] > 0 and outs["always"][1]["rc_retry_skipped"] == 0
    a, b = outs["bounded"][1], outs["always"][1]
    assert {k: v for k, v in a.items() if k != "rc_retry_skipped"} == {k: v for k, v in b.items() if k != "rc_retry_skipped"}


def test_kernel_argument_checks():
    lay = PROFILES["v4-balanced"][0]
    mpos, want, starts = _template(lay)
    codes = np.zeros(lay.strand_nt, dtype=np.uint8)
    one = np.array([lay.strand_nt], dtype=np.int64)
    zero = np.zeros(1, dtype=np.int64)
    assert no.rc_bound(codes, one, zero, mpos, want, starts, 6, 4, 6, 6).shape == (1,)
    with pytest.raises(ValueError):
        no.rc_bound(codes, one, zero + 1, mpos, want, starts, 6, 4, 6, 6)        # read past the buffer
    with pytest.raises(ValueError):
        no.rc_bound(codes, one, zero, mpos, want, starts, -1, 4, 6, 6)           # negative band
    with pytest.raises(ValueError):
        no.rc_bound(codes, one, np.zeros(2, dtype=np.int64), mpos, want, starts, 6, 4, 6, 6)   # offs/lengths mismatch
    assert no.KERNEL.lib().vnx_or_abi_version() == no.ABI_VERSION == 2
