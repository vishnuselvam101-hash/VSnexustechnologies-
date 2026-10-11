"""V11-RT-07 (LOW): keys and passphrases never appear in reprs, error messages or CLI error JSON; a ``vnx`` key file
that group or others can access is accepted with a warning (V3 ``vnx-dna`` already warned). SYNTHETIC SOFTWARE TEST
data; the keys below are test-only."""
from __future__ import annotations

import json
import logging
import os

from typer.testing import CliRunner

from vnxdna.archive.crypto import load_key_file
from vnxdna.archive.operations import ArchiveOptions
from vnxdna.commands.cli import app
from vnxdna.secure.identity import TokenAuthority

KEY = bytes.fromhex("5e" * 32)
PW = "test-passphrase-not-secret-7319"


def test_reprs_hold_no_secrets():
    for text in (repr(ArchiveOptions(key=KEY)), repr(ArchiveOptions(passphrase=PW)), repr(TokenAuthority(KEY, None))):
        assert PW not in text and repr(KEY)[2:-1] not in text and KEY.hex() not in text


def test_key_file_permission_warning(tmp_path, caplog):
    loose, tight = tmp_path / "loose.key", tmp_path / "tight.key"
    for p, mode in ((loose, 0o644), (tight, 0o600)):
        p.write_text(KEY.hex() + "\n")
        os.chmod(p, mode)
    with caplog.at_level(logging.WARNING):
        assert load_key_file(tight) == KEY
    assert not caplog.records
    with caplog.at_level(logging.WARNING):
        assert load_key_file(loose) == KEY
    assert any("group or others" in r.getMessage() and "644" in r.getMessage() for r in caplog.records)
    assert all(KEY.hex() not in r.getMessage() for r in caplog.records)


def test_wrong_key_and_passphrase_errors_hold_no_secret(tmp_path, monkeypatch):
    src = tmp_path / "in.bin"
    src.write_bytes(b"x" * 1000)
    kf = tmp_path / "k.key"
    kf.write_text(KEY.hex())
    os.chmod(kf, 0o600)
    r = CliRunner().invoke(app, ["archive", "--key-file", str(kf), str(src), str(tmp_path / "a.vnx")])
    assert r.exit_code == 0, r.output
    wrong = tmp_path / "w.key"
    wrong.write_text("00" * 32)
    os.chmod(wrong, 0o600)
    monkeypatch.setenv("VNX_TEST_PW", PW)
    for extra in (["--key-file", str(wrong)], ["--passphrase-env", "VNX_TEST_PW"]):
        r = CliRunner().invoke(app, ["extract", *extra, str(tmp_path / "a.vnx"), str(tmp_path / "out")])
        assert r.exit_code != 0
        blob = r.output + (r.stderr or "")
        assert PW not in blob and KEY.hex() not in blob and "00" * 32 not in blob
        assert json.loads(r.stderr)["code"]          # a structured error, not a traceback
