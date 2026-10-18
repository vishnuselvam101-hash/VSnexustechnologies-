"""V14 Phase 6 workstream A: linear-time shuffled-pool stream decoding (vnxdna.pipeline.decode.PoolDecoder).

The pool path of :func:`vnxdna.pipeline.stream.decode_stream` now runs the layout probe and pass 1 ONCE for the whole
shuffled pool and decodes each extent from its own archive-tag partition, instead of re-running a whole-pool decode per
extent. These tests pin that path to the legacy per-extent path:

- byte-for-byte output equivalence (decode_stream, and per-extent containers) for E in {1, 2, 4, 8}, random shuffles,
  duplicated/dropped/corrupted reads, reverse-complement reads, several archives (incl. a foreign archive in the pool),
  empty streams and a partial final extent;
- per-extent DecodeResult-report equivalence (the linear partition decode vs the whole-pool decode of the same tag);
- determinism across worker counts;
- the manifest's duplicate-archive-tag refusal is preserved.

Every read here is SIMULATED (vnxdna.simulation.channel).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import replace

import numpy as np
import pytest

from vnxdna.core.errors import VNXError, VNXFormatError
from vnxdna.pipeline import stream as st
from vnxdna.pipeline import decode as dc
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.outer import _pass2
from vnxdna.v4 import channel as ch

KIB = 1 << 10


def _data(n: int, seed: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8).tobytes()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _pool(tmp_path, data: bytes, extent_bytes: int, cfg: ch.ChannelConfig, *, name: str = "s",
          archive=None):
    """Encode ``data`` as a stream, then simulate a sequenced pool of all its strands. Returns (reads, manifest)."""
    sink = tmp_path / f"{name}.fasta"
    opts = st.StreamOptions(extent_bytes=extent_bytes)
    if archive is not None:
        opts = st.StreamOptions(extent_bytes=extent_bytes, archive=archive)
    st.encode_stream(data, sink, options=opts)
    reads = tmp_path / f"{name}.fastq"
    ch.simulate_file(sink, reads, cfg)
    return reads, sink.with_name(f"{name}.fasta.stream.json")


def _decode_legacy(monkeypatch, reads, man, out, **kw):
    """Force the whole-pool per-extent path by disabling the linear pool decode."""
    monkeypatch.setattr(dc, "pool_decode_refusal", lambda opt: "forced legacy (differential test)")
    return st.decode_stream(reads, man, out, **kw)


# ============================================================================ output equivalence
@pytest.mark.parametrize("extents", [1, 2, 4, 8])
def test_linear_matches_legacy_output(tmp_path, monkeypatch, extents):
    data = _data(extents * 8 * KIB - 123, 1300 + extents)       # a partial final extent (not a whole multiple)
    cfg = ch.ChannelConfig(seed=7, coverage=4, substitution_rate=0.01, insertion_rate=0.002, deletion_rate=0.002,
                           reverse_complement_rate=0.4, shuffle_window=100_000)
    reads, man = _pool(tmp_path, data, 8 * KIB, cfg)

    lin = tmp_path / "lin.bin"
    rep = st.decode_stream(reads, man, lin)
    assert lin.read_bytes() == data
    assert rep["by_pool"] == extents and rep["by_range"] == 0
    assert rep["by_pool_linear"] == extents and rep["by_pool_fallback"] == 0      # the linear path carried every extent

    leg = tmp_path / "leg.bin"
    with pytest.MonkeyPatch.context() as mp:
        rep_l = _decode_legacy(mp, reads, man, leg)
    assert leg.read_bytes() == data == lin.read_bytes()
    assert rep_l["by_pool"] == extents and rep_l["by_pool_linear"] == 0 and rep_l["by_pool_fallback"] == extents
    # the whole-stream summary matches but for timing, the output path, the linear/fallback split and the linear
    # cost-split fields (which only the linear path reports)
    drop = ("seconds", "mb_s", "by_pool_linear", "by_pool_fallback", "output", "pass1_seconds", "partition_seconds",
            "pool_pass2_seconds", "pool_records", "pool_partition_records")
    assert {k: v for k, v in rep.items() if k not in drop} == {k: v for k, v in rep_l.items() if k not in drop}


def test_quality_weighting_matches_legacy(tmp_path):
    # consensus_weighting="quality" is allowed through the linear pool path; it must still equal the legacy path
    data = _data(4 * 8 * KIB - 7, 909)
    cfg = ch.ChannelConfig(seed=23, coverage=5, substitution_rate=0.02, insertion_rate=0.003, deletion_rate=0.003,
                           reverse_complement_rate=0.3, shuffle_window=100_000)
    reads, man = _pool(tmp_path, data, 8 * KIB, cfg)
    opt = DecodeOptions(consensus_weighting="quality")
    lin = tmp_path / "lin.bin"
    rep = st.decode_stream(reads, man, lin, options=opt)
    assert rep["by_pool_linear"] == rep["by_pool"] and rep["by_pool_fallback"] == 0
    leg = tmp_path / "leg.bin"
    with pytest.MonkeyPatch.context() as mp:
        _decode_legacy(mp, reads, man, leg, options=DecodeOptions(consensus_weighting="quality"))
    assert lin.read_bytes() == data == leg.read_bytes()


def test_a_wall_time_budget_uses_the_legacy_path(tmp_path):
    # a finite recovery wall-time budget must be honoured, so the pool path defers to the single-planner legacy path
    from vnxdna.recovery.planner import RecoveryBudget
    data = _data(3 * 8 * KIB, 51)
    cfg = ch.ChannelConfig(seed=5, coverage=4, substitution_rate=0.01, shuffle_window=100_000)
    reads, man = _pool(tmp_path, data, 8 * KIB, cfg)
    opt = DecodeOptions(recovery_budget=RecoveryBudget(max_wall_seconds=600.0))
    assert dc.pool_decode_refusal(opt) is not None
    out = tmp_path / "o.bin"
    rep = st.decode_stream(reads, man, out, options=opt)
    assert out.read_bytes() == data
    assert rep["by_pool_fallback"] == rep["by_pool"] and rep["by_pool_linear"] == 0


def test_determinism_across_workers(tmp_path):
    data = _data(6 * 8 * KIB + 9, 44)
    cfg = ch.ChannelConfig(seed=11, coverage=4, substitution_rate=0.015, insertion_rate=0.003, deletion_rate=0.003,
                           duplication_rate=0.1, shuffle_window=100_000)
    reads, man = _pool(tmp_path, data, 8 * KIB, cfg)
    outs = []
    for workers in (1, 2, 4):
        out = tmp_path / f"w{workers}.bin"
        rep = st.decode_stream(reads, man, out, options=DecodeOptions(workers=workers))
        assert rep["by_pool_linear"] == rep["by_pool"] and rep["by_pool_fallback"] == 0
        outs.append(out.read_bytes())
    assert outs[0] == outs[1] == outs[2] == data


def test_missing_duplicated_corrupted_reads(tmp_path, monkeypatch):
    # heavier noise + dropout + duplication: some extents may need consensus recovery; linear must still equal legacy
    data = _data(4 * 8 * KIB + 50, 71)
    cfg = ch.ChannelConfig(seed=23, coverage=10, substitution_rate=0.02, insertion_rate=0.004, deletion_rate=0.004,
                           dropout_rate=0.05, duplication_rate=0.25, reverse_complement_rate=0.5, shuffle_window=100_000)
    reads, man = _pool(tmp_path, data, 8 * KIB, cfg)

    lin = tmp_path / "lin.bin"
    rep = st.decode_stream(reads, man, lin)
    leg = tmp_path / "leg.bin"
    with pytest.MonkeyPatch.context() as mp:
        _decode_legacy(mp, reads, man, leg)
    assert lin.read_bytes() == leg.read_bytes() == data
    assert rep["by_pool_fallback"] == 0        # even under this channel the linear path recovers every extent


def test_foreign_archive_in_pool(tmp_path, monkeypatch):
    """A pool holding the stream's extents plus the strands of an unrelated archive (a foreign tag not in the
    manifest): the foreign reads are ignored and the linear path equals the legacy path."""
    data = _data(3 * 8 * KIB + 7, 91)
    cfg = ch.ChannelConfig(seed=3, coverage=4, substitution_rate=0.01, insertion_rate=0.002, deletion_rate=0.002,
                           shuffle_window=100_000)
    reads, man = _pool(tmp_path, data, 8 * KIB, cfg, name="main")
    foreign, _ = _pool(tmp_path, _data(8 * KIB, 9999), 8 * KIB, ch.ChannelConfig(seed=8, coverage=4), name="foreign")

    mixed = tmp_path / "mixed.fastq"
    mixed.write_bytes(reads.read_bytes() + foreign.read_bytes())

    lin = tmp_path / "lin.bin"
    rep = st.decode_stream(mixed, man, lin)
    assert rep["by_pool_linear"] == rep["by_pool"] and rep["by_pool_fallback"] == 0
    leg = tmp_path / "leg.bin"
    with pytest.MonkeyPatch.context() as mp:
        _decode_legacy(mp, mixed, man, leg)
    assert lin.read_bytes() == leg.read_bytes() == data


def test_empty_and_single_and_partial(tmp_path):
    # empty stream: no extents, nothing is pooled, the rebuilt stream is empty (decoded from the empty sink itself)
    st.encode_stream(b"", tmp_path / "e.fasta", options=st.StreamOptions(extent_bytes=4096))
    st.decode_stream(tmp_path / "e.fasta", tmp_path / "e.fasta.stream.json", tmp_path / "e.bin")
    assert (tmp_path / "e.bin").read_bytes() == b""

    # a single extent and a shorter final extent
    for size in (4000, 4096 + 11):
        data = _data(size, size)
        r, m = _pool(tmp_path, data, 4096, ch.ChannelConfig(seed=5, coverage=4, substitution_rate=0.01,
                                                            shuffle_window=100_000), name=f"s{size}")
        out = tmp_path / f"o{size}.bin"
        rep = st.decode_stream(r, m, out)
        assert out.read_bytes() == data and rep["by_pool_fallback"] == 0


# ============================================================================ encrypted extents
def test_encrypted_pool(tmp_path, monkeypatch):
    from vnxdna.archive.operations import ArchiveOptions
    key = bytes(range(32))
    data = _data(3 * 8 * KIB + 3, 17)
    reads, man = _pool(tmp_path, data, 8 * KIB,
                       ch.ChannelConfig(seed=4, coverage=4, substitution_rate=0.01, insertion_rate=0.002,
                                        deletion_rate=0.002, shuffle_window=100_000),
                       archive=ArchiveOptions(key=key))
    lin = tmp_path / "lin.bin"
    rep = st.decode_stream(reads, man, lin, key=key)
    assert lin.read_bytes() == data and rep["by_pool_fallback"] == 0
    leg = tmp_path / "leg.bin"
    with pytest.MonkeyPatch.context() as mp:
        _decode_legacy(mp, reads, man, leg, key=key)
    assert leg.read_bytes() == data
    # without the key nothing is produced
    with pytest.raises(VNXError):
        st.decode_stream(reads, man, tmp_path / "x.bin")
    assert not (tmp_path / "x.bin").exists()


# ============================================================================ ambiguous-tag refusal preserved
def test_ambiguous_tags_refused(tmp_path):
    data = _data(3 * 8 * KIB, 55)
    reads, man = _pool(tmp_path, data, 8 * KIB,
                       ch.ChannelConfig(seed=2, coverage=4, substitution_rate=0.01, shuffle_window=100_000))
    doc = json.loads(man.read_text())
    assert len({e["archive_tag"] for e in doc["extents"]}) == len(doc["extents"])
    ext = [dict(e) for e in doc["extents"]]
    ext[1]["archive_tag"] = ext[0]["archive_tag"]          # two different archives forced to share one 16-bit tag
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**doc, "extents": ext}, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(VNXFormatError):
        st.decode_stream(reads, bad, tmp_path / "o.bin")
    assert not (tmp_path / "o.bin").exists()


# ============================================================================ per-extent fallback to the legacy path
def test_per_extent_fallback(tmp_path, monkeypatch):
    """A per-extent failure in the linear path falls back to the whole-pool decode_extent, and the output is correct."""
    data = _data(4 * 8 * KIB - 1, 61)                    # four extents (the last one partial)
    reads, man = _pool(tmp_path, data, 8 * KIB,
                       ch.ChannelConfig(seed=6, coverage=4, substitution_rate=0.01, insertion_rate=0.002,
                                        deletion_rate=0.002, shuffle_window=100_000))
    real = dc.PoolDecoder.decode

    def flaky(self, rec, consume):
        if rec["index"] == 2:                   # fail exactly one extent in the linear path
            raise RuntimeError("simulated linear-path failure")
        return real(self, rec, consume)

    monkeypatch.setattr(dc.PoolDecoder, "decode", flaky)
    out = tmp_path / "out.bin"
    rep = st.decode_stream(reads, man, out)
    assert out.read_bytes() == data
    assert rep["by_pool"] == 4 and rep["by_pool_linear"] == 3 and rep["by_pool_fallback"] == 1


# ============================================================================ per-extent report equivalence
#: consensus/snap counters the whole-pool path can raise above the partition's, because it also votes with pending reads
#: of OTHER archive tags that snap within one address byte onto this tag's missing addresses (a cross-tag snap). The
#: partition decode drops those, so for these counters the whole-pool path is an UPPER bound, never a lower one; every
#: other field (recovery outcome and pass-1 read accounting) must match exactly.
_CONSENSUS_KEYS = ("addresses_snapped", "consensus_attempted", "consensus_recovered", "consensus_recovered_smart",
                   "consensus_recovered_soft", "consensus_weighted", "consensus_weighting_fallback")


def _outcome(report: dict) -> dict:
    """The fields that must match exactly between the whole-pool decode of a tag and its partition decode: recovery
    outcome plus the pass-1 read accounting (the consensus/snap counters are compared separately)."""
    sb = report.get("superblock", {})
    reads = {k: v for k, v in report.get("reads", {}).items() if k not in _CONSENSUS_KEYS}
    return {"status": report.get("status"), "archive_id": sb.get("archive_id"), "container_size": sb.get("container_size"),
            "groups_decoded": report.get("groups_decoded"), "groups_failed": report.get("groups_failed"),
            "duplicate_conflicts": report.get("duplicate_conflicts"),
            "container_sha256": report.get("container_sha256"), "reads": reads}


def test_per_extent_report_equivalence(tmp_path):
    from vnxdna.pipeline.decode import decode_reads, PoolDecoder
    data = _data(4 * 8 * KIB + 20, 88)
    cfg = ch.ChannelConfig(seed=13, coverage=5, substitution_rate=0.02, insertion_rate=0.004, deletion_rate=0.004,
                           duplication_rate=0.15, reverse_complement_rate=0.4, shuffle_window=100_000)
    reads, man = _pool(tmp_path, data, 8 * KIB, cfg)
    doc = json.loads(man.read_text())
    opt = DecodeOptions()
    opt.validate()

    pd = PoolDecoder(reads, opt, [int(e["archive_tag"], 16) for e in doc["extents"]], workdir=str(tmp_path))
    try:
        for e in doc["extents"]:
            tag = int(e["archive_tag"], 16)
            # legacy: a whole-pool decode of this tag
            opt_leg = replace(opt, expect_archive_id=e["archive_id"], archive_tag=tag)
            res_leg = decode_reads(reads, tmp_path / f"leg{e['index']}.vnx", opt_leg, overwrite=True,
                                   workdir=str(tmp_path))
            # linear: pass 2 over this tag's partition of the single pass 1
            from vnxdna.recovery.planner import RecoveryPlanner
            opt_lin = replace(opt, expect_archive_id=e["archive_id"], archive_tag=tag)
            wdir = tmp_path / f"w{e['index']}"
            wdir.mkdir()
            res_lin = _pass2(pd.parts[tag], pd.lay, opt_lin, Counter(pd.stats), {}, 0.0,
                             tmp_path / f"lin{e['index']}.vnx", True, None, None, None, None, None,
                             str(wdir), RecoveryPlanner(opt_lin.recovery_budget, 0.0),
                             None, None, False, pool=None)
            assert res_leg.status == "SUCCESS" and res_lin.status == "SUCCESS"
            # the decoded containers are byte-for-byte identical (both are SHA-256-verified against the same superblock)
            assert (tmp_path / f"leg{e['index']}.vnx").read_bytes() == (tmp_path / f"lin{e['index']}.vnx").read_bytes()
            # recovery outcome and pass-1 read accounting match exactly
            assert _outcome(res_leg.report) == _outcome(res_lin.report)
            # the whole-pool path never does LESS consensus/snap work than the partition (cross-tag snaps only add)
            for k in _CONSENSUS_KEYS:
                assert res_lin.report["reads"].get(k, 0) <= res_leg.report["reads"].get(k, 0)
    finally:
        pd.close()
