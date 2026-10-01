"""The README's command blocks run verbatim with the installed CLI (documentation must match the implementation)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[2] / "README.md"
NEEDS_CLI = pytest.mark.skipif(shutil.which("vnx-dna") is None or shutil.which("bash") is None,
                               reason="needs the installed vnx-dna on PATH and bash")


def _blocks(heading: str, count: int = 1) -> str:
    text = README.read_text(encoding="utf-8")
    section = text[text.index(heading):]
    return "\n".join(re.findall(r"```bash\n(.*?)```", section, re.S)[:count])


def _bash(script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=cwd, capture_output=True, text=True, timeout=900)


@NEEDS_CLI
def test_readme_basic_example_runs_verbatim(tmp_path):
    proc = _bash(_blocks("## Basic example"), tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "identical" in proc.stdout


@NEEDS_CLI
def test_readme_complete_workflow_damage_recovery_and_encryption_run_verbatim(tmp_path):
    script = "\n".join([_blocks("## Complete DNA workflow", 2), _blocks("## Damage simulation, recovery and verification"),
                        _blocks("### Encrypted archives")])
    proc = _bash(script, tmp_path)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert "container rebuilt byte for byte" in proc.stdout and proc.stdout.count("identical") >= 1
    assert (tmp_path / "recovered.bin").read_bytes() == (tmp_path / "input.bin").read_bytes()
    assert (tmp_path / "from-consensus.bin").read_bytes() == (tmp_path / "input.bin").read_bytes()


@NEEDS_CLI
def test_readme_v3_error_sweep_and_burst_repair_run_verbatim(tmp_path):
    proc = _bash(_blocks("## V3: error sweeps and coverage-1 repair"), tmp_path)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    assert "| substitution:0 |" in proc.stdout and "identical" in proc.stdout
