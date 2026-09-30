"""V3 CLI regressions (installed CLI as a subprocess): report files, path validation, broken pipes, exit codes, bursts."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from v2_support import mixed_bytes

SMALL = ["--chunk-size", "4096", "--data-shards", "8", "--parity-shards", "4", "--payload-bytes", "24"]


def cli() -> list[str]:
    exe = shutil.which("vnx-dna")
    return [exe] if exe else [sys.executable, "-m", "vnxdna"]


def run(*args, check=None, cwd=None):
    proc = subprocess.run(cli() + [str(a) for a in args], capture_output=True, text=True, timeout=900, cwd=cwd)
    if check is not None:
        assert proc.returncode == check, (args, proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
    assert "Traceback" not in proc.stderr and "INTERNAL_ERROR" not in proc.stderr, proc.stderr
    return proc


@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pool")
    (tmp / "in.bin").write_bytes(mixed_bytes(20_000, seed=5))
    run("store", tmp / "in.bin", "-o", tmp / "a.vxdna", *SMALL, check=0)
    run("encode", tmp / "a.vxdna", "-o", tmp / "s.fasta", check=0)
    run("sequence", tmp / "s.fasta", "-o", tmp / "r.fastq", "--coverage", "3", "--seed", "1", check=0)
    run("cluster", tmp / "r.fastq", "-o", tmp / "c.jsonl", "-j", "1", check=0)
    return tmp


def _listing(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir())


# ---------------------------------------------------------------- B4 --report
def test_report_never_overwrites_an_input_or_an_existing_file(pool, tmp_path):
    shutil.copy(pool / "s.fasta", tmp_path / "s.fasta")
    before = (tmp_path / "s.fasta").read_bytes()
    p = run("sequence", tmp_path / "s.fasta", "-o", tmp_path / "r.fastq", "--force", "--report", tmp_path / "s.fasta", check=8)
    assert "also an input or output" in p.stderr
    assert (tmp_path / "s.fasta").read_bytes() == before and not (tmp_path / "r.fastq").exists()  # refused before any work
    (tmp_path / "old.json").write_text("keep me")
    run("sequence", tmp_path / "s.fasta", "-o", tmp_path / "r.fastq", "--report", tmp_path / "old.json", check=8)
    assert (tmp_path / "old.json").read_text() == "keep me"
    run("sequence", tmp_path / "s.fasta", "-o", tmp_path / "r.fastq", "--force", "--report", tmp_path / "old.json", check=0)
    assert json.loads((tmp_path / "old.json").read_text())["operation"] == "sequence"
    run("verify", pool / "a.vxdna", "--report", pool / "a.vxdna", check=8)  # verify never overwrites anything


# ---------------------------------------------------------------- B6 clean errors instead of internal errors
@pytest.mark.parametrize("args,code", [
    (["consensus", "{tmp}/missing.jsonl", "-o", "{tmp}/k.fasta"], 3),
    (["consensus", "{tmp}", "-o", "{tmp}/k.fasta"], 3),
    (["consensus", "{pool}/in.bin", "-o", "{tmp}/k.fasta"], 3),
    (["cluster", "{pool}/r.fastq", "-o", "{pool}/in.bin/x"], 8),
    (["cluster", "{pool}/r.fastq", "-o", "{tmp}", "--force"], 8),
    (["sequence", "{pool}/s.fasta", "-o", "{tmp}/q.fastq", "--temp-dir", "{tmp}/missing"], 8),
    (["cluster", "{pool}/r.fastq", "-o", "{tmp}/c.jsonl", "--temp-dir", "{tmp}/missing"], 8),
    (["experiment", "run", "-i", "{pool}/in.bin", "-o", "{pool}/in.bin", "--coverage", "1"], 8),
    (["benchmark", "generate", "--size", "1KB", "-o", "{tmp}", "--force"], 8),
    (["store", "{pool}/in.bin", "-o", "{pool}/in.bin/z.vxdna"], 8),
])
def test_bad_paths_fail_cleanly_with_documented_codes(pool, tmp_path, args, code):
    run(*[a.format(tmp=tmp_path, pool=pool) for a in args], check=code)
    assert not [n for n in _listing(tmp_path) if "partial" in n]


def test_malformed_cluster_file_is_invalid_input(pool, tmp_path):
    lines = (pool / "c.jsonl").read_text().splitlines()
    (tmp_path / "cut.jsonl").write_text("\n".join(lines[:2]) + "\n" + lines[2][: len(lines[2]) // 2])
    run("consensus", tmp_path / "cut.jsonl", "-o", tmp_path / "k.fasta", check=3)
    assert _listing(tmp_path) == ["cut.jsonl"]


def test_closed_stdout_ends_quietly(pool, tmp_path):
    proc = subprocess.run(" ".join(cli() + ["sequence", str(pool / "s.fasta"), "-o", str(tmp_path / "p.fastq"), "--json"])
                          + " | true", shell=True, capture_output=True, text=True, executable="/bin/bash")
    assert "BrokenPipe" not in proc.stderr and "INTERNAL_ERROR" not in proc.stderr and "Traceback" not in proc.stderr


def test_interrupted_sequencing_leaves_no_partial_file(pool, tmp_path):
    import signal
    import time
    big = tmp_path / "big.fasta"
    big.write_text((pool / "s.fasta").read_text() * 40)
    proc = subprocess.Popen(cli() + ["sequence", str(big), "-o", str(tmp_path / "o.fastq"), "--coverage", "30"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 60
    while time.time() < deadline and not any("partial" in n for n in _listing(tmp_path)):
        time.sleep(0.05)
    proc.send_signal(signal.SIGINT)
    _, err = proc.communicate(timeout=120)
    if proc.returncode == 0:
        pytest.skip("the command finished before it could be interrupted")
    assert proc.returncode == 130 and "Traceback" not in err
    assert _listing(tmp_path) == ["big.fasta"]


# ---------------------------------------------------------------- B7 / B8 / B14 / B15
def test_generate_onto_an_existing_file_is_an_output_error(tmp_path):
    run("benchmark", "generate", "--size", "1KB", "-o", tmp_path / "g.bin", check=0)
    run("benchmark", "generate", "--size", "1KB", "-o", tmp_path / "g.bin", check=8)


@pytest.mark.parametrize("option,value", [("--max-edit-fraction", "-1"), ("--min-winner-share", "5"),
                                          ("--min-winner-share", "nan")])
def test_consensus_rejects_nonsense_parameters(pool, tmp_path, option, value):
    run("consensus", pool / "c.jsonl", "-o", tmp_path / "k.fasta", option, value, check=7)
    assert not (tmp_path / "k.fasta").exists()


def test_explicit_missing_dna_index_is_an_error(pool, tmp_path):
    run("extract", pool / "s.fasta", "-o", tmp_path / "e.bin", "--offset", "0", "--length", "10", "--dna-index",
        tmp_path / "missing.vxidx", check=3)


@pytest.mark.parametrize("name", ["r.fastq.gz", "r.bam", "r.fasta.zst"])
def test_compressed_or_alignment_output_names_are_refused(pool, tmp_path, name):
    run("sequence", pool / "s.fasta", "-o", tmp_path / name, check=7)
    assert _listing(tmp_path) == []


# ---------------------------------------------------------------- B9 pipeline
def test_pipeline_checks_up_front_and_cleans_up(pool, tmp_path):
    run("pipeline", pool / "in.bin", "-o", tmp_path / "x.bin", "--strand-format", "bad", check=7)
    (tmp_path / "exists.bin").write_bytes(b"x")
    run("pipeline", pool / "in.bin", "-o", tmp_path / "exists.bin", check=8)
    temp = tmp_path / "tmp"
    temp.mkdir()
    run("pipeline", pool / "in.bin", "-o", tmp_path / "rec.bin", "--coverage", "2", "--temp-dir", temp, check=0)
    assert (tmp_path / "rec.bin").read_bytes() == (pool / "in.bin").read_bytes()
    assert _listing(temp) == []  # the temporary work directory is removed (2.0 left it with every intermediate)
    work = tmp_path / "work"
    run("pipeline", pool / "in.bin", "-o", tmp_path / "rec2.bin", "--coverage", "3", "--work-dir", work, "--cleanup", "all", check=0)
    assert _listing(work) == []  # 2.0 kept the raw reads and the DNA index


# ---------------------------------------------------------------- bursts on the CLI
def test_burst_options_reach_the_simulator(pool, tmp_path):
    p = run("sequence", pool / "s.fasta", "-o", tmp_path / "b.fastq", "--coverage", "2", "--burst-rate", "0.5", "--burst-length",
            "3", "--burst-kind", "mixed", "--json", check=0)
    r = json.loads(p.stdout)
    assert r["config"]["burst_kind"] == "mixed" and sum(r["errors"][f"bursts_{k}"] for k in ("substitution", "deletion", "insertion")) > 0
    run("sequence", pool / "s.fasta", "-o", tmp_path / "c.fastq", "--burst-kind", "chimera", check=7)
    e = run("experiment", "run", "-i", pool / "in.bin", "-o", tmp_path / "exp", "--coverage", "1", "--coverage-model", "fixed",
            "--burst-rate", "0.2", "--burst-length", "1", *["--chunk-size", "4096"], check=0)
    assert "detected" in e.stdout and "all detected" not in e.stdout
    conf = json.loads((tmp_path / "exp" / "configuration.json").read_text())
    assert conf["channel"]["burst_rate"] == 0.2
