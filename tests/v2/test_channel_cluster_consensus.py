"""Sequencing simulator, clustering, consensus and alignment."""
import hashlib
import random
from collections import Counter

import numpy as np
import pytest

from v2_support import FAST, mixed_bytes, write
from vnxdna.dna.mapping import _COMPLEMENT
from vnxdna.errors import ConfigurationError
from vnxdna.v2 import api
from vnxdna.v2.align import GAP, align_batch, edit_distance_one
from vnxdna.v2.archive import store_file
from vnxdna.v2.cluster import cluster_file, iter_clusters
from vnxdna.v2.consensus import _vote, consensus_file
from vnxdna.v2.encoder import encode_file
from vnxdna.v2.sequencing import SequencingConfig, _edit, bernoulli_positions, sequence_file, simulate_batch


@pytest.fixture
def pool(tmp_path):
    data = mixed_bytes(30_000, seed=21)
    write(tmp_path / "in.bin", data)
    store_file(tmp_path / "in.bin", tmp_path / "a.vxdna", options=FAST)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.fasta")
    return tmp_path, data


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sequencing_is_deterministic_for_a_seed(pool):
    tmp, _ = pool
    cfg = SequencingConfig(seed=7, coverage=5, substitution_rate=0.01, insertion_rate=0.001, deletion_rate=0.001, dropout_rate=0.05,
                           reverse_complement_rate=0.5, duplication_rate=0.05)
    a = sequence_file(tmp / "s.fasta", tmp / "a.fastq", cfg)
    b = sequence_file(tmp / "s.fasta", tmp / "b.fastq", cfg)
    c = sequence_file(tmp / "s.fasta", tmp / "c.fastq", SequencingConfig(**{**cfg.to_dict(), "seed": 8}))
    assert a["output_sha256"] == b["output_sha256"] == _sha(tmp / "a.fastq") != c["output_sha256"]


def test_fixed_coverage_zero_error_mode_yields_exact_copies(pool):
    tmp, _ = pool
    r = sequence_file(tmp / "s.fasta", tmp / "r.fasta", SequencingConfig(coverage=3, coverage_model="fixed", shuffle=False))
    assert r["reads"] == 3 * r["strands"] and r["coverage_distribution"] == {3: r["strands"]}
    strands = [l for l in (tmp / "s.fasta").read_text().splitlines() if not l.startswith(">")]
    reads = [l for l in (tmp / "r.fasta").read_text().splitlines() if not l.startswith(">")]
    assert reads == [s for s in strands for _ in range(3)]


def test_event_counts_match_configured_rates_statistically():
    rng = np.random.default_rng(0)
    codes = rng.integers(0, 4, 4000 * 200).astype(np.uint8)
    cfg = SequencingConfig(seed=3, coverage=10, coverage_model="fixed", substitution_rate=0.01, insertion_rate=0.002,
                           deletion_rate=0.003, dropout_rate=0.1)
    _, per_strand, counts = simulate_batch(codes, np.full(4000, 200), cfg, 0, 200)
    bases = 4000 * 200 * 10 * (1 - counts["strands_dropped"] / 4000)
    for name, rate in (("sequencing_substitutions", 0.01), ("sequencing_insertions", 0.002), ("sequencing_deletions", 0.003)):
        expected = bases * rate
        assert abs(counts[name] - expected) < 5 * np.sqrt(expected), name
    assert abs(counts["strands_dropped"] - 400) < 5 * np.sqrt(400 * 0.9)
    assert (per_strand[per_strand > 0] == 10).all()


def test_bernoulli_positions_have_the_right_distribution():
    rng = np.random.default_rng(1)
    counts = [bernoulli_positions(rng, 50_000, 0.02).size for _ in range(300)]
    assert abs(np.mean(counts) - 1000) < 15 and abs(np.var(counts) - 980) < 250
    pos = bernoulli_positions(rng, 1000, 0.3)
    assert (np.diff(pos) > 0).all() and pos.min() >= 0 and pos.max() < 1000


