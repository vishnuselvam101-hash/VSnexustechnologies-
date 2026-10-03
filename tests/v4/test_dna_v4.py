"""DNA layer: codecs, frame, constraints, sync, channel and the full reads → container decoder."""

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from vnxdna.ecc import rs_batch
from vnxdna.ecc.inner_rs import InnerReedSolomon
from vnxdna.v4 import channel as ch
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4 import rs_fast
from vnxdna.v4.codecs import CauchyRSCodec, InnerRS, LTFountainCodec
from vnxdna.v4.constraints import ConstraintConfig, diagnose, satisfied_batch, to_codes, validate_file
from vnxdna.v4.errors import (VNXAddressError, VNXConfigurationError, VNXConstraintError, VNXDecodeError, VNXError, VNXFormatError)
from vnxdna.v4.frame import PROFILES, build_strands, bytes_to_nt, decode_frames, nt_to_bytes
from vnxdna.v4.sync import TemplateAligner, frame_erasures_to_bytes, strip_markers_exact

RNG = np.random.default_rng(11)


# ---------------------------------------------------------------- codecs
def test_cauchy_any_k_of_n_and_m_plus_one_fails():
    c = CauchyRSCodec(8, 4)
    d = RNG.integers(0, 256, (8, 16), dtype=np.uint8)
    e = c.encode(d)
    import itertools
    for lost in itertools.combinations(range(12), 4):
        syms = {i: e[i] for i in range(12) if i not in lost}
        assert (c.decode(syms, 8, 16) == d).all()
    with pytest.raises(VNXDecodeError):
        c.decode({i: e[i] for i in range(7)}, 8, 16)


def test_cauchy_shortened_block():
    c = CauchyRSCodec(64, 16)
    d = RNG.integers(0, 256, (5, 40), dtype=np.uint8)
    e = c.encode(d)
    assert e.shape == (21, 40)
    keep = RNG.permutation(21)[:5]
    assert (c.decode({int(i): e[i] for i in keep}, 5, 40) == d).all()


@pytest.mark.parametrize("dist", ["dense", "robust-soliton"])
def test_lt_fountain_complete_and_insufficient(dist):
    lt = LTFountainCodec(64, 32, distribution=dist)
    d = RNG.integers(0, 256, (64, 20), dtype=np.uint8)
    e = lt.encode_block(d, 3)
    assert (lt.decode_block({i: e[i] for i in range(e.shape[0])}, 64, 20, 3) == d).all()
    with pytest.raises(VNXDecodeError):
        lt.decode_block({i: e[i] for i in range(40)}, 64, 20, 3)


def test_lt_dense_recovers_with_small_overhead():
    lt = LTFountainCodec(128, 64)
    d = RNG.integers(0, 256, (128, 8), dtype=np.uint8)
    e = lt.encode_block(d, 0)
    wins = 0
    for t in range(20):
        keep = np.random.default_rng(t).permutation(e.shape[0])[:140]
        try:
            assert (lt.decode_block({int(i): e[i] for i in keep}, 128, 8, 0) == d).all()
            wins += 1
        except VNXDecodeError:
            pass
    assert wins >= 18   # 12 extra symbols: failure probability ≈ 2^-12 per trial in theory


def test_lt_deterministic_graph():
    a, b = LTFountainCodec(50, 10, seed=7), LTFountainCodec(50, 10, seed=7)
    assert all((a.neighbours(1, j, 50) == b.neighbours(1, j, 50)).all() for j in range(50, 60))


