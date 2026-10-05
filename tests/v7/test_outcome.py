"""V7 protocol §6: the experiment outcome classifier (EXACT, FALSE_SUCCESS, PARTIAL, EXPLICIT_FAILURE, CRASH).

Includes forged false-success cases, both as claims and end to end (a real decode whose published output is altered
after the decoder returned SUCCESS). SYNTHETIC test data; SIMULATED channel.
"""
from __future__ import annotations

import hashlib

import pytest

from vnxdna.benchmark.outcome import (CRASH, EXACT, EXPLICIT_FAILURE, FALSE_SUCCESS, PARTIAL, DecodeClaim,
                                      classify_outcome, decode_claim)
from vnxdna.simulation.channel import ChannelConfig, simulate_file
from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en

A = "a" * 64
B = "b" * 64
TRUTH = {"container": A, "f1.bin": B, "f2.bin": "c" * 64}


def cls(**kw) -> str:
    return classify_outcome(DecodeClaim(**kw), TRUTH)["outcome"]


def test_exact():
    assert cls(status="SUCCESS", exit_code=0, published={"container": A}) == EXACT


def test_forged_false_success_wrong_container():
    r = classify_outcome(DecodeClaim(status="SUCCESS", exit_code=0, published={"container": B}), TRUTH)
    assert r["outcome"] == FALSE_SUCCESS and r["wrong"] == ["container"]


def test_false_success_outranks_every_status():
    for status, code in (("SUCCESS", 0), ("SUCCESS", 5), ("PARTIAL", 9), ("FAILURE", 5), (None, 5)):
        kw = dict(status=status, exit_code=code, published={"f1.bin": A})
        if status is None:
            kw["typed_error"] = "NO_SUPERBLOCK"
        assert cls(**kw) == FALSE_SUCCESS, status


def test_false_success_in_partial_and_select_and_invented_names():
    assert cls(status="PARTIAL", exit_code=9, published={"f1.bin": B, "f2.bin": B}, verified_bytes=10,
               total_bytes=20) == FALSE_SUCCESS
    assert cls(status="SUCCESS", exit_code=0, selective=True, published={"f1.bin": "d" * 64}) == FALSE_SUCCESS
    assert cls(status="SUCCESS", exit_code=0, published={"container": A, "ghost.txt": A}) == FALSE_SUCCESS


def test_partial_records_the_verified_share():
    r = classify_outcome(DecodeClaim(status="PARTIAL", exit_code=9, published={"f1.bin": B}, verified_bytes=25,
                                     total_bytes=100), TRUTH)
    assert r["outcome"] == PARTIAL and r["verified_share"] == 0.25
    assert cls(status="PARTIAL", exit_code=9, published={}, total_bytes=100) == PARTIAL


def test_explicit_failure():
    assert cls(status="FAILURE", exit_code=5) == EXPLICIT_FAILURE
    assert cls(typed_error="NO_SUPERBLOCK", exit_code=5) == EXPLICIT_FAILURE
    assert cls(typed_error="CONTAINER_HASH_MISMATCH", exit_code=1) == EXPLICIT_FAILURE


def test_crash_and_inconsistent_claims():
    assert cls(crash="uncaught KeyError: 3") == CRASH
    assert cls(crash="timeout", status="SUCCESS", exit_code=0, published={"container": A}) == CRASH
    assert cls(status="SUCCESS", exit_code=9, published={"container": A}) == CRASH
    assert cls(status="SUCCESS", exit_code=0, published={}) == CRASH
    assert cls(status="SUCCESS", exit_code=0, selective=True, published={}) == CRASH
    assert cls(status="FAILURE", exit_code=5, published={"f1.bin": B}) == CRASH
    assert cls(typed_error="X", exit_code=0) == CRASH
    assert cls(status="SUCCESS", exit_code=0, selective=True, published={"f1.bin": B}) == EXACT


def test_malformed_claims_are_refused():
    with pytest.raises(ValueError):
        classify_outcome(DecodeClaim(), TRUTH)
    with pytest.raises(ValueError):
        classify_outcome(DecodeClaim(status="RECOVERED", exit_code=0), TRUTH)


# ------------------------------------------------------------------------------------------- end to end, real decodes
@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    d = tmp_path_factory.mktemp("v7outcome")
    datagen.generate(d / "in.bin", 6000, "random", 7801)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions(compression="none"))
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    simulate_file(d / "s.fasta", d / "ok.fastq", ChannelConfig(substitution_rate=0.003, coverage=4.0, seed=7802))
    simulate_file(d / "s.fasta", d / "bad.fastq", ChannelConfig(deletion_rate=0.06, insertion_rate=0.02, coverage=2.0,
                                                                seed=7803))
    sha = hashlib.sha256((d / "a.vnx").read_bytes()).hexdigest()
    return d, {"container": sha}


def test_real_decode_is_exact(pool, tmp_path):
    d, truth = pool
    out = tmp_path / "o.vnx"
    claim, res, err = decode_claim(lambda: de.decode_reads(d / "ok.fastq", out, de.DecodeOptions()), out)
    assert err is None and classify_outcome(claim, truth)["outcome"] == EXACT


def test_forged_decoder_that_alters_published_bytes_is_false_success(pool, tmp_path):
    """A decoder that returns SUCCESS but publishes one wrong byte (forged here after the real decode)."""
    d, truth = pool
    out = tmp_path / "o.vnx"

    def forged():
        res = de.decode_reads(d / "ok.fastq", out, de.DecodeOptions())
        data = bytearray(out.read_bytes())
        data[len(data) // 2] ^= 1
        out.write_bytes(bytes(data))
        return res

    claim, res, err = decode_claim(forged, out)
    assert res.status == "SUCCESS" and claim.exit_code == 0
    r = classify_outcome(claim, truth)
    assert r["outcome"] == FALSE_SUCCESS and r["wrong"] == ["container"]


def test_real_refusal_is_explicit_failure_and_exception_is_crash(pool, tmp_path):
    d, truth = pool
    out = tmp_path / "o.vnx"
    claim, res, err = decode_claim(lambda: de.decode_reads(d / "bad.fastq", out, de.DecodeOptions()), out)
    assert res is None or res.status != "SUCCESS"
    assert classify_outcome(claim, truth)["outcome"] == EXPLICIT_FAILURE
    assert not out.exists()

    def boom():
        raise KeyError("internal")

    claim, _, err = decode_claim(boom, out)
    assert isinstance(err, KeyError) and classify_outcome(claim, truth)["outcome"] == CRASH


def test_stale_output_is_refused(pool, tmp_path):
    out = tmp_path / "o.vnx"
    out.write_bytes(b"stale")
    with pytest.raises(ValueError):
        decode_claim(lambda: None, out)
