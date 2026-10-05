"""V6-SEC-03 (MEDIUM): archive substitution and rollback. The decoder had no notion of an *expected* archive: a pool could
be replaced by any self-consistent pool (clear archives, FC-8) or by an older or different archive under the same key.
``--expect-archive-id`` / ``--expect-sha256`` on ``vnx decode`` and ``vnx extract`` (SDK: ``expect_archive_id`` /
``expect_sha256``) refuse anything else with ``ARCHIVE_MISMATCH`` (exit 1) and publish nothing. Decode checks the
superblock before pass 2 and the recovered manifest before SUCCESS. SYNTHETIC SOFTWARE TEST data; keys generated per run."""
from __future__ import annotations

import hashlib
import json

import pytest
from typer.testing import CliRunner

from vnxdna import sdk
from vnxdna.archive import container as ct
from vnxdna.archive import crypto
from vnxdna.archive import operations as ar
from vnxdna.benchmark import datagen
from vnxdna.commands.cli import app
from vnxdna.core import schema
from vnxdna.core.errors import CODES, VNXAddressError, VNXConfigurationError, VNXIntegrityError
from vnxdna.pipeline import encode as en
from vnxdna.pipeline.decode import decode_reads
from vnxdna.recovery import outer as outer_mod
from vnxdna.recovery.options import DecodeOptions


def _sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    d = tmp_path_factory.mktemp("sec03")
    datagen.generate(d / "a.bin", 9_000, "random", 3301)
    datagen.generate(d / "b.bin", 7_000, "random", 3302)
    ar.build_archive([d / "a.bin"], d / "A.vnx", ar.ArchiveOptions(chunk_size=4096))
    ar.build_archive([d / "b.bin"], d / "B.vnx", ar.ArchiveOptions(chunk_size=4096))
    for n in ("A", "B"):
        en.encode_container(d / f"{n}.vnx", d / f"{n}.fasta", en.DNAOptions())
    crypto.generate_key_file(d / "k.key")
    key = crypto.load_key_file(d / "k.key")
    # two encrypted archives under one key: "v1" (older) and "v2" (newer); random archive IDs
    ar.build_archive([d / "a.bin"], d / "v1.vnx", ar.ArchiveOptions(chunk_size=4096, key=key))
    ar.build_archive([d / "a.bin", d / "b.bin"], d / "v2.vnx", ar.ArchiveOptions(chunk_size=4096, key=key))
    for n in ("v1", "v2"):
        en.encode_container(d / f"{n}.vnx", d / f"{n}.fasta", en.DNAOptions())
    (d / "mixed.fasta").write_text((d / "A.fasta").read_text() + (d / "B.fasta").read_text())
    return d


def _id(p) -> str:
    return ct.open_container(p).manifest["archive_id"]


