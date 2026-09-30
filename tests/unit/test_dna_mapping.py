"""Binary↔DNA mappings: deterministic, reversible, versioned; constraints are a separate layer."""
import numpy as np
import pytest

from vnxdna.dna import constraints as C
from vnxdna.dna.mapping import MAPPINGS, codebook8_words, get_mapping, reverse_complement, to_codes, to_string
from vnxdna.errors import ConfigurationError, InvalidDNAError

ALL_BYTES = np.arange(256, dtype=np.uint8).reshape(1, 256)


@pytest.mark.parametrize("name", sorted(MAPPINGS))
def test_every_byte_roundtrips_and_encoding_is_deterministic(name):
    m = get_mapping(name)
    codes = m.encode(ALL_BYTES)
    assert codes.shape == (1, 256 * m.nt_per_byte) and set(np.unique(codes)) <= {0, 1, 2, 3}
    data, erasures = m.decode(codes)
    assert np.array_equal(data, ALL_BYTES) and not erasures.any()
    assert np.array_equal(m.encode(ALL_BYTES), codes)


def test_two_bit_mapping_is_the_documented_table():
    assert to_string(get_mapping("2bit").encode(np.array([[0x1B, 0xE4]], dtype=np.uint8))[0]) == "ACGTTGCA"


def test_rotation3_never_repeats_a_base_and_codebook8_is_gc_balanced():
    data = np.random.default_rng(0).integers(0, 256, (50, 60), dtype=np.uint8)
    rot = get_mapping("rotation3").encode(data)
    assert not (rot[:, 1:] == rot[:, :-1]).any()
    words = codebook8_words()
    assert len(set(words)) == 256
    assert all(sum(b in "GC" for b in w) == 4 and C.longest_homopolymer(w) <= 3 for w in words)
    cb = get_mapping("codebook8").encode(data)
    assert all(C.longest_homopolymer(to_string(r)) <= 3 for r in cb)


def test_unreadable_symbols_become_erasures_and_invalid_symbols_raise():
    m = get_mapping("2bit")
    codes = to_codes("ACGTNCGT")
    data, erasures = m.decode(codes[None, :])
    assert erasures.tolist() == [[False, True]]
    with pytest.raises(InvalidDNAError):
        to_codes("ACGX")
    with pytest.raises(InvalidDNAError):
        m.decode(to_codes("ACG")[None, :])
    with pytest.raises(ConfigurationError):
        get_mapping("3bit")


def test_reverse_complement_is_an_involution():
    s = "ACGTTGCAAN"
    assert reverse_complement(s) == "NTTGCAACGT"
    assert reverse_complement(reverse_complement(s)) == s


def test_vectorized_constraints_agree_with_scalar_reference():
    rng = np.random.default_rng(4)
    specs = [C.ConstraintSpec(40, 60, 3), C.ConstraintSpec(30, 70, 2, gc_window_nt=20), C.ConstraintSpec(0, 100, 0, forbidden_motifs=("GAATTC", "AAAC"))]
    seqs = rng.integers(0, 4, (300, 80), dtype=np.uint8)
    seqs[:30, 10:16] = 0  # force some homopolymers
    for spec in specs:
        fast = C.satisfied(seqs, spec)
        slow = [C.analyze(to_string(s), spec)["valid"] for s in seqs]
        assert fast.tolist() == slow


def test_constraint_spec_validation():
    with pytest.raises(ConfigurationError):
        C.ConstraintSpec(70, 30)
    with pytest.raises(ConfigurationError):
        C.ConstraintSpec(forbidden_motifs=("ACGU",))
    assert C.ConstraintSpec(forbidden_motifs=("gaattc",)).forbidden_motifs == ("GAATTC",)
