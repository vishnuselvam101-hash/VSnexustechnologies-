"""V3 release review: data-loss, temporary-file, signal and CLI-contract regressions (installed CLI as a subprocess).

Each test reproduces a defect found in the final pre-release review of 3.0.0 and fails on the code before the fix.
"""
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from v2_support import mixed_bytes
from test_cli_v3 import SMALL, cli, run


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("arc")
    (tmp / "in.bin").write_bytes(mixed_bytes(30_000, seed=9))
    run("keygen", "-o", tmp / "k.txt", check=0)
    run("store", tmp / "in.bin", "-o", tmp / "a.vxdna", *SMALL, check=0)
    run("store", tmp / "in.bin", "-o", tmp / "e.vxdna", "-k", tmp / "k.txt", *SMALL, check=0)
    run("encode", tmp / "a.vxdna", "-o", tmp / "s.fasta", check=0)
    return tmp


# ---------------------------------------------------------------- H1 pipeline --work-dir never destroys user files
def test_pipeline_work_dir_refuses_existing_files_and_cleans_only_its_own(arc, tmp_path):
    src = tmp_path / "photo.jpg"
    src.write_bytes(mixed_bytes(5_000, seed=1))
    mine = {"photo.jpg.vxdna": b"my archive", "photo.jpg.fasta": b"my strands", "photo.jpg.clusters.jsonl": b"my clusters"}
    for name, data in mine.items():
        (tmp_path / name).write_bytes(data)
    p = run("pipeline", src, "-o", tmp_path / "out.jpg", "--work-dir", tmp_path, "--cleanup", "all", check=8)
    assert "already holds" in p.stderr and "photo.jpg.vxdna" in p.stderr
    assert all((tmp_path / n).read_bytes() == d for n, d in mine.items()) and not (tmp_path / "out.jpg").exists()
    # a file the run never writes (no channel -> no clusters) survives --force --cleanup all
    (tmp_path / "photo.jpg.vxdna").unlink()
    (tmp_path / "photo.jpg.fasta").unlink()
    run("pipeline", src, "-o", tmp_path / "out.jpg", "--work-dir", tmp_path, "--cleanup", "all", "--force", check=0)
    assert (tmp_path / "out.jpg").read_bytes() == src.read_bytes()
    assert (tmp_path / "photo.jpg.clusters.jsonl").read_bytes() == b"my clusters"
    assert not (tmp_path / "photo.jpg.vxdna").exists() and not (tmp_path / "photo.jpg.fasta").exists()


def test_pipeline_intermediate_may_not_be_the_output_or_the_report(tmp_path):
    src = tmp_path / "x"
    src.write_bytes(b"input x")
    run("pipeline", src, "-o", tmp_path / "x.vxdna", "--work-dir", tmp_path, "--force", check=8)  # output = the container
    run("pipeline", src, "-o", tmp_path / "o", "--work-dir", tmp_path, "--force", "--report", tmp_path / "x.fasta", check=8)
    assert src.read_bytes() == b"input x" and sorted(os.listdir(tmp_path)) == ["x"]


# ---------------------------------------------------------------- H2 --report never replaces a key file or a DNA index
def test_report_cannot_replace_the_key_file_or_the_dna_index(arc, tmp_path):
    key = tmp_path / "k.txt"
    key.write_bytes((arc / "k.txt").read_bytes())
    before = key.read_bytes()
    run("store", arc / "in.bin", "-o", tmp_path / "x.vxdna", "-k", key, "--report", key, "--force", check=8)
    run("restore", arc / "e.vxdna", "-o", tmp_path / "x.bin", "-k", key, "--report", key, "--force", check=8)
    run("verify", arc / "e.vxdna", "-k", key, "--report", key, "--force", check=8)
    assert key.read_bytes() == before
    run("encode", arc / "a.vxdna", "-o", tmp_path / "t.fasta", "--report", tmp_path / "t.fasta.vxidx", "--force", check=8)
    assert not (tmp_path / "t.fasta").exists()


# ---------------------------------------------------------------- L2 an output is never one of the inputs
@pytest.mark.parametrize("args", [
    ["extract", "{arc}/c.vxdna", "-o", "{arc}/c.vxdna", "--offset", "0", "--length", "10", "--force"],
    ["restore", "{arc}/c.vxdna", "-o", "{arc}/c.vxdna", "--force"],
    ["store", "{arc}/c.bin", "-o", "{arc}/c.bin", "--force"],
    ["store", "{arc}/c.bin", "-o", "{arc}/k.txt", "-k", "{arc}/k.txt", "--force"],
    ["encode", "{arc}/c.vxdna", "-o", "{arc}/c.vxdna", "--force"],
])
def test_output_equal_to_input_is_refused_even_with_force(arc, tmp_path, args):
    for name in ("c.vxdna", "c.bin", "k.txt"):
        src = arc / ("a.vxdna" if name == "c.vxdna" else "in.bin" if name == "c.bin" else "k.txt")
        (tmp_path / name).write_bytes(src.read_bytes())
    before = {n: (tmp_path / n).read_bytes() for n in ("c.vxdna", "c.bin", "k.txt")}
    p = run(*[a.replace("{arc}", str(tmp_path)) for a in args], check=8)
    assert "also an input" in p.stderr
    assert {n: (tmp_path / n).read_bytes() for n in before} == before


