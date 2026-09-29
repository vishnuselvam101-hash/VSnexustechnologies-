"""Strand frame: in-band identity, CRC, inner RS, screening, orientation."""
import numpy as np
import pytest

from vnxdna.dna.constraints import UNCONSTRAINED, ConstraintSpec, analyze
from vnxdna.dna.mapping import get_mapping, reverse_complement_codes, to_string
from vnxdna.dna.strand import KIND_DATA, KIND_META, StrandGeometry, build_strands, parse_frame
from vnxdna.errors import ConfigurationError, ConstraintError
from vnxdna.storage.decoder import scan_reads

G = StrandGeometry("2bit", 24, 8)


def _build(n=40, spec=ConstraintSpec(), geometry=G, seed=0):
    rng = np.random.default_rng(seed)
    payloads = rng.integers(0, 256, (n, geometry.payload_bytes), dtype=np.uint8)
    kinds = np.full(n, KIND_DATA, dtype=np.uint8)
    codes, variants = build_strands(geometry, spec, 0xABCDEF, kinds, np.arange(n) // 12, np.arange(n) % 12, payloads)
    return codes, payloads, variants


def test_geometry_and_limits():
    assert G.frame_bytes == 13 + 24 + 8 and G.strand_nt == 4 * G.frame_bytes
    with pytest.raises(ConfigurationError):
        StrandGeometry("2bit", 240, 8)
    with pytest.raises(ConfigurationError):
        StrandGeometry("2bit", 0, 8)


@pytest.mark.parametrize("mapping", ["2bit", "rotation3", "codebook8"])
def test_identity_lives_in_the_dna_and_every_strand_meets_constraints(mapping):
    geometry = StrandGeometry(mapping, 24, 8)
    spec = ConstraintSpec(40, 60, 3)
    codes, payloads, _ = _build(geometry=geometry, spec=spec)
    for row, payload, i in zip(codes, payloads, range(len(codes))):
        assert analyze(to_string(row), spec)["valid"]
        frames, erasures = get_mapping(mapping).decode(row[None, :])
        (kind, tag, stripe, shard, got), corrected = parse_frame(geometry, frames[0], erasures[0])
        assert (kind, tag, stripe, shard, got, corrected) == (KIND_DATA, 0xABCDEF, i // 12, i % 12, payload.tobytes(), 0)


def test_substitutions_within_inner_capacity_are_corrected_and_beyond_are_rejected():
    codes, payloads, _ = _build()
    rng = np.random.default_rng(1)
    ok = bad = 0
    for row, payload in zip(codes, payloads):
        for errors, expect in ((4, True), (12, False)):
            damaged = row.copy()
            pos = rng.choice(len(row), errors, replace=False)
            damaged[pos] = (damaged[pos] + rng.integers(1, 4, errors)) % 4
            frames, er = get_mapping("2bit").decode(damaged[None, :])
            result = parse_frame(G, frames[0], er[0])
            if expect:
                assert result is not None and result[0][4] == payload.tobytes()
                ok += 1
            else:
                assert result is None or result[0][4] == payload.tobytes()  # never a wrong payload
                bad += result is None
    assert ok == len(codes) and bad >= len(codes) - 1


def test_scan_accepts_reverse_complement_and_ignores_headers():
    codes, payloads, _ = _build(n=10)
    reads = [to_string(reverse_complement_codes(c)) if i % 2 else to_string(c) for i, c in enumerate(codes)]
    scan = scan_reads(reads, G)
    assert scan.stats["reads_valid"] == 10 and scan.stats["reads_reverse_complement"] == 5


def test_unsatisfiable_constraints_fail_loudly():
    with pytest.raises(ConstraintError):
        _build(n=5, spec=ConstraintSpec(gc_min_percent=90, gc_max_percent=100))


def test_screening_is_deterministic():
    a, _, va = _build(seed=3)
    b, _, vb = _build(seed=3)
    assert np.array_equal(a, b) and np.array_equal(va, vb)


def test_metadata_kind_round_trips():
    payloads = np.zeros((2, 24), dtype=np.uint8)
    codes, _ = build_strands(G, UNCONSTRAINED, 1, np.array([KIND_META, KIND_META]), np.array([0, 0]), np.array([0, 1]), payloads)
    frames, er = get_mapping("2bit").decode(codes)
    assert parse_frame(G, frames[1], er[1])[0][:4] == (KIND_META, 1, 0, 1)
