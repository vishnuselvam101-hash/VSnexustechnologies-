"""Configuration matrix, special inputs, random access, tampering at the container level."""
import hashlib
import json
from pathlib import Path

import pytest

from conftest import FAST, OTHER_KEY, TEST_KEY, mixed_bytes
from vnxdna import api
from vnxdna.container import manifest as mf
from vnxdna.container import vxdna
from vnxdna.container.builder import StoreOptions, build_container
from vnxdna.dna.constraints import ConstraintSpec
from vnxdna.dna.reads import read_sequences
from vnxdna.errors import (ConfigurationError, InsufficientRedundancyError, IntegrityError, InvalidInputError, KeyRequiredError,
                           OutputError, WrongKeyError)

CONFIGS = {
    "fast-2bit": FAST,
    "default": StoreOptions(),
    "rotation3-zlib": StoreOptions(chunk_size=3000, mapping="rotation3", compression="zlib", compression_level=6,
                                   data_shards=10, parity_shards=5, payload_bytes=30, inner_parity_bytes=10),
    "codebook8-nocomp": StoreOptions(chunk_size=5000, mapping="codebook8", compression="none", compression_level=0,
                                     data_shards=4, parity_shards=2, payload_bytes=16, inner_parity_bytes=4,
                                     constraints=ConstraintSpec(45, 55, 3)),
    "no-parity-no-inner": StoreOptions(chunk_size=2048, data_shards=6, parity_shards=0, payload_bytes=20, inner_parity_bytes=0,
                                       constraints=ConstraintSpec(30, 70, 5, forbidden_motifs=("GAATTC",))),
}


@pytest.mark.parametrize("name", list(CONFIGS))
@pytest.mark.parametrize("size", [0, 1, 777, 20_000])
def test_configuration_matrix_round_trips_through_dna(tmp_path, name, size):
    data = mixed_bytes(size, size)
    src = tmp_path / "f"
    src.write_bytes(data)
    api.store(src, tmp_path / "a.vxdna", options=CONFIGS[name])
    api.encode(tmp_path / "a.vxdna", tmp_path / "p.fasta")
    api.recover(tmp_path / "p.fasta", tmp_path / "o")
    assert (tmp_path / "o").read_bytes() == data


def test_large_file_round_trip(tmp_path):
    data = mixed_bytes(2_500_000, 9)
    src = tmp_path / "big.bin"
    src.write_bytes(data)
    api.store(src, tmp_path / "a.vxdna")
    api.encode(tmp_path / "a.vxdna", tmp_path / "p.fasta")
    api.simulate(tmp_path / "p.fasta", tmp_path / "r.fasta", api.ChannelConfig(seed=1, dropout_rate=0.03, substitution_rate=0.001, shuffle=True))
    api.recover(tmp_path / "r.fasta", tmp_path / "o")
    assert hashlib.sha256((tmp_path / "o").read_bytes()).digest() == hashlib.sha256(data).digest()


@pytest.mark.parametrize("name", ["données é.txt", "日本語ファイル.bin", "x" * 300 + ".tar.gz", "emoji-🧬.dat"])
def test_unicode_and_long_file_names_are_recorded_safely(tmp_path, name):
    c = build_container(b"abc", FAST, None, name)
    stored = c.manifest.content.name
    assert stored is not None and len(stored) <= 255
    assert stored == name if len(name) <= 255 else stored.endswith(".gz")
    assert build_container(b"abc", FAST, None, "../../etc/" + name).manifest.content.name == (name if len(name) <= 255 else stored)


def test_random_access_decodes_only_the_needed_stripes(tmp_path):
    data = mixed_bytes(40_000, 5)
    src = tmp_path / "in"
    src.write_bytes(data)
    api.store(src, tmp_path / "a.vxdna", options=FAST, key=TEST_KEY)
    api.encode(tmp_path / "a.vxdna", tmp_path / "p.fasta")
    r = api.extract(tmp_path / "p.fasta", tmp_path / "c3", chunk=3, key=TEST_KEY)
    assert (tmp_path / "c3").read_bytes() == data[3 * 4096:4 * 4096]
    assert 0 < r["stripes_decoded"] < r["stripes_total"]
    r = api.extract(tmp_path / "a.vxdna", tmp_path / "rng", start=5000, end=13000, key=TEST_KEY)
    assert (tmp_path / "rng").read_bytes() == data[5000:13000] and r["selection"]["chunks"] == [1, 2, 3]


