"""V6-SEC-01 (MEDIUM): a forged, CRC-valid superblock can claim a huge ``container_size``. The decoder must refuse a claim
above the configured cap (``DecodeOptions.max_container_bytes``, ``vnx decode --max-container-bytes``) right after the
superblock is decoded: before the work file is sized and before pass 2 walks any group. SYNTHETIC SOFTWARE TEST data."""
from __future__ import annotations

import json
import struct
import zlib

import numpy as np
import pytest
from typer.testing import CliRunner

from vnxdna.archive import operations as ar
from vnxdna.benchmark import datagen
from vnxdna.codec.codecs import CauchyRSCodec
from vnxdna.commands.cli import app
from vnxdna.core.errors import VNXConfigurationError, VNXResourceError
from vnxdna.dnaenc.frame4 import build_strands
from vnxdna.dnaenc.layout import KIND_SUPER
from vnxdna.dnaenc.superblock import Superblock
from vnxdna.pipeline import encode as en
from vnxdna.pipeline.decode import decode_reads
from vnxdna.recovery import outer as outer_mod
from vnxdna.recovery import schedule as schedule_mod
from vnxdna.recovery.options import DEFAULT_MAX_CONTAINER_BYTES, DecodeOptions


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    d = tmp_path_factory.mktemp("sec01")
    datagen.generate(d / "a.bin", 20_000, "random", 3101)
    ar.build_archive([d / "a.bin"], d / "a.vnx", ar.ArchiveOptions(chunk_size=4096))
    en.encode_container(d / "a.vnx", d / "a.fasta", en.DNAOptions())
    return d


