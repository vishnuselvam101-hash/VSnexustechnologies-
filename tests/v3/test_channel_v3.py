"""V3 channel simulator: burst errors and regression tests for the audited simulator/read-processing bugs (B1–B3, B5, B10–B13)."""
import hashlib
import json
from collections import Counter

import numpy as np
import pytest

from v2_support import FAST, mixed_bytes, write
from vnxdna.errors import ConfigurationError, InvalidInputError, VNXDNAError
from vnxdna.v2 import api
from vnxdna.v2.archive import store_file
from vnxdna.v2.cluster import _HEAD_DTYPE, _cluster_orphans, _record, cluster_file, iter_clusters
from vnxdna.v2.consensus import consensus_file
from vnxdna.v2.encoder import encode_file
from vnxdna.v2.experiment import run_experiment
from vnxdna.v2.frame import FrameGeometry
from vnxdna.v2.sequencing import BATCH_TARGET_BASES, SequencingConfig, sequence_file, simulate_batch


@pytest.fixture
def pool(tmp_path):
    data = mixed_bytes(30_000, seed=21)
    write(tmp_path / "in.bin", data)
    store_file(tmp_path / "in.bin", tmp_path / "a.vxdna", options=FAST)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.fasta")
    return tmp_path, data


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _random_pool(n=300, length=120, seed=7):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 4, n * length).astype(np.uint8), np.full(n, length, dtype=np.int64)


# ---------------------------------------------------------------- V2 byte identity
# SHA-256 of VNX-DNA 2.0.0's output (v2.0.0 tag) for these inputs, recorded before the V3 changes. Channels without bursts at
# ordinary coverage must keep producing exactly these bytes for the same seed.
V2_GOLDEN = {
    ("plain", "fastq"): "8471aef79ba4d2edd0f4f9479c03552938dc4e957405e8721fda9fded7214c71",
    ("plain", "fasta"): "ba5f646a650e07504cc00ef3406ec0a56cc737c61495c2d47bbad891509c879b",
    ("mixed", "fastq"): "a97ff19750bdeba57938ff4ab46ea7ac5cec1391ee4018f6f3f302badf5fdd7c",
    ("mixed", "fasta"): "98da0ca85cc9360ff7ae53a3137ac983e3eff19e1b4ce74f83cd65cbec8e8318",
    ("noshuffle", "fastq"): "7c72f667fa940268cd373feb8ad5a9eaa469345f97a93f2104d27e8751641dce",
    ("noshuffle", "fasta"): "6572070021a85ed5ca3c64604ef7baee8f1c17acd34f6e1f2e30c9bba48273a9",
}
GOLDEN_CONFIGS = {
    "plain": dict(seed=3, coverage=5),
    "mixed": dict(seed=11, coverage=4, coverage_model="lognormal", abundance_sigma=0.5, dropout_rate=0.05,
                  synthesis_substitution_rate=0.002, synthesis_insertion_rate=0.001, synthesis_deletion_rate=0.001,
                  substitution_rate=0.01, insertion_rate=0.002, deletion_rate=0.002, duplication_rate=0.1, truncation_rate=0.1,
                  n_rate=0.01, invalid_read_rate=0.02, contamination_rate=0.02, reverse_complement_rate=0.5),
    "noshuffle": dict(seed=5, coverage=3, substitution_rate=0.01, shuffle=False),
}


def _golden_pool(tmp_path):
    rng = np.random.default_rng(7)
    lines = [f">s{i}\n" + "".join("ACGT"[c] for c in rng.integers(0, 4, 120)) + "\n" for i in range(300)]
    (tmp_path / "s.fasta").write_text("".join(lines))
    return tmp_path / "s.fasta"


