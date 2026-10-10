"""The opt acceptance harness never turns a harness failure into a row: crashes and timeouts raise HarnessError, and
the decode's whole process group is gone afterwards."""
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("v10_opt_bench_t", ROOT / "experiments/v10/opt/bench.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"


def test_normal_run_returns_output_and_peak(bench):
    p, peak = bench._run_sampled([sys.executable, "-c", "import time; time.sleep(1.2); print('row')"], dict(os.environ))
    assert p.returncode == 0 and p.stdout.strip() == "row" and peak > 0


def test_nonzero_exit_is_returned_not_hidden(bench):
    p, _ = bench._run_sampled([sys.executable, "-c", "raise SystemExit(5)"], dict(os.environ))
    assert p.returncode == 5


def test_timeout_raises_and_kills_the_group(bench, tmp_path):
    pidf = tmp_path / "child"
    with pytest.raises(bench.HarnessError, match="no row written"):
        bench._run_sampled(["bash", "-c", f"sleep 300 & echo $! > {pidf}; wait"], dict(os.environ), timeout=1.0)
    time.sleep(0.2)
    assert _gone(int(pidf.read_text()))


def test_leftover_workers_are_killed_after_a_normal_exit(bench, tmp_path):
    pidf = tmp_path / "child"
    p, _ = bench._run_sampled(["bash", "-c", f"(sleep 300 >/dev/null 2>&1 & echo $! > {pidf}); echo done"], dict(os.environ))
    assert p.returncode == 0
    time.sleep(0.2)
    assert _gone(int(pidf.read_text()))


def test_harness_error_is_not_a_success_exit(bench):
    assert issubclass(bench.HarnessError, SystemExit)
    assert bench.HarnessError("x").code == "x"  # a message exit is status 1, never 0
