"""V1 clean-room acceptance test: the installed V1 CLI (``vnx-dna v1``) on a real mixed binary file.

Runs the exact documented command sequence in a fresh temporary directory, then
compares bytes with ``cmp``-equivalent equality and SHA-256 computed
independently (``sha256sum`` when available, and hashlib).
"""
import hashlib
import random
import shutil
import subprocess

import pytest

from test_cli_v1 import cli


def _sh(args, cwd):
    return subprocess.run(cli() + args, cwd=cwd, capture_output=True, text=True, timeout=900)


def _sha256sum(path):
    tool = shutil.which("sha256sum")
    if tool:
        return subprocess.run([tool, str(path)], capture_output=True, text=True, check=True).stdout.split()[0]
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mixed_file(path):
    r = random.Random(20260929)
    path.write_bytes(r.randbytes(400_000) + b"VNX-DNA repeated content. " * 12_000 + bytes(r.choice(b"\x00\x01\xff") for _ in range(100_000)))


@pytest.mark.parametrize("encrypted", [False, True], ids=["plaintext", "encrypted"])
def test_clean_room_cli_lifecycle(tmp_path, encrypted):
    _mixed_file(tmp_path / "input.bin")
    key = []
    if encrypted:
        assert _sh(["keygen", "--output", "key.txt"], tmp_path).returncode == 0
        key = ["--key-file", "key.txt"]
    steps = [
        ["store", "input.bin", "--output", "archive.vxdna", *key],
        ["encode", "archive.vxdna", "--output", "storage.fasta"],
        ["info", "storage.fasta", *key],
        ["simulate", "storage.fasta", "--dropout-rate", "0.05", "--substitution-rate", "0.001", "--seed", "12345",
         "--output", "damaged.fasta"],
        ["decode", "damaged.fasta", "--output", "recovered-archive.vxdna"],
        ["restore", "recovered-archive.vxdna", "--output", "recovered.bin", *key],
        ["verify", "recovered-archive.vxdna", *key],
    ]
    for step in steps:
        proc = _sh(step, tmp_path)
        assert proc.returncode == 0, (step, proc.stdout, proc.stderr)
    assert "PASS" in proc.stdout
    original, recovered = tmp_path / "input.bin", tmp_path / "recovered.bin"
    assert original.read_bytes() == recovered.read_bytes()                     # cmp
    assert _sha256sum(original) == _sha256sum(recovered) == hashlib.sha256(original.read_bytes()).hexdigest()
    assert (tmp_path / "archive.vxdna").read_bytes() == (tmp_path / "recovered-archive.vxdna").read_bytes()


def test_clean_room_beyond_guarantee_fails_honestly(tmp_path):
    _mixed_file(tmp_path / "input.bin")
    assert _sh(["store", "input.bin", "-o", "a.vxdna"], tmp_path).returncode == 0
    assert _sh(["encode", "a.vxdna", "-o", "s.fasta"], tmp_path).returncode == 0
    assert _sh(["simulate", "s.fasta", "--dropout-rate", "0.45", "--seed", "1", "-o", "d.fasta"], tmp_path).returncode == 0
    proc = _sh(["decode", "d.fasta", "-o", "r.vxdna"], tmp_path)
    assert proc.returncode == 5 and "INSUFFICIENT_REDUNDANCY" in proc.stderr
    proc = _sh(["recover", "d.fasta", "-o", "r.bin"], tmp_path)
    assert proc.returncode == 5
    assert not (tmp_path / "r.vxdna").exists() and not (tmp_path / "r.bin").exists()