@pytest.mark.parametrize("name,fmt", sorted(V2_GOLDEN))
def test_channels_without_bursts_reproduce_v2_output_exactly(tmp_path, name, fmt):
    src = _golden_pool(tmp_path)
    for extra in ({}, {"burst_rate": 0.0, "burst_kind": "mixed", "burst_length_mean": 9}):  # a zero burst rate draws nothing
        out = tmp_path / f"{name}-{len(extra)}.{fmt}"
        sequence_file(src, out, SequencingConfig(**GOLDEN_CONFIGS[name], **extra), fmt=fmt)
        assert _sha(out) == V2_GOLDEN[(name, fmt)]


# ---------------------------------------------------------------- bursts
def test_burst_configuration_is_validated():
    for bad in ({"burst_rate": 1.5}, {"burst_rate": -0.1}, {"burst_length_mean": 0.5}, {"burst_length_mean": 65},
                {"burst_kind": "chimera"}, {"burst_length_mean": True}):
        with pytest.raises(ConfigurationError):
            SequencingConfig(**bad)
    assert not SequencingConfig(burst_rate=0.5).changes_length  # substitution bursts keep the length
    assert SequencingConfig(burst_rate=0.5, burst_kind="deletion").changes_length
    assert SequencingConfig(burst_rate=0.5, burst_kind="mixed").changes_length


@pytest.mark.parametrize("kind", ["substitution", "deletion", "insertion"])
def test_each_burst_is_one_contiguous_run_and_counts_are_exact(kind):
    codes, lengths = _random_pool(n=400, length=100)
    cfg = SequencingConfig(seed=2, coverage=1, coverage_model="fixed", burst_rate=0.5, burst_length_mean=5, burst_kind=kind)
    reads, _, counts = simulate_batch(codes, lengths, cfg, 0, 100)
    bursts, bases = counts[f"bursts_{kind}"], counts[f"burst_bases_{kind}"]
    assert 150 < bursts < 250 and bases >= bursts
    assert sum(counts[f"bursts_{k}"] for k in ("substitution", "deletion", "insertion")) == bursts
    changed = 0
    for i in range(reads.count):
        before = codes[i * 100:(i + 1) * 100]
        after = reads.read(i)
        if kind == "substitution":
            diff = np.flatnonzero(after != before)
            if diff.size:
                changed += diff.size
                assert diff[-1] - diff[0] + 1 == diff.size  # one run, every base in it different
        elif after.size != before.size:
            d = abs(after.size - before.size)
            changed += d
            longer, shorter = (before, after) if kind == "deletion" else (after, before)
            # removing one contiguous run of d bases from the longer read gives the shorter one
            assert any(np.array_equal(np.concatenate([longer[:j], longer[j + d:]]), shorter) for j in range(longer.size - d + 1))
    assert changed == bases


def test_mixed_bursts_use_every_kind_and_are_deterministic():
    codes, lengths = _random_pool()
    cfg = SequencingConfig(seed=9, coverage=3, coverage_model="fixed", burst_rate=0.6, burst_kind="mixed")
    a, _, ca = simulate_batch(codes, lengths, cfg, 0, 120)
    b, _, cb = simulate_batch(codes, lengths, cfg, 0, 120)
    c, _, _ = simulate_batch(codes, lengths, SequencingConfig(**{**cfg.to_dict(), "seed": 10}), 0, 120)
    assert np.array_equal(a.codes, b.codes) and ca == cb and not np.array_equal(a.codes, c.codes)
    assert all(ca[f"bursts_{k}"] > 0 for k in ("substitution", "deletion", "insertion"))
    assert a.codes.size == 3 * codes.size - ca["burst_bases_deletion"] + ca["burst_bases_insertion"]


def test_burst_counts_reach_the_report(pool):
    tmp, _ = pool
    r = sequence_file(tmp / "s.fasta", tmp / "r.fastq", SequencingConfig(seed=1, coverage=2, burst_rate=0.3, burst_kind="mixed"))
    assert sum(r["errors"][f"bursts_{k}"] for k in ("substitution", "deletion", "insertion")) > 0
    assert r["config"]["burst_rate"] == 0.3


