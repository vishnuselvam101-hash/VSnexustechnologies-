"""DNA layer (format 5): encode/decode round trips, ECC guarantees and their boundary, adversarial strand pools."""
import random
from pathlib import Path

import numpy as np
import pytest

from v2_support import FAST, KEY, mixed_bytes, write
from vnxdna.errors import InsufficientRedundancyError, InvalidInputError, MetadataError, UnrecoverableCorruptionError
from vnxdna.v2 import api
from vnxdna.v2.archive import store_file
from vnxdna.v2.decoder import DecodeOptionsV2, resolve_copies, record_dtype
from vnxdna.v2.encoder import cauchy_parity, encode_file, read_dna_index
from vnxdna.v2.strandio import ReadBatch, StrandWriter, iter_batches, vxs_info
from vnxdna.ecc.cauchy import CauchyErasureCode


def _archive(tmp_path: Path, size: int = 40_000, key=None, seed: int = 1, options=FAST):
    data = mixed_bytes(size, seed=seed)
    src = write(tmp_path / "in.bin", data)
    store_file(src, tmp_path / "a.vxdna", options=options, key=key)
    return data


def _read_fasta(path: Path) -> tuple[list[str], list[str]]:
    labels, seqs = [], []
    for line in path.read_text().splitlines():
        if line.startswith(">"):
            labels.append(line[1:])
        elif line:
            seqs.append(line)
    return labels, seqs


def _write_fasta(path: Path, seqs: list[str]) -> Path:
    path.write_text("".join(f">r{i}\n{s}\n" for i, s in enumerate(seqs)))
    return path


def test_cauchy_parity_fast_path_equals_generic_encoder():
    rng = np.random.default_rng(0)
    for k, m in ((8, 4), (64, 16), (128, 12), (32, 32), (5, 0)):
        code = CauchyErasureCode(k, m)
        data = rng.integers(0, 256, (7, k, 13), dtype=np.uint8)
        assert (cauchy_parity(code, data) == code.encode(data)).all()


@pytest.mark.parametrize("fmt", ["fasta", "vxs"])
@pytest.mark.parametrize("key", [None, KEY], ids=["plain", "encrypted"])
def test_encode_decode_gives_the_identical_container(tmp_path, fmt, key):
    _archive(tmp_path, key=key)
    encode_file(tmp_path / "a.vxdna", tmp_path / f"s.{fmt}", workers=2)
    api.decode(tmp_path / f"s.{fmt}", tmp_path / "d.vxdna", workers=2)
    assert (tmp_path / "d.vxdna").read_bytes() == (tmp_path / "a.vxdna").read_bytes()


def test_encoding_is_deterministic_across_worker_counts(tmp_path):
    _archive(tmp_path)
    encode_file(tmp_path / "a.vxdna", tmp_path / "one.vxs", workers=1)
    encode_file(tmp_path / "a.vxdna", tmp_path / "many.vxs", workers=4)
    assert (tmp_path / "one.vxs").read_bytes() == (tmp_path / "many.vxs").read_bytes()


