"""V3 ECC engine and decoder regressions: the vectorised RS decoder, batched correction and indel repair, and the
decoder fixes found in the V2 audit (docs/V3_AUDIT.md, E1, D1–D7)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from v2_support import FAST, mixed_bytes, write
from vnxdna.dna.constraints import ConstraintSpec
from vnxdna.dna.mapping import get_mapping
from vnxdna.ecc import rs_batch
from vnxdna.ecc.cauchy import CauchyErasureCode
from vnxdna.ecc.inner_rs import InnerReedSolomon
from vnxdna.errors import MetadataError
from vnxdna.v2 import api
from vnxdna.v2.archive import store_file
from vnxdna.v2.constraints import ConstraintSpecV2
from vnxdna.v2.decoder import DecodeOptionsV2
from vnxdna.v2.encoder import META_MAGIC, encode_file, stripe_rows
from vnxdna.v2.frame import KIND_META, FrameGeometry, build_strands, correct_frames, parse_batch, parse_many_corrected
from vnxdna.v2.strandio import MAX_READ_NT, ReadBatch, serialize_batch
from vnxdna.v2.sync import repair_read


# ---------------------------------------------------------------- vectorised RS decoder
def _codewords(rng, n_words: int, n: int, nsym: int) -> np.ndarray:
    msgs = rng.integers(0, 256, (n_words, n - nsym), dtype=np.uint8)
    return np.concatenate([msgs, InnerReedSolomon(nsym).encode_batch(msgs)], axis=1)


def _damage(rng, cws: np.ndarray, errors: np.ndarray, erasures: np.ndarray):
    recv = cws.copy()
    mask = np.zeros(cws.shape, dtype=bool)
    for i in range(cws.shape[0]):
        pos = rng.permutation(cws.shape[1])
        e, f = int(errors[i]), int(erasures[i])
        recv[i, pos[:e]] ^= rng.integers(1, 256, e, dtype=np.uint8)
        mask[i, pos[e:e + f]] = True
        recv[i, pos[e:e + f]] ^= rng.integers(0, 256, f, dtype=np.uint8)
    return recv, mask


@pytest.mark.parametrize("nsym,n", [(2, 20), (4, 63), (8, 69), (8, 255), (12, 100), (16, 111), (32, 200), (64, 255)])
def test_batch_decoder_corrects_everything_inside_the_rs_bound(nsym, n):
    rng = np.random.default_rng(nsym * 1000 + n)
    cws = _codewords(rng, 600, n, nsym)
    f = rng.integers(0, nsym + 1, 600)
    e = (nsym - f) // 2 - rng.integers(0, 2, 600).clip(0, None)
    e = e.clip(0, None)
    recv, mask = _damage(rng, cws, e, f)
    out, ok, errata = rs_batch.decode_batch(recv, nsym, mask)
    assert ok.all()
    assert (out == cws).all()
    assert (errata <= nsym).all()


def test_batch_decoder_matches_reedsolo_inside_the_bound_and_is_strict_beyond_it():
    rng = np.random.default_rng(5)
    nsym, n = 8, 60
    inner = InnerReedSolomon(nsym)
    cws = _codewords(rng, 1500, n, nsym)
    e, f = rng.integers(0, 8, 1500), rng.integers(0, 9, 1500)
    recv, mask = _damage(rng, cws, e, f)
    out, ok, _ = rs_batch.decode_batch(recv, nsym, mask)
    for i in range(1500):
        ref = inner.correct_codeword(recv[i].tobytes(), np.flatnonzero(mask[i]).tolist())
        if 2 * e[i] + f[i] <= nsym:
            assert ok[i] and ref is not None and ref[0] == out[i].tobytes() == cws[i].tobytes()
        if ok[i]:
            # bounded distance: the returned codeword is within 2e + f <= nsym of what was received
            diff = out[i] != recv[i]
            assert 2 * int((diff & ~mask[i]).sum()) + int(mask[i].sum()) <= nsym
            assert not rs_batch.syndromes(out[i:i + 1], nsym).any()


def test_reedsolo_miscorrections_outside_the_radius_are_refused_by_the_batch_decoder():
    """E1: reedsolo (the V2 inner decoder) returns codewords at 2e + f = nsym + 1; the V3 decoder never does."""
    rng = np.random.default_rng(11)
    nsym, n = 8, 60
    inner = InnerReedSolomon(nsym)
    found = 0
    for _ in range(40):
        cws = _codewords(rng, 400, n, nsym)
        recv, mask = _damage(rng, cws, rng.integers(1, 9, 400), np.full(400, 7))
        out, ok, _ = rs_batch.decode_batch(recv, nsym, mask)
        for i in range(400):
            ref = inner.correct_codeword(recv[i].tobytes(), np.flatnonzero(mask[i]).tolist())
            if ref is None:
                continue
            diff = np.frombuffer(ref[0], dtype=np.uint8) != recv[i]
            if 2 * int((diff & ~mask[i]).sum()) + int(mask[i].sum()) > nsym:
                found += 1
                assert not ok[i]
        if found >= 3:
            break
    assert found >= 3, "expected reedsolo to return out-of-radius codewords for this seed"


def test_batch_decoder_edge_cases():
    rng = np.random.default_rng(1)
    cws = _codewords(rng, 5, 30, 6)
    out, ok, errata = rs_batch.decode_batch(cws, 6)
    assert ok.all() and (out == cws).all() and not errata.any()
    too_many = np.ones((1, 30), dtype=bool)
    out, ok, _ = rs_batch.decode_batch(cws[:1], 6, too_many)
    assert not ok[0] and (out == cws[:1]).all()
    out, ok, _ = rs_batch.decode_batch(np.zeros((0, 30), np.uint8), 6)
    assert out.shape == (0, 30) and ok.shape == (0,)
    with pytest.raises(ValueError):
        rs_batch.decode_batch(np.zeros((2, 300), np.uint8), 6)
    with pytest.raises(ValueError):
        rs_batch.decode_batch(cws, 6, np.zeros((5, 29), bool))


# ---------------------------------------------------------------- batched frame correction and indel repair
GEOMETRY = FrameGeometry("2bit", 40, 8)


def _strands(n: int, seed: int = 0):
    rng = np.random.default_rng(seed)
    payloads = rng.integers(0, 256, (n, 40), dtype=np.uint8)
    codes, _ = build_strands(GEOMETRY, ConstraintSpec(), 0xA1B2C3D4, np.zeros(n, np.uint8), np.arange(n, dtype=np.uint32),
                             np.zeros(n, np.uint8), payloads)
    return codes, payloads


def test_correct_frames_accepts_only_crc_verified_corrections():
    codes, payloads = _strands(200)
    mapping = get_mapping("2bit")
    frames, erasures = mapping.decode(codes)
    rng = np.random.default_rng(3)
    damaged = frames.copy()
    for i in range(200):
        k = 4 if i < 150 else 9  # 4 byte errors are within r = 8; 9 are not
        damaged[i, rng.choice(frames.shape[1], k, replace=False)] ^= rng.integers(1, 256, k, dtype=np.uint8)
    fixed, accepted, counts = correct_frames(GEOMETRY, damaged, erasures)
    assert accepted[:150].all() and (fixed[:150] == frames[:150]).all() and (counts[:150] == 4).all()
    assert (fixed[accepted] == frames[accepted]).all()  # nothing wrong is ever accepted
    parsed, _ = parse_many_corrected(GEOMETRY, damaged, erasures)
    assert (parsed.payload[parsed.ok] == payloads[parsed.ok]).all()
    assert parse_batch(GEOMETRY, fixed[:, :GEOMETRY.systematic_bytes]).ok[accepted].all()


@pytest.mark.parametrize("indels", [1, 2])
def test_indel_repair_recovers_same_direction_indels(indels):
    codes, payloads = _strands(12, seed=indels)
    rng = np.random.default_rng(indels)
    for i in range(12):
        read = codes[i]
        for _ in range(indels):
            read = np.delete(read, int(rng.integers(read.size)))
        result = repair_read(read, GEOMETRY, max_indel=indels, max_candidates=10_000, reverse_complement=False)
        assert result is not None
        assert result[0][2] == i and result[0][4] == payloads[i].tobytes()


def test_indel_repair_handles_reads_that_also_contain_n():
    """D7: VNX-DNA 2.0 skipped any read with an N."""
    codes, payloads = _strands(10, seed=9)
    for i in range(10):
        read = np.delete(codes[i], 100).astype(np.uint8)
        read[20] = 4  # N
        result = repair_read(read, GEOMETRY, max_indel=1, reverse_complement=True)
        assert result is not None and result[0][4] == payloads[i].tobytes()


# ---------------------------------------------------------------- decoder fixes
def _archive_fasta(tmp_path: Path, name: str = "a", size: int = 30_000, seed: int = 1):
    data = mixed_bytes(size, seed)
    src = write(tmp_path / f"{name}.bin", data)
    store_file(src, tmp_path / f"{name}.vxdna", options=FAST, workers=1)
    encode_file(tmp_path / f"{name}.vxdna", tmp_path / f"{name}.fasta", workers=1)
    labels, seqs = [], []
    for line in (tmp_path / f"{name}.fasta").read_text().splitlines():
        (labels if line.startswith(">") else seqs).append(line)
    return data, seqs


def _fasta(path: Path, seqs: list[str]) -> Path:
    path.write_text("".join(f">r{i}\n{s}\n" for i, s in enumerate(seqs)))
    return path


def _substitute(seq: str, pos: int) -> str:
    return seq[:pos] + {"A": "C", "C": "G", "G": "T", "T": "A"}[seq[pos]] + seq[pos + 1:]


def test_long_junk_reads_do_not_abort_decoding_of_noisy_pools(tmp_path):
    """D1: with no error-free read and a few reads longer than 1,020 nt, VNX-DNA 2.0 exited with a configuration error."""
    data, seqs = _archive_fasta(tmp_path)
    noisy = [_substitute(s, 30) for s in seqs] + ["ACGT" * 275] * 3
    r = api.recover(_fasta(tmp_path / "n.fasta", noisy), tmp_path / "o.bin", workers=1)
    assert r["status"] == "RECOVERED" and (tmp_path / "o.bin").read_bytes() == data


def test_forged_metadata_lengths_cannot_force_large_allocations(tmp_path):
    """D2: a 4 KB pool declaring a 1 GiB index made VNX-DNA 2.0 allocate hundreds of MiB before failing."""
    geometry = FrameGeometry("2bit", 40, 8)
    head = META_MAGIC + (100).to_bytes(4, "big") + (1 << 30).to_bytes(4, "big") + (16).to_bytes(4, "big")
    stream = head + bytes(8 * 40 - len(head))
    rows, stripes, shards = stripe_rows(stream, CauchyErasureCode(8, 8), 40, 0, shorten=False)
    codes, _ = build_strands(geometry, ConstraintSpecV2(), 0xDEADBEEF, np.full(len(rows), KIND_META, np.uint8), stripes, shards, rows)
    data, _, _ = serialize_batch(ReadBatch.from_matrix(codes), "fasta", None)
    evil = tmp_path / "evil.fasta"
    evil.write_bytes(data)
    code = ("import sys; from vnxdna.v2 import api\n"
            "try:\n    api.recover(sys.argv[1], sys.argv[2], workers=1)\nexcept Exception as e: print(type(e).__name__)\n"
            "print([int(l.split()[1]) for l in open('/proc/self/status') if l.startswith('VmHWM')][0] // 1024)")
    r = subprocess.run([sys.executable, "-c", code, str(evil), str(tmp_path / "o.bin")], capture_output=True, text=True, check=True)
    error, peak_mib = r.stdout.split()
    assert error == "InsufficientRedundancyError"
    assert int(peak_mib) < 200  # VNX-DNA 2.0 grew linearly with the forged length (413 MiB for 2^30, ~3 GB extrapolated)


def test_over_long_lines_are_read_with_bounded_memory(tmp_path):
    """D3: iterating the file object read whole lines (a 190 MiB line cost 430 MiB in VNX-DNA 2.0)."""
    path = tmp_path / "big.fasta"
    with path.open("wb") as f:
        f.write(b">x\n")
        for _ in range(96):
            f.write(b"ACGT" * (1 << 18))  # 96 MiB, one line
        f.write(b"\n>y\nACGT\n")
    # VmHWM is this process's own peak (ru_maxrss would include the forking pytest process's memory)
    code = ("import sys; from vnxdna.v2.strandio import iter_batches\n"
            "b=list(iter_batches(sys.argv[1]))\n"
            "hwm=[int(l.split()[1]) for l in open('/proc/self/status') if l.startswith('VmHWM')][0]\n"
            "print(b[0].count, int(b[0].lengths[0]), int(b[0].lengths[1]), hwm//1024)")
    r = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=True)
    count, first, second, peak_mib = map(int, r.stdout.split())
    assert (count, first, second) == (2, MAX_READ_NT + 1, 4)
    assert peak_mib < 150


def test_metadata_repaired_by_the_outer_code_is_reported_as_recovered(tmp_path):
    """D4: VNX-DNA 2.0 reported SUCCESS although half of every metadata group had to be rebuilt."""
    data, seqs = _archive_fasta(tmp_path)
    mapping = get_mapping("2bit")
    geometry = FrameGeometry("2bit", 24, 8)
    codes = np.stack([np.frombuffer(s.encode(), np.uint8) for s in seqs])
    codes = np.select([codes == 65, codes == 67, codes == 71], [0, 1, 2], 3).astype(np.uint8)
    frames, _ = mapping.decode(codes)
    parsed = parse_batch(geometry, frames)
    assert parsed.ok.all()
    drop = set()
    for stripe in np.unique(parsed.stripe[parsed.kind == KIND_META]).tolist():
        rows = np.flatnonzero((parsed.kind == KIND_META) & (parsed.stripe == stripe))
        drop.update(rows[:8].tolist())
    kept = [s for i, s in enumerate(seqs) if i not in drop]
    r = api.recover(_fasta(tmp_path / "m.fasta", kept), tmp_path / "o.bin", workers=1)
    assert r["status"] == "RECOVERED"
    assert r["recovery"]["metadata_groups_repaired"] >= 1 and r["recovery"]["metadata_shards_erased"] == len(drop)
    assert (tmp_path / "o.bin").read_bytes() == data


def test_reads_with_iupac_symbols_are_decoded_with_erasures(tmp_path):
    """D5: a read containing a non-ACGTN symbol was dropped entirely by VNX-DNA 2.0."""
    data, seqs = _archive_fasta(tmp_path)
    damaged = [s[:40] + "R" + s[41:] if i % 3 == 0 else s for i, s in enumerate(seqs)]
    r = api.recover(_fasta(tmp_path / "i.fasta", damaged), tmp_path / "o.bin", workers=1)
    assert r["reads"]["reads_invalid_symbols"] == len(range(0, len(seqs), 3))
    assert r["reads"]["reads_valid"] == len(seqs)
    assert (tmp_path / "o.bin").read_bytes() == data


def test_a_pool_with_two_archives_is_decoded_by_archive_tag(tmp_path):
    """D6: VNX-DNA 2.0 refused any pool that held metadata of two archives."""
    data_a, seqs_a = _archive_fasta(tmp_path, "a", seed=1)
    data_b, seqs_b = _archive_fasta(tmp_path, "b", size=20_000, seed=2)
    pool = _fasta(tmp_path / "pool.fasta", seqs_a + seqs_b)
    with pytest.raises(MetadataError, match="--archive-tag"):
        api.recover(pool, tmp_path / "o.bin", workers=1)
    for data, name in ((data_a, "a"), (data_b, "b")):
        tag = api.info(tmp_path / f"{name}.vxdna")["archive_id"][:8]
        out = tmp_path / f"o-{name}.bin"
        api.recover(pool, out, options=DecodeOptionsV2(archive_tag=tag), workers=1)
        assert out.read_bytes() == data
    with pytest.raises(Exception):
        DecodeOptionsV2(archive_tag="XYZ")