# ---------------------------------------------------------------- M2 temporary files are private, never fixed names
def test_planted_symlinks_at_old_temporary_names_are_never_written_through(arc, tmp_path):
    precious = tmp_path / "precious.txt"
    precious.write_text("keep")
    for old in (".rep.json.report.partial", ".c.jsonl.partial", ".t.fasta.vxidx.partial", "d.vxdna.partial", ".r.fastq.partial"):
        (tmp_path / old).symlink_to(precious)
    run("restore", arc / "a.vxdna", "-o", tmp_path / "x.bin", "--report", tmp_path / "rep.json", check=0)
    run("encode", arc / "a.vxdna", "-o", tmp_path / "t.fasta", check=0)
    run("sequence", arc / "s.fasta", "-o", tmp_path / "r.fastq", "--coverage", "2", "--seed", "1", "--report", tmp_path / "r.json", check=0)
    run("cluster", tmp_path / "r.fastq", "-o", tmp_path / "c.jsonl", "-j", "1", check=0)
    run("decode", arc / "s.fasta", "-o", tmp_path / "d.vxdna", check=0)
    assert precious.read_text() == "keep"
    for out in ("rep.json", "t.fasta.vxidx", "c.jsonl", "d.vxdna"):
        assert not (tmp_path / out).is_symlink(), out
    assert (tmp_path / "d.vxdna").read_bytes() == (arc / "a.vxdna").read_bytes()


# ---------------------------------------------------------------- M3 inputs named like a work file are never destroyed
def test_input_named_like_a_partial_file_survives(arc, tmp_path):
    src = tmp_path / "movie.mkv.partial"
    src.write_bytes((arc / "in.bin").read_bytes())
    run("store", src, "-o", tmp_path / "movie.mkv", check=8)
    assert src.read_bytes() == (arc / "in.bin").read_bytes()
    reads = tmp_path / "reads.partial"
    reads.write_bytes((arc / "s.fasta").read_bytes())
    run("decode", reads, "-o", tmp_path / "reads", check=0)
    assert reads.read_bytes() == (arc / "s.fasta").read_bytes()
    assert (tmp_path / "reads").read_bytes() == (arc / "a.vxdna").read_bytes()


# ---------------------------------------------------------------- L1 keygen
def test_keygen_never_writes_through_symlinks_and_never_truncates_an_old_key(tmp_path):
    target = tmp_path / "elsewhere.txt"
    dangling = tmp_path / "dangling.txt"
    dangling.symlink_to(target)
    run("keygen", "-o", dangling, check=8)
    assert not target.exists()
    target.write_text("not a key")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    run("keygen", "-o", link, "--force", check=0)
    assert target.read_text() == "not a key" and not link.is_symlink() and (link.stat().st_mode & 0o777) == 0o600
    old = tmp_path / "old.txt"
    run("keygen", "-o", old, check=0)
    before = old.read_bytes()

    def no_writes():  # every write fails with EFBIG
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    p = subprocess.run(cli() + ["keygen", "-o", str(old), "--force"], capture_output=True, text=True, preexec_fn=no_writes)
    assert p.returncode == 8, p.stderr
    assert old.read_bytes() == before
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".old.txt")]


# ---------------------------------------------------------------- L4 stale DNA index
def test_encode_no_index_force_removes_a_stale_index(arc, tmp_path):
    run("store", arc / "in.bin", "-o", tmp_path / "b.vxdna", *SMALL, check=0)  # a second archive (new archive id)
    run("encode", arc / "a.vxdna", "-o", tmp_path / "s.fasta", check=0)
    run("encode", tmp_path / "b.vxdna", "-o", tmp_path / "s.fasta", "--no-index", "--force", check=0)
    assert not (tmp_path / "s.fasta.vxidx").exists()
    run("extract", tmp_path / "s.fasta", "--offset", "100", "--length", "50", "-o", tmp_path / "x.bin", check=0)
    assert (tmp_path / "x.bin").read_bytes() == (arc / "in.bin").read_bytes()[100:150]


# ---------------------------------------------------------------- L5 conflicting range options
@pytest.mark.parametrize("extra", [["--chunk", "0", "--length", "5"], ["--offset", "10", "--length", "5", "--end", "100"],
                                   ["--offset", "1", "--start", "2"], ["--chunk", "0", "--offset", "0"]])
