import re
from pathlib import Path

from vnxdna import __version__
from vnxdna._version import __version__ as v2

RELEASE = "15.0.0"


def test_version_matches_changelog_head():
    text = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    head = re.search(r"^## (\S+)", text, re.M).group(1)
    assert head == __version__ == v2 == RELEASE


def test_no_stale_unreleased_sections():
    text = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    stale = [m for m in re.findall(r"^## Unreleased.*$", text, re.M)]
    assert stale == []


def test_native_reports_version(capsys):
    import subprocess, sys
    out = subprocess.run([sys.executable, "-m", "vnxdna.native"], capture_output=True, text=True).stdout
    assert RELEASE in out and "6.0.0.dev0" not in out


def test_release_version_is_final_pep440():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__) and __version__ == RELEASE


def test_cli_version_flags_match_package_version():
    from typer.testing import CliRunner

    from vnxdna.cli import app as vnx_dna_app
    from vnxdna.commands.cli import app as vnx_app
    runner = CliRunner()
    r1 = runner.invoke(vnx_dna_app, ["--version"])
    r2 = runner.invoke(vnx_app, ["--version"])
    assert r1.exit_code == 0 and r1.output.strip() == f"vnx-dna {v2}"
    assert r2.exit_code == 0 and r2.output.strip() == f"vnx {v2}"


def test_cli_version_commands_report_package_version():
    import json

    from typer.testing import CliRunner

    from vnxdna.cli import app as vnx_dna_app
    from vnxdna.commands.cli import app as vnx_app
    runner = CliRunner()
    assert json.loads(runner.invoke(vnx_dna_app, ["version", "--json"]).output)["vnxdna"] == v2
    out = runner.invoke(vnx_app, ["version"]).output
    assert json.loads(out[out.index("{"):])["software"] == v2
