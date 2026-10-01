"""Unit tests: vectorised CRC, frame format 5, linear screening, inner RS, V2 constraints."""
import zlib

import numpy as np
import pytest

from vnxdna.dna.constraints import ConstraintSpec, satisfied
from vnxdna.dna.mapping import get_mapping, reverse_complement_codes, to_string
from vnxdna.errors import ConfigurationError, ConstraintError
from vnxdna.v2.constraints import ConstraintSpecV2, analyze_v2, longest_tandem_repeat, satisfied_v2
from vnxdna.v2.crc import crc32_rows
from vnxdna.v2.frame import (FrameGeometry, _plain_rows, _screen_direct, _screen_linear_2bit, build_strands, parse_batch,
                             parse_one_corrected, rs_parity, tentative_address)

rng = np.random.default_rng(11)


@pytest.mark.parametrize("width", [0, 1, 7, 50, 200])
def test_crc32_rows_equals_zlib(width):
    rows = rng.integers(0, 256, (300, width), dtype=np.uint8)
    got = crc32_rows(rows)
    assert all(int(got[i]) == zlib.crc32(rows[i].tobytes()) for i in range(rows.shape[0]))


@pytest.mark.parametrize("p,r", [(40, 8), (36, 12), (48, 6), (32, 16), (24, 0)])
def test_rs_parity_matches_reedsolo_encoder(p, r):
    g = FrameGeometry("2bit", p, r)
    messages = rng.integers(0, 256, (200, g.systematic_bytes), dtype=np.uint8)
    assert (rs_parity(g, messages) == g.inner.encode_batch(messages)).all()


@pytest.mark.parametrize("spec", [ConstraintSpecV2(), ConstraintSpecV2(gc_min_percent=45, gc_max_percent=55, max_tandem_repeat_nt=12),
                                  ConstraintSpecV2(gc_min_percent=35, gc_max_percent=65, max_homopolymer=5, forbidden_motifs=("GAATTC",))])
def test_linear_screening_equals_direct_screening(spec):
    g = FrameGeometry("2bit", 40, 8)
    n = 2000
    plain = _plain_rows(0xABCDEF01, np.zeros(n, np.uint8), np.arange(n), np.arange(n) % 80, rng.integers(0, 256, (n, 40), dtype=np.uint8))
    a, b = _screen_direct(g, spec, plain), _screen_linear_2bit(g, spec, plain)
    assert (a[0] == b[0]).all() and (a[1] == b[1]).all() and a[2].size == b[2].size == 0


@pytest.mark.parametrize("mapping", ["2bit", "rotation3", "codebook8"])
def test_frame_roundtrip_all_mappings_and_32bit_addresses(mapping):
    g = FrameGeometry(mapping, 30, 8)
    stripes = np.array([0, 1, 2**24, 2**32 - 1], dtype=np.int64)
    shards = np.array([0, 5, 200, 255])
    payload = rng.integers(0, 256, (4, 30), dtype=np.uint8)
    spec = ConstraintSpecV2() if mapping == "2bit" else ConstraintSpecV2(gc_min_percent=0, gc_max_percent=100, max_homopolymer=0)
    codes, _ = build_strands(g, spec, 0xFFFFFFFF, np.array([0, 1, 0, 1], np.uint8), stripes, shards, payload)
    assert codes.shape == (4, g.strand_nt)
    frames, erasures = get_mapping(mapping).decode(codes)
    pb = parse_batch(g, frames, erasures)
    assert pb.ok.all() and (pb.stripe == stripes).all() and (pb.shard == shards).all() and (pb.tag == 0xFFFFFFFF).all()
    assert (pb.payload == payload).all() and pb.kind.tolist() == [0, 1, 0, 1]


