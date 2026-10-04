"""V6 random access: smart/soft recovery runs only for the index groups and the stripes of the selected files.

A multi-file archive is decoded from noisy reads with smart indel recovery. Selecting one small file must give the
exact file (verified chunk by chunk), examine fewer reads in smart recovery than a full decode, and never report
SUCCESS with wrong bytes.
"""
from __future__ import annotations

import hashlib

import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    d = tmp_path_factory.mktemp("ra")
    src = d / "ds"
    src.mkdir()
    sha = {}
    for i, size in enumerate((60_000, 60_000, 60_000, 2_000)):
        p = src / f"f{i}.bin"
        datagen.generate(p, size, "random", 9300 + i)
        sha[f"ds/f{i}.bin"] = hashlib.sha256(p.read_bytes()).hexdigest()
    ar.build_archive([src], d / "a.vnx", ar.ArchiveOptions())
    out = {"dir": d, "sha": sha}
    for name, kw in (("v5", {}), ("v6", dict(stripe_depth=4, column_parity=1))):
        en.encode_container(d / "a.vnx", d / f"{name}.fasta", en.DNAOptions(**kw))
        ch.simulate_file(d / f"{name}.fasta", d / f"{name}.fastq",
                         ch.ChannelConfig(substitution_rate=0.004, insertion_rate=0.006, deletion_rate=0.006, coverage=3,
                                          seed=9310))
    return out


@pytest.mark.parametrize("layout,workers", [("v5", 1), ("v6", 2)])
def test_selective_smart_recovery_examines_fewer_reads(ds, tmp_path, layout, workers):
    """Selecting one small file examines fewer reads than selecting every file (which needs every group)."""
    reads = ds["dir"] / f"{layout}.fastq"
    opts = dict(indel_recovery="smart", workers=workers)
    every = de.decode_reads(reads, None, de.DecodeOptions(**opts), select=sorted(ds["sha"]), select_dir=tmp_path / "all")
    assert every.status == "SUCCESS"
    for name, sha in ds["sha"].items():
        assert hashlib.sha256((tmp_path / "all" / name).read_bytes()).hexdigest() == sha
    every_reads = every.report["recovery_plan"]["spent"]["reads_examined"]
    assert every_reads > 0, "fixture must need smart recovery"
    sel = de.decode_reads(reads, None, de.DecodeOptions(**opts), select=["ds/f3.bin"], select_dir=tmp_path / "sel")
    assert sel.status == "SUCCESS"
    got = (tmp_path / "sel" / "ds" / "f3.bin").read_bytes()
    assert hashlib.sha256(got).hexdigest() == ds["sha"]["ds/f3.bin"]
    assert sel.report["recovery_plan"]["spent"]["reads_examined"] < every_reads
    sched = sel.report["recovery_schedule"]
    assert sched["mode"] == "deferred" and sched["random_access_stages"] == 2
    assert sel.report["fraction_of_groups_decoded"] < 1
    stages = [d["signals"].get("random_access_stage") for d in sel.report["recovery_plan"]["decisions"]
              if "random_access_stage" in d["signals"]]
    assert stages == [1, 2]


def test_selective_without_smart_recovery_is_unchanged(ds, tmp_path):
    reads = ds["dir"] / "v5.fasta"
    res = de.decode_reads(reads, None, de.DecodeOptions(), select=["ds/f1.bin"], select_dir=tmp_path / "s")
    assert res.status == "SUCCESS"
    assert hashlib.sha256((tmp_path / "s" / "ds" / "f1.bin").read_bytes()).hexdigest() == ds["sha"]["ds/f1.bin"]
    assert "recovery_schedule" not in res.report


def test_selective_with_budget_never_lies(ds, tmp_path):
    from vnxdna.v4.errors import VNXDecodeError
    from vnxdna.v6.recovery import RecoveryBudget
    reads = ds["dir"] / "v5.fastq"
    try:
        res = de.decode_reads(reads, None, de.DecodeOptions(indel_recovery="smart",
                                                            recovery_budget=RecoveryBudget(max_reads_examined=0)),
                              select=["ds/f0.bin"], select_dir=tmp_path / "b")
    except VNXDecodeError:
        return
    if res.status == "SUCCESS":
        assert hashlib.sha256((tmp_path / "b" / "ds" / "f0.bin").read_bytes()).hexdigest() == ds["sha"]["ds/f0.bin"]