def test_random_access_survives_loss_of_unrelated_chunks(tmp_path):
    """Delete every strand of chunks other than 2: chunk 2 is still recoverable; the full file is not."""
    data = mixed_bytes(30_000, 6)
    src = tmp_path / "in"
    src.write_bytes(data)
    api.store(src, tmp_path / "a.vxdna", options=FAST)
    api.encode(tmp_path / "a.vxdna", tmp_path / "p.fasta")
    m = mf.validate(json.loads(vxdna.read(tmp_path / "a.vxdna").manifest_bytes))
    keep_stripes = range(m.chunks[2].first_stripe, m.chunks[2].first_stripe + m.chunks[2].stripe_count)
    labels = [l for l in (tmp_path / "p.fasta").read_text().splitlines() if l.startswith(">")]
    seqs = read_sequences(tmp_path / "p.fasta")
    kept = [s for l, s in zip(labels, seqs) if ":m:" in l or int(l.split(":")[3]) in keep_stripes]  # labels used only to *build* the test pool
    (tmp_path / "sub.fasta").write_text("".join(f">x\n{s}\n" for s in kept))
    api.extract(tmp_path / "sub.fasta", tmp_path / "c2", chunk=2)
    assert (tmp_path / "c2").read_bytes() == data[2 * 4096:3 * 4096]
    with pytest.raises(InsufficientRedundancyError):
        api.recover(tmp_path / "sub.fasta", tmp_path / "all")


def test_extract_argument_validation(tmp_path):
    src = tmp_path / "in"
    src.write_bytes(b"x" * 100)
    api.store(src, tmp_path / "a.vxdna", options=FAST)
    for kwargs in ({}, {"chunk": 0, "start": 0}, {"chunk": 9}, {"start": 50, "end": 10}, {"start": 0, "end": 101}):
        with pytest.raises(InvalidInputError):
            api.extract(tmp_path / "a.vxdna", tmp_path / "o", **kwargs)


def test_encrypted_container_tampering_and_keys(tmp_path):
    src = tmp_path / "in"
    src.write_bytes(mixed_bytes(10_000, 7))
    api.store(src, tmp_path / "a.vxdna", options=FAST, key=TEST_KEY)
    with pytest.raises(KeyRequiredError):
        api.restore(tmp_path / "a.vxdna", tmp_path / "o")
    with pytest.raises(WrongKeyError):
        api.restore(tmp_path / "a.vxdna", tmp_path / "o", key=OTHER_KEY)
    cf = vxdna.read(tmp_path / "a.vxdna")
    # flip one ciphertext byte and re-seal the *file* trailer: stored SHA-256 (HMAC-covered) catches it
    body = bytearray(cf.body)
    body[len(body) // 2] ^= 0x01
    (tmp_path / "t.vxdna").write_bytes(vxdna.serialize(cf.manifest_bytes, bytes(body)))
    with pytest.raises(IntegrityError):
        api.restore(tmp_path / "t.vxdna", tmp_path / "o", key=TEST_KEY)
    assert api.verify(tmp_path / "t.vxdna", key=TEST_KEY)["status"] == "FAIL"
    assert not (tmp_path / "o").exists()
    # swap two ciphertext chunks while *also* fixing their stored hashes would require forging the HMAC: covered in unit tests


def test_unencrypted_tampering_with_recomputed_digest_is_caught_by_content_hashes_only_if_inconsistent(tmp_path):
    """Documented limitation: without a key there is no authentication, only corruption detection."""
    src = tmp_path / "in"
    src.write_bytes(b"A" * 5000)
    api.store(src, tmp_path / "a.vxdna", options=FAST)
    cf = vxdna.read(tmp_path / "a.vxdna")
    raw = json.loads(cf.manifest_bytes)
    raw["content"]["sha256"] = "0" * 64  # attacker edits, then recomputes the unkeyed digest
    payload = mf.digest_payload(raw)
    raw["seal"]["manifest_sha256"] = hashlib.sha256(payload).hexdigest()
    (tmp_path / "t.vxdna").write_bytes(vxdna.serialize(mf.canonical_bytes(raw), cf.body))
    with pytest.raises(IntegrityError):  # the recomputed object hash exposes the inconsistency
        api.restore(tmp_path / "t.vxdna", tmp_path / "o")


def test_output_safety(tmp_path):
    src = tmp_path / "in"
    src.write_bytes(b"data")
    api.store(src, tmp_path / "a.vxdna", options=FAST)
    with pytest.raises(OutputError):
        api.store(src, tmp_path / "a.vxdna", options=FAST)
    with pytest.raises(InvalidInputError):
        api.store(tmp_path / "missing", tmp_path / "b.vxdna")
    with pytest.raises(ConfigurationError):
        api.store(src, tmp_path / "c.vxdna", options=StoreOptions(data_shards=200, parity_shards=100))
    with pytest.raises(InvalidInputError):
        api.encode(src, tmp_path / "x.fasta")


def test_deterministic_unencrypted_encoding(tmp_path):
    src = tmp_path / "in"
    src.write_bytes(mixed_bytes(9000, 1))
    for i in range(2):
        api.store(src, tmp_path / f"a{i}.vxdna", options=FAST)
        api.encode(tmp_path / f"a{i}.vxdna", tmp_path / f"p{i}.fasta")
    assert (tmp_path / "a0.vxdna").read_bytes() == (tmp_path / "a1.vxdna").read_bytes()
    assert (tmp_path / "p0.fasta").read_bytes() == (tmp_path / "p1.fasta").read_bytes()