def test_edit_bookkeeping_matches_a_scalar_reference():
    rng = np.random.default_rng(2)
    flat = rng.integers(0, 4, 3000).astype(np.uint8)
    lengths = np.array([1000, 1500, 500])
    counts = Counter()
    out, new_lengths, flags = _edit(flat, lengths, 0.0, 0.05, 0.05, np.random.default_rng(9), counts, "", np.zeros(3000, bool))
    assert new_lengths.sum() == out.size == 3000 + counts["insertions"] - counts["deletions"]
    assert flags.sum() == counts["insertions"]
    # replay the same random draws with a scalar implementation
    r = np.random.default_rng(9)
    deleted = set(bernoulli_positions(r, 3000, 0.05).tolist())
    inserted = bernoulli_positions(r, 3000, 0.05).tolist()
    bases = r.integers(0, 4, len(inserted)).astype(np.uint8)
    add = dict(zip(inserted, bases.tolist()))
    ref = []
    for i, b in enumerate(flat.tolist()):
        if i not in deleted:
            ref.append(b)
        if i in add:
            ref.append(add[i])
    assert out.tolist() == ref


def test_reverse_complemented_reads_are_exact_reverse_complements():
    rng = np.random.default_rng(3)
    codes = rng.integers(0, 4, 50 * 30).astype(np.uint8)
    reads, _, _ = simulate_batch(codes, np.full(50, 30), SequencingConfig(coverage=1, coverage_model="fixed",
                                                                          reverse_complement_rate=1.0, shuffle=False), 0, 30)
    for i in range(50):
        assert (reads.read(i) == _COMPLEMENT[codes[i * 30:(i + 1) * 30][::-1]]).all()


def test_informative_quality_marks_sequencing_errors():
    rng = np.random.default_rng(4)
    codes = rng.integers(0, 4, 2000 * 100).astype(np.uint8)
    reads, _, _ = simulate_batch(codes, np.full(2000, 100), SequencingConfig(seed=1, coverage=1, coverage_model="fixed",
                                                                             substitution_rate=0.02, quality_informativeness=1.0,
                                                                             shuffle=False), 0, 100)
    changed = reads.codes != codes
    assert (reads.quals[changed] <= 20).all() and (reads.quals[~changed] >= 30).all()


def test_vxs_output_refuses_length_changing_channels(pool):
    tmp, _ = pool
    with pytest.raises(ConfigurationError):
        sequence_file(tmp / "s.fasta", tmp / "r.vxs", SequencingConfig(insertion_rate=0.001))


def test_align_batch_equals_reference_edit_distance():
    rng = np.random.default_rng(5)

    def reference(a, b):
        d = np.zeros((len(a) + 1, len(b) + 1), int)
        d[:, 0], d[0, :] = range(len(a) + 1), range(len(b) + 1)
        for i in range(1, len(a) + 1):
            for j in range(1, len(b) + 1):
                d[i, j] = min(d[i - 1, j - 1] + (a[i - 1] != b[j - 1]), d[i - 1, j] + 1, d[i, j - 1] + 1)
        return d[-1, -1]

    for _ in range(150):
        ref = rng.integers(0, 4, int(rng.integers(15, 50))).astype(np.uint8)
        read = ref.copy()
        for _ in range(int(rng.integers(0, 5))):
            op, pos = int(rng.integers(0, 3)), int(rng.integers(0, max(1, read.size)))
            if op == 0 and read.size:
                read[pos] = (read[pos] + 1) % 4
            elif op == 1 and read.size:
                read = np.delete(read, pos)
            else:
                read = np.insert(read, pos, int(rng.integers(0, 4)))
        assert edit_distance_one(read, ref, 12) == reference(read.tolist(), ref.tolist())
    ref = rng.integers(0, 4, 60).astype(np.uint8)
    proj, cost, inserted, ok = align_batch([np.delete(ref, 30)], ref[None, :], 8)
    assert ok[0] and cost[0] == 2 and (proj[0] == GAP).sum() == 1


def test_consensus_writes_n_for_ambiguous_positions_instead_of_guessing():
    from vnxdna.v2.frame import FrameGeometry
    geometry = FrameGeometry("2bit", 1, 0)  # 16-byte frames = 64 nt; random reads are not valid frames
    length = geometry.strand_nt
    rng = random.Random(3)
    a = "".join(rng.choice("ACGT") for _ in range(length))
    b = a[:10] + ("T" if a[10] != "T" else "A") + a[11:]
    block = [{"id": 0, "reads": [a, b], "quals": ["I" * length, "I" * length], "verified": 0}]
    seq = _vote(block, geometry, 8, 0.15, 0.6, 10, Counter())[0]
    assert seq.size == length and seq[10] == 4 and (np.delete(seq, 10) != 4).all()
    block = [{"id": 0, "reads": [a, a, b], "quals": ["I" * length] * 3, "verified": 0}]
    assert (_vote(block, geometry, 8, 0.15, 0.6, 10, Counter())[0] != 4).all()