def _forged_reads(claimed: int, tag: int = 0xBEEF) -> str:
    """Superblock strands (CRC-valid, every field consistent) that claim a container of ``claimed`` bytes."""
    lay = en.DNAOptions().resolve()[0]
    K = 64
    groups = -(-claimed // (K * lay.payload_bytes))
    aid = tag.to_bytes(2, "big") + bytes(14)
    raw = Superblock("cauchy-rs", K, 16, lay, "dense", 0, aid, claimed, bytes(32), claimed, groups).pack()
    assert Superblock.unpack(raw).container_size == claimed          # a valid superblock, not a format error
    P = lay.payload_bytes
    ks, ms = Superblock.symbols(P)
    data = np.zeros((ks, P), dtype=np.uint8)
    data.reshape(-1)[: len(raw)] = np.frombuffer(raw, dtype=np.uint8)
    coded = CauchyRSCodec(ks, ms).encode(data)
    strands, _ = build_strands(lay, en.DNAOptions().constraints, tag, KIND_SUPER, np.zeros(ks + ms, dtype=np.int64),
                               np.arange(ks + ms, dtype=np.int64), coded)
    acgt = np.frombuffer(b"ACGT", dtype=np.uint8)
    return "".join(f">forged{i}\n{acgt[row].tobytes().decode()}\n" for i, row in enumerate(strands))


@pytest.fixture()
def no_pass2_walk(monkeypatch):
    """Fail loudly if pass 2 starts walking groups (it must not for an over-cap claim)."""
    def boom(*a, **k):
        raise AssertionError("pass 2 walked groups of an over-cap superblock")
    monkeypatch.setattr(outer_mod, "resolve_duplicates", boom)
    monkeypatch.setattr(schedule_mod, "resolve_duplicates", boom)        # the deferred smart/soft group walk


def test_default_cap_is_large_enough_for_the_1_gib_experiments():
    # EXP-0012 decodes 1 GiB inputs; the default must leave room for them (and their container overhead)
    assert DEFAULT_MAX_CONTAINER_BYTES >= 4 << 30
    assert DecodeOptions().max_container_bytes == DEFAULT_MAX_CONTAINER_BYTES


def test_claim_above_default_cap_is_refused_before_pass2(tmp_path, no_pass2_walk):
    reads = tmp_path / "r.fasta"
    reads.write_text(_forged_reads(1 << 40))                          # 1 TiB claim, ~10^8 groups
    with pytest.raises(VNXResourceError) as info:
        decode_reads(reads, tmp_path / "o.vnx", DecodeOptions())
    err = info.value
    assert err.code == "RESOURCE_LIMIT" and err.exit_code == 3 and err.stage == "superblock"
    assert err.details["container_size"] == 1 << 40
    assert err.details["max_container_bytes"] == DEFAULT_MAX_CONTAINER_BYTES
    assert not (tmp_path / "o.vnx").exists()


def test_configured_cap_refuses_a_smaller_claim(tmp_path, no_pass2_walk):
    reads = tmp_path / "r.fasta"
    reads.write_text(_forged_reads(10 ** 9))
    with pytest.raises(VNXResourceError):
        decode_reads(reads, tmp_path / "o.vnx", DecodeOptions(max_container_bytes=1 << 20))


@pytest.mark.parametrize("mode", [{"indel_recovery": "smart"}, {"soft_decoding": "erasure"}])
def test_cap_also_guards_the_deferred_recovery_schedule(tmp_path, no_pass2_walk, mode):
    reads = tmp_path / "r.fasta"
    reads.write_text(_forged_reads(10 ** 9))
    with pytest.raises(VNXResourceError):
        decode_reads(reads, tmp_path / "o.vnx", DecodeOptions(max_container_bytes=1 << 20, **mode))


def test_cap_also_guards_selective_and_partial_decodes(tmp_path, no_pass2_walk):
    reads = tmp_path / "r.fasta"
    reads.write_text(_forged_reads(10 ** 9))
    with pytest.raises(VNXResourceError):
        decode_reads(reads, None, DecodeOptions(max_container_bytes=1 << 20), select=["a.bin"], select_dir=tmp_path / "s")
    with pytest.raises(VNXResourceError):
        decode_reads(reads, None, DecodeOptions(max_container_bytes=1 << 20), partial_dir=tmp_path / "p")
    assert not (tmp_path / "s").exists() and not (tmp_path / "p").exists()


def test_genuine_archive_under_the_cap_decodes(plain, tmp_path):
    size = (plain / "a.vnx").stat().st_size
    res = decode_reads(plain / "a.fasta", tmp_path / "o.vnx", DecodeOptions(max_container_bytes=size))   # cap is inclusive
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == (plain / "a.vnx").read_bytes()
    with pytest.raises(VNXResourceError):
        decode_reads(plain / "a.fasta", tmp_path / "o2.vnx", DecodeOptions(max_container_bytes=size - 1))
    assert not (tmp_path / "o2.vnx").exists()


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "1"])
def test_cap_must_be_a_positive_integer(bad):
    with pytest.raises(VNXConfigurationError):
        DecodeOptions(max_container_bytes=bad).validate()


def test_cli_max_container_bytes(plain, tmp_path):
    size = (plain / "a.vnx").stat().st_size
    r = CliRunner().invoke(app, ["decode", str(plain / "a.fasta"), "-o", str(tmp_path / "o.vnx"),
                                 "--max-container-bytes", str(size - 1)])
    assert r.exit_code == 3, r.output
    err = json.loads(r.stderr)
    assert err["code"] == "RESOURCE_LIMIT"
    assert not (tmp_path / "o.vnx").exists()
    r = CliRunner().invoke(app, ["decode", str(plain / "a.fasta"), "-o", str(tmp_path / "o.vnx"),
                                 "--max-container-bytes", str(size)])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["status"] == "SUCCESS"
    from vnxdna.sdk.config import decode_options_for
    assert decode_options_for(None, None, max_container_bytes=size).max_container_bytes == size
    assert decode_options_for(None, None).max_container_bytes == DEFAULT_MAX_CONTAINER_BYTES


def test_forged_claim_crc_layout_matches_struct():
    # guard for the helper: the claimed size sits at bytes 38..46 of the packed superblock
    raw = Superblock("cauchy-rs", 64, 16, en.DNAOptions().resolve()[0], "dense", 0, bytes(16), 12345, bytes(32), 0, 1)
    packed = raw.pack()
    assert struct.unpack(">Q", packed[38:46])[0] == 12345 and zlib.crc32(packed[:-4]) == struct.unpack(">I", packed[-4:])[0]
