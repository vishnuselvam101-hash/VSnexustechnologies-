"""The vnx CLI: workflow, exit codes and machine-readable output."""
import json
import os
import subprocess
import sys


VNX = [sys.executable, "-m", "vnxdna.v4.cli"]


def run(*args, cwd, check=0, env=None):
    p = subprocess.run(VNX + [str(a) for a in args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})},
                       timeout=600)
    assert p.returncode == check, (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    return p


def test_full_cli_workflow(tmp_path):
    (tmp_path / "ds").mkdir()
    run("generate", "ds/a.bin", "--size", "120KB", cwd=tmp_path)
    run("generate", "ds/b.txt", "--size", "40KB", "--pattern", "text", cwd=tmp_path)
    rep = json.loads(run("archive", "ds", "a.vnx", cwd=tmp_path).stdout)
    assert rep["files"] == 2
    assert json.loads(run("verify", "a.vnx", cwd=tmp_path).stdout)["status"] == "VERIFIED"
    loc = json.loads(run("locate", "a.vnx", "ds/b.txt", "--dna-profile", "v4-balanced", cwd=tmp_path).stdout)
    assert loc["dna"]["groups"]
    run("encode", "a.vnx", "s.fasta", cwd=tmp_path)
    assert json.loads(run("validate", "s.fasta", cwd=tmp_path).stdout)["valid"] is True
    (tmp_path / "ch.json").write_text(json.dumps({"substitution_rate": 0.002, "insertion_rate": 0.0005, "deletion_rate": 0.0005,
                                                  "dropout_rate": 0.02, "coverage": 4, "coverage_model": "poisson", "seed": 3}))
    run("channel", "simulate", "s.fasta", "r.fastq", "--config", "ch.json", cwd=tmp_path)
    out = json.loads(run("decode", "r.fastq", "-o", "rec.vnx", "--extract", "out", cwd=tmp_path).stdout)
    assert out["status"] == "SUCCESS"
    assert (tmp_path / "rec.vnx").read_bytes() == (tmp_path / "a.vnx").read_bytes()
    assert (tmp_path / "out" / "ds" / "a.bin").read_bytes() == (tmp_path / "ds" / "a.bin").read_bytes()


def test_encode_plain_directory_and_encrypted(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x").write_bytes(b"secret data " * 500)
    run("keygen", "k.key", cwd=tmp_path)
    run("encode", "d", "s.fasta", "--key-file", "k.key", cwd=tmp_path)
    run("decode", "s.fasta", "-o", "r.vnx", cwd=tmp_path)
    run("list", "r.vnx", cwd=tmp_path, check=4)                      # no key → authentication error
    run("extract", "r.vnx", "out", "--key-file", "k.key", cwd=tmp_path)
    assert (tmp_path / "out" / "d" / "x").read_bytes() == b"secret data " * 500
    run("keygen", "other.key", cwd=tmp_path)
    run("extract", "r.vnx", "out2", "--key-file", "other.key", cwd=tmp_path, check=4)


def test_exit_codes(tmp_path):
    run("verify", "missing.vnx", cwd=tmp_path, check=3)
    (tmp_path / "bad.json").write_text('{"substitution_rate": 3}')
    (tmp_path / "s.fasta").write_text(">a\nACGT\n")
    run("channel", "simulate", "s.fasta", "r.fastq", "--config", "bad.json", cwd=tmp_path, check=7)
    (tmp_path / "v.fasta").write_text(">a\nAAAAAAAAAAGC\n")
    rep = json.loads(run("validate", "v.fasta", cwd=tmp_path, check=3).stdout)
    assert rep["violation_counts"]["HOMOPOLYMER"] == 1
    err = json.loads(run("verify", "missing.vnx", cwd=tmp_path, check=3).stderr)
    assert err["error_class"] == "VNXFormatError" and err["retryable"] is False


def test_partial_exit_code(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_bytes(os.urandom(60_000))
    run("archive", "d", "a.vnx", cwd=tmp_path)
    run("encode", "a.vnx", "s.fasta", cwd=tmp_path)
    run("channel", "simulate", "s.fasta", "r.fastq", "--dropout-rate", "0.5", "--seed", "1", cwd=tmp_path, check=0)
    p = subprocess.run(VNX + ["decode", "r.fastq", "-o", "x.vnx"], cwd=tmp_path, capture_output=True, text=True, timeout=600)
    assert p.returncode in (5, 9)
    assert not (tmp_path / "x.vnx").exists()


def test_version_and_profiles(tmp_path):
    assert json.loads(run("version", cwd=tmp_path).stdout)["vnx4_format"] == [4, 0]
    assert "v4-balanced" in json.loads(run("profiles", cwd=tmp_path).stdout)["layouts"]
