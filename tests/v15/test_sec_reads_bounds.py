"""F3: the V1 read parser must bound the line length and the record count.

``dna/reads.py:_lines`` iterated the file object directly, so one unwrapped line (or a file without newlines) was
read into memory in one piece before any length check. The bound is the one of ``dnaenc/strandio.py``: a line is cut
at ``MAX_LINE_BYTES`` and the rest of it is skipped in bounded blocks, and the number of records is bounded too.
"""
from __future__ import annotations

import tracemalloc

import pytest

from vnxdna.dna import reads
from vnxdna.dna.reads import read_sequences
from vnxdna.errors import InvalidInputError


def test_a_long_line_is_cut_instead_of_being_read_whole(tmp_path):
    p = tmp_path / "long.fasta"
    p.write_text(">r0\n" + "A" * (16 << 20) + "\n")
    tracemalloc.start()
    try:
        seqs = read_sequences(p)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(seqs) == 1
    assert peak < (6 << 20), f"the reader held {peak} bytes of an over-long line"
    assert len(seqs[0]) <= reads.MAX_LINE_BYTES


def test_a_cut_line_is_still_too_long_to_be_a_read(tmp_path):
    p = tmp_path / "long.fasta"
    p.write_text(">r0\n" + "A" * (reads.MAX_READ_NT + 10) + "\n")
    seqs = read_sequences(p)
    assert len(seqs[0]) > reads.MAX_READ_NT, "the cut turned an invalid read into a valid one"


def test_the_record_count_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(reads, "MAX_READ_RECORDS", 4)
    p = tmp_path / "many.txt"
    p.write_text("ACGT\n" * 5)
    with pytest.raises(InvalidInputError, match="records"):
        read_sequences(p)


def test_the_record_count_is_bounded_for_fastq(tmp_path, monkeypatch):
    monkeypatch.setattr(reads, "MAX_READ_RECORDS", 2)
    p = tmp_path / "many.fastq"
    p.write_text("@r0\nACGT\n+\nIIII\n@r1\nTTTT\n+\nJJJJ\n@r2\nGGGG\n+\nKKKK\n")
    with pytest.raises(InvalidInputError, match="records"):
        read_sequences(p)


def test_the_record_limit_is_finite():
    assert 0 < reads.MAX_READ_RECORDS <= (1 << 26)


def test_an_ordinary_fasta_is_read_unchanged(tmp_path):
    p = tmp_path / "pool.fasta"
    p.write_text(">r0\nACGT\n>r1\nTTTT\n")
    assert read_sequences(p) == ["ACGT", "TTTT"]


def test_an_ordinary_fastq_is_read_unchanged(tmp_path):
    p = tmp_path / "reads.fastq"
    p.write_text("@r0\nACGT\n+\nIIII\n@r1\nTTTT\n+\nJJJJ\n")
    assert read_sequences(p) == ["ACGT", "TTTT"]


def test_a_malformed_fastq_is_refused(tmp_path):
    p = tmp_path / "bad.fastq"
    p.write_text("@r0\nACGT\n-\nIIII\n")
    with pytest.raises(InvalidInputError, match="malformed FASTQ"):
        read_sequences(p)


def test_an_incomplete_fastq_is_refused(tmp_path):
    p = tmp_path / "short.fastq"
    p.write_text("@r0\nACGT\n+\n")
    with pytest.raises(InvalidInputError, match="multiple of 4"):
        read_sequences(p)
