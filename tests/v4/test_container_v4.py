"""VNX4 container: round trips, determinism, dedup, encryption, Merkle proofs, corruption and malformed input."""
import os

import numpy as np
import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import container as ct
from vnxdna.v4 import merkle
from vnxdna.v4.errors import (VNXConfigurationError, VNXError, VNXFormatError, VNXIntegrityError, VNXKeyError, VNXOutputError,
                              VNXUnsupportedVersionError)

from .conftest import tree_equal


def test_clean_round_trip_directory(tmp_path, dataset, small_archive):
    res = ar.extract(small_archive, tmp_path / "out")
    assert res["status"] == "EXTRACTED"
    assert tree_equal(dataset, tmp_path / "out" / "ds")
    assert (tmp_path / "out" / "ds" / "empty_dir").is_dir()
    assert (tmp_path / "out" / "ds" / "empty.dat").read_bytes() == b""


def test_deterministic_for_any_worker_count(tmp_path, dataset):
    digests = {ar.build_archive([dataset], tmp_path / f"w{w}.vnx", ar.ArchiveOptions(chunk_size=16384, workers=w)).container_sha256
               for w in (1, 2, 4)}
    assert len(digests) == 1


def test_dedup_stores_identical_chunks_once(tmp_path, dataset):
    rep = ar.build_archive([dataset], tmp_path / "d.vnx", ar.ArchiveOptions(chunk_size=16384))
    nodedup = ar.build_archive([dataset], tmp_path / "n.vnx", ar.ArchiveOptions(chunk_size=16384, dedup=False))
    assert rep.unique_chunks < rep.chunk_refs == nodedup.chunk_refs == nodedup.unique_chunks
    assert rep.container_bytes < nodedup.container_bytes


def test_verify_full_and_single_chunk(small_archive):
    assert ar.verify_container(small_archive)["status"] == "VERIFIED"
    c = ct.open_container(small_archive)
    for i in range(len(c.chunk_table)):
        r = ar.verify_container(small_archive, chunk=i)
        assert r["checks"]["merkle_proof"] is True


def test_list_locate_and_selective_extract(tmp_path, small_archive):
    names = [f["path"] for f in ar.list_container(small_archive)]
    assert names == sorted(names, key=lambda s: s.encode())
    loc = ar.locate(small_archive, "ds/random.bin")
    assert loc["file"]["size"] == 150_000 and loc["bytes_to_read"] <= 150_000 + 64
    res = ar.extract(small_archive, tmp_path / "sel", names=["ds/sub/text.txt"])
    assert res["files"] == 1 and not (tmp_path / "sel" / "ds" / "random.bin").exists()
    with pytest.raises(VNXFormatError):
        ar.locate(small_archive, "ds/missing")


@pytest.mark.parametrize("mode", ["key", "passphrase"])
def test_encrypted_round_trip_and_wrong_key(tmp_path, dataset, mode):
    key = os.urandom(32)
    opts = ar.ArchiveOptions(chunk_size=16384, key=key) if mode == "key" else \
        ar.ArchiveOptions(chunk_size=16384, passphrase="correct horse", scrypt={"n": 1024, "r": 8, "p": 1})
    out = tmp_path / "e.vnx"
    ar.build_archive([dataset], out, opts)
    kw = {"key": key} if mode == "key" else {"passphrase": "correct horse"}
    ar.extract(out, tmp_path / "o", **kw)
    assert tree_equal(dataset, tmp_path / "o" / "ds")
    raw = out.read_bytes()
    assert b"random.bin" not in raw and b"VNX-DNA V4 test line" not in raw   # file table and content are sealed
    bad = {"key": os.urandom(32)} if mode == "key" else {"passphrase": "wrong"}
    with pytest.raises(VNXKeyError):
        ar.extract(out, tmp_path / "o2", **bad)
    with pytest.raises(VNXKeyError):
        ar.list_container(out)            # no key: sealed table cannot be listed
    assert ar.verify_container(out)["status"] == "VERIFIED_STORED"


def test_no_plaintext_key_in_archive(tmp_path, dataset):
    key = os.urandom(32)
    out = tmp_path / "k.vnx"
    ar.build_archive([dataset], out, ar.ArchiveOptions(key=key))
    data = out.read_bytes()
    assert key not in data and key.hex().encode() not in data


def _flip(path, offset):
    data = bytearray(path.read_bytes())
    data[offset] ^= 0x40
    path.write_bytes(bytes(data))


def test_body_corruption_detected(tmp_path, small_archive):
    _flip(small_archive, ct.HEADER_BYTES + 100)
    with pytest.raises(VNXIntegrityError):
        ar.verify_container(small_archive)
    with pytest.raises(VNXIntegrityError):
        ar.extract(small_archive, tmp_path / "x")
    assert not list((tmp_path / "x").rglob("random.bin"))      # nothing unverified published


