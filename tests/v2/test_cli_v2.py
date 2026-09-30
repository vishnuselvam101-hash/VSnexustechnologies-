"""The installed V2 CLI, as a subprocess: commands, exit codes, canonical workflow, pipeline, experiments, generator."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from v2_support import mixed_bytes

COMMANDS = ["store", "encode", "simulate", "sequence", "reads", "cluster", "consensus", "decode", "recover", "restore", "verify",
            "info", "extract", "migrate", "experiment", "benchmark", "pipeline", "keygen", "version", "legacy", "v1"]
SMALL = ["--chunk-size", "4096", "--data-shards", "8", "--parity-shards", "4", "--payload-bytes", "24"]


def cli() -> list[str]:
    exe = shutil.which("vnx-dna")
    return [exe] if exe else [sys.executable, "-m", "vnxdna"]


def run(*args, check=None, cwd=None):
    proc = subprocess.run(cli() + [str(a) for a in args], capture_output=True, text=True, timeout=900, cwd=cwd)
    if check is not None:
        assert proc.returncode == check, (args, proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
    assert "Traceback" not in proc.stderr, proc.stderr
    return proc


def test_help_lists_every_command():
    out = run("--help", check=0).stdout
    for command in COMMANDS:
        assert command in out


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_has_help(command):
    assert "Usage" in run(command, "--help", check=0).stdout


def test_version_reports_formats():
    info = json.loads(run("version", "--json", check=0).stdout)
    assert info["writes"]["archive_format"] == 5 and 4 in info["reads"]["archive_formats"]


def test_canonical_workflow_and_independent_checks(tmp_path):
    data = mixed_bytes(60_000, seed=41)
    (tmp_path / "input.bin").write_bytes(data)
    c = tmp_path
    run("store", c / "input.bin", "--output", c / "archive.v2.vxdna", *SMALL, check=0)
    run("encode", c / "archive.v2.vxdna", "--output", c / "strands.fasta", check=0)
    run("sequence", c / "strands.fasta", "--coverage", "10", "--substitution-rate", "0.001", "--insertion-rate", "0.0001",
        "--deletion-rate", "0.0001", "--dropout-rate", "0.02", "--seed", "42", "--output", c / "reads.fastq", check=0)
    run("reads", c / "reads.fastq", "--json", check=0)
    run("cluster", c / "reads.fastq", "--output", c / "clusters.jsonl", check=0)
    run("consensus", c / "clusters.jsonl", "--output", c / "consensus.fasta", check=0)
    run("decode", c / "consensus.fasta", "--output", c / "recovered.vxdna", check=0)
    run("restore", c / "recovered.vxdna", "--output", c / "recovered.bin", check=0)
    run("verify", c / "recovered.vxdna", "--file", c / "recovered.bin", check=0)
    assert (c / "recovered.bin").read_bytes() == data
    assert (c / "recovered.vxdna").read_bytes() == (c / "archive.v2.vxdna").read_bytes()
    if shutil.which("sha256sum"):
        out = subprocess.run(["sha256sum", str(c / "input.bin"), str(c / "recovered.bin")], capture_output=True, text=True).stdout.split()
        assert out[0] == out[2] == hashlib.sha256(data).hexdigest()


def test_documented_exit_codes(tmp_path):
    (tmp_path / "in.bin").write_bytes(os.urandom(20_000))
    run("keygen", "-o", tmp_path / "k", check=0)
    run("keygen", "-o", tmp_path / "k2", check=0)
    assert oct((tmp_path / "k").stat().st_mode & 0o777) == "0o600"
    run("store", tmp_path / "in.bin", "-o", tmp_path / "a.vxdna", "-k", tmp_path / "k", *SMALL, check=0)
    run("encode", tmp_path / "a.vxdna", "-o", tmp_path / "s.fasta", check=0)
    lines = (tmp_path / "s.fasta").read_text().splitlines()
    seqs = lines[1::2]
    # delete M+1 strands of the first data ECC group -> insufficient redundancy (5)
    labels = lines[0::2]
    first_group = [i for i, l in enumerate(labels) if l.split(":")[2] == "d" and l.split(":")[3] == "0"][:5]
    (tmp_path / "bad.fasta").write_text("".join(f">x\n{s}\n" for i, s in enumerate(seqs) if i not in first_group))
    blob = bytearray((tmp_path / "a.vxdna").read_bytes())
    blob[8:10] = (9).to_bytes(2, "big")
    (tmp_path / "future.vxdna").write_bytes(blob)
    cases = [
        (("restore", tmp_path / "a.vxdna", "-o", tmp_path / "o"), 4),                         # key missing
        (("restore", tmp_path / "a.vxdna", "-o", tmp_path / "o", "-k", tmp_path / "k2"), 4),  # wrong key
        (("restore", tmp_path / "missing", "-o", tmp_path / "o"), 3),                        # invalid input
        (("recover", tmp_path / "bad.fasta", "-o", tmp_path / "o", "-k", tmp_path / "k"), 5),  # beyond the ECC guarantee
        (("restore", tmp_path / "future.vxdna", "-o", tmp_path / "o"), 6),                   # unsupported format
        (("store", tmp_path / "in.bin", "-o", tmp_path / "x.vxdna", "--data-shards", "250", "--parity-shards", "10"), 7),
        (("store", tmp_path / "in.bin", "-o", tmp_path / "a.vxdna"), 8),                     # output exists
        (("verify", tmp_path / "bad.fasta", "-k", tmp_path / "k"), 5),
        (("store",), 2),
    ]
    for args, code in cases:
        proc = run(*args)
        assert proc.returncode == code, (args, proc.returncode, proc.stderr)
    assert not (tmp_path / "o").exists()


def test_pipeline_command_one_shot(tmp_path):
    data = mixed_bytes(40_000, seed=42)
    (tmp_path / "in.bin").write_bytes(data)
    run("pipeline", tmp_path / "in.bin", "-o", tmp_path / "out.bin", "--work-dir", tmp_path / "work", "--coverage", "8",
        "--substitution-rate", "0.001", "--insertion-rate", "0.0001", "--deletion-rate", "0.0001", "--dropout-rate", "0.02",
        "--seed", "42", "--chunk-size", "8192", "--report", tmp_path / "report.json", check=0)
    report = json.loads((tmp_path / "report.json").read_text())
    assert (tmp_path / "out.bin").read_bytes() == data and report["bytes_identical"] and report["container_roundtrip_identical"]
    assert {"store", "encode", "sequence", "cluster", "consensus", "decode", "restore", "verify"} <= set(report["steps"])


def test_experiment_run_writes_generated_results(tmp_path):
    (tmp_path / "in.bin").write_bytes(mixed_bytes(8000, seed=43))
    run("experiment", "run", "--input", tmp_path / "in.bin", "--output", tmp_path / "exp", "--trials", "3", "--coverage", "5",
        "--substitution-rate", "0.002", "--dropout-rate", "0.02", "--seed", "5", "--chunk-size", "4096", check=0)
    for name in ("configuration.json", "results.json", "results.csv", "summary.txt"):
        assert (tmp_path / "exp" / name).exists()
    results = json.loads((tmp_path / "exp" / "results.json").read_text())
    assert results["summary"]["trials"] == 3 and results["summary"]["undetected_corruption"] == 0
    assert [t["seed"] for t in results["trials"]] == [5, 6, 7]
    assert "SOFTWARE SIMULATION" in (tmp_path / "exp" / "summary.txt").read_text()


def test_generator_is_reproducible_and_reports_exact_size_and_hash(tmp_path):
    a = json.loads(run("benchmark", "generate", "--size", "3MB", "--pattern", "mixed", "--seed", "42", "-o", tmp_path / "a.bin",
                       "--json", check=0).stdout)
    b = json.loads(run("benchmark", "generate", "--size", "3MB", "--pattern", "mixed", "--seed", "42", "-o", tmp_path / "b.bin",
                       "--json", check=0).stdout)
    assert a["size"] == 3_000_000 == (tmp_path / "a.bin").stat().st_size and a["sha256"] == b["sha256"]
    assert a["sha256"] == hashlib.sha256((tmp_path / "a.bin").read_bytes()).hexdigest()
    c = json.loads(run("benchmark", "generate", "--size", "3MB", "--pattern", "mixed", "--seed", "43", "-o", tmp_path / "c.bin",
                       "--json", check=0).stdout)
    assert c["sha256"] != a["sha256"]


def test_extract_and_info_from_the_cli(tmp_path):
    data = mixed_bytes(50_000, seed=44)
    (tmp_path / "in.bin").write_bytes(data)
    run("store", tmp_path / "in.bin", "-o", tmp_path / "a.vxdna", *SMALL, check=0)
    run("encode", tmp_path / "a.vxdna", "-o", tmp_path / "s.vxs", check=0)
    for source in ("a.vxdna", "s.vxs"):
        run("extract", tmp_path / source, "--offset", "12345", "--length", "5000", "-o", tmp_path / f"{source}.part", check=0)
        assert (tmp_path / f"{source}.part").read_bytes() == data[12345:17345]
        info = json.loads(run("info", tmp_path / source, "--json", check=0).stdout)
        assert info["content"]["sha256"] == hashlib.sha256(data).hexdigest()


def test_experiments_are_reproducible_regardless_of_worker_count(tmp_path):
    from vnxdna.v2.experiment import run_experiment
    from vnxdna.v2.sequencing import SequencingConfig
    from v2_support import FAST
    (tmp_path / "in.bin").write_bytes(mixed_bytes(6000, seed=45))
    channel = SequencingConfig(seed=9, coverage=4, substitution_rate=0.004, dropout_rate=0.05)
    runs = []
    for workers, name in ((1, "a"), (3, "b")):
        run_experiment(tmp_path / "in.bin", tmp_path / name, channel=channel, trials=4, options=FAST, workers=workers)
        trials = json.loads((tmp_path / name / "results.json").read_text())["trials"]
        runs.append([{k: v for k, v in t.items() if k != "runtime_s"} for t in trials])
    assert runs[0] == runs[1]


def test_pipeline_resume_reuses_only_matching_intermediates(tmp_path):
    from vnxdna.v2 import api
    from v2_support import FAST
    src = tmp_path / "in.bin"
    src.write_bytes(mixed_bytes(20_000, seed=46))
    first = api.pipeline(src, tmp_path / "o1.bin", work_dir=tmp_path / "w", options=FAST, workers=1)
    assert first["steps"]["store"]["status"] == "SUCCESS"
    again = api.pipeline(src, tmp_path / "o2.bin", work_dir=tmp_path / "w", options=FAST, workers=1, resume=True)
    assert again["steps"]["store"]["status"] == "SKIPPED" and again["steps"]["encode"]["status"] == "SKIPPED"
    assert (tmp_path / "o2.bin").read_bytes() == src.read_bytes()
    src.write_bytes(mixed_bytes(20_000, seed=47))  # same name, different content: must not reuse the old archive
    changed = api.pipeline(src, tmp_path / "o3.bin", work_dir=tmp_path / "w", options=FAST, workers=1, resume=True)
    assert changed["steps"]["store"]["status"] == "SUCCESS" and changed["steps"]["encode"]["status"] == "SUCCESS"
    assert (tmp_path / "o3.bin").read_bytes() == src.read_bytes()


def test_experiment_counts_a_clustering_refusal_as_a_detected_failure(tmp_path):
    from vnxdna.v2.experiment import run_experiment
    from vnxdna.v2.sequencing import SequencingConfig
    from v2_support import FAST
    (tmp_path / "in.bin").write_bytes(mixed_bytes(6000, seed=46))
    channel = SequencingConfig(seed=11, coverage=4, substitution_rate=0.2, insertion_rate=0.0, deletion_rate=0.0)
    summary = run_experiment(tmp_path / "in.bin", tmp_path / "exp", channel=channel, trials=2, options=FAST,
                             use_consensus=True, workers=1)["summary"]
    assert summary["internal_errors"] == 0 and summary["undetected_corruption"] == 0
    assert summary["failures_by_category"] == {"UNRECOVERABLE_CORRUPTION": 2}
