"""EXPERIMENTAL indel realignment: exact guarantee for single indels."""
import numpy as np
import pytest

from vnxdna.dna.constraints import ConstraintSpec
from vnxdna.dna.mapping import to_string
from vnxdna.dna.strand import KIND_DATA, StrandGeometry, build_strands
from vnxdna.storage.decoder import DecodeOptions, scan_reads
from vnxdna.sync.indel import repair_read

G = StrandGeometry("2bit", 24, 8)
OPTS = DecodeOptions(indel_repair=True, max_indel=1)


def _strand(seed=0):
    rng = np.random.default_rng(seed)
    payload = rng.integers(0, 256, (1, 24), dtype=np.uint8)
    codes, _ = build_strands(G, ConstraintSpec(), 0x123456, np.array([KIND_DATA]), np.array([5]), np.array([3]), payload)
    return to_string(codes[0]), payload[0].tobytes()


@pytest.mark.parametrize("position", range(0, 180, 7))
def test_single_deletion_anywhere_is_repaired(position):
    seq, payload = _strand(position)
    read = seq[:position] + seq[position + 1:]
    parsed, _, orientation, repair = repair_read(read, G, OPTS)
    assert parsed[2:] == (5, 3, payload) and repair == "indel"


@pytest.mark.parametrize("position", range(0, 180, 11))
def test_single_insertion_plus_substitutions_is_repaired(position):
    seq, payload = _strand(position + 1)
    read = seq[:position] + "A" + seq[position:]
    read = read[:40] + ("C" if read[40] != "C" else "G") + read[41:]  # plus one substitution
    parsed, *_ = repair_read(read, G, OPTS)
    assert parsed[4] == payload


def test_repair_is_off_by_default_and_lengths_beyond_max_are_rejected():
    seq, _ = _strand()
    read = seq[:10] + seq[12:]
    assert scan_reads([read], G).stats["reads_length_mismatch"] == 1
    assert repair_read(read, G, OPTS) is None