def test_every_table_and_manifest_byte_is_protected(tmp_path, small_archive):
    size = small_archive.stat().st_size
    _, (body, ctb, ft, rf, mn), _, _ = ct.read_header_trailer(small_archive)
    start = ct.HEADER_BYTES + body
    rng = np.random.default_rng(3)
    original = small_archive.read_bytes()
    for off in sorted(set(rng.integers(start, size, 60).tolist())):
        small_archive.write_bytes(original)
        _flip(small_archive, off)
        with pytest.raises(VNXError):
            ar.verify_container(small_archive)


def test_truncated_and_garbage_inputs(tmp_path, small_archive):
    data = small_archive.read_bytes()
    for cut in (0, 10, 100, len(data) // 2, len(data) - 1):
        p = tmp_path / f"t{cut}.vnx"
        p.write_bytes(data[:cut])
        with pytest.raises(VNXError):
            ct.open_container(p)
    g = tmp_path / "g.vnx"
    g.write_bytes(os.urandom(5000))
    with pytest.raises(VNXFormatError):
        ct.open_container(g)


def test_unsupported_version_and_flags(tmp_path, small_archive):
    data = bytearray(small_archive.read_bytes())
    v = bytearray(data)
    v[8:10] = (5).to_bytes(2, "big")
    p = tmp_path / "v.vnx"
    p.write_bytes(bytes(v))
    with pytest.raises(VNXUnsupportedVersionError):
        ct.open_container(p)
    f = bytearray(data)
    f[12:16] = (1).to_bytes(4, "big")
    p.write_bytes(bytes(f))
    with pytest.raises(VNXUnsupportedVersionError):
        ct.open_container(p)


def test_v3_container_is_refused_with_a_pointer(tmp_path):
    p = tmp_path / "old.vxdna"
    p.write_bytes(b"\x89VXDNA\r\n" + bytes(300))
    with pytest.raises(VNXUnsupportedVersionError):
        ct.open_container(p)


def test_merkle_matches_rfc6962_reference_and_proofs():
    rng = np.random.default_rng(0)
    for n in list(range(0, 18)) + [31, 32, 33, 100]:
        leaves = [merkle.leaf_hash(rng.bytes(20)) for _ in range(n)]
        root = merkle.root_from_leaves(leaves)
        assert root == merkle.root_recursive(leaves)
        for i in range(n):
            proof = merkle.inclusion_proof(leaves, i)
            assert merkle.verify_inclusion(leaves[i], i, n, proof, root)
            if n > 1:
                assert not merkle.verify_inclusion(leaves[(i + 1) % n], i, n, proof, root)


@pytest.mark.parametrize("bad", ["../evil", "/abs", "a//b", "a/./b", "a\\b", "a\x00b", ""])
def test_unsafe_archive_paths_rejected(bad):
    with pytest.raises(VNXFormatError):
        ct.validate_archive_path(bad)


def test_extract_refuses_symlink_escape(tmp_path, small_archive):
    out = tmp_path / "out"
    out.mkdir()
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", out / "ds")
    with pytest.raises(VNXOutputError):
        ar.extract(small_archive, out)
    assert not list((tmp_path / "elsewhere").iterdir())


def test_extract_does_not_overwrite_without_force(tmp_path, small_archive):
    ar.extract(small_archive, tmp_path / "o")
    with pytest.raises(VNXOutputError):
        ar.extract(small_archive, tmp_path / "o")
    ar.extract(small_archive, tmp_path / "o", overwrite=True)


def test_invalid_configuration_rejected(tmp_path, dataset):
    for opts in (ar.ArchiveOptions(chunk_size=10), ar.ArchiveOptions(compression="lzma"), ar.ArchiveOptions(workers=0),
                 ar.ArchiveOptions(key=b"k" * 32, passphrase="p")):
        with pytest.raises(VNXConfigurationError):
            ar.build_archive([dataset], tmp_path / "x.vnx", opts)


def test_symlinks_skipped_not_followed(tmp_path, dataset):
    os.symlink("/etc/passwd", dataset / "link")
    rep = ar.build_archive([dataset], tmp_path / "s.vnx")
    assert any("symlink" in w for w in rep.warnings)
    assert "ds/link" not in [f["path"] for f in ar.list_container(tmp_path / "s.vnx")]


def test_reordered_file_table_rejected(tmp_path, small_archive):
    c = ct.open_container(small_archive)
    recs = list(c.files)
    recs[0], recs[1] = recs[1], recs[0]
    blob = b"".join(r.pack() for r in recs)
    with pytest.raises(VNXFormatError):
        ct.parse_file_table(blob, len(recs), int(c.refs.size))


def test_decompression_bomb_bounded():
    """Regression (found in V4 development): zstd's max_output_size is ignored when the frame declares its content size.
    V4 decompresses through a bounded stream reader, so a hostile chunk cannot expand beyond its table size."""
    import zstandard
    bomb = zstandard.ZstdCompressor(write_content_size=True).compress(b"\x00" * (64 << 20))
    assert len(zstandard.ZstdDecompressor().decompress(bomb, max_output_size=1000)) == 64 << 20   # the trap being avoided
    with pytest.raises(VNXIntegrityError):
        ar.bounded_zstd(bomb, 16384, "chunk 0")
    assert ar.bounded_zstd(zstandard.ZstdCompressor().compress(b"abc" * 100), 300, "c") == b"abc" * 100