@pytest.fixture()
def no_pass2_walk(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("pass 2 ran for an unexpected archive")
    monkeypatch.setattr(outer_mod, "resolve_duplicates", boom)


def _cli(*args):
    return CliRunner().invoke(app, [str(a) for a in args])


def test_archive_mismatch_is_a_stable_verification_code():
    assert CODES["ARCHIVE_MISMATCH"] == ("VERIFICATION_FAILED", 1, False)


# ------------------------------------------------------------------------------------------------------------ decode
def test_decode_with_matching_expectations_succeeds(arc, tmp_path):
    opts = DecodeOptions(expect_archive_id=_id(arc / "A.vnx"), expect_sha256=_sha(arc / "A.vnx").upper())
    res = decode_reads(arc / "A.fasta", tmp_path / "o.vnx", opts)
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == (arc / "A.vnx").read_bytes()
    assert res.report["expected"] == {"archive_id": _id(arc / "A.vnx"), "container_sha256": _sha(arc / "A.vnx")}


@pytest.mark.parametrize("which", ["archive_id", "sha256"])
def test_decode_substituted_pool_is_refused_before_pass2(arc, tmp_path, no_pass2_walk, which):
    opts = (DecodeOptions(expect_archive_id=_id(arc / "A.vnx")) if which == "archive_id"
            else DecodeOptions(expect_sha256=_sha(arc / "A.vnx")))
    with pytest.raises(VNXIntegrityError) as info:
        decode_reads(arc / "B.fasta", tmp_path / "o.vnx", opts)          # pool B substituted for A
    err = info.value
    assert err.code == "ARCHIVE_MISMATCH" and err.exit_code == 1 and err.stage == "superblock"
    assert not (tmp_path / "o.vnx").exists()


def test_decode_rollback_under_the_same_key_is_refused(arc, tmp_path, no_pass2_walk):
    key = crypto.load_key_file(arc / "k.key")
    with pytest.raises(VNXIntegrityError, match="expected"):
        decode_reads(arc / "v1.fasta", tmp_path / "o.vnx", DecodeOptions(expect_archive_id=_id(arc / "v2.vnx")), key=key)
    assert not (tmp_path / "o.vnx").exists()


def test_decode_selective_and_partial_paths_check_the_expectation(arc, tmp_path, no_pass2_walk):
    opts = DecodeOptions(expect_archive_id=_id(arc / "A.vnx"))
    with pytest.raises(VNXIntegrityError):
        decode_reads(arc / "B.fasta", None, opts, select=["b.bin"], select_dir=tmp_path / "s")
    with pytest.raises(VNXIntegrityError):
        decode_reads(arc / "B.fasta", None, opts, partial_dir=tmp_path / "p")
    assert not (tmp_path / "s").exists() and not (tmp_path / "p").exists()


def test_expected_archive_id_selects_its_archive_in_a_mixed_pool(arc, tmp_path):
    a, b = _id(arc / "A.vnx"), _id(arc / "B.vnx")
    assert a[:4] != b[:4]                                     # different tags: one pool, two archives
    with pytest.raises(VNXAddressError):
        decode_reads(arc / "mixed.fasta", tmp_path / "x.vnx", DecodeOptions())        # MULTIPLE_ARCHIVES
    res = decode_reads(arc / "mixed.fasta", tmp_path / "b.vnx", DecodeOptions(expect_archive_id=b))
    assert res.status == "SUCCESS" and (tmp_path / "b.vnx").read_bytes() == (arc / "B.vnx").read_bytes()


def test_expected_tag_absent_from_the_pool_is_a_mismatch(arc, tmp_path, no_pass2_walk):
    other = "ffff" + "00" * 14
    assert not _id(arc / "A.vnx").startswith("ffff") and not _id(arc / "B.vnx").startswith("ffff")
    with pytest.raises(VNXIntegrityError) as info:
        decode_reads(arc / "mixed.fasta", tmp_path / "o.vnx", DecodeOptions(expect_archive_id=other))
    assert info.value.code == "ARCHIVE_MISMATCH"


def test_decode_recovered_manifest_must_carry_the_expected_id(arc, tmp_path, monkeypatch):
    # the superblock is checked first; the manifest of the recovered container is checked again before SUCCESS (for an
    # encrypted archive opened with the key, that manifest is MAC-authenticated). Simulate a superblock/manifest
    # disagreement by making the manifest check see a different ID.
    real = ct.open_container

    class Forged:
        def __init__(self, c):
            self._c = c

        def __getattr__(self, name):
            return getattr(self._c, name)

        @property
        def archive_id(self):
            return bytes(16)

    monkeypatch.setattr(outer_mod.ct, "open_container", lambda *a, **k: Forged(real(*a, **k)))
    with pytest.raises(VNXIntegrityError) as info:
        decode_reads(arc / "A.fasta", tmp_path / "o.vnx", DecodeOptions(expect_archive_id=_id(arc / "A.vnx")))
    assert info.value.code == "ARCHIVE_MISMATCH" and info.value.stage == "integrity"
    assert not (tmp_path / "o.vnx").exists()


@pytest.mark.parametrize("field,value", [("expect_archive_id", "abc"), ("expect_archive_id", "g" * 32),
                                         ("expect_sha256", "0" * 63), ("expect_sha256", 5)])
def test_malformed_expectations_are_configuration_errors(field, value):
    with pytest.raises(VNXConfigurationError):
        DecodeOptions(**{field: value}).validate()


def test_cli_decode_expectations(arc, tmp_path):
    r = _cli("decode", arc / "B.fasta", "-o", tmp_path / "o.vnx", "--expect-archive-id", _id(arc / "A.vnx"))
    assert r.exit_code == 1, r.output
    err = json.loads(r.stderr)
    schema.validate(err, "vnx.error/1")
    assert err["code"] == "ARCHIVE_MISMATCH" and not (tmp_path / "o.vnx").exists()
    r = _cli("decode", arc / "B.fasta", "-o", tmp_path / "o.vnx", "--expect-sha256", _sha(arc / "A.vnx"))
    assert r.exit_code == 1, r.output
    r = _cli("decode", arc / "A.fasta", "-o", tmp_path / "o.vnx", "--expect-archive-id", _id(arc / "A.vnx"),
             "--expect-sha256", _sha(arc / "A.vnx"))
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    schema.validate(doc, doc["schema"])
    assert doc["result"]["expected"]["archive_id"] == _id(arc / "A.vnx")
    assert _cli("decode", arc / "A.fasta", "-o", tmp_path / "o2.vnx", "--expect-archive-id", "xyz").exit_code == 7


# ------------------------------------------------------------------------------------------------------------ extract
def test_extract_expectations(arc, tmp_path):
    res = ar.extract(arc / "A.vnx", tmp_path / "ok", expect_archive_id=_id(arc / "A.vnx"),
                     expect_sha256=_sha(arc / "A.vnx"))
    assert res["files"] == 1 and res["archive_id"] == _id(arc / "A.vnx") and res["container_sha256"] == _sha(arc / "A.vnx")
    assert res["expected"] == {"archive_id": _id(arc / "A.vnx"), "container_sha256": _sha(arc / "A.vnx")}
    for kw in ({"expect_archive_id": _id(arc / "A.vnx")}, {"expect_sha256": _sha(arc / "A.vnx")}):
        with pytest.raises(VNXIntegrityError) as info:
            ar.extract(arc / "B.vnx", tmp_path / "bad", **kw)
        assert info.value.code == "ARCHIVE_MISMATCH"
        assert not (tmp_path / "bad").exists() or not any((tmp_path / "bad").rglob("*.bin"))
    assert ar.extract(arc / "B.vnx", tmp_path / "plain")["archive_id"] == _id(arc / "B.vnx")      # catalogue field


def test_extract_rollback_under_the_same_key_is_refused(arc, tmp_path):
    key = crypto.load_key_file(arc / "k.key")
    with pytest.raises(VNXIntegrityError):
        sdk.extract(arc / "v1.vnx", tmp_path / "x", key=key, expect_archive_id=_id(arc / "v2.vnx"))
    assert not (tmp_path / "x").exists() or not any((tmp_path / "x").rglob("*.bin"))
    assert sdk.extract(arc / "v2.vnx", tmp_path / "y", key=key, expect_archive_id=_id(arc / "v2.vnx")).body["status"] == "EXTRACTED"


def test_cli_extract_expectations(arc, tmp_path):
    r = _cli("extract", arc / "B.vnx", tmp_path / "x", "--expect-archive-id", _id(arc / "A.vnx"))
    assert r.exit_code == 1, r.output
    schema.validate(json.loads(r.stderr), "vnx.error/1")
    r = _cli("extract", arc / "B.vnx", tmp_path / "x", "--expect-sha256", _sha(arc / "A.vnx"))
    assert r.exit_code == 1, r.output
    assert not (tmp_path / "x").exists() or not any((tmp_path / "x").rglob("*.bin"))
    r = _cli("extract", arc / "B.vnx", tmp_path / "y", "--expect-sha256", _sha(arc / "B.vnx"))
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    schema.validate(doc, doc["schema"])
    assert doc["result"]["container_sha256"] == _sha(arc / "B.vnx")
    assert _cli("extract", arc / "B.vnx", tmp_path / "z", "--expect-sha256", "nothex").exit_code == 7
