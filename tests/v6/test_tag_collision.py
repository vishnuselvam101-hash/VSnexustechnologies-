"""Archive-tag collision (spec §3.10 step 7, V6 Phase 2.7): two different archives whose strands carry the same 16-bit
tag in one pool must be refused with ARCHIVE_TAG_AMBIGUOUS (exit 3), never resolved by picking one.

Unencrypted archive IDs are derived from the options, paths and sizes only (audit §5.5), so two datasets with the same
file names and sizes share their archive ID and tag. SYNTHETIC SOFTWARE TEST data; channel results are SIMULATED.
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from vnxdna.commands import app
from vnxdna.core.errors import VNXError
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import container as ct
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en


def _archive(d, seed, **v6):
    (d / "ds").mkdir(parents=True)
    datagen.generate(d / "ds" / "f.bin", 12_000, "random", seed)
    ar.build_archive([d / "ds"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions(**v6))
    return d / "a.vnx", d / "s.fasta"


@pytest.fixture(scope="module", params=[{}, {"stripe_depth": 4, "column_parity": 2}], ids=["sb1", "sb2"])
def pools(request, tmp_path_factory):
    d = tmp_path_factory.mktemp("tag")
    a1, s1 = _archive(d / "one", 91, **request.param)
    a2, s2 = _archive(d / "two", 92, **request.param)
    assert ct.open_container(a1).archive_id == ct.open_container(a2).archive_id      # the collision: same derived ID
    assert a1.read_bytes() != a2.read_bytes()
    return d, (a1, s1), (a2, s2)


def _mix(d, *paths, coverage=2, seed=93):
    reads = []
    for i, p in enumerate(paths):
        r = d / f"r{i}.fastq"
        ch.simulate_file(p, r, ch.ChannelConfig(coverage=coverage, seed=seed + i), overwrite=True)
        reads.append(r.read_text())
    out = d / "mixed.fastq"
    out.write_text("".join(reads))
    return out


def test_two_archives_under_one_tag_are_refused_as_ambiguous(pools, tmp_path):
    d, (_, s1), (_, s2) = pools
    reads = _mix(tmp_path, s1, s2)
    with pytest.raises(VNXError) as e:
        de.decode_reads(reads, tmp_path / "o.vnx", de.DecodeOptions())
    assert e.value.code == "ARCHIVE_TAG_AMBIGUOUS" and e.value.exit_code == 3 and e.value.retryable is False
    assert len(e.value.details["container_sha256"]) == 2
    assert not (tmp_path / "o.vnx").exists()
    r = CliRunner().invoke(app, ["decode", str(reads), "-o", str(tmp_path / "c.vnx")])
    assert r.exit_code == 3 and json.loads(r.stderr)["code"] == "ARCHIVE_TAG_AMBIGUOUS"


def test_unequal_mixture_is_still_ambiguous(pools, tmp_path):
    """The minority archive is found too, as long as it has enough superblock strands of its own."""
    d, (_, s1), (_, s2) = pools
    r1 = tmp_path / "r1.fastq"
    ch.simulate_file(s1, r1, ch.ChannelConfig(coverage=3, seed=94))
    r2 = tmp_path / "r2.fastq"
    ch.simulate_file(s2, r2, ch.ChannelConfig(coverage=1, seed=95))
    (tmp_path / "m.fastq").write_text(r1.read_text() + r2.read_text())
    with pytest.raises(VNXError) as e:
        de.decode_reads(tmp_path / "m.fastq", tmp_path / "o.vnx", de.DecodeOptions())
    assert e.value.code == "ARCHIVE_TAG_AMBIGUOUS"


def test_one_archive_with_duplicates_is_not_ambiguous(pools, tmp_path):
    d, (a1, s1), _ = pools
    reads = _mix(tmp_path, s1, s1)
    res = de.decode_reads(reads, tmp_path / "o.vnx", de.DecodeOptions())
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == a1.read_bytes()
