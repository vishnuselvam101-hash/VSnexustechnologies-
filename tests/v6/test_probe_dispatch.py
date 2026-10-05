"""Decode-side dispatch (spec §3.10, V6 Phase 2.5): an unknown frame version, a legacy pool and random reads are told
apart and refused with the right stable code, and supported pools are never refused.

Before 6.0 a pool of an unknown frame version ended as "no superblock could be decoded", exit 5, retryable (audit §4).
SYNTHETIC SOFTWARE TEST data; channel results are SIMULATED.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from typer.testing import CliRunner

from . import probe_support as ps
from vnxdna import sdk
from vnxdna.commands import app
from vnxdna.core import schema
from vnxdna.core.errors import VNXError
from vnxdna.v4 import channel as ch
from vnxdna.v4 import decoder as de


@pytest.fixture(scope="module")
def pools(tmp_path_factory):
    d = tmp_path_factory.mktemp("probe")
    return {"frame4": ps.frame4_pool(d / "f4"), "nibble7": ps.nibble_pool(d / "n7"), "nibble6": ps.nibble_pool(d / "n6", nibble=6),
            "v3": ps.v3_pool(d / "v3"), "v1": ps.v1_pool(d / "v1"), "random": ps.random_pool(d / "rnd"), "dir": d}


def _cli_error(path, tmp_path, *extra):
    r = CliRunner().invoke(app, ["decode", str(path), "-o", str(tmp_path / "o.vnx"), *extra])
    doc = json.loads(r.stderr)
    schema.validate(doc, "vnx.error/1")
    assert not (tmp_path / "o.vnx").exists()
    return r.exit_code, doc


@pytest.mark.parametrize("pool,code,exit_code", [("nibble7", "FRAME_VERSION_UNSUPPORTED", 6), ("nibble6", "FRAME_VERSION_UNSUPPORTED", 6),
                                                 ("v3", "LEGACY_FORMAT", 6), ("v1", "LEGACY_FORMAT", 6),
                                                 ("random", "LAYOUT_UNDETECTED", 3)])
def test_negative_pools_are_refused_with_the_stable_code(pools, tmp_path, pool, code, exit_code):
    rc, err = _cli_error(pools[pool], tmp_path)
    assert (rc, err["code"], err["exit_code"], err["retryable"]) == (exit_code, code, exit_code, False)
    if code == "FRAME_VERSION_UNSUPPORTED":
        assert err["details"]["frame_version"] == int(pool[-1]) and err["details"]["supported"] == [4]
    if code == "LEGACY_FORMAT":
        assert "vnx-dna" in err["hint"]


def test_inspect_answers_can_i_read_this(pools):
    want = {"frame4": "yes", "nibble7": "no", "v3": "no", "random": "unknown"}
    for pool, readable in want.items():
        body = sdk.inspect(pools[pool]).body
        schema.validate(body, "vnx.probe/1")
        assert body["readable"] == readable, pool
    assert sdk.inspect(pools["v3"]).body["reason"]["code"] == "LEGACY_FORMAT"


def test_step_7_after_pass_1_refuses_before_any_superblock_error(pools, tmp_path):
    """With an explicit profile there is no D1 probe; pass 1 accepts no frame and step 7 decides (exit 6, not 5)."""
    rc, err = _cli_error(pools["nibble7"], tmp_path, "--profile", "v4-balanced")
    assert (rc, err["code"], err["retryable"]) == (6, "FRAME_VERSION_UNSUPPORTED", False)


@pytest.mark.parametrize("profile,v6", [("v4-balanced", {}), ("v4-dense", {}), ("v4-indel", {}), ("v4-archival", {}),
                                        ("v4-balanced", {"stripe_depth": 4, "column_parity": 2}),
                                        ("v4-archival", {"strand_order": "interleaved", "outer_plan": "adaptive"})])
def test_supported_pools_are_never_refused(tmp_path, profile, v6):
    strands = ps.frame4_pool(tmp_path / "p", profile, size=4000, seed=17, **v6)
    want = hashlib.sha256((tmp_path / "p" / "a.vnx").read_bytes()).hexdigest()
    for name, cfg in (("clean", ch.ChannelConfig(coverage=2, seed=5)),
                      ("noisy", ch.ChannelConfig(substitution_rate=0.01, insertion_rate=0 if profile == "v4-dense" else 0.003,
                                                 deletion_rate=0 if profile == "v4-dense" else 0.003, coverage=6, seed=6))):
        # (v4-dense has no sync markers: an indel erases its read, so its noisy channel is substitutions only)
        reads = tmp_path / f"{name}.fastq"
        ch.simulate_file(strands, reads, cfg)
        res = de.decode_reads(reads, tmp_path / f"{name}.vnx", de.DecodeOptions(), overwrite=True)
        assert res.status == "SUCCESS", (profile, v6, name)
        assert hashlib.sha256((tmp_path / f"{name}.vnx").read_bytes()).hexdigest() == want


def test_tiny_supported_sample_is_not_refused(tmp_path):
    """Fewer than 64 reads: never a version refusal; a supported pool still decodes or fails as before (exit 5)."""
    strands = ps.frame4_pool(tmp_path / "p", size=800, seed=19)
    seqs = ps.read_fasta(strands)
    small = ps.write_fasta(tmp_path / "small.fasta", seqs[:60])
    try:
        res = de.decode_reads(small, tmp_path / "o.vnx", de.DecodeOptions())
        status = res.status
    except VNXError as error:
        assert error.code not in ("FRAME_VERSION_UNSUPPORTED", "LEGACY_FORMAT", "LAYOUT_UNDETECTED"), error.code
        status = error.code
    assert status in ("SUCCESS", "PARTIAL", "FAILURE", "NO_SUPERBLOCK")


def test_foreign_domain_coincidence_is_not_mistaken_for_a_version(pools):
    """A structured pool shows one nibble in ~half of the reads in every *other* domain too (most strands use variant
    0); only the pool's own domain shows it consistently across variant bytes."""
    from vnxdna.recovery.probe import probe_reads
    p = probe_reads(pools["v3"])
    u, share, rest, n_rest = p.dominant("VNX4 scrambler")
    assert n_rest >= 16 and rest < 0.5 and not p.supports("VNX4 scrambler")
    assert p.supports("VNX-DNA/5 scrambler", 5)