def test_substitution_bursts_within_the_inner_code_recover_at_coverage_one(pool):
    """FAST strands carry 8 inner parity bytes (4 byte errors); a 1–3 nt burst touches at most 2 bytes of the 2bit frame."""
    tmp, data = pool
    cfg = SequencingConfig(seed=4, coverage=1, coverage_model="fixed", burst_rate=1.0, burst_length_mean=1.5, burst_kind="substitution")
    seq = sequence_file(tmp / "s.fasta", tmp / "r.fastq", cfg)
    assert seq["errors"]["bursts_substitution"] == seq["strands"]  # every strand's only read carries a burst
    rep = api.recover(tmp / "r.fastq", tmp / "out.bin")
    assert (tmp / "out.bin").read_bytes() == data
    assert rep["reads"]["reads_inner_corrected"] > 0


def test_deletion_bursts_at_coverage_six_recover_or_fail_detectably_never_wrongly(pool):
    tmp, data = pool
    outcomes = Counter()
    for seed in range(3):
        cfg = SequencingConfig(seed=seed, coverage=6, burst_rate=0.3, burst_length_mean=3, burst_kind="deletion")
        sequence_file(tmp / "s.fasta", tmp / f"r{seed}.fastq", cfg)
        out = tmp / f"out{seed}.bin"
        try:
            cluster_file(tmp / f"r{seed}.fastq", tmp / f"c{seed}.jsonl", workers=1)
            consensus_file(tmp / f"c{seed}.jsonl", tmp / f"k{seed}.fasta")
            api.recover(tmp / f"k{seed}.fasta", out)
        except VNXDNAError:
            assert not out.exists()
            outcomes["detected"] += 1
            continue
        assert out.read_bytes() == data  # never wrong output
        outcomes["exact"] += 1
    # measured result (software simulation): consensus absorbs these deletion bursts at 6x coverage
    assert outcomes == Counter(exact=3)


# ---------------------------------------------------------------- B1 truncation past the read
def test_truncation_never_reads_past_a_read_that_indels_emptied():
    codes = np.array([0, 0, 0, 3, 3, 3], dtype=np.uint8)
    cfg = SequencingConfig(seed=6, coverage=1, coverage_model="fixed", deletion_rate=0.6, truncation_rate=1.0, shuffle=False)
    reads, source, _ = simulate_batch(codes, np.array([3, 3]), cfg, 0, 3)
    for i in range(reads.count):  # every base of a read comes from its own strand (2.0 copied a T into the A read)
        assert set(reads.read(i).tolist()) <= {0 if i == 0 else 3}
    assert (reads.lengths <= 3).all()


def test_total_deletion_with_truncation_does_not_crash(pool):
    tmp, _ = pool
    r = sequence_file(tmp / "s.fasta", tmp / "r.fastq", SequencingConfig(seed=0, coverage=2, deletion_rate=1.0, truncation_rate=0.5))
    assert r["bases_out"] == 0 and r["reads"] > 0


# ---------------------------------------------------------------- B2 / B12 bucket sizing, B3 batch sizing
def test_output_is_independent_of_the_input_file_format(pool, monkeypatch):
    tmp, _ = pool
    import vnxdna.v2.sequencing as sq
    monkeypatch.setattr(sq, "BUCKET_TARGET_BYTES", 4096)  # many shuffle buckets, as for a large pool
    encode_file(tmp / "a.vxdna", tmp / "s.vxs")
    cfg = SequencingConfig(seed=4, coverage=8, substitution_rate=0.01)
    a = sequence_file(tmp / "s.fasta", tmp / "a.fastq", cfg)
    b = sequence_file(tmp / "s.vxs", tmp / "b.fastq", cfg)
    assert a["output_sha256"] == b["output_sha256"]


