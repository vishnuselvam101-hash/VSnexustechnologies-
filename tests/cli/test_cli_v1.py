"""The V1 CLI (``vnx-dna v1 ...``, unchanged from 1.0.0), run as a subprocess: help, exit codes, error contract, JSON reports."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

COMMANDS = ["store", "pack", "encode", "simulate", "decode", "restore", "recover", "verify", "info", "extract", "pipeline",
            "keygen", "version", "benchmark", "legacy"]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "v0_1"


def cli() -> list[str]:
    exe = shutil.which("vnx-dna")
    return ([exe] if exe else [sys.executable, "-m", "vnxdna"]) + ["v1"]


def run(*args, cwd=None, env=None, check=None):
    proc = subprocess.run(cli() + [str(a) for a in args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=600)
    if check is not None:
        assert proc.returncode == check, (args, proc.returncode, proc.stdout, proc.stderr)
    return proc


def test_top_level_help_lists_the_workflow_and_every_command():
    out = run("--help", check=0).stdout
    for command in COMMANDS:
        assert command in out
    assert "vnx-dna store" in out and "Exit codes" in out


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_has_help(command):
    assert "Usage" in run(command, "--help", check=0).stdout


def test_version():
    assert "vnx-dna 0." in run("version", check=0).stdout or "vnx-dna 1." in run("version").stdout
    assert json.loads(run("version", "--json", check=0).stdout)["archive_format"] == 4


def test_usage_errors_exit_2():
    assert run("no-such-command").returncode == 2
    assert run("store").returncode == 2
    assert run("simulate", "x", "-o", "y", "--max-indel", "9").returncode == 2


def test_documented_exit_codes_and_no_tracebacks(tmp_path):
    (tmp_path / "in.bin").write_bytes(os.urandom(5000))
    run("keygen", "-o", tmp_path / "k", check=0)
    assert oct((tmp_path / "k").stat().st_mode & 0o777) == "0o600"
    run("keygen", "-o", tmp_path / "k2", check=0)
    run("store", tmp_path / "in.bin", "-o", tmp_path / "a.vxdna", "-k", tmp_path / "k", check=0)
    cases = [
        (("restore", tmp_path / "a.vxdna", "-o", tmp_path / "o"), 4),                       # key missing
        (("restore", tmp_path / "a.vxdna", "-o", tmp_path / "o", "-k", tmp_path / "k2"), 4),  # wrong key
        (("restore", tmp_path / "missing", "-o", tmp_path / "o"), 3),
        (("store", tmp_path / "in.bin", "-o", tmp_path / "a.vxdna"), 8),                    # exists
        (("store", tmp_path / "in.bin", "-o", tmp_path / "b.vxdna", "--data-shards", "0"), 7),
        (("store", tmp_path / "in.bin", "-o", tmp_path / "b.vxdna", "--encrypt"), 7),        # --encrypt without key
        (("restore", FIX / "dataset_v2_rs_8_4", "-o", tmp_path / "o"), 6),                  # legacy needs `legacy`
        (("store", tmp_path / "in.bin", "-o", tmp_path / "c.vxdna", "--max-strand-nt", "100"), 7),
    ]
    for args, code in cases:
        proc = run(*args)
        assert proc.returncode == code, (args, proc.stderr)
        assert "Traceback" not in proc.stderr and proc.stderr.startswith("vnx-dna: error [")
    assert not (tmp_path / "o").exists()
    bad = tmp_path / "bad.vxdna"
    blob = bytearray((tmp_path / "a.vxdna").read_bytes())
    blob[-100] ^= 1
    bad.write_bytes(bytes(blob))
    assert run("restore", bad, "-o", tmp_path / "o", "-k", tmp_path / "k").returncode == 3
    assert run("verify", bad, "-k", tmp_path / "k").returncode == 1


def test_env_key_and_json_reports(tmp_path):
    key = run("keygen", check=0).stdout.strip()
    (tmp_path / "in.txt").write_text("secret payload\n" * 50)
    env = {"VNXDNA_KEY": key}
    report = json.loads(run("store", tmp_path / "in.txt", "-o", tmp_path / "a.vxdna", "--json", env=env, check=0).stdout)
    assert report["encrypted"] is True
    run("encode", tmp_path / "a.vxdna", "-o", tmp_path / "p.fasta", check=0)
    assert "secret" not in (tmp_path / "p.fasta").read_text() and "in.txt" not in (tmp_path / "a.vxdna").read_bytes().decode("latin-1")
    info = json.loads(run("info", tmp_path / "p.fasta", "--json", check=0).stdout)
    assert isinstance(info["content"], str)  # sealed without the key
    info = json.loads(run("info", tmp_path / "p.fasta", "--json", env=env, check=0).stdout)
    assert info["content"]["name"] == "in.txt"
    run("recover", tmp_path / "p.fasta", "-o", tmp_path / "out.txt", "--report", tmp_path / "r.json", env=env, check=0)
    assert (tmp_path / "out.txt").read_bytes() == (tmp_path / "in.txt").read_bytes()
    assert json.loads((tmp_path / "r.json").read_text())["sha256_match"] is True


def test_simulate_records_events_and_is_reproducible(tmp_path):
    (tmp_path / "in.bin").write_bytes(os.urandom(3000))
    run("store", tmp_path / "in.bin", "-o", tmp_path / "a.vxdna", check=0)
    run("encode", tmp_path / "a.vxdna", "-o", tmp_path / "p.fasta", check=0)
    for name in ("d1", "d2"):
        run("simulate", tmp_path / "p.fasta", "-o", tmp_path / f"{name}.fasta", "--substitution-rate", "0.01", "--dropout-rate", "0.05",
            "--seed", "12345", "--events", tmp_path / f"{name}.jsonl", "--report", tmp_path / f"{name}.json", check=0)
    assert (tmp_path / "d1.fasta").read_bytes() == (tmp_path / "d2.fasta").read_bytes()
    report = json.loads((tmp_path / "d1.json").read_text())
    events = [json.loads(line) for line in (tmp_path / "d1.jsonl").read_text().splitlines()]
    assert report["channel"]["substitutions"] == sum(e["type"] == "substitution" for e in events) > 0
    assert report["channel"]["config"]["seed"] == 12345 and report["provenance"]["vnxdna_version"]


def test_pipeline_command(tmp_path):
    (tmp_path / "in.bin").write_bytes(os.urandom(20000) + b"z" * 20000)
    proc = run("pipeline", tmp_path / "in.bin", "-o", tmp_path / "out.bin", "--dna-output", tmp_path / "pool.fasta",
               "--dropout-rate", "0.01", "--substitution-rate", "0.001", "--seed", "42", "--json", check=0)
    report = json.loads(proc.stdout)
    assert report["bytes_identical"] and report["container_roundtrip_identical"]
    assert (tmp_path / "out.bin").read_bytes() == (tmp_path / "in.bin").read_bytes() and (tmp_path / "pool.fasta").exists()


def test_extract_and_legacy_commands(tmp_path):
    data = os.urandom(10000)
    (tmp_path / "in.bin").write_bytes(data)
    run("store", tmp_path / "in.bin", "-o", tmp_path / "a.vxdna", "--chunk-size", "2000", check=0)
    run("encode", tmp_path / "a.vxdna", "-o", tmp_path / "p.fasta", check=0)
    run("extract", tmp_path / "p.fasta", "-o", tmp_path / "c", "--chunk", "2", check=0)
    assert (tmp_path / "c").read_bytes() == data[4000:6000]
    run("extract", tmp_path / "a.vxdna", "-o", tmp_path / "r", "--start", "123", "--end", "4567", check=0)
    assert (tmp_path / "r").read_bytes() == data[123:4567]
    assert run("extract", tmp_path / "a.vxdna", "-o", tmp_path / "x", "--chunk", "99").returncode == 3
    fixtures = json.loads((FIX / "fixtures.json").read_text())
    (tmp_path / "fkey").write_text(fixtures["test_only_fernet_key"])
    run("legacy", "restore", FIX / "dataset_v1_zstd_encrypted", "-o", tmp_path / "l", "-k", tmp_path / "fkey", check=0)
    assert (tmp_path / "l").read_bytes() == (FIX / "dataset_v1_zstd_encrypted.input.bin").read_bytes()
    assert "rd1-archive" in run("legacy", "info", FIX / "rd1_archive_plain.vnxdna.json", check=0).stdout