def test_iterative_consensus_repairs_indels_including_insertions():
    from vnxdna.v2.frame import FrameGeometry
    geometry = FrameGeometry("2bit", 1, 0)
    length = geometry.strand_nt
    rng = random.Random(4)
    truth = "".join(rng.choice("ACGT") for _ in range(length))
    reads = []
    for k in range(9):
        r = list(truth)
        pos = rng.randrange(5, length - 5)
        if k % 3 == 0:
            del r[pos]
        elif k % 3 == 1:
            r.insert(pos, rng.choice("ACGT"))
        else:
            r[pos] = "ACGT"[("ACGT".index(r[pos]) + 1) % 4]
        reads.append("".join(r))
    seed_is_wrong = [reads[1]] + reads  # a seed with an insertion must still converge to the truth
    block = [{"id": 0, "reads": seed_is_wrong, "quals": ["I" * len(r) for r in seed_is_wrong], "verified": 0}]
    seq = _vote(block, geometry, 8, 0.15, 0.6, 10, Counter())[0]
    assert "".join("ACGTN"[c] for c in seq) == truth


def test_cluster_and_consensus_recover_under_indels_and_read_order_does_not_matter(pool):
    tmp, data = pool
    cfg = SequencingConfig(seed=11, coverage=8, substitution_rate=0.004, insertion_rate=0.002, deletion_rate=0.002, dropout_rate=0.03,
                           reverse_complement_rate=0.5)
    sequence_file(tmp / "s.fasta", tmp / "reads.fastq", cfg)
    records = (tmp / "reads.fastq").read_text().splitlines()
    groups = [records[i:i + 4] for i in range(0, len(records), 4)]
    random.Random(1).shuffle(groups)
    (tmp / "shuffled.fastq").write_text("\n".join(line for g in groups for line in g) + "\n")
    results = []
    for name in ("reads", "shuffled"):
        cl = cluster_file(tmp / f"{name}.fastq", tmp / f"{name}.jsonl", workers=2)
        consensus_file(tmp / f"{name}.jsonl", tmp / f"{name}.cons.fasta")
        r = api.recover(tmp / f"{name}.cons.fasta", tmp / f"{name}.out")
        assert (tmp / f"{name}.out").read_bytes() == data
        results.append(r["recovery"]["shards_erased"])
        assert cl["stats"]["reads_tentative"] > 0
    assert results[0] == results[1]
    header = next(iter_clusters(tmp / "reads.jsonl"))
    assert header["format"] == "vnx-clusters-1"


def test_truncated_cluster_file_is_rejected(pool):
    tmp, _ = pool
    sequence_file(tmp / "s.fasta", tmp / "r.fastq", SequencingConfig(coverage=2))
    cluster_file(tmp / "r.fastq", tmp / "c.jsonl", workers=1)
    lines = (tmp / "c.jsonl").read_text().splitlines()
    (tmp / "t.jsonl").write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(Exception):
        consensus_file(tmp / "t.jsonl", tmp / "x.fasta")
    assert not (tmp / "x.fasta").exists()


def test_fallback_without_a_verifying_member_writes_the_invalid_consensus(pool):
    # A cluster that counts verified reads but none of whose reads verifies again (e.g. edited after clustering) used
    # to crash consensus with AttributeError; it is written as an "invalid" consensus, as without the fallback.
    import json
    tmp, _ = pool
    sequence_file(tmp / "s.fasta", tmp / "r.fastq", SequencingConfig(seed=3, coverage=3))
    cluster_file(tmp / "r.fastq", tmp / "c.jsonl", workers=1)
    lines = (tmp / "c.jsonl").read_text().splitlines()
    for i, line in enumerate(lines):
        rec = json.loads(line)
        if "reads" in rec and rec.get("verified", 0):
            rec["reads"] = ["ACGT" * 5 for _ in rec["reads"]]
            rec.pop("quals", None)
            lines[i] = json.dumps(rec)
            target = rec["id"]
            break
    (tmp / "e.jsonl").write_text("\n".join(lines) + "\n")
    res = consensus_file(tmp / "e.jsonl", tmp / "e.fasta")
    labels = [ln for ln in (tmp / "e.fasta").read_text().splitlines() if ln.startswith(f">c{target};")]
    assert len(labels) == 1 and labels[0].endswith(";invalid")
    assert res["stats"]["consensus_unverified_written"] >= 1
