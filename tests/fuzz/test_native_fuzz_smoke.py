"""Smoke run of the libFuzzer harnesses in fuzz/native/ (ASan + UBSan), inside the normal suite.

Each harness is built exactly as fuzz/run.sh builds it and replays its seed corpus (fuzz/corpus/<target>) and its
regression corpus (fuzz/regressions/<target>), then mutates for a bounded number of runs. Any sanitizer report or
harness invariant violation aborts the binary, so the test only checks the exit status. Long campaigns:
``fuzz/run.sh <target> <seconds>``. Skipped when clang with libFuzzer is not installed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FUZZ = ROOT / "fuzz"
TARGETS = sorted(p.name[: -len("_fuzz.c")] for p in (FUZZ / "native").glob("*_fuzz.c"))
FLAGS = ["-g", "-O1", "-std=c11", "-fsanitize=fuzzer,address,undefined", "-fno-sanitize-recover=undefined",
         "-fno-omit-frame-pointer"]
RUNS = {"rs": 300, "align": 1500, "reads": 3000}


def _clang() -> str | None:
    cc = shutil.which("clang")
    if cc is None:
        return None
    probe = subprocess.run([cc, "-fsanitize=fuzzer", "-x", "c", "-", "-o", "/dev/null"], input=b"#include <stdint.h>\n#include <stddef.h>\nint LLVMFuzzerTestOneInput(const uint8_t *d, size_t n) { (void)d; (void)n; return 0; }\n",
                           capture_output=True)
    return cc if probe.returncode == 0 else None


CLANG = _clang()


def test_every_target_has_seeds_and_a_regression_dir():
    assert set(TARGETS) >= {"reads", "rs", "align"}
    for t in TARGETS:
        assert any((FUZZ / "corpus" / t).iterdir()), t
        assert (FUZZ / "regressions" / t).is_dir(), t


@pytest.mark.skipif(CLANG is None, reason="clang with -fsanitize=fuzzer is not installed")
@pytest.mark.parametrize("target", TARGETS)
def test_native_harness_smoke(tmp_path, target):
    binary = tmp_path / f"{target}_fuzz"
    build = subprocess.run([CLANG, *FLAGS, f"-I{ROOT / 'src'}", str(FUZZ / "native" / f"{target}_fuzz.c"), "-o", str(binary),
                            "-lm"], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr[-4000:]
    work = tmp_path / "corpus"
    work.mkdir()
    dirs = [str(work)] + [str(d) for d in (FUZZ / "corpus" / target, FUZZ / "regressions" / target) if d.is_dir()]
    run = subprocess.run([str(binary), f"-runs={RUNS.get(target, 1000)}", "-seed=1", "-rss_limit_mb=2048", "-timeout=20",
                          "-max_len=4096", f"-artifact_prefix={tmp_path}/", *dirs],
                         capture_output=True, text=True, timeout=240,
                         env={"ASAN_OPTIONS": "detect_leaks=1:abort_on_error=1", "UBSAN_OPTIONS": "halt_on_error=1",
                              "PATH": "/usr/bin:/bin"})
    assert run.returncode == 0, run.stderr[-4000:]
    assert "Done" in run.stderr
