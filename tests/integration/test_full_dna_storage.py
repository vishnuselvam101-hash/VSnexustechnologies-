"""End-to-end DNA storage lifecycle with real files (API level; the CLI is tested in tests/cli).

store → encode (DNA) → verify DNA → simulate damage → decode → restore → independent SHA-256 → byte equality.
"""
import hashlib
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from conftest import FAST, TEST_KEY, mixed_bytes
from vnxdna import api
from vnxdna.channel import ChannelConfig
from vnxdna.container import vxdna
from vnxdna.dna.constraints import analyze
from vnxdna.dna.reads import read_sequences
from vnxdna.dna.strand import KIND_DATA
from vnxdna.errors import InsufficientRedundancyError, VNXDNAError
from vnxdna.storage.decoder import discover_geometry, scan_reads

DAMAGE = {
    "none": None,
    "substitutions": ChannelConfig(seed=11, substitution_rate=0.004),
    "dropout-within-capacity": ChannelConfig(seed=12, dropout_rate=0.05),
    "reordered": ChannelConfig(seed=13, shuffle=True),
    "duplicated": ChannelConfig(seed=14, coverage=3, shuffle=True),
    "reverse-complemented": ChannelConfig(seed=15, reverse_complement_rate=0.5, shuffle=True),
    "mixed-supported": ChannelConfig(seed=16, substitution_rate=0.003, dropout_rate=0.02, coverage=4, coverage_model="poisson",
                                     reverse_complement_rate=0.3, burst_rate=0.02, shuffle=True),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("encrypted", [False, True], ids=["plain", "aes-gcm"])
@pytest.mark.parametrize("damage", list(DAMAGE), ids=list(DAMAGE))
def test_full_lifecycle(tmp_path, damage, encrypted):
    key = TEST_KEY if encrypted else None
    source = tmp_path / "input.bin"
    source.write_bytes(mixed_bytes(30_000, seed=len(damage)))                    # 1. real test input
    api.store(source, tmp_path / "a.vxdna", options=FAST, key=key)                # 2. store
    enc = api.encode(tmp_path / "a.vxdna", tmp_path / "pool.fasta")              # 3. encode to DNA
    pool = read_sequences(tmp_path / "pool.fasta")                                # 4. verify the DNA representation
    assert len(pool) == enc["strands"] and all(set(s) <= set("ACGT") for s in pool)
    assert all(analyze(s, api.StoreOptions().constraints)["valid"] for s in pool[:200])
    reads_path = tmp_path / "pool.fasta"
    if DAMAGE[damage] is not None:                                                # 5. controlled damage
        sim = api.simulate(reads_path, tmp_path / "reads.fasta", DAMAGE[damage])
        reads_path = tmp_path / "reads.fasta"
        if DAMAGE[damage].dropout_rate:
            assert sim["channel"]["strands_dropped"] > 0
        if DAMAGE[damage].substitution_rate:
            assert sim["channel"]["substitutions"] > 0
    dec = api.decode(reads_path, tmp_path / "b.vxdna")                            # 6. decode
    assert (tmp_path / "a.vxdna").read_bytes() == (tmp_path / "b.vxdna").read_bytes()
    api.restore(tmp_path / "b.vxdna", tmp_path / "out.bin", key=key)             # 7-8. recover + restore
    assert _sha(source) == _sha(tmp_path / "out.bin")                            # 9-10. independent SHA-256
    assert source.read_bytes() == (tmp_path / "out.bin").read_bytes()            # 11. exact equality
    assert api.verify(tmp_path / "b.vxdna", key=key)["status"] == "PASS"
    assert dec["status"] == ("SUCCESS" if damage in ("none", "reordered", "duplicated", "reverse-complemented") else "RECOVERED")


def _stripe_members(pool):
    scan = scan_reads(pool, discover_geometry(pool))
    members = {}
    for i, s in enumerate(pool):
        one = scan_reads([s], scan.geometry)
        (tag, kind, stripe, shard), = one.candidates.keys()
        if kind == KIND_DATA:
            members.setdefault(stripe, []).append(i)
    return members


def test_worst_case_exactly_m_strands_lost_in_every_stripe_is_recovered(tmp_path):
    """Adversarial erasure at the guarantee boundary: remove M = 4 strands from every stripe."""
    source = tmp_path / "in.bin"
    source.write_bytes(np.random.default_rng(0).integers(0, 256, 20_000, dtype=np.uint8).tobytes())
    api.store(source, tmp_path / "a.vxdna", options=FAST)
    api.encode(tmp_path / "a.vxdna", tmp_path / "pool.fasta")
    pool = read_sequences(tmp_path / "pool.fasta")
    members = _stripe_members(pool)
    rng = np.random.default_rng(1)
    drop = {int(i) for idx in members.values() for i in rng.choice(idx, min(4, len(idx)), replace=False)}
    (tmp_path / "cut.fasta").write_text("".join(f">r\n{s}\n" for i, s in enumerate(pool) if i not in drop))
    report = api.recover(tmp_path / "cut.fasta", tmp_path / "out.bin")
    assert (tmp_path / "out.bin").read_bytes() == source.read_bytes()
    assert report["recovery"]["max_erasures_in_a_stripe"] == 4


def test_beyond_capacity_fails_honestly_and_writes_nothing(tmp_path):
    source = tmp_path / "in.bin"
    source.write_bytes(mixed_bytes(20_000, 3))
    api.store(source, tmp_path / "a.vxdna", options=FAST)
    api.encode(tmp_path / "a.vxdna", tmp_path / "pool.fasta")
    pool = read_sequences(tmp_path / "pool.fasta")
    members = _stripe_members(pool)
    victim = sorted(members)[0]
    drop = set(members[victim][:5])  # M + 1 = 5 strands of one stripe
    (tmp_path / "cut.fasta").write_text("".join(f">r\n{s}\n" for i, s in enumerate(pool) if i not in drop))
    with pytest.raises(InsufficientRedundancyError):
        api.decode(tmp_path / "cut.fasta", tmp_path / "b.vxdna")
    with pytest.raises(InsufficientRedundancyError):
        api.recover(tmp_path / "cut.fasta", tmp_path / "out.bin")
    assert not (tmp_path / "b.vxdna").exists() and not (tmp_path / "out.bin").exists()
    report = api.verify(tmp_path / "cut.fasta")
    assert report["status"] == "FAIL" and report["exit_code"] == 5


def test_heavy_random_damage_either_recovers_exactly_or_fails_cleanly(tmp_path):
    source = tmp_path / "in.bin"
    source.write_bytes(mixed_bytes(15_000, 4))
    api.store(source, tmp_path / "a.vxdna", options=FAST)
    api.encode(tmp_path / "a.vxdna", tmp_path / "pool.fasta")
    outcomes = Counter()
    for seed in range(6):
        api.simulate(tmp_path / "pool.fasta", tmp_path / f"r{seed}.fasta",
                     ChannelConfig(seed=seed, dropout_rate=0.25, substitution_rate=0.02, shuffle=True))
        try:
            api.recover(tmp_path / f"r{seed}.fasta", tmp_path / f"o{seed}.bin")
            assert (tmp_path / f"o{seed}.bin").read_bytes() == source.read_bytes()
            outcomes["recovered"] += 1
        except VNXDNAError:
            assert not (tmp_path / f"o{seed}.bin").exists()
            outcomes["failed"] += 1
    assert sum(outcomes.values()) == 6