def test_batches_shrink_with_coverage_so_memory_does_not_grow_with_it(pool):
    tmp, _ = pool
    low = sequence_file(tmp / "s.fasta", tmp / "l.fasta", SequencingConfig(coverage=10, shuffle=False))
    high = sequence_file(tmp / "s.fasta", tmp / "h.fasta", SequencingConfig(coverage=400, shuffle=False))
    assert low["batch_strands"] == 8192  # ordinary channels keep the 2.0 batches (and output)
    strand_nt = FAST.validate().strand_nt
    assert high["batch_strands"] == BATCH_TARGET_BASES // (strand_nt * 400) < 8192
    assert high["batch_strands"] * strand_nt * 400 <= BATCH_TARGET_BASES


# ---------------------------------------------------------------- B5 partial files
def test_failed_sequencing_leaves_no_partial_file(tmp_path):
    (tmp_path / "bad.fasta").write_text(">a\nACGTACGT\n>b\nACGTXXGT\n")
    with pytest.raises(InvalidInputError):
        sequence_file(tmp_path / "bad.fasta", tmp_path / "out.fastq", SequencingConfig(coverage=2))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad.fasta"]


def test_failed_clustering_leaves_no_partial_file(pool, monkeypatch):
    tmp, _ = pool
    sequence_file(tmp / "s.fasta", tmp / "r.fastq", SequencingConfig(seed=1, coverage=2))
    import vnxdna.v2.cluster as cl

    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(cl, "_cluster_orphans", boom)
    with pytest.raises(KeyboardInterrupt):
        cluster_file(tmp / "r.fastq", tmp / "c.jsonl", workers=1)
    assert not [p for p in tmp.iterdir() if "partial" in p.name or p.name == "c.jsonl"]


# ---------------------------------------------------------------- B6 malformed cluster files
def _cluster_file(tmp, lines, header=None):
    header = header or {"format": "vnx-clusters-1", "geometry": {"mapping": "2bit", "payload_bytes": 24, "inner_parity_bytes": 8}}
    path = tmp / "c.jsonl"
    path.write_text("\n".join([json.dumps(header)] + lines) + "\n")
    return path


@pytest.mark.parametrize("line", [
    '{"id": 0, "reads": ["ACGT"], "quals": ["II"]}',          # quality length differs
    '["not", "an", "object"]',                                 # not a dict
    '{"id": 0, "reads": []}',                                  # empty cluster
    '{"id": 0, "reads": ["ACG\\u00e9"]}',                      # non-ASCII read
    '{"id": 0, "reads": ["ACGT"], "quals": "IIII"}',           # quals not a list
    '{"id": 0, "reads": ["ACGT"',                              # truncated mid-record
])
def test_malformed_cluster_records_are_invalid_input(tmp_path, line):
    path = _cluster_file(tmp_path, [line, '{"end": true}'])
    with pytest.raises(InvalidInputError):
        list(iter_clusters(path))
    with pytest.raises(InvalidInputError):
        consensus_file(path, tmp_path / "k.fasta")
    assert not (tmp_path / "k.fasta").exists()


def test_cluster_file_truncated_mid_line_and_bad_headers_are_invalid_input(tmp_path):
    path = _cluster_file(tmp_path, ['{"id": 0, "reads": ["ACGT"]}', '{"end": true}'])
    raw = path.read_bytes()
    path.write_bytes(raw[:-5])  # cut inside the end record
    with pytest.raises(InvalidInputError):
        list(iter_clusters(path))
    path.write_bytes(b"\xff\xfe binary")
    with pytest.raises(InvalidInputError):
        list(iter_clusters(path))
    for header in ({"format": "vnx-clusters-1"}, {"format": "vnx-clusters-1", "geometry": {"mapping": "2bit", "payload_bytes": "x"}}):
        with pytest.raises(InvalidInputError):
            list(iter_clusters(_cluster_file(tmp_path, ['{"end": true}'], header)))
    bad_geometry = {"format": "vnx-clusters-1", "geometry": {"mapping": "2bit", "payload_bytes": 400, "inner_parity_bytes": 8}}
    with pytest.raises(InvalidInputError):
        consensus_file(_cluster_file(tmp_path, ['{"end": true}'], bad_geometry), tmp_path / "k.fasta")
    for missing in (tmp_path / "nope.jsonl", tmp_path):
        with pytest.raises(InvalidInputError):
            list(iter_clusters(missing))


