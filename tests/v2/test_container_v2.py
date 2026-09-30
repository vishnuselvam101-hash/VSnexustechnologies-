"""Streaming container (format 5): round trips, determinism, random access, tamper detection, crypto, resume."""
import hashlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from v2_support import FAST, KEY, OTHER_KEY, mixed_bytes, write
from vnxdna.errors import (AuthenticationError, IntegrityError, InvalidInputError, KeyRequiredError, MetadataError, OutputError,
                           VNXDNAError, WrongKeyError)
from vnxdna.v2 import crypto
from vnxdna.v2.archive import (extract_range, open_container, restore_file, store_file, verify_container)
from vnxdna.v2.container import ContainerFileV2, TRAILER_BYTES
from vnxdna.v2.profiles import PROFILES, options_for


@pytest.mark.parametrize("size", [0, 1, 4095, 4096, 4097, 3 * 4096, 50_000])
@pytest.mark.parametrize("key", [None, KEY], ids=["plain", "encrypted"])
def test_store_restore_roundtrip(tmp_path, size, key):
    data = mixed_bytes(size, seed=size)
    src = write(tmp_path / "in.bin", data)
    report = store_file(src, tmp_path / "a.vxdna", options=FAST, key=key, workers=2)
    assert report["chunks"] == max(1, -(-size // 4096))
    restored = restore_file(tmp_path / "a.vxdna", tmp_path / "out.bin", key=key)
    assert (tmp_path / "out.bin").read_bytes() == data
    assert restored["recovered_sha256"] == hashlib.sha256(data).hexdigest()


def test_unencrypted_archives_are_deterministic_and_encrypted_ones_are_not(tmp_path):
    src = write(tmp_path / "in.bin", mixed_bytes(20_000))
    for name in ("a", "b"):
        store_file(src, tmp_path / f"{name}.vxdna", options=FAST, workers=1 if name == "a" else 4)
    assert (tmp_path / "a.vxdna").read_bytes() == (tmp_path / "b.vxdna").read_bytes()
    store_file(src, tmp_path / "c.vxdna", options=FAST, key=KEY)
    store_file(src, tmp_path / "d.vxdna", options=FAST, key=KEY)
    assert (tmp_path / "c.vxdna").read_bytes() != (tmp_path / "d.vxdna").read_bytes()


def test_compression_is_kept_only_when_it_helps(tmp_path):
    data = os.urandom(8192) + b"A" * 8192
    src = write(tmp_path / "in.bin", data)
    store_file(src, tmp_path / "a.vxdna", options=FAST)
    _, loaded = open_container(tmp_path / "a.vxdna", None)
    assert loaded.index["codec"].tolist() == [0, 0, 1, 1]  # random chunks stay raw, repetitive chunks are compressed
    assert (loaded.index["stored_size"][:2] == 4096).all()


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_every_profile_roundtrips(tmp_path, profile):
    data = mixed_bytes(30_000, seed=3)
    src = write(tmp_path / "in.bin", data)
    store_file(src, tmp_path / "a.vxdna", options=options_for(profile, chunk_size=8192))
    restore_file(tmp_path / "a.vxdna", tmp_path / "o.bin")
    assert (tmp_path / "o.bin").read_bytes() == data


def test_random_access_reads_only_the_needed_chunks(tmp_path):
    data = mixed_bytes(100_000, seed=9)
    src = write(tmp_path / "in.bin", data)
    store_file(src, tmp_path / "a.vxdna", options=FAST, key=KEY)
    rng = np.random.default_rng(1)
    for _ in range(20):
        offset = int(rng.integers(0, len(data)))
        length = int(rng.integers(0, 12_000))
        length = min(length, len(data) - offset)
        r = extract_range(tmp_path / "a.vxdna", tmp_path / "s.bin", offset=offset, length=length, key=KEY, overwrite=True)
        assert (tmp_path / "s.bin").read_bytes() == data[offset:offset + length]
        assert len(r["chunks_processed"]) <= length // 4096 + 2
    with pytest.raises(InvalidInputError):
        extract_range(tmp_path / "a.vxdna", tmp_path / "x.bin", offset=len(data), length=1, key=KEY)


def _flip(path: Path, position: int, out: Path) -> Path:
    blob = bytearray(path.read_bytes())
    blob[position] ^= 0x01
    out.write_bytes(blob)
    return out


def test_any_single_bit_flip_is_detected_and_never_yields_wrong_output(tmp_path):
    data = mixed_bytes(20_000, seed=4)
    src = write(tmp_path / "in.bin", data)
    store_file(src, tmp_path / "a.vxdna", options=FAST, key=KEY)
    size = (tmp_path / "a.vxdna").stat().st_size
    rng = np.random.default_rng(2)
    for position in sorted(set(rng.integers(0, size, 60).tolist()) | {0, 9, 16, size - 1, size - 40, size - TRAILER_BYTES}):
        bad = _flip(tmp_path / "a.vxdna", position, tmp_path / "bad.vxdna")
        assert verify_container(bad, key=KEY)["status"] == "FAIL", position
        out = tmp_path / "o.bin"
        try:
            restore_file(bad, out, key=KEY, overwrite=True)
        except VNXDNAError as error:
            assert not out.exists() or out.read_bytes() == data, error
        else:  # a flip that restore does not look at (only the file trailer): output must still be exact
            assert out.read_bytes() == data


def test_truncated_and_unfinished_archives_are_rejected(tmp_path):
    src = write(tmp_path / "in.bin", mixed_bytes(10_000))
    store_file(src, tmp_path / "a.vxdna", options=FAST)
    blob = (tmp_path / "a.vxdna").read_bytes()
    for cut in (10, 100, len(blob) // 2, len(blob) - 1):
        (tmp_path / "t.vxdna").write_bytes(blob[:cut])
        with pytest.raises(InvalidInputError):
            ContainerFileV2.open(tmp_path / "t.vxdna")
    (tmp_path / "t.vxdna").write_bytes(blob + b"x")
    with pytest.raises(InvalidInputError):
        ContainerFileV2.open(tmp_path / "t.vxdna")


def test_wrong_and_missing_keys(tmp_path):
    src = write(tmp_path / "in.bin", mixed_bytes(9000))
    store_file(src, tmp_path / "a.vxdna", options=FAST, key=KEY)
    with pytest.raises(KeyRequiredError):
        restore_file(tmp_path / "a.vxdna", tmp_path / "o.bin")
    with pytest.raises(WrongKeyError):
        restore_file(tmp_path / "a.vxdna", tmp_path / "o.bin", key=OTHER_KEY)
    assert not (tmp_path / "o.bin").exists()
    r = verify_container(tmp_path / "a.vxdna")
    assert r["status"] == "FAIL" and any(c["check"] == "manifest-authentication" and c["result"] == "FAIL" for c in r["checks"])


def test_streaming_aead_detects_modified_reordered_duplicated_missing_and_truncated_chunks():
    keys = crypto.ArchiveKeys.derive(KEY, bytes(16))
    cipher = crypto.ChunkCipher(keys, b"\x01" * 16, chunk_count=3)
    sealed = [cipher.seal(crypto.DOMAIN_CHUNK, i, f"chunk {i}".encode()) for i in range(3)]
    assert [cipher.open(crypto.DOMAIN_CHUNK, i, sealed[i]) for i in range(3)] == [b"chunk 0", b"chunk 1", b"chunk 2"]
    modified = bytearray(sealed[1])
    modified[0] ^= 1
    cases = [(1, bytes(modified), None),  # modified
             (0, sealed[1], None),        # reordered
             (2, sealed[1], None),        # duplicated into another position
             (1, sealed[1], 2)]           # truncated/missing: the archive now claims 2 chunks
    for index, blob, count in cases:
        with pytest.raises(AuthenticationError):
            cipher.open(crypto.DOMAIN_CHUNK, index, blob, count=count)
    other = crypto.ChunkCipher(keys, b"\x02" * 16, chunk_count=3)
    with pytest.raises(AuthenticationError):
        other.open(crypto.DOMAIN_CHUNK, 0, sealed[0])  # spliced from another archive


def test_verify_against_a_recovered_file_names_the_damaged_chunks(tmp_path):
    data = mixed_bytes(30_000, seed=7)
    src = write(tmp_path / "in.bin", data)
    store_file(src, tmp_path / "a.vxdna", options=FAST)
    damaged = bytearray(data)
    damaged[4096 * 2 + 5] ^= 0xFF
    write(tmp_path / "d.bin", bytes(damaged))
    r = verify_container(tmp_path / "a.vxdna", against=tmp_path / "d.bin")
    assert r["status"] == "FAIL" and r["file_comparison"]["mismatched_chunks"] == [2]
    assert verify_container(tmp_path / "a.vxdna", against=src)["status"] == "PASS"


def test_existing_output_is_not_overwritten(tmp_path):
    src = write(tmp_path / "in.bin", b"x" * 100)
    store_file(src, tmp_path / "a.vxdna", options=FAST)
    with pytest.raises(OutputError):
        store_file(src, tmp_path / "a.vxdna", options=FAST)
    store_file(src, tmp_path / "a.vxdna", options=FAST, overwrite=True)


def _store_process(src: Path, out: Path, *extra: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "vnxdna", "store", str(src), "-o", str(out), "--chunk-size", "65536",
                             "--checkpoint-interval", "8", "--workers", "1", *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_killed_store_leaves_no_valid_archive_and_resumes_exactly(tmp_path):
    data = os.urandom(30 << 20)
    src = write(tmp_path / "in.bin", data)
    out = tmp_path / "a.vxdna"
    ckpt = out.with_name(out.name + ".partial.ckpt")
    proc = _store_process(src, out, "--compression", "zlib", "--level", "9")
    deadline = time.time() + 120
    while not ckpt.exists() and proc.poll() is None and time.time() < deadline:
        time.sleep(0.02)
    if proc.poll() is not None:
        pytest.skip("store finished before it could be interrupted on this machine")
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    assert not out.exists(), "a killed store must not publish an archive"
    partial = out.with_name(out.name + ".partial")
    assert partial.exists()
    with pytest.raises(InvalidInputError):
        ContainerFileV2.open(partial)
    resumed = subprocess.run([sys.executable, "-m", "vnxdna", "store", str(src), "-o", str(out), "--chunk-size", "65536",
                              "--compression", "zlib", "--level", "9", "--resume", "--json"], capture_output=True, text=True)
    assert resumed.returncode == 0, resumed.stderr
    assert '"resumed_from_chunk": 0' not in resumed.stdout
    reference = tmp_path / "ref.vxdna"
    subprocess.run([sys.executable, "-m", "vnxdna", "store", str(src), "-o", str(reference), "--chunk-size", "65536",
                    "--compression", "zlib", "--level", "9"], check=True, capture_output=True)
    assert out.read_bytes() == reference.read_bytes(), "resumed archive must equal an uninterrupted one"
    assert not partial.exists() and not ckpt.exists()


def test_resume_refuses_a_changed_input(tmp_path):
    data = os.urandom(30 << 20)
    src = write(tmp_path / "in.bin", data)
    out = tmp_path / "a.vxdna"
    ckpt = out.with_name(out.name + ".partial.ckpt")
    proc = _store_process(src, out, "--compression", "zlib", "--level", "9")
    while not ckpt.exists() and proc.poll() is None:
        time.sleep(0.02)
    if proc.poll() is not None:
        pytest.skip("store finished before it could be interrupted on this machine")
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    with src.open("r+b") as handle:
        handle.write(b"changed!")
    r = subprocess.run([sys.executable, "-m", "vnxdna", "store", str(src), "-o", str(out), "--chunk-size", "65536",
                        "--compression", "zlib", "--level", "9", "--resume"], capture_output=True, text=True)
    assert r.returncode == 3 and "cannot resume" in r.stderr
    assert not out.exists()


def test_aead_epochs_give_distinct_nonces_and_bind_the_chunk():
    keys = crypto.ArchiveKeys.derive(KEY, bytes(16))
    cipher = crypto.ChunkCipher(keys, b"\x03" * 16, chunk_count=2)
    a = cipher.seal(crypto.DOMAIN_CHUNK, 1, b"same plaintext", epoch=0)
    b = cipher.seal(crypto.DOMAIN_CHUNK, 1, b"same plaintext", epoch=1)
    assert a != b  # a different nonce: re-sealing after a resume never reuses (key, nonce)
    assert cipher.open(crypto.DOMAIN_CHUNK, 1, b, epoch=1) == b"same plaintext"
    with pytest.raises(AuthenticationError):
        cipher.open(crypto.DOMAIN_CHUNK, 1, b, epoch=0)


def test_killed_encrypted_store_resumes_with_a_new_aead_epoch(tmp_path):
    data = os.urandom(30 << 20)
    src = write(tmp_path / "in.bin", data)
    key_file = tmp_path / "k"
    subprocess.run([sys.executable, "-m", "vnxdna", "keygen", "-o", str(key_file)], check=True, capture_output=True)
    out = tmp_path / "a.vxdna"
    ckpt = out.with_name(out.name + ".partial.ckpt")
    proc = _store_process(src, out, "--compression", "zlib", "--level", "9", "--key-file", str(key_file))
    while not ckpt.exists() and proc.poll() is None:
        time.sleep(0.02)
    if proc.poll() is not None:
        pytest.skip("store finished before it could be interrupted on this machine")
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    r = subprocess.run([sys.executable, "-m", "vnxdna", "store", str(src), "-o", str(out), "--chunk-size", "65536", "--compression",
                        "zlib", "--level", "9", "--key-file", str(key_file), "--resume", "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    key = crypto.parse_key(key_file.read_text())
    _, loaded = open_container(out, key)
    epochs = loaded.index["epoch"]
    assert epochs[0] == 0 and epochs[-1] == 1 and set(np.unique(epochs).tolist()) == {0, 1}
    restore_file(out, tmp_path / "o.bin", key=key)
    assert (tmp_path / "o.bin").read_bytes() == data
