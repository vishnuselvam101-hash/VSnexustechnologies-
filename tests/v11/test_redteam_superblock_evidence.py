"""V11-RT-01 (MEDIUM, Phase 0 audit D10, job #61): a forged, CRC-valid superblock under ``max_container_bytes`` can still
claim ~10^8 groups (K = 1). Pass 2 then materialises one missing address per claimed symbol: 25 s and > 5 GB before a
MemoryError, from 51 reads. The superblock stage refuses a claim whose full decode is impossible with the reads seen
(fewer reads than data symbols) and whose address count exceeds ``EVIDENCE_FACTOR`` x reads + ``EVIDENCE_FLOOR``.
SYNTHETIC SOFTWARE TEST data."""
from __future__ import annotations

import resource
import time

import numpy as np
import pytest

from vnxdna.codec.codecs import CauchyRSCodec
from vnxdna.core.errors import VNXResourceError
from vnxdna.dnaenc.frame4 import build_strands
from vnxdna.dnaenc.layout import KIND_SUPER
from vnxdna.dnaenc.superblock import Superblock
from vnxdna.pipeline import encode as en
from vnxdna.pipeline.decode import decode_reads
from vnxdna.recovery import outer as outer_mod
from vnxdna.recovery import schedule as schedule_mod
from vnxdna.recovery import superblock as sb_mod
from vnxdna.recovery.options import DEFAULT_MAX_CONTAINER_BYTES, DecodeOptions


def _forged_reads(claimed: int, K: int, index_offset: int | None = None) -> str:
    lay = en.DNAOptions().resolve()[0]
    P = lay.payload_bytes
    groups = -(-claimed // (K * P))
    aid = (0xBEEF).to_bytes(2, "big") + bytes(14)
    raw = Superblock("cauchy-rs", K, 16, lay, "dense", 0, aid, claimed, bytes(32),
                     claimed if index_offset is None else index_offset, groups).pack()
    assert Superblock.unpack(raw).group_count == groups               # consistent: not a format error
    ks, ms = Superblock.symbols(P)
    data = np.zeros((ks, P), dtype=np.uint8)
    data.reshape(-1)[: len(raw)] = np.frombuffer(raw, dtype=np.uint8)
    coded = CauchyRSCodec(ks, ms).encode(data)
    strands, _ = build_strands(lay, en.DNAOptions().constraints, 0xBEEF, KIND_SUPER, np.zeros(ks + ms, dtype=np.int64),
                               np.arange(ks + ms, dtype=np.int64), coded)
    acgt = np.frombuffer(b"ACGT", dtype=np.uint8)
    return "".join(f">forged{i}\n{acgt[row].tobytes().decode()}\n" for i, row in enumerate(strands))


@pytest.fixture()
def no_pass2_walk(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("pass 2 walked groups of a superblock the reads cannot support")
    monkeypatch.setattr(outer_mod, "resolve_duplicates", boom)
    monkeypatch.setattr(schedule_mod, "resolve_duplicates", boom)


@pytest.mark.parametrize("index_offset", [None, 0])          # index_offset 0: random access wants every group
def test_huge_group_count_under_the_size_cap_is_refused_in_bounded_memory(tmp_path, no_pass2_walk, index_offset):
    reads = tmp_path / "r.fasta"
    reads.write_text(_forged_reads(DEFAULT_MAX_CONTAINER_BYTES, K=1, index_offset=index_offset))   # ~1.07e8 groups
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    with pytest.raises(VNXResourceError) as info:
        decode_reads(reads, tmp_path / "o.vnx", DecodeOptions())
    assert time.perf_counter() - t0 < 30
    assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - rss0 < 512 * 1024        # KiB: < 512 MiB growth
    err = info.value
    assert err.code == "RESOURCE_LIMIT" and err.exit_code == 3 and err.stage == "superblock"
    assert err.details["claimed_addresses"] > err.details["evidence_limit"]
    assert not (tmp_path / "o.vnx").exists()


def test_selective_and_deferred_paths_are_guarded(tmp_path, no_pass2_walk):
    reads = tmp_path / "r.fasta"
    reads.write_text(_forged_reads(DEFAULT_MAX_CONTAINER_BYTES, K=1, index_offset=0))
    with pytest.raises(VNXResourceError):
        decode_reads(reads, None, DecodeOptions(), select=["a.bin"], select_dir=tmp_path / "s")
    with pytest.raises(VNXResourceError):
        decode_reads(reads, tmp_path / "o.vnx", DecodeOptions(indel_recovery="smart"))
    assert not (tmp_path / "s").exists()


def test_evidence_rule_never_refuses_a_decodable_claim():
    lay = en.DNAOptions().resolve()[0]
    P = lay.payload_bytes

    def sb(size, K, M):
        return Superblock("cauchy-rs", K, M, lay, "dense", 0, bytes(16), size, bytes(32), size, -(-size // (K * P)))
    # small archives are never refused, whatever the read count
    assert sb_mod.evidence_refusal(sb(1 << 20, 1, 16), 1) is None
    # enough reads for every data symbol: never refused (a high-redundancy K = 1 geometry included)
    big = sb(1 << 30, 1, 200)
    assert sb_mod.evidence_refusal(big, -(-big.container_size // P)) is None
    # fewer reads than data symbols and far more addresses than the reads can cover: refused
    refusal = sb_mod.evidence_refusal(big, 1000)
    assert refusal is not None and refusal["claimed_addresses"] > refusal["evidence_limit"]
