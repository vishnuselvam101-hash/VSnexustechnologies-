"""V6-SEC-02 (MEDIUM): a full ``vnx decode -o OUT`` (and ``vnx encode`` of a container source) ignored --key-file /
--passphrase-env. With a key or passphrase, the decoder must authenticate the recovered container with it (key check and
manifest MAC) before SUCCESS, and refuse an unencrypted (downgraded) archive unless --allow-unencrypted, exactly as
extract does. Nothing is published on refusal. SYNTHETIC SOFTWARE TEST data; the keys are generated per test run."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from vnxdna import sdk
from vnxdna.archive import crypto
from vnxdna.archive import operations as ar
from vnxdna.benchmark import datagen
from vnxdna.commands.cli import app
from vnxdna.core.errors import VNXKeyError
from vnxdna.pipeline import encode as en
from vnxdna.pipeline.decode import decode_reads
from vnxdna.recovery.options import DecodeOptions

PW = "sec02-pass-TEST-ONLY"
SCRYPT = {"n": 1024, "r": 8, "p": 1}


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    d = tmp_path_factory.mktemp("sec02")
    datagen.generate(d / "a.bin", 9_000, "random", 3201)
    ar.build_archive([d / "a.bin"], d / "plain.vnx", ar.ArchiveOptions(chunk_size=4096))
    en.encode_container(d / "plain.vnx", d / "plain.fasta", en.DNAOptions())
    crypto.generate_key_file(d / "k.key")
    crypto.generate_key_file(d / "other.key")
    key = crypto.load_key_file(d / "k.key")
    ar.build_archive([d / "a.bin"], d / "enc.vnx", ar.ArchiveOptions(chunk_size=4096, key=key))
    en.encode_container(d / "enc.vnx", d / "enc.fasta", en.DNAOptions())
    ar.build_archive([d / "a.bin"], d / "pw.vnx", ar.ArchiveOptions(chunk_size=4096, passphrase=PW, scrypt=SCRYPT))
    en.encode_container(d / "pw.vnx", d / "pw.fasta", en.DNAOptions())
    return d


def _cli(*args, env=None):
    return CliRunner().invoke(app, [str(a) for a in args], env=env)


# ------------------------------------------------------------------------------------------------------------ API
def test_full_decode_with_key_refuses_unencrypted_archive(arc, tmp_path):
    key = crypto.load_key_file(arc / "k.key")
    with pytest.raises(VNXKeyError) as info:
        decode_reads(arc / "plain.fasta", tmp_path / "o.vnx", DecodeOptions(), key=key)
    assert info.value.code == "KEY_FOR_UNENCRYPTED" and info.value.exit_code == 4
    assert not (tmp_path / "o.vnx").exists()
    with pytest.raises(VNXKeyError):
        decode_reads(arc / "plain.fasta", tmp_path / "o.vnx", DecodeOptions(), passphrase=PW)
    assert not (tmp_path / "o.vnx").exists()


def test_full_decode_allow_unencrypted_opt_in(arc, tmp_path):
    key = crypto.load_key_file(arc / "k.key")
    res = decode_reads(arc / "plain.fasta", tmp_path / "o.vnx", DecodeOptions(), key=key, allow_unencrypted=True)
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == (arc / "plain.vnx").read_bytes()
    res = decode_reads(arc / "plain.fasta", tmp_path / "n.vnx", DecodeOptions())          # no key: unchanged
    assert res.status == "SUCCESS" and res.report["encrypted"] is False


def test_full_decode_checks_the_key_of_an_encrypted_archive(arc, tmp_path):
    key = crypto.load_key_file(arc / "k.key")
    other = crypto.load_key_file(arc / "other.key")
    res = decode_reads(arc / "enc.fasta", tmp_path / "o.vnx", DecodeOptions(), key=key)
    assert res.status == "SUCCESS" and res.report["encrypted"] is True
    assert (tmp_path / "o.vnx").read_bytes() == (arc / "enc.vnx").read_bytes()
    with pytest.raises(VNXKeyError) as info:
        decode_reads(arc / "enc.fasta", tmp_path / "w.vnx", DecodeOptions(), key=other)
    assert info.value.code == "WRONG_KEY"
    assert not (tmp_path / "w.vnx").exists()
    with pytest.raises(VNXKeyError):                     # a passphrase for a key-file archive
        decode_reads(arc / "enc.fasta", tmp_path / "w.vnx", DecodeOptions(), passphrase=PW)
    assert not (tmp_path / "w.vnx").exists()
    res = decode_reads(arc / "pw.fasta", tmp_path / "p.vnx", DecodeOptions(), passphrase=PW)
    assert res.status == "SUCCESS"
    with pytest.raises(VNXKeyError):
        decode_reads(arc / "pw.fasta", tmp_path / "p2.vnx", DecodeOptions(), passphrase=PW + "x")
    assert not (tmp_path / "p2.vnx").exists()


def test_sdk_decode_without_extract_honours_the_key(arc, tmp_path):
    key = crypto.load_key_file(arc / "k.key")
    with pytest.raises(VNXKeyError):
        sdk.decode(arc / "plain.fasta", tmp_path / "o.vnx", key=key)
    assert not (tmp_path / "o.vnx").exists()
    assert sdk.decode(arc / "plain.fasta", tmp_path / "o.vnx", key=key, allow_unencrypted=True).status == "SUCCESS"


# ------------------------------------------------------------------------------------------------------------ CLI
def test_cli_full_decode_with_key_refuses_unencrypted(arc, tmp_path):
    k = arc / "k.key"
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o.vnx", "--key-file", k)
    assert r.exit_code == 4, r.output
    assert json.loads(r.stderr)["code"] == "KEY_FOR_UNENCRYPTED"
    assert not (tmp_path / "o.vnx").exists()
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o.vnx", "--passphrase-env", "VNX_PW", env={"VNX_PW": PW})
    assert r.exit_code == 4, r.output
    assert not (tmp_path / "o.vnx").exists()
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o.vnx", "--key-file", k, "--allow-unencrypted")
    assert r.exit_code == 0, r.output
    r = _cli("decode", arc / "enc.fasta", "-o", tmp_path / "w.vnx", "--key-file", arc / "other.key")
    assert r.exit_code == 4, r.output
    assert not (tmp_path / "w.vnx").exists()
    assert _cli("decode", arc / "enc.fasta", "-o", tmp_path / "e.vnx", "--key-file", k).exit_code == 0


# ------------------------------------------------------------------------------------------------ container source
def test_encode_container_source_with_key_refuses_unencrypted(arc, tmp_path):
    key = crypto.load_key_file(arc / "k.key")
    with pytest.raises(VNXKeyError) as info:
        sdk.encode(arc / "plain.vnx", tmp_path / "s.fasta", key=key)
    assert info.value.code == "KEY_FOR_UNENCRYPTED"
    assert not (tmp_path / "s.fasta").exists()
    assert sdk.encode(arc / "plain.vnx", tmp_path / "s.fasta", key=key, allow_unencrypted=True).status == "SUCCESS"
    with pytest.raises(VNXKeyError):
        sdk.encode(arc / "enc.vnx", tmp_path / "w.fasta", key=crypto.load_key_file(arc / "other.key"))
    assert not (tmp_path / "w.fasta").exists()
    assert sdk.encode(arc / "enc.vnx", tmp_path / "e.fasta", key=key).status == "SUCCESS"


def test_cli_encode_container_source_with_key(arc, tmp_path):
    k = arc / "k.key"
    r = _cli("encode", arc / "plain.vnx", tmp_path / "s.fasta", "--key-file", k)
    assert r.exit_code == 4, r.output
    assert not (tmp_path / "s.fasta").exists()
    assert _cli("encode", arc / "plain.vnx", tmp_path / "s.fasta", "--key-file", k, "--allow-unencrypted").exit_code == 0
    assert _cli("encode", arc / "enc.vnx", tmp_path / "w.fasta", "--key-file", arc / "other.key").exit_code == 4
    assert _cli("encode", arc / "enc.vnx", tmp_path / "e.fasta", "--key-file", k).exit_code == 0
