import re
from pathlib import Path

from vnxdna import __version__
from vnxdna._version import __version__ as v2


def test_version_matches_changelog_head():
    text = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    head = re.search(r"^## (\S+)", text, re.M).group(1)
    assert head == __version__ == v2 == "11.0.0.dev0"


def test_no_stale_unreleased_sections():
    text = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    stale = [m for m in re.findall(r"^## Unreleased.*$", text, re.M)]
    assert stale == []


def test_native_reports_version(capsys):
    import subprocess, sys
    out = subprocess.run([sys.executable, "-m", "vnxdna.native"], capture_output=True, text=True).stdout
    assert "11.0.0.dev0" in out and "6.0.0.dev0" not in out