def test_every_strand_satisfies_the_recorded_constraints():
    spec = ConstraintSpecV2(gc_min_percent=45, gc_max_percent=55, max_homopolymer=4, max_tandem_repeat_nt=12, forbidden_motifs=("GGATCC",))
    g = FrameGeometry("2bit", 32, 16)
    n = 500
    codes, _ = build_strands(g, spec, 1, np.zeros(n, np.uint8), np.arange(n), np.arange(n) % 64, rng.integers(0, 256, (n, 32), dtype=np.uint8))
    for row in codes[:200]:
        assert analyze_v2(to_string(row), spec)["valid"]


def test_impossible_constraints_fail_clearly_instead_of_violating():
    spec = ConstraintSpecV2(gc_min_percent=50, gc_max_percent=50, max_homopolymer=1)
    g = FrameGeometry("2bit", 40, 8)
    with pytest.raises(ConstraintError):
        build_strands(g, spec, 1, np.zeros(64, np.uint8), np.arange(64), np.arange(64), rng.integers(0, 256, (64, 40), dtype=np.uint8))


def test_inner_rs_corrects_up_to_half_its_parity_and_crc_rejects_the_rest():
    g = FrameGeometry("2bit", 40, 8)
    codes, _ = build_strands(g, ConstraintSpecV2(), 7, np.zeros(1, np.uint8), np.array([9]), np.array([3]),
                             rng.integers(0, 256, (1, 40), dtype=np.uint8))
    frame, _ = get_mapping("2bit").decode(codes)
    for errors in range(0, 5):
        damaged = frame[0].copy()
        for pos in rng.choice(g.frame_bytes, errors, replace=False):
            damaged[pos] ^= 0x5A
        parsed = parse_one_corrected(g, damaged, np.zeros(g.frame_bytes, bool)) if errors else ((0,), 0)
        assert parsed is not None, errors
    very = frame[0].copy()
    very[:12] ^= 0xFF
    assert parse_one_corrected(g, very, np.zeros(g.frame_bytes, bool)) is None


def test_reverse_complement_reads_parse_after_flipping():
    g = FrameGeometry("2bit", 40, 8)
    codes, _ = build_strands(g, ConstraintSpecV2(), 5, np.zeros(3, np.uint8), np.arange(3), np.arange(3), rng.integers(0, 256, (3, 40), dtype=np.uint8))
    rc = reverse_complement_codes(codes)
    assert not parse_batch(g, *get_mapping("2bit").decode(rc)).ok.any()
    assert parse_batch(g, *get_mapping("2bit").decode(reverse_complement_codes(rc))).ok.all()


def test_tentative_address_reads_the_header_without_the_crc():
    g = FrameGeometry("2bit", 40, 8)
    codes, _ = build_strands(g, ConstraintSpecV2(), 0x01020304, np.zeros(1, np.uint8), np.array([77]), np.array([12]),
                             rng.integers(0, 256, (1, 40), dtype=np.uint8))
    frames, _ = get_mapping("2bit").decode(codes)
    damaged = frames[0].copy()
    damaged[40] ^= 1  # payload damage: CRC fails, header still readable
    assert not parse_batch(g, damaged[None, :]).ok[0]
    assert tentative_address(g, damaged) == (0, 0x01020304, 77, 12)


def test_v2_constraint_check_matches_v1_for_v1_rules_and_scalar_reference_for_tandem_repeats():
    codes = rng.integers(0, 4, (3000, 120), dtype=np.uint8)
    for spec in (ConstraintSpec(), ConstraintSpec(gc_window_nt=20, max_homopolymer=3, forbidden_motifs=("ACGTA",))):
        assert (satisfied(codes, spec) == satisfied_v2(codes, spec)).all()
    spec = ConstraintSpecV2(max_tandem_repeat_nt=8, gc_min_percent=0, gc_max_percent=100, max_homopolymer=0)
    ref = np.array([analyze_v2(to_string(r), spec)["valid"] for r in codes[:1500]])
    assert (satisfied_v2(codes[:1500], spec) == ref).all()
    assert longest_tandem_repeat("ACACACAC", 2) == 8 and longest_tandem_repeat("AGTAGTAGTA", 3) == 10
    with pytest.raises(ConfigurationError):
        ConstraintSpecV2(max_tandem_repeat_nt=4)