def test_extract_conflicting_selections_are_refused(arc, tmp_path, extra):
    run("extract", arc / "a.vxdna", "-o", tmp_path / "x.bin", *extra, check=3)
    assert not (tmp_path / "x.bin").exists()


# ---------------------------------------------------------------- L6 missing inputs are exit 3 everywhere
@pytest.mark.parametrize("args", [["store", "{t}/missing", "-o", "{t}/o.vxdna"], ["pipeline", "{t}/missing", "-o", "{t}/o"],
                                  ["simulate-errors", "{t}/missing", "-o", "{t}/sw", "--sweep", "substitution=0"],
                                  ["experiment", "run", "-i", "{t}/missing", "-o", "{t}/ex"], ["store", "{t}", "-o", "{t}/o.vxdna"]])
def test_missing_input_is_invalid_input(tmp_path, args):
    p = run(*[a.replace("{t}", str(tmp_path)) for a in args], check=3)
    assert "Usage" not in p.stderr


# ---------------------------------------------------------------- L7 key files
def test_key_file_must_be_a_small_regular_file(arc, tmp_path):
    run("restore", arc / "e.vxdna", "-o", tmp_path / "x.bin", "-k", "/dev/zero", check=3)
    big = tmp_path / "big.txt"
    big.write_bytes(b"A" * 100_000)
    run("restore", arc / "e.vxdna", "-o", tmp_path / "x.bin", "-k", big, check=7)
    loose = tmp_path / "loose.txt"
    loose.write_bytes((arc / "k.txt").read_bytes())
    loose.chmod(0o644)
    p = run("restore", arc / "e.vxdna", "-o", tmp_path / "x.bin", "-k", loose, check=0)
    assert "warning" in p.stderr and "chmod 600" in p.stderr
    assert (tmp_path / "x.bin").read_bytes() == (arc / "in.bin").read_bytes()


# ---------------------------------------------------------------- M1 SIGTERM and a killed parent
def _children(pid: int) -> list[int]:
    out = subprocess.run(["ps", "-o", "pid=", "--ppid", str(pid)], capture_output=True, text=True).stdout
    return [int(x) for x in out.split()]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie is dead for our purposes
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


@pytest.fixture(scope="module")
def big_strands(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("big")
    (tmp / "in.bin").write_bytes(os.urandom(12_000_000))
    run("store", tmp / "in.bin", "-o", tmp / "a.vxdna", check=0)
    run("encode", tmp / "a.vxdna", "-o", tmp / "s.fasta", check=0)
    return tmp


def _start_recover(big_strands, out_dir: Path, scratch: Path) -> subprocess.Popen:
    return subprocess.Popen(cli() + ["recover", str(big_strands / "s.fasta"), "-o", str(out_dir / "o.bin"), "-j", "3",
                                     "--temp-dir", str(scratch)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _wait_for_workers(proc: subprocess.Popen, timeout: float = 60.0) -> list[int]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        kids = _children(proc.pid)
        if len(kids) >= 2:
            return kids
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    pytest.skip("the command finished before its worker pool could be observed (machine too fast for this input)")


@pytest.mark.skipif(sys.platform != "linux", reason="process-tree inspection uses Linux ps//proc")
@pytest.mark.parametrize("delay", [0.0, 0.25, 0.6, 1.0])
def test_sigterm_cleans_up_like_ctrl_c(big_strands, tmp_path, delay):
    """Signalled at several points while the worker pool runs. A worker killed while writing its result used to leave
    the pool's shutdown waiting forever (the command hung after SIGTERM); the timeout below catches a hang."""
    out_dir, scratch = tmp_path / "out", tmp_path / "scratch"
    out_dir.mkdir()
    scratch.mkdir()
    proc = _start_recover(big_strands, out_dir, scratch)
    kids = _wait_for_workers(proc)
    time.sleep(delay)
    if proc.poll() is not None:
        pytest.skip("the command finished before the signal (machine too fast for this input)")
    proc.send_signal(signal.SIGTERM)
    _, err = proc.communicate(timeout=60)
    assert proc.returncode == 130, err
    assert "Traceback" not in err and "interrupted" in err
    assert os.listdir(out_dir) == [] and os.listdir(scratch) == []
    deadline = time.time() + 10
    while any(_alive(k) for k in kids) and time.time() < deadline:
        time.sleep(0.1)
    assert not [k for k in kids if _alive(k)]


@pytest.mark.skipif(sys.platform != "linux", reason="process-tree inspection uses Linux ps//proc")
def test_workers_exit_when_the_parent_is_killed(big_strands, tmp_path):
    proc = _start_recover(big_strands, tmp_path, tmp_path)
    kids = _wait_for_workers(proc)
    proc.kill()
    proc.communicate(timeout=60)
    deadline = time.time() + 15
    while any(_alive(k) for k in kids) and time.time() < deadline:
        time.sleep(0.1)
    assert not [k for k in kids if _alive(k)], "worker processes outlived their killed parent"