def test_shuffled_and_duplicated_strands_recover_exactly(tmp_path):
    data = _archive(tmp_path)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.fasta")
    _, seqs = _read_fasta(tmp_path / "s.fasta")
    pool = seqs + random.Random(1).sample(seqs, len(seqs) // 2)
    random.Random(2).shuffle(pool)
    _write_fasta(tmp_path / "shuffled.fasta", pool)
    r = api.recover(tmp_path / "shuffled.fasta", tmp_path / "o.bin")
    assert (tmp_path / "o.bin").read_bytes() == data
    assert r["records_in_stripe_order"] is False and r["recovery"]["pass2_mode"].startswith("bucket-sort")


def _groups(tmp_path: Path) -> tuple[list[str], list[str], dict]:
    labels, seqs = _read_fasta(tmp_path / "s.fasta")
    groups: dict = {}
    for i, label in enumerate(labels):
        _, _, kind, stripe, shard = label.split(":")
        if kind == "d":
            groups.setdefault(int(stripe), []).append(i)
    return labels, seqs, groups


def test_losing_exactly_m_strands_in_every_ecc_group_recovers(tmp_path):
    data = _archive(tmp_path)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.fasta")
    labels, seqs, groups = _groups(tmp_path)
    rng = random.Random(3)
    drop = set()
    for members in groups.values():
        drop.update(rng.sample(members, min(FAST.parity_shards, len(members) - 1)))
    _write_fasta(tmp_path / "damaged.fasta", [s for i, s in enumerate(seqs) if i not in drop])
    r = api.recover(tmp_path / "damaged.fasta", tmp_path / "o.bin")
    assert (tmp_path / "o.bin").read_bytes() == data
    assert r["recovery"]["max_erasures_in_a_group"] == FAST.parity_shards


def test_losing_m_plus_one_strands_in_one_group_fails_clearly_without_output(tmp_path):
    _archive(tmp_path)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.fasta")
    labels, seqs, groups = _groups(tmp_path)
    full = next(m for m in groups.values() if len(m) == FAST.data_shards + FAST.parity_shards)
    drop = set(full[: FAST.parity_shards + 1])
    _write_fasta(tmp_path / "damaged.fasta", [s for i, s in enumerate(seqs) if i not in drop])
    with pytest.raises(InsufficientRedundancyError) as info:
        api.recover(tmp_path / "damaged.fasta", tmp_path / "o.bin")
    assert info.value.details["worst_group_erasures"] == FAST.parity_shards + 1
    assert not (tmp_path / "o.bin").exists()
    v = api.verify(tmp_path / "damaged.fasta")
    assert v["status"] == "FAIL" and v["exit_code"] == 5 and v["damaged_chunks"]


def test_substitutions_in_addresses_and_payloads_are_corrected_by_the_inner_code(tmp_path):
    data = _archive(tmp_path)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.fasta")
    _, seqs = _read_fasta(tmp_path / "s.fasta")
    rng = random.Random(4)
    damaged = []
    for s in seqs:
        chars = list(s)
        for pos in rng.sample(range(8, 44), 2):  # header region (address) of the frame
            chars[pos] = "ACGT"[("ACGT".index(chars[pos]) + 1) % 4]
        damaged.append("".join(chars))
    _write_fasta(tmp_path / "d.fasta", damaged)
    r = api.recover(tmp_path / "d.fasta", tmp_path / "o.bin")
    assert (tmp_path / "o.bin").read_bytes() == data and r["reads"]["reads_inner_corrected"] > 0


def test_conflicting_copies_majority_wins_and_ties_become_erasures():
    p = 4
    rec = record_dtype(p)
    rows = np.zeros(6, dtype=rec)
    rows["stripe"] = [1, 1, 1, 2, 2, 3]
    rows["shard"] = [0, 0, 0, 5, 5, 7]
    rows["payload"] = [[1] * 4, [1] * 4, [9] * 4, [3] * 4, [4] * 4, [8] * 4]
    stripes, shards, payloads, stats = resolve_copies(rows, p)
    assert list(zip(stripes.tolist(), shards.tolist())) == [(1, 0), (3, 7)]
    assert payloads[0].tolist() == [1] * 4
    assert stats["shards_conflict_majority"] == 1 and stats["shards_conflict_tie"] == 1 and stats["shards_unique"] == 1


def test_junk_invalid_foreign_and_truncated_reads_are_rejected_not_trusted(tmp_path):
    data = _archive(tmp_path)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.fasta")
    _, seqs = _read_fasta(tmp_path / "s.fasta")
    rng = random.Random(5)
    junk = ["".join(rng.choice("ACGT") for _ in range(len(seqs[0]))) for _ in range(200)]
    invalid = ["ACGTXYZ" * 30, "NNNN" * 63, ""]
    truncated = [s[: len(s) // 2] for s in seqs[:50]]
    (tmp_path / "other.bin").write_bytes(b"another archive" * 500)
    store_file(tmp_path / "other.bin", tmp_path / "o.vxdna", options=FAST)
    encode_file(tmp_path / "o.vxdna", tmp_path / "o.fasta")
    _, foreign = _read_fasta(tmp_path / "o.fasta")
    foreign_data = [s for s in foreign][: len(foreign) // 2]  # includes some metadata strands of the other archive? keep data only below
    pool = seqs + junk + invalid + truncated
    _write_fasta(tmp_path / "pool.fasta", pool)
    r = api.recover(tmp_path / "pool.fasta", tmp_path / "out.bin")
    assert (tmp_path / "out.bin").read_bytes() == data
    assert r["reads"]["reads_invalid_symbols"] >= 1 and r["reads"]["reads_length_mismatch"] >= 50
    # a pool mixing the metadata of two archives is refused rather than guessed
    _write_fasta(tmp_path / "mixed.fasta", seqs + foreign_data + foreign)
    with pytest.raises(MetadataError):
        api.recover(tmp_path / "mixed.fasta", tmp_path / "mixed.bin")


def test_malformed_fastq_and_vxs_are_rejected(tmp_path):
    (tmp_path / "bad.fastq").write_text("@r1\nACGT\n+\nII\n")
    with pytest.raises(InvalidInputError):
        list(iter_batches(tmp_path / "bad.fastq"))
    (tmp_path / "bad2.fastq").write_text("@r1\nACGT\nIIII\n")
    with pytest.raises(InvalidInputError):
        list(iter_batches(tmp_path / "bad2.fastq"))
    _archive(tmp_path)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.vxs")
    blob = (tmp_path / "s.vxs").read_bytes()
    (tmp_path / "t.vxs").write_bytes(blob[:-10])
    with pytest.raises(InvalidInputError):
        vxs_info(tmp_path / "t.vxs")
    (tmp_path / "noise.fasta").write_text(">x\nACGTACGT\n>y\nTTTTGGGG\n")
    with pytest.raises(UnrecoverableCorruptionError):
        api.recover(tmp_path / "noise.fasta", tmp_path / "o.bin")


def test_quality_scores_can_flag_erasures(tmp_path):
    data = _archive(tmp_path)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.fasta")
    _, seqs = _read_fasta(tmp_path / "s.fasta")
    rng = random.Random(6)
    lines = []
    for i, s in enumerate(seqs):
        chars, quals = list(s), ["I"] * len(s)
        for pos in rng.sample(range(len(s)), 6):  # 6 wrong bases: beyond errors-only correction (4), fine as erasures (8)
            chars[pos] = "ACGT"[("ACGT".index(chars[pos]) + 2) % 4]
            quals[pos] = "#"
        lines.append(f"@r{i}\n{''.join(chars)}\n+\n{''.join(quals)}\n")
    (tmp_path / "q.fastq").write_text("".join(lines))
    with pytest.raises(Exception):
        api.recover(tmp_path / "q.fastq", tmp_path / "no.bin")
    api.recover(tmp_path / "q.fastq", tmp_path / "o.bin", options=DecodeOptionsV2(quality_erasure_below=10))
    assert (tmp_path / "o.bin").read_bytes() == data


@pytest.mark.parametrize("fmt", ["fasta", "vxs"])
def test_random_access_from_dna_uses_the_index_and_reads_only_needed_strands(tmp_path, fmt):
    data = _archive(tmp_path, size=60_000)
    encode_file(tmp_path / "a.vxdna", tmp_path / f"s.{fmt}")
    index = read_dna_index(tmp_path / f"s.{fmt}.vxidx")
    r = api.extract(tmp_path / f"s.{fmt}", tmp_path / "part.bin", offset=20_000, length=3000)
    assert (tmp_path / "part.bin").read_bytes() == data[20_000:23_000]
    assert r["strands_processed"] < index["strands_total"] / 3
    assert r["chunks_processed"] == [4, 5]
    (tmp_path / f"s.{fmt}.vxidx").unlink()
    r2 = api.extract(tmp_path / f"s.{fmt}", tmp_path / "part2.bin", offset=20_000, length=3000)
    assert (tmp_path / "part2.bin").read_bytes() == data[20_000:23_000]
    assert r2["strands_processed"] == index["strands_total"]


def test_strand_writer_is_atomic(tmp_path):
    target = tmp_path / "x.vxs"
    with pytest.raises(RuntimeError):
        with StrandWriter(target, "vxs", strand_nt=8) as writer:
            writer.write_batch(ReadBatch.from_matrix(np.zeros((2, 8), np.uint8)))
            raise RuntimeError("interrupted")
    assert not target.exists() and not list(tmp_path.glob("*.partial"))
