"""F2: write_fasta() must not write through a symlink planted at its temporary name.

``dna/reads.py:write_fasta`` wrote to a fixed temporary name (``<name>.tmp``): anyone who could write in the output
directory could plant a symlink there and have the FASTA written through it, and the output was left holding the
link. The file also kept the permissions of whatever was planted there.
"""
from __future__ import annotations

import os
import stat

import pytest

from vnxdna.dna.reads import write_fasta


def test_write_fasta_never_writes_through_a_symlink_planted_at_a_temporary_name(tmp_path):
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"precious")
    out = tmp_path / "out.fasta"
    (tmp_path / "out.fasta.tmp").symlink_to(victim)
    write_fasta(out, ["ACGT", "TTTT"], ["r0", "r1"])
    assert victim.read_bytes() == b"precious", "the FASTA was written through the planted symlink"
    assert out.read_text() == ">r0\nACGT\n>r1\nTTTT\n"


def test_write_fasta_leaves_a_regular_file_and_not_a_symlink(tmp_path):
    out = tmp_path / "out.fasta"
    (tmp_path / "out.fasta.tmp").symlink_to(tmp_path / "victim.bin")
    write_fasta(out, ["ACGT"], ["r0"])
    assert not out.is_symlink()
    assert stat.S_ISREG(os.lstat(out).st_mode)
    assert not (tmp_path / "victim.bin").exists()


def test_write_fasta_leaves_a_private_output_file(tmp_path):
    out = tmp_path / "out.fasta"
    write_fasta(out, ["ACGT"], ["r0"])
    assert out.stat().st_mode & 0o777 == 0o600


def test_write_fasta_leaves_no_temporary_file_behind_after_a_failure(tmp_path):
    out = tmp_path / "out.fasta"
    with pytest.raises(UnicodeEncodeError):
        write_fasta(out, ["ACGé"], ["r0"])
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_write_fasta_never_keeps_the_permissions_of_a_planted_temporary_name(tmp_path):
    out = tmp_path / "out.fasta"
    planted = tmp_path / "out.fasta.tmp"
    planted.write_bytes(b"old")
    planted.chmod(0o644)
    write_fasta(out, ["ACGT"], ["r0"])
    assert out.stat().st_mode & 0o777 == 0o600
