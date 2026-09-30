"""Manifest schema/canonical form/seal; key handling; AEAD binding; bounded decompression."""
import copy
import hashlib
import json

import pytest

from v1_support import FAST, OTHER_KEY, TEST_KEY
from vnxdna.container import compression, crypto
from vnxdna.container import manifest as mf
from vnxdna.container.builder import build_container
from vnxdna.container.reader import load_manifest
from vnxdna.errors import (AuthenticationError, ConfigurationError, IntegrityError, KeyRequiredError, MetadataError,
                           UnsupportedFormatError, WrongKeyError)


def _raw(key=None, data=b"hello world" * 100):
    c = build_container(data, FAST, key, "file.txt")
    return c, json.loads(c.manifest_bytes)


def _reseal(raw):
    """What an attacker without the key can do: recompute the digest, keep the old HMAC tag."""
    payload = mf.digest_payload(raw)
    raw["seal"] = {**raw["seal"], "manifest_sha256": hashlib.sha256(payload).hexdigest()}
    return mf.canonical_bytes(raw)


def test_manifest_is_canonical_and_deterministic_without_encryption():
    a, _ = _raw()
    b, _ = _raw()
    assert a.manifest_bytes == b.manifest_bytes == mf.canonical_bytes(json.loads(a.manifest_bytes))
    assert load_manifest(a.manifest_bytes, None).authentication == "digest-only"


@pytest.mark.parametrize("mutate,error", [
    (lambda r: r.pop("chunks"), MetadataError),
    (lambda r: r.__setitem__("unexpected", 1), MetadataError),
    (lambda r: r.__setitem__("chunk_size", "4096"), MetadataError),
    (lambda r: r.__setitem__("chunk_size", 4096.0), MetadataError),
    (lambda r: r.__setitem__("format_version", 5), UnsupportedFormatError),
    (lambda r: r.__setitem__("format", "OTHER"), UnsupportedFormatError),
    (lambda r: r["required_features"].append("quantum-ecc-v9"), UnsupportedFormatError),
    (lambda r: r["erasure_code"].__setitem__("stripe_count", r["erasure_code"]["stripe_count"] + 1), MetadataError),
    (lambda r: r["chunks"][0].__setitem__("stored_size", r["chunks"][0]["stored_size"] + 1), MetadataError),
    (lambda r: r["strand"].__setitem__("strand_nt", 1), MetadataError),
    (lambda r: r["content"].__setitem__("size", r["content"]["size"] + 1), MetadataError),
    (lambda r: r["content"].__setitem__("name", "../etc/passwd"), MetadataError),
])
def test_schema_and_semantic_violations_are_structured_errors(mutate, error):
    _, raw = _raw()
    mutate(raw)
    with pytest.raises(error):
        load_manifest(_reseal(raw), None)


def test_unresealed_modification_is_detected_by_digest():
    _, raw = _raw()
    raw["content"]["sha256"] = "0" * 64
    with pytest.raises(MetadataError, match="digest"):
        load_manifest(mf.canonical_bytes(raw), None)


def test_malformed_json_inputs():
    for blob in (b"", b"[]", b"{", b'{"a":1,"a":2}', b'{"x":NaN}', b"\xff\xfe", b"1" * 10):
        with pytest.raises((MetadataError, UnsupportedFormatError)):
            load_manifest(blob, None)


def test_encrypted_manifest_hides_content_and_is_hmac_authenticated():
    c, raw = _raw(TEST_KEY)
    assert raw["content"] is None and "file.txt" not in c.manifest_bytes.decode() and raw["sealed_content"]
    loaded = load_manifest(c.manifest_bytes, TEST_KEY)
    assert loaded.authentication == "hmac-sha256" and loaded.content.name == "file.txt"
    with pytest.raises(KeyRequiredError):
        load_manifest(c.manifest_bytes, None)
    with pytest.raises(WrongKeyError):
        load_manifest(c.manifest_bytes, OTHER_KEY)
    # an attacker without the key re-computes the digest after editing: the HMAC still fails
    tampered = copy.deepcopy(raw)
    tampered["created_at"] = "2020-01-01"
    with pytest.raises(AuthenticationError):
        load_manifest(_reseal(tampered), TEST_KEY)
    bad_tag = copy.deepcopy(raw)
    bad_tag["seal"]["manifest_hmac_sha256"] = "0" * 64
    with pytest.raises(AuthenticationError):
        load_manifest(mf.canonical_bytes(bad_tag), TEST_KEY)


def test_each_encrypted_archive_has_fresh_salt_and_id():
    a, ra = _raw(TEST_KEY)
    b, rb = _raw(TEST_KEY)
    assert ra["encryption"]["salt"] != rb["encryption"]["salt"] and ra["archive_id"] != rb["archive_id"]
    assert a.stored_chunks != b.stored_chunks


def test_aead_binds_chunk_position_and_archive():
    keys = crypto.ArchiveKeys.derive(TEST_KEY, bytes(16))
    aid = bytes(16)
    ct = crypto.seal(keys, aid, crypto.DOMAIN_CHUNK, 0, 2, b"payload")
    assert crypto.open_sealed(keys, aid, crypto.DOMAIN_CHUNK, 0, 2, ct) == b"payload"
    for args in ((aid, crypto.DOMAIN_CHUNK, 1, 2), (aid, crypto.DOMAIN_CHUNK, 0, 3), (b"x" * 16, crypto.DOMAIN_CHUNK, 0, 2),
                 (aid, crypto.DOMAIN_SEALED, 0, 2)):
        with pytest.raises(AuthenticationError):
            crypto.open_sealed(keys, *args, ct)
    flipped = bytes([ct[0] ^ 1]) + ct[1:]
    with pytest.raises(AuthenticationError):
        crypto.open_sealed(keys, aid, crypto.DOMAIN_CHUNK, 0, 2, flipped)
    with pytest.raises(AuthenticationError):
        crypto.open_sealed(keys, aid, crypto.DOMAIN_CHUNK, 0, 2, ct[:10])


def test_key_parsing():
    k = crypto.generate_key()
    assert len(crypto.parse_key(k)) == 32
    assert crypto.parse_key("ab" * 32) == bytes([0xAB]) * 32
    for bad in ("", "short", "zz" * 32, "A" * 43 + "!"):
        with pytest.raises(ConfigurationError):
            crypto.parse_key(bad)


@pytest.mark.parametrize("algorithm,level", [("zlib", 6), ("zstd", 3), ("none", 0)])
def test_compression_roundtrip_and_bomb_bound(algorithm, level):
    data = b"A" * 100_000
    packed = compression.compress(data, algorithm, level)
    assert compression.decompress(packed, algorithm, len(data)) == data
    if algorithm != "none":
        with pytest.raises(IntegrityError):
            compression.decompress(packed, algorithm, 1000)  # claims a small size: refuse to inflate further
        with pytest.raises(IntegrityError):
            compression.decompress(packed[:-3], algorithm, len(data))
    with pytest.raises(ConfigurationError):
        compression.validate("zstd", 99)