# ---------------------------------------------------------------- B8 consensus parameters
@pytest.mark.parametrize("kwargs", [{"max_edit_fraction": -1}, {"max_edit_fraction": float("nan")}, {"min_winner_share": 5},
                                    {"min_winner_share": float("nan")}, {"band": 0}, {"single_read_min_quality": -1}])
def test_consensus_rejects_out_of_range_parameters(tmp_path, kwargs):
    path = _cluster_file(tmp_path, ['{"end": true}'])
    with pytest.raises(ConfigurationError):
        consensus_file(path, tmp_path / "k.fasta", **kwargs)


# ---------------------------------------------------------------- B10 experiment statistics of failed trials
def test_failed_trials_report_the_damage_they_saw(tmp_path):
    write(tmp_path / "in.bin", mixed_bytes(12_000, seed=3))
    r = run_experiment(tmp_path / "in.bin", tmp_path / "exp", channel=SequencingConfig(seed=0, coverage=1, coverage_model="fixed",
                       dropout_rate=0.35), trials=2, options=FAST, use_consensus=False, workers=1)
    s = r["summary"]
    assert s["failed_recovery"] == 2 and s["detected_failures"] == 2
    records = json.loads((tmp_path / "exp" / "results.json").read_text())["trials"]
    worst = [t.get("max_erasures_in_group") for t in records]
    # seed 0 loses the DNA manifest (no group statistics exist); seed 1 fails in ECC decoding and reports the worst group
    # (VNX-DNA 2.0 reported 0 for every failed trial)
    assert worst[0] is None and isinstance(worst[1], int) and worst[1] > FAST.parity_shards
    assert s["max_erasures_in_any_group"] == worst[1]
    assert "bursts" in (tmp_path / "exp" / "results.csv").read_text().splitlines()[0]


# ---------------------------------------------------------------- B11 observed rates and duplicates
def test_observed_rates_are_not_diluted_by_duplicates():
    codes, lengths = _random_pool(n=2000, length=100)
    rates = []
    for dup in (0.0, 1.0):
        cfg = SequencingConfig(seed=5, coverage=4, coverage_model="fixed", substitution_rate=0.01, duplication_rate=dup, shuffle=False)
        _, _, counts = simulate_batch(codes, lengths, cfg, 0, 100)
        rates.append(counts["sequencing_substitutions"] / counts["bases_designed_independent"])
    assert all(abs(r - 0.01) < 0.0015 for r in rates)  # 2.0 reported ~0.005 with duplication 1


# ---------------------------------------------------------------- B13 orphan cap independent of read order
def test_orphan_cap_keeps_the_same_reads_whatever_the_order(tmp_path):
    geometry = FrameGeometry("2bit", 24, 8)
    rng = np.random.default_rng(1)
    reads = [rng.integers(0, 4, geometry.strand_nt).astype(np.uint8) for _ in range(60)]
    head = np.zeros(1, dtype=_HEAD_DTYPE)[0]
    results = []
    for order in (list(range(60)), list(reversed(range(60)))):
        path = tmp_path / f"orphans{order[0]}"
        with path.open("wb") as handle:
            for i in order:
                h = head.copy()
                h["length"] = reads[i].size
                handle.write(_record(h, reads[i], np.full(reads[i].size, 30, np.uint8)))
        stats = Counter()
        clusters = _cluster_orphans(path, geometry, 20, 0.3, stats)
        results.append(sorted(tuple(c.tobytes() for c, _ in members) for members in clusters))
        assert stats["orphans_dropped_over_limit"] == 40
    assert results[0] == results[1]
