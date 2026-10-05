"""tools/msan: the golden-vector replays used by the MemorySanitizer gate (CI job `msan`, tools/msan.sh).

The MSan build itself needs clang and its MSan runtime and runs in CI. Here the same replay programs are built with
the default C compiler (no sanitizer) on small vector files, so the suite checks that (a) the vector writer and the C
readers agree on the format, (b) every kernel reproduces the reference outputs through the replay, and (c) the
replay's comparison is not vacuous: one flipped expected byte makes it exit 1.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler")


@pytest.fixture(scope="module")
def vectors(tmp_path_factory):
    from vnxdna.native import align, reads, rs

    if not (align.available() and reads.available() and rs.available()):
        pytest.skip("native kernels not built")
    out = tmp_path_factory.mktemp("msan-vectors")
    res = subprocess.run([sys.executable, str(REPO / "tools/msan/make_vectors.py"), str(out), "--align-rounds", "6",
                          "--rs-rounds", "40", "--reads-cases", "60"], capture_output=True, text=True, cwd=REPO)
    assert res.returncode == 0, res.stderr[-2000:]
    return out


def _build(tmp_path: Path, kernel: str) -> Path:
    exe = tmp_path / f"replay_{kernel}"
    cmd = [CC, "-O1", "-std=c11", "-D_DEFAULT_SOURCE", f"-I{REPO / 'src'}", f"-I{REPO / 'tools/msan'}",
           str(REPO / f"tools/msan/replay_{kernel}.c"), "-o", str(exe), "-lm"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[-2000:]
    return exe


@pytest.mark.parametrize("kernel", ["align", "rs", "reads"])
def test_replay_matches_reference(vectors, tmp_path, kernel):
    exe = _build(tmp_path, kernel)
    res = subprocess.run([str(exe), str(vectors / f"{kernel}.bin")], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[-2000:]
    assert '"mismatches": 0' in res.stdout


def _last_array_offset(path: Path) -> int:
    """Byte offset of the last array's data in a vector file (walks the documented format)."""
    blob = path.read_bytes()
    pos, last = 8, None
    while pos < len(blob):
        typ, count = struct.unpack_from("<iq", blob, pos)
        size = {1: 1, 2: 2, 3: 4, 4: 8}[typ] * count
        if size:
            last = pos + 12
        pos += 12 + size
    assert last is not None
    return last


def test_replay_detects_a_wrong_expected_byte(vectors, tmp_path):
    exe = _build(tmp_path, "rs")
    bad = tmp_path / "rs-bad.bin"
    blob = bytearray((vectors / "rs.bin").read_bytes())
    blob[_last_array_offset(vectors / "rs.bin")] ^= 0x01        # the last record's expected errata count
    bad.write_bytes(bytes(blob))
    res = subprocess.run([str(exe), str(bad)], capture_output=True, text=True)
    assert res.returncode == 1
    assert "MISMATCH" in res.stderr
