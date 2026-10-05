"""V7 Phase A (protocol §8.1): opt-in per-stage decode counters (``DecodeOptions.stage_counters``).

The counters are observability only. These tests pin that (1) with the option on, the decoded bytes, the status and
every pre-existing report key are identical to a decode without it, on the V6 golden fixtures and on harsher simulated
pools that fail; (2) with it off the report has no new key; (3) the counters do not depend on the worker count;
(4) the counters are conserved (every read has exactly one first-failure stage); (5) a typed decode error carries the
block in its details without changing the error. SYNTHETIC test data; SIMULATED channel.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.frame4 import decode_frames
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.dnaenc.mapping import nt_to_bytes
from vnxdna.recovery.stagecount import STAGES, StageCounters, terminal_stage
from vnxdna.simulation.channel import ChannelConfig, simulate_file
from vnxdna.sync.template import strip_markers_exact
from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.constraints import iter_fasta
from vnxdna.v4.errors import VNXError

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "v6_0"
MANIFEST = json.loads((FIX / "manifest.json").read_text())
PASSPHRASE = "vnx-test-passphrase-NOT-A-SECRET"
CASES = sorted(MANIFEST["cases"])
#: report keys whose values are wall-clock or process measurements (they differ between two identical decodes)
VOLATILE = {"seconds", "stage_seconds", "peak_rss_bytes", "elapsed", "elapsed_seconds", "wall_seconds"}


def _strip_volatile(x):
    if isinstance(x, dict):
        return {k: _strip_volatile(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [_strip_volatile(v) for v in x]
    return x


def _run(reads: Path, out: Path, **kw) -> tuple:
    key = kw.pop("passphrase", None)
    try:
        res = de.decode_reads(reads, out, de.DecodeOptions(**kw), overwrite=True, passphrase=key)
    except VNXError as error:
        details = dict(error.details)
        sc = details.pop("stage_counters", None)
        return (f"ERROR:{type(error).__name__}:{error.code}:{error}", None,
                json.loads(json.dumps(_strip_volatile(details), default=str)), sc)
    rep = json.loads(json.dumps(_strip_volatile(res.report), default=str))
    sc = rep.pop("stage_counters", None)
    data = out.read_bytes() if out.exists() else None
    if out.exists():
        out.unlink()
    return res.status, data, rep, sc


@pytest.fixture(scope="module")
def golden_reads(tmp_path_factory):
    d = tmp_path_factory.mktemp("v7golden")
    out = {}
    for c in CASES:
        p = d / f"{c}.fastq"
        p.write_bytes(gzip.decompress((FIX / f"{c}.reads.fastq.gz").read_bytes()))
        out[c] = p
    return out


@pytest.fixture(scope="module")
def harsh_pools(tmp_path_factory):
    """One small archive and three SIMULATED pools: indel-heavy (consensus active, groups fail), nanopore-like
    (NO_SUPERBLOCK) and clean (SUCCESS)."""
    d = tmp_path_factory.mktemp("v7harsh")
    datagen.generate(d / "in.bin", 8000, "random", 7701)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions(compression="none"))
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions(profile="v4-balanced"))
    common = dict(coverage_model="negative-binomial", coverage_dispersion=4.0, reverse_complement_rate=0.5,
                  n_rate=0.001, quality_informative=0.5, quality_correct=15, quality_error=7)
    cfgs = {
        "indel": ChannelConfig(substitution_rate=0.01, insertion_rate=0.003, deletion_rate=0.012, coverage=6.0,
                               seed=7702, **common),
        "nanopore": ChannelConfig(substitution_rate=0.02, insertion_rate=0.01, deletion_rate=0.03, coverage=10.0,
                                  homopolymer_indel_multiplier=3.0, burst_rate=0.005, burst_max_len=6, seed=7703,
                                  **common),
        "clean": ChannelConfig(substitution_rate=0.002, coverage=4.0, coverage_model="fixed", reverse_complement_rate=0.5,
                               seed=7704),
    }
    paths = {}
    for name, cfg in cfgs.items():
        simulate_file(d / "s.fasta", d / f"{name}.fastq", cfg)
        paths[name] = d / f"{name}.fastq"
    return {"dir": d, "container": (d / "a.vnx").read_bytes(), "reads": paths, "strands": d / "s.fasta"}


def _assert_identical_except_block(off, on):
    s0, b0, r0, sc0 = off
    s1, b1, r1, sc1 = on
    assert sc0 is None, "stage_counters must not appear when the option is off"
    assert sc1 is not None and sc1["schema"] == "vnx.stage-counters/1"
    assert s0 == s1
    assert b0 == b1                                   # byte-identical decoded output (or both nothing)
    assert r0 == r1                                   # every pre-existing report key and value unchanged
    assert set(sc1["stages"]) == set(STAGES)
    assert sc1["stages"]["clustering"] == {"applicable": 0}


@pytest.mark.parametrize("c", CASES)
def test_golden_fixtures_decode_byte_identically_with_counters(c, golden_reads, tmp_path):
    kw = {"passphrase": PASSPHRASE} if MANIFEST["cases"][c]["encrypted"] else {}
    off = _run(golden_reads[c], tmp_path / "off.vnx", **kw)
    on = _run(golden_reads[c], tmp_path / "on.vnx", stage_counters=True, **kw)
    _assert_identical_except_block(off, on)
    assert off[0] == "SUCCESS"
    assert on[1] == (FIX / f"{c}.vnx").read_bytes()
    assert on[3]["terminal_stage"] is None
    assert on[3]["stages"]["archive"]["sha256_match"] == 1


@pytest.mark.parametrize("pool", ["indel", "nanopore", "clean"])
def test_failing_and_passing_pools_identical_with_counters(pool, harsh_pools, tmp_path):
    p = harsh_pools["reads"][pool]
    off = _run(p, tmp_path / "off.vnx")
    on = _run(p, tmp_path / "on.vnx", stage_counters=True)
    _assert_identical_except_block(off, on)
    if pool == "clean":
        assert on[0] == "SUCCESS" and on[1] == harsh_pools["container"]
    else:
        assert on[1] is None                          # nothing published; never a false SUCCESS
    if pool == "nanopore":
        assert on[0].startswith("ERROR:VNXDecodeError:NO_SUPERBLOCK")
        assert on[3]["terminal_stage"] == "superblock"
    if pool == "indel":
        assert on[0] in ("FAILURE", "PARTIAL")
        assert on[3]["terminal_stage"] == "outer_ecc"
        st = on[3]["stages"]
        assert st["consensus"]["attempted"] > 0 and st["coverage"]["missing_addresses"] > 0
        assert on[3]["outer_ecc_failed_rows"], "failed rows are listed with their symbol counts"
        for row in on[3]["outer_ecc_failed_rows"]:
            assert row["have"] < row["k"] and row["have"] == row["verified_pass1"] + row["from_consensus"]


@pytest.mark.parametrize("pool", ["indel", "nanopore"])
def test_counters_do_not_depend_on_worker_count(pool, harsh_pools, tmp_path):
    p = harsh_pools["reads"][pool]
    one = _run(p, tmp_path / "w1.vnx", stage_counters=True, workers=1, batch_reads=256)
    four = _run(p, tmp_path / "w4.vnx", stage_counters=True, workers=4, batch_reads=256)
    assert one[0] == four[0] and one[1] == four[1]
    assert one[3] == four[3]


def test_golden_counters_do_not_depend_on_worker_count(golden_reads, tmp_path):
    c = "max-recovery"
    one = _run(golden_reads[c], tmp_path / "w1.vnx", stage_counters=True, workers=1, batch_reads=128)
    four = _run(golden_reads[c], tmp_path / "w4.vnx", stage_counters=True, workers=4, batch_reads=128)
    assert one[3] == four[3] and one[1] == four[1]


@pytest.mark.parametrize("pool", ["indel", "nanopore", "clean"])
def test_every_read_has_exactly_one_pass1_fate(pool, harsh_pools, tmp_path):
    sc = _run(harsh_pools["reads"][pool], tmp_path / "o.vnx", stage_counters=True)[3]["stages"]
    n = sc["read_parsing"]["reads"]
    verified = sum(sc["inner_ecc"].get(f"verified_{p}", 0) for p in ("fast", "sync", "smart", "soft"))
    failed = (sc["alignment"].get("fail_drift_beyond_band", 0) + sc["alignment"].get("fail_path_beyond_band", 0)
              + sc["indel_placement"].get("fail_erasures_exceed_parity", 0) + sc["inner_ecc"].get("fail_decode", 0)
              + sc["crc"].get("fail_mismatch", 0) + sc["address"].get("fail_invalid_version_or_kind", 0))
    assert verified + failed == n
    aligned = sc["alignment"].get("aligned", 0)
    assert aligned + sc["alignment"].get("fail_drift_beyond_band", 0) + sc["alignment"].get("fail_path_beyond_band", 0) == n
    # every aligned read that failed is kept as pending or dropped for an unreadable header
    assert (sc["address"].get("pending_kept", 0) + sc["address"].get("pending_dropped_header_unreadable", 0)
            == aligned - verified)
    assert (sc["address"].get("pending_projected_reading", 0) + sc["address"].get("pending_raw_prefix_reading", 0)
            == sc["address"].get("pending_kept", 0))


def test_pass2_placement_counters_add_up(harsh_pools, tmp_path):
    sc = _run(harsh_pools["reads"]["indel"], tmp_path / "o.vnx", stage_counters=True)[3]["stages"]
    a = sc["address"]
    assert (a.get("pass2_placed_direct", 0) + a.get("pass2_placed_snapped", 0) + a.get("pass2_unplaced", 0)
            == a["pass2_pending_records"])
    cv = sc["coverage"]
    assert (cv["missing_no_placed_read"] + cv["missing_one_placed_read"] + cv["missing_several_placed_reads"]
            == cv["missing_addresses"])
    c = sc["consensus"]
    assert c["single_read"] + c.get("several_reads", 0) == c["attempted"]
    outcomes = (c.get("recovered", 0) + c.get("fail_erasures_exceed_parity", 0) + sc["inner_ecc"].get("pass2_fail_decode", 0)
                + sc["crc"].get("pass2_fail_mismatch", 0) + a.get("pass2_verified_other_address", 0))
    assert outcomes == c["attempted"]


def test_error_details_are_unchanged_apart_from_the_block(harsh_pools, tmp_path):
    p = harsh_pools["reads"]["nanopore"]
    with pytest.raises(VNXError) as off:
        de.decode_reads(p, tmp_path / "a.vnx", de.DecodeOptions(), overwrite=True)
    with pytest.raises(VNXError) as on:
        de.decode_reads(p, tmp_path / "b.vnx", de.DecodeOptions(stage_counters=True), overwrite=True)
    assert "stage_counters" not in off.value.details
    d_on = dict(on.value.details)
    block = d_on.pop("stage_counters")
    assert d_on == off.value.details and str(on.value) == str(off.value) and on.value.code == off.value.code
    assert block["terminal_stage"] == "superblock"
    assert block["stages"]["superblock"]["symbols_needed"] == 3
    assert not (tmp_path / "a.vnx").exists() and not (tmp_path / "b.vnx").exists()


def test_option_defaults_off_and_is_validated():
    assert de.DecodeOptions().stage_counters is False
    with pytest.raises(VNXConfigurationError):
        de.DecodeOptions(stage_counters=1).validate()
    with pytest.raises(VNXConfigurationError):
        de.DecodeOptions(stage_counters="yes").validate()


def test_parsed_rs_and_crc_flags(harsh_pools):
    """``ok`` = inner RS decoded ∧ CRC verified ∧ valid version/kind; the new flags only expose the first two."""
    lay = PROFILES["v4-balanced"][0]
    codes = np.stack([np.frombuffer(s.encode() if isinstance(s, str) else s, dtype=np.uint8) for _, s in
                      list(iter_fasta(harsh_pools["strands"]))[:8]])
    lut = np.full(256, 4, dtype=np.uint8)
    for i, ch in enumerate(b"ACGT"):
        lut[ch] = i
    fb, _ = strip_markers_exact(lay, lut[codes])
    frames = nt_to_bytes(fb)
    rng = np.random.default_rng(7705)
    bad = frames.copy()
    for row in bad[4:]:                               # far beyond the inner parity: RS must fail or miscorrect
        pos = rng.choice(lay.frame_bytes, size=lay.inner_parity + 6, replace=False)
        row[pos] ^= 0x5A
    P = decode_frames(lay, np.concatenate([frames[:4], bad[4:]]))
    assert P.ok[:4].all() and P.rs_ok[:4].all() and P.crc_ok[:4].all()
    assert not P.ok[4:].any()
    assert (P.ok == (P.rs_ok & P.crc_ok & P.ok)).all()
    assert not (P.ok & ~(P.rs_ok & P.crc_ok)).any()


def test_terminal_stage_mapping():
    class E(Exception):
        def __init__(self, code, stage):
            self.code, self.stage = code, stage
    assert terminal_stage("SUCCESS", {}, None) is None
    assert terminal_stage("FAILURE", {"groups_failed": 2}, None) == "outer_ecc"
    assert terminal_stage(None, None, E("NO_SUPERBLOCK", "superblock")) == "superblock"
    assert terminal_stage(None, None, E("CONTAINER_HASH_MISMATCH", "integrity")) == "archive"
    assert terminal_stage(None, None, E("FORMAT_ERROR", "input")) == "read_parsing"
    sc = StageCounters()
    with pytest.raises(ValueError):
        sc.add("not-a-stage", "x")
    sc.add("alignment", "aligned", 3)
    sc.update({"alignment.aligned": 2})
    assert sc.block(status="SUCCESS")["stages"]["alignment"] == {"aligned": 5}


GOLDEN = Path(__file__).resolve().parent / "stage_counters_golden.json"


@pytest.mark.parametrize("pool", ["indel", "nanopore"])
def test_counters_match_the_pinned_golden(pool, harsh_pools, tmp_path):
    """Regression pin: the whole counter block of two failing SIMULATED pools (identical with the native and the reference
    backends when it was generated). A change here means the decoder or the instrumentation changed behaviour."""
    golden = json.loads(GOLDEN.read_text())[pool]
    assert _run(harsh_pools["reads"][pool], tmp_path / "o.vnx", stage_counters=True)[3] == golden