def test_fast_rs_kernels_bit_identical_to_v3():
    for n, r in [(70, 16), (70, 20), (66, 12), (255, 32)]:
        code = InnerReedSolomon(r)
        msg = RNG.integers(0, 256, (800, n - r), dtype=np.uint8)
        rx = np.concatenate([msg, code.encode_batch(msg)], 1)
        er = np.zeros_like(rx, dtype=bool)
        for i in range(len(rx)):
            e, f = RNG.integers(0, r // 2 + 3), RNG.integers(0, r + 3)
            pos = RNG.permutation(n)[:e + f]
            rx[i, pos[:e]] ^= RNG.integers(1, 256, e).astype(np.uint8)
            er[i, pos[e:]] = True
        a, b = rs_batch.decode_batch(rx, r, er), rs_fast.decode_batch(rx, r, er)
        assert all((x == y).all() for x, y in zip(a, b))


def test_inner_parity_matches_v3():
    m = RNG.integers(0, 256, (100, 54), dtype=np.uint8)
    assert (InnerRS(16).parity(m) == InnerReedSolomon(16).encode_batch(m)).all()


# ---------------------------------------------------------------- frame + constraints
@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_frames_round_trip_and_satisfy_constraints(profile):
    lay = PROFILES[profile][0]
    pay = RNG.integers(0, 256, (300, lay.payload_bytes), dtype=np.uint8)
    cfg = ConstraintConfig()
    strands, var = build_strands(lay, cfg, 0xABCD, 0, np.arange(300), np.arange(300) % 80, pay)
    assert strands.shape == (300, lay.strand_nt)
    assert satisfied_batch(strands, cfg).all()
    fb, mism = strip_markers_exact(lay, strands)
    assert (mism == 0).all()
    p = decode_frames(lay, nt_to_bytes(fb))
    assert p.ok.all() and (p.payload == pay).all() and (p.tag == 0xABCD).all() and (p.group == np.arange(300)).all()


def test_bit_mapping_round_trip():
    b = RNG.integers(0, 256, (10, 33), dtype=np.uint8)
    assert (nt_to_bytes(bytes_to_nt(b)) == b).all()


def test_substitutions_within_bound_corrected_beyond_never_wrong():
    lay = PROFILES["v4-balanced"][0]
    pay = RNG.integers(0, 256, (400, lay.payload_bytes), dtype=np.uint8)
    strands, _ = build_strands(lay, ConstraintConfig(), 1, 0, np.arange(400), np.zeros(400), pay)
    fb, _ = strip_markers_exact(lay, strands)
    frames = nt_to_bytes(fb)
    for nerr, expect_all in ((lay.inner_parity // 2, True), (lay.inner_parity, False)):
        rx = frames.copy()
        for i in range(len(rx)):
            pos = RNG.permutation(lay.frame_bytes)[:nerr]
            rx[i, pos] ^= RNG.integers(1, 256, nerr).astype(np.uint8)
        p = decode_frames(lay, rx)
        assert not (p.ok & ~(p.payload == pay).all(1)).any()       # never a wrong accepted frame
        if expect_all:
            assert p.ok.all()


def test_unsatisfiable_constraints_fail_explicitly():
    lay = PROFILES["v4-balanced"][0]
    cfg = ConstraintConfig(gc_min_percent=70, gc_max_percent=72)
    with pytest.raises(VNXConstraintError):
        build_strands(lay, cfg, 1, 0, np.arange(4), np.arange(4), RNG.integers(0, 256, (4, 40), dtype=np.uint8), max_variants=8)


def test_constraint_diagnostics():
    d = diagnose("GGGGGGCCCCAT", ConstraintConfig(max_homopolymer=4))
    assert not d["valid"] and set(d["violations"]) == {"GC_CONTENT", "HOMOPOLYMER"} and d["max_homopolymer"] == 6
    d = diagnose("ACGTACGTGAATTC", ConstraintConfig(forbidden_motifs=["GAATTC"]))
    assert "FORBIDDEN_MOTIF" in d["violations"]
    assert diagnose("ACGTNACGT", ConstraintConfig(gc_min_percent=0, gc_max_percent=100))["violations"] == ["INVALID_BASE"]
    assert diagnose("ACGTACGTAC", ConstraintConfig())["valid"]


@pytest.mark.parametrize("bad", [dict(gc_min_percent=70, gc_max_percent=30), dict(max_homopolymer=-1),
                                 dict(forbidden_motifs=["ACXT"]), dict(max_tandem_repeat_nt=3), dict(bogus=1)])
def test_invalid_constraint_config(bad):
    with pytest.raises(VNXConfigurationError):
        ConstraintConfig.from_dict(bad)


def test_validate_file_reports_violations(tmp_path):
    f = tmp_path / "s.fasta"
    f.write_text(">a\nACGTACGTAC\n>b\nAAAAAAAAGC\n")
    rep = validate_file(f, ConstraintConfig())
    assert rep["sequences"] == 2 and rep["invalid_sequences"] == 1 and rep["violation_counts"]["HOMOPOLYMER"] == 1


@settings(max_examples=40, deadline=None)
@given(st.lists(st.sampled_from("ACGT"), min_size=1, max_size=200))
def test_vectorised_homopolymer_matches_scalar(seq):
    s = "".join(seq)
    longest = max(len(r) for r in __import__("re").findall(r"A+|C+|G+|T+", s))
    cfg = ConstraintConfig(gc_min_percent=0, gc_max_percent=100, max_homopolymer=3)
    assert bool(satisfied_batch(to_codes(s)[None, :], cfg)[0]) == (longest <= 3)


# ---------------------------------------------------------------- sync
def _mutate(rng, s, nins, ndel):
    s = list(s)
    for _ in range(ndel):
        del s[rng.integers(len(s))]
    for _ in range(nins):
        s.insert(rng.integers(len(s) + 1), int(rng.integers(4)))
    return np.array(s, dtype=np.uint8)


@pytest.mark.parametrize("nins,ndel", [(1, 0), (0, 1), (2, 0), (0, 2), (1, 1)])
def test_marker_sync_repairs_indels_at_coverage_one(nins, ndel):
    lay = PROFILES["v4-indel"][0]
    pay = RNG.integers(0, 256, (200, lay.payload_bytes), dtype=np.uint8)
    strands, _ = build_strands(lay, ConstraintConfig(), 2, 0, np.arange(200), np.zeros(200), pay)
    rng = np.random.default_rng(nins * 10 + ndel)
    reads = [_mutate(rng, s, nins, ndel) for s in strands]
    pr = TemplateAligner(lay).project(reads)
    p = decode_frames(lay, nt_to_bytes(np.minimum(pr.bases, 3)), frame_erasures_to_bytes(pr.erased), errors_only_retry=False)
    good = p.ok & (p.payload == pay).all(1)
    assert not (p.ok & ~(p.payload == pay).all(1)).any()
    assert good.mean() >= 0.95


def test_sync_clean_read_has_no_erasures():
    lay = PROFILES["v4-balanced"][0]
    pay = RNG.integers(0, 256, (20, 40), dtype=np.uint8)
    strands, _ = build_strands(lay, ConstraintConfig(), 2, 0, np.arange(20), np.zeros(20), pay)
    pr = TemplateAligner(lay).project(list(strands))
    assert pr.ok.all() and not pr.erased.any() and (pr.insertions == 0).all() and (pr.deletions == 0).all()


def test_sync_rejects_reads_outside_band():
    lay = PROFILES["v4-balanced"][0]
    pr = TemplateAligner(lay, band=4).project([np.zeros(lay.strand_nt + 10, dtype=np.uint8), np.zeros(5, dtype=np.uint8)])
    assert not pr.ok.any() and pr.erased.all()


# ---------------------------------------------------------------- channel
def test_channel_deterministic_and_worker_independent(tmp_path, small_strands):
    strands, _ = small_strands
    cfg = ch.ChannelConfig(substitution_rate=0.01, insertion_rate=0.002, deletion_rate=0.002, dropout_rate=0.05, coverage=4,
                           coverage_model="negative-binomial", duplication_rate=0.05, homopolymer_indel_multiplier=3.0,
                           gc_bias_strength=0.5, n_rate=0.001, reverse_complement_rate=0.3, quality_informative=0.5, seed=99)
    a = ch.simulate_file(strands, tmp_path / "a.fastq", cfg, workers=1)
    b = ch.simulate_file(strands, tmp_path / "b.fastq", cfg, workers=3)
    assert (tmp_path / "a.fastq").read_bytes() == (tmp_path / "b.fastq").read_bytes()
    assert a["reads"] == b["reads"] > 0
    cfg2 = ch.ChannelConfig(**{**cfg.to_dict(), "seed": 100})
    ch.simulate_file(strands, tmp_path / "c.fastq", cfg2)
    assert (tmp_path / "c.fastq").read_bytes() != (tmp_path / "a.fastq").read_bytes()


def test_channel_rates_are_respected():
    codes = np.random.default_rng(0).integers(0, 4, (1000, 200)).astype(np.uint8)
    cfg = ch.ChannelConfig(substitution_rate=0.01, insertion_rate=0.005, deletion_rate=0.002, coverage=5, seed=1).validate()
    res = ch.simulate_batch(codes, cfg, 0)
    bases = 1000 * 200 * 5
    s = res["stats"]
    for key, rate in (("substitutions", 0.01), ("insertions", 0.005), ("deletions", 0.002)):
        assert abs(s[key] / bases - rate) < 0.15 * rate + 2e-4
    assert s["reads"] == 5000


def test_channel_full_dropout_and_homopolymer_model():
    codes = np.zeros((50, 100), dtype=np.uint8)      # one long homopolymer per strand
    res = ch.simulate_batch(codes, ch.ChannelConfig(dropout_rate=1.0, coverage=5).validate(), 0)
    assert res["stats"]["reads"] == 0
    base = ch.simulate_batch(codes, ch.ChannelConfig(deletion_rate=0.001, coverage=20, seed=2).validate(), 0)["stats"]["deletions"]
    hp = ch.simulate_batch(codes, ch.ChannelConfig(deletion_rate=0.001, coverage=20, seed=2, homopolymer_indel_multiplier=10.0)
                           .validate(), 0)["stats"]["deletions"]
    assert hp > 5 * base


@pytest.mark.parametrize("bad", [dict(substitution_rate=1.5), dict(coverage=-1), dict(coverage_model="zipf"), dict(seed=-3),
                                 dict(substitution_rate=0.3, insertion_rate=0.3), dict(unknown=1), dict(burst_rate=0.1)])
def test_invalid_channel_config(bad):
    with pytest.raises(VNXConfigurationError):
        ch.ChannelConfig.from_dict(bad)


# ---------------------------------------------------------------- full decoder
def test_clean_dna_round_trip_identical_container(tmp_path, small_archive, small_strands):
    strands, rep = small_strands
    res = de.decode_reads(strands, tmp_path / "r.vnx")
    assert res.status == "SUCCESS"
    assert (tmp_path / "r.vnx").read_bytes() == small_archive.read_bytes()


def test_encoding_is_deterministic(tmp_path, small_archive, small_strands):
    strands, _ = small_strands
    en.encode_container(small_archive, tmp_path / "b.fasta", en.DNAOptions(workers=2, groups_per_task=3))
    assert (tmp_path / "b.fasta").read_bytes() == strands.read_bytes()


@pytest.mark.parametrize("name,cfg", [
    ("substitution", dict(substitution_rate=0.005, coverage=3, coverage_model="poisson")),
    ("insertion", dict(insertion_rate=0.002, coverage=3, coverage_model="poisson")),
    ("deletion", dict(deletion_rate=0.002, coverage=3, coverage_model="poisson")),
    ("mixed+dropout", dict(substitution_rate=0.003, insertion_rate=0.001, deletion_rate=0.001, dropout_rate=0.05, coverage=5,
                           coverage_model="poisson")),
    ("duplicates+rc+N", dict(substitution_rate=0.002, coverage=2, duplication_rate=0.2, reverse_complement_rate=0.5, n_rate=0.002)),
])
def test_noisy_channels_recover(tmp_path, small_archive, small_strands, name, cfg):
    strands, _ = small_strands
    ch.simulate_file(strands, tmp_path / "r.fastq", ch.ChannelConfig(seed=5, **cfg))
    res = de.decode_reads(tmp_path / "r.fastq", tmp_path / "r.vnx", de.DecodeOptions(workers=2))
    assert res.status == "SUCCESS", res.report
    assert (tmp_path / "r.vnx").read_bytes() == small_archive.read_bytes()


def test_total_dropout_fails_controlled(tmp_path, small_strands):
    strands, _ = small_strands
    ch.simulate_file(strands, tmp_path / "r.fastq", ch.ChannelConfig(dropout_rate=0.0, coverage=1, seed=1))
    # keep only data strands: superblock strands lost
    lines = (tmp_path / "r.fastq").read_text().splitlines()
    nsb = sum(en.Superblock.symbols(40))
    (tmp_path / "nosb.fastq").write_text("\n".join(lines[4 * nsb:]) + "\n")
    with pytest.raises(VNXDecodeError):
        de.decode_reads(tmp_path / "nosb.fastq", tmp_path / "x.vnx")
    assert not (tmp_path / "x.vnx").exists()


def test_excess_dropout_is_partial_or_failure_never_success(tmp_path, small_strands):
    strands, _ = small_strands
    ch.simulate_file(strands, tmp_path / "r.fastq", ch.ChannelConfig(dropout_rate=0.45, coverage=1, seed=3))
    try:
        res = de.decode_reads(tmp_path / "r.fastq", tmp_path / "x.vnx", partial_dir=tmp_path / "partial")
        assert res.status in ("PARTIAL", "FAILURE")
        assert res.report["groups_failed"] > 0
    except VNXDecodeError:
        pass
    assert not (tmp_path / "x.vnx").exists()


def test_partial_recovery_extracts_only_verified_files(tmp_path, dataset):
    from vnxdna.v4 import archive as ar
    arc = tmp_path / "p.vnx"
    ar.build_archive([dataset], arc, ar.ArchiveOptions(chunk_size=16384, compression="none"))
    strands = tmp_path / "p.fasta"
    en.encode_container(arc, strands, en.DNAOptions(data_symbols=16, parity_symbols=2))
    lines = strands.read_text().splitlines()
    nsb = sum(en.Superblock.symbols(40))
    # delete 3 data symbols of group 0 (beyond M = 2): the first file's first bytes are lost
    kept = lines[:2 * nsb] + lines[2 * nsb + 6:]
    (tmp_path / "damaged.fasta").write_text("\n".join(kept) + "\n")
    res = de.decode_reads(tmp_path / "damaged.fasta", tmp_path / "x.vnx", partial_dir=tmp_path / "part")
    assert res.status == "PARTIAL"
    assert res.report["files_lost"] and res.report["files_recovered"]
    assert not (tmp_path / "x.vnx").exists()
    for name in res.report["files_recovered"]:
        assert (tmp_path / "part" / name).read_bytes() == (dataset.parent / name).read_bytes()


def test_random_garbage_reads_fail_controlled(tmp_path):
    rng = np.random.default_rng(0)
    with open(tmp_path / "g.fasta", "w") as f:
        for i in range(2000):
            f.write(f">g{i}\n" + "".join(rng.choice(list("ACGT"), 296)) + "\n")
    with pytest.raises(VNXDecodeError):
        de.decode_reads(tmp_path / "g.fasta", tmp_path / "x.vnx", de.DecodeOptions(profile="v4-balanced"))


def test_wrong_archive_tag_and_mixed_pools(tmp_path, dataset, small_strands):
    from vnxdna.v4 import archive as ar
    strands, rep = small_strands
    other = tmp_path / "o.vnx"
    (tmp_path / "one").mkdir()
    (tmp_path / "one" / "f").write_bytes(b"different archive" * 100)
    ar.build_archive([tmp_path / "one"], other)
    en.encode_container(other, tmp_path / "o.fasta", en.DNAOptions())
    pool = tmp_path / "pool.fasta"
    pool.write_bytes(strands.read_bytes() + (tmp_path / "o.fasta").read_bytes())
    with pytest.raises(VNXAddressError):
        de.decode_reads(pool, tmp_path / "x.vnx")
    tag = int(rep["archive_tag"], 16)
    res = de.decode_reads(pool, tmp_path / "x.vnx", de.DecodeOptions(archive_tag=tag))
    assert res.status == "SUCCESS"
    with pytest.raises(VNXAddressError):
        de.decode_reads(pool, tmp_path / "y.vnx", de.DecodeOptions(archive_tag=0x0001 if tag != 1 else 2))


def test_selective_random_access_from_reads(tmp_path, dataset):
    from vnxdna.v4 import archive as ar
    arc = tmp_path / "s.vnx"
    ar.build_archive([dataset], arc, ar.ArchiveOptions(chunk_size=16384, compression="none"))
    en.encode_container(arc, tmp_path / "s.fasta", en.DNAOptions())
    res = de.decode_reads(tmp_path / "s.fasta", None, select=["ds/sub/text.txt"], select_dir=tmp_path / "sel")
    assert res.status == "SUCCESS" and res.report["fraction_of_groups_decoded"] < 1.0
    assert (tmp_path / "sel" / "ds" / "sub" / "text.txt").read_bytes() == (dataset / "sub" / "text.txt").read_bytes()


def test_fountain_outer_code_requires_experimental_and_round_trips(tmp_path, small_archive):
    with pytest.raises(VNXConfigurationError):
        en.encode_container(small_archive, tmp_path / "f.fasta", en.DNAOptions(outer_code="lt-fountain"))
    en.encode_container(small_archive, tmp_path / "f.fasta", en.DNAOptions(outer_code="lt-fountain", experimental=True))
    ch.simulate_file(tmp_path / "f.fasta", tmp_path / "f.fastq", ch.ChannelConfig(dropout_rate=0.1, coverage=1, seed=4))
    res = de.decode_reads(tmp_path / "f.fastq", tmp_path / "f.vnx")
    assert res.status == "SUCCESS"
    assert (tmp_path / "f.vnx").read_bytes() == small_archive.read_bytes()


def test_malformed_read_files(tmp_path):
    cases = {"trunc.fastq": "@r1\nACGT\n+\n", "badq.fastq": "@r1\nACGT\n+\nII\n", "bin.fa": "\x00\x01\x02garbage",
             "empty.fasta": ""}
    for name, text in cases.items():
        p = tmp_path / name
        p.write_text(text)
        with pytest.raises(VNXError):
            de.decode_reads(p, tmp_path / "x.vnx", de.DecodeOptions(profile="v4-balanced"))


def test_superblock_pack_unpack_and_corruption():
    lay = PROFILES["v4-balanced"][0]
    sb = en.Superblock("cauchy-rs", 64, 16, lay, "dense", 0, bytes(range(16)), 10_000, bytes(32), 9000, -(-10_000 // 2560))
    raw = sb.pack()
    assert en.Superblock.unpack(raw) == sb
    bad = bytearray(raw)
    bad[40] ^= 1
    with pytest.raises(VNXFormatError):
        en.Superblock.unpack(bytes(bad))
