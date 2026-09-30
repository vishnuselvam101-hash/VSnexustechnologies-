"""The README quick start is executable exactly as written (documentation must match the implementation)."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[2] / "README.md"


def _block(heading: str) -> str:
    text = README.read_text(encoding="utf-8")
    section = text[text.index(heading):]
    return re.search(r"```bash\n(.*?)```", section, re.S).group(1)


@pytest.mark.skipif(shutil.which("vnx-dna") is None or shutil.which("bash") is None, reason="needs installed vnx-dna and bash")
def test_readme_quick_start_runs_verbatim(tmp_path):
    script = "set -euo pipefail\n" + _block("## Quick start")
    proc = subprocess.run(["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=600, env=os.environ)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "recovered.txt").read_bytes() == b"Hello VNX-DNA\n"
    lines = [l.split()[0] for l in proc.stdout.splitlines() if l.endswith(("input.txt", "recovered.txt")) and len(l.split()[0]) == 64]
    assert len(lines) == 2 and lines[0] == lines[1]


@pytest.mark.skipif(shutil.which("vnx-dna") is None or shutil.which("bash") is None, reason="needs installed vnx-dna and bash")
def test_readme_encryption_example_runs(tmp_path):
    (tmp_path / "secret.pdf").write_bytes(os.urandom(1_300_000))
    script = "set -euo pipefail\n" + _block("### Encrypted archives") + "\n" + _block("### Random access")
    proc = subprocess.run(["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=600, env=os.environ)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = (tmp_path / "secret.pdf").read_bytes()
    assert (tmp_path / "secret-restored.pdf").read_bytes() == data
    assert (tmp_path / "part3.bin").read_bytes() == data[3 * 262144:4 * 262144]
    assert (tmp_path / "slice.bin").read_bytes() == data[1_000_000:1_200_000]
