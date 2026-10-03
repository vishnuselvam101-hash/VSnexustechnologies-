"""Fuzzing: malformed archives, manifests, frames, read files and configurations.

Every malformed input must end in a controlled VNXError (or an explicit, verified success when the mutation was a
no-op): never another exception type, never a hang, never unbounded allocation, never wrong bytes reported valid.
"""
import json
import os
import struct

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import config as cf
from vnxdna.v4 import container as ct
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.constraints import ConstraintConfig
from vnxdna.v4.errors import VNXError
from vnxdna.v4.frame import PROFILES, decode_frames
from vnxdna.v4.reads import iter_reads
from vnxdna.v4.util import canonical_json

FUZZ = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


def _check_archive(path, original_files: dict) -> str:
    """Open + verify + extract a possibly corrupted archive; any success must reproduce the original bytes exactly."""
    try:
        ar.verify_container(path)
    except VNXError:
        return "rejected"
    out = path.parent / ("out-" + path.name)
    try:
        ar.extract(path, out, overwrite=True)
    except VNXError:
        return "rejected"
    for rel, data in original_files.items():
        assert (out / rel).read_bytes() == data, "corrupted archive produced different bytes reported as valid"
    return "accepted"


@pytest.fixture
def fuzz_archive(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    files = {"d/a.bin": os.urandom(30_000), "d/b.txt": b"fuzz " * 3000}
    for rel, data in files.items():
        (tmp_path / rel).write_bytes(data)
    out = tmp_path / "f.vnx"
    ar.build_archive([d], out, ar.ArchiveOptions(chunk_size=8192))
    return out, files


@FUZZ
@given(data=st.data())
def test_fuzz_byte_flips(tmp_path, fuzz_archive, data):
    src, files = fuzz_archive
    raw = bytearray(src.read_bytes())
    n = data.draw(st.integers(1, 8))
    for _ in range(n):
        pos = data.draw(st.integers(0, len(raw) - 1))
        raw[pos] ^= data.draw(st.integers(1, 255))
    if bytes(raw) == src.read_bytes():
        return                           # flips cancelled out: the file is unchanged, nothing to detect
    p = tmp_path / "m.vnx"
    p.write_bytes(bytes(raw))
    assert _check_archive(p, files) == "rejected"     # any flipped byte is covered by some integrity check


@FUZZ
@given(data=st.data())
def test_fuzz_truncation_insertion_deletion(tmp_path, fuzz_archive, data):
    src, files = fuzz_archive
    raw = src.read_bytes()
    op = data.draw(st.sampled_from(["truncate", "insert", "delete", "append"]))
    pos = data.draw(st.integers(0, len(raw)))
    junk = data.draw(st.binary(min_size=1, max_size=64))
    mutated = {"truncate": raw[:pos], "insert": raw[:pos] + junk + raw[pos:], "delete": raw[:pos] + raw[pos + len(junk):],
               "append": raw + junk}[op]
    if mutated == raw:
        return
    p = tmp_path / "m.vnx"
    p.write_bytes(mutated)
    assert _check_archive(p, files) == "rejected"


@FUZZ
@given(st.binary(min_size=0, max_size=4096))
def test_fuzz_random_files_as_containers(tmp_path, blob):
    p = tmp_path / "r.vnx"
    p.write_bytes(ct.MAGIC + struct.pack(">HHI", 4, 0, 0) + blob if blob[:1] == b"\x00" else blob)
    with pytest.raises(VNXError):
        ct.open_container(p)


def _rebuild_with_manifest(src, tmp_path, manifest_bytes: bytes):
    """Re-wrap a container with a replacement manifest and a *valid* trailer (so only the semantic checks can catch it)."""
    import hashlib
    raw = src.read_bytes()
    _, (body, c, f, r, m), _, _ = ct.read_header_trailer(src)
    head = raw[: ct.HEADER_BYTES + body + c + f + r]
    trailer = struct.pack(">QQQQQ", body, c, f, r, len(manifest_bytes)) + hashlib.sha256(manifest_bytes).digest() + ct.TRAILER_MAGIC
    data = head + manifest_bytes + trailer
    p = tmp_path / "man.vnx"
    p.write_bytes(data + hashlib.sha256(data).digest())
    return p


MUTATIONS = [
    ("format", "VNX5"), ("format_version", [5, 0]), ("format_version", [4, 9]), ("required_features", ["vnx4-container", "time-travel"]),
    ("archive_id", "zz"), ("chunking", {"algorithm": "cdc", "chunk_size": 8192}), ("chunking", {"algorithm": "fixed", "chunk_size": 1}),
    ("compression", {"algorithm": "lzma", "level": 1, "policy": "keep-if-smaller"}), ("encryption", {"algorithm": "ROT13"}),
    ("counts", {"files": -1}), ("tables", {}), ("integrity", {"merkle": "md5"}), ("extensions", []),
]


@pytest.mark.parametrize("key,value", MUTATIONS)
def test_fuzz_semantic_manifest_mutations(tmp_path, fuzz_archive, key, value):
    src, files = fuzz_archive
    m = ct.open_container(src).manifest
    m[key] = value
    p = _rebuild_with_manifest(src, tmp_path, canonical_json(m))
    with pytest.raises(VNXError):
        ct.open_container(p)


def test_fuzz_counts_and_root_mutations(tmp_path, fuzz_archive):
    src, _ = fuzz_archive
    base = ct.open_container(src).manifest
    for mut in ({"counts": {**base["counts"], "content_bytes": base["counts"]["content_bytes"] + 1}},
                {"counts": {**base["counts"], "files": base["counts"]["files"] + 1}},
                {"integrity": {**base["integrity"], "merkle_root": "00" * 32}},
                {"tables": {**base["tables"], "refs": {**base["tables"]["refs"], "sha256": "00" * 32}}}):
        p = _rebuild_with_manifest(src, tmp_path, canonical_json({**base, **mut}))
        with pytest.raises(VNXError):
            ct.open_container(p)


def test_fuzz_noncanonical_and_hostile_json(tmp_path, fuzz_archive):
    src, _ = fuzz_archive
    m = ct.open_container(src).manifest
    for blob in (json.dumps(m, indent=1).encode(), b'{"a":1,"a":2}', b"[" * 100_000, b'{"x": 1.5}', b"\xff\xfe",
                 canonical_json(m)[:-1]):
        p = _rebuild_with_manifest(src, tmp_path, blob[: ct.MAX_MANIFEST_BYTES])
        with pytest.raises(VNXError):
            ct.open_container(p)


@FUZZ
@given(data=st.data())
def test_fuzz_frames_never_accept_wrong_payload(data):
    lay = PROFILES["v4-balanced"][0]
    from vnxdna.v4.frame import build_strands, nt_to_bytes
    from vnxdna.v4.sync import strip_markers_exact
    rng = np.random.default_rng(data.draw(st.integers(0, 2 ** 32 - 1)))
    pay = rng.integers(0, 256, (64, lay.payload_bytes), dtype=np.uint8)
    strands, _ = build_strands(lay, ConstraintConfig(), 7, 0, np.arange(64), np.arange(64), pay)
    frames = nt_to_bytes(strip_markers_exact(lay, strands)[0])
    nerr = data.draw(st.integers(0, lay.frame_bytes))
    for i in range(64):
        pos = rng.permutation(lay.frame_bytes)[:nerr]
        frames[i, pos] ^= rng.integers(1, 256, nerr).astype(np.uint8)
    p = decode_frames(lay, frames)
    assert not (p.ok & ~(p.payload == pay).all(axis=1)).any()


@FUZZ
@given(st.lists(st.text(alphabet="ACGTNacgtnXYZ>@+\n\r \t!", max_size=400), max_size=30))
def test_fuzz_read_file_parser(tmp_path, lines):
    p = tmp_path / "r.txt"
    p.write_text("\n".join(lines))
    try:
        for batch in iter_reads(p, 7):
            assert batch.codes.max(initial=0) <= 4 and batch.lengths.sum() == batch.codes.size
    except VNXError:
        pass


def test_fuzz_hostile_read_files(tmp_path):
    cases = {
        "huge_line.fasta": ">a\n" + "A" * 300_000 + "\n",
        "huge_fastq.fastq": "@a\n" + "A" * 200_000 + "\n+\n" + "I" * 200_000 + "\n",
        "binary.fasta": os.urandom(5000).decode("latin-1"),
        "gzip.fastq.gz": "\x1f\x8b\x08\x00garbage",
        "no_header.fasta": "ACGT\n>x\nACGT\n",
        "plus_missing.fastq": "@a\nACGT\nIIII\n@b\nACGT\n+\nIIII\n",
    }
    for name, text in cases.items():
        p = tmp_path / name
        p.write_bytes(text.encode("latin-1"))
        with pytest.raises(VNXError):
            de.decode_reads(p, tmp_path / "x.vnx", de.DecodeOptions(profile="v4-balanced"))
        assert not (tmp_path / "x.vnx").exists()


def test_fuzz_random_dna_and_shuffled_valid_reads(tmp_path, small_strands):
    strands, _ = small_strands
    rng = np.random.default_rng(1)
    lines = strands.read_text().splitlines()
    seqs = lines[1::2]
    garbage = ["".join(rng.choice(list("ACGT"), rng.integers(1, 600))) for _ in range(3000)]
    mixed = seqs + garbage
    rng.shuffle(mixed)
    (tmp_path / "m.fasta").write_text("".join(f">r{i}\n{s}\n" for i, s in enumerate(mixed)))
    res = de.decode_reads(tmp_path / "m.fasta", tmp_path / "x.vnx", de.DecodeOptions(profile="v4-balanced"))
    assert res.status == "SUCCESS"      # garbage must not prevent or corrupt the decode


def test_fuzz_corrupted_ecc_and_forged_frames(tmp_path, small_archive, small_strands):
    """Strands whose payload is replaced by forged frames with valid CRC but wrong content must not produce output."""
    strands, rep = small_strands
    lay = PROFILES["v4-balanced"][0]
    from vnxdna.v4.frame import build_strands
    lines = strands.read_text().splitlines()
    nsb = sum(en.Superblock.symbols(lay.payload_bytes))
    forged, _ = build_strands(lay, ConstraintConfig(), int(rep["archive_tag"], 16), 0, np.zeros(40, dtype=np.int64),
                              np.arange(40, dtype=np.int64), np.zeros((40, lay.payload_bytes), dtype=np.uint8))
    acgt = np.frombuffer(b"ACGT", dtype=np.uint8)
    forged_lines = [">f\n" + acgt[f].tobytes().decode() for f in forged]
    # replace group 0's genuine strands with forged ones: data must not decode to anything other than the original
    kept = lines[: 2 * nsb] + lines[2 * nsb + 160:]
    (tmp_path / "forged.fasta").write_text("\n".join(kept + forged_lines) + "\n")
    try:
        res = de.decode_reads(tmp_path / "forged.fasta", tmp_path / "x.vnx")
        assert res.status != "SUCCESS" or (tmp_path / "x.vnx").read_bytes() == small_archive.read_bytes()
        assert res.status != "SUCCESS"
    except VNXError:
        pass
    assert not (tmp_path / "x.vnx").exists() or (tmp_path / "x.vnx").read_bytes() == small_archive.read_bytes()


@FUZZ
@given(st.dictionaries(st.sampled_from(["archive", "dna", "channel", "decode", "constraints", "performance", "bogus"]),
                       st.one_of(st.integers(), st.text(max_size=5), st.dictionaries(st.text(max_size=8), st.integers(), max_size=3)),
                       max_size=4))
def test_fuzz_configuration_files(cfg):
    try:
        cf.validate_config(cfg)
    except VNXError:
        pass


@FUZZ
@given(st.dictionaries(st.sampled_from(list(ch.ChannelConfig.__dataclass_fields__) + ["nope"]),
                       st.one_of(st.floats(allow_nan=True), st.integers(-5, 10**6), st.text(max_size=4)), max_size=6))
def test_fuzz_channel_configuration(cfg):
    try:
        ch.ChannelConfig.from_dict(cfg).validate()
    except VNXError:
        pass


def test_fuzz_superblock_bytes():
    rng = np.random.default_rng(5)
    for _ in range(2000):
        blob = rng.integers(0, 256, en.SB_BYTES, dtype=np.uint8).tobytes()
        with pytest.raises(VNXError):
            en.Superblock.unpack(blob)
