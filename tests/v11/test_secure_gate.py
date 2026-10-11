"""V11 WP4: the opt-in VNX-Secure gate can never be bypassed through SDK / legacy API / CLI entry paths."""
import hashlib

import pytest

from vnxdna import api as v1api
from vnxdna.sdk import api as sdk
from vnxdna.core import guard
from vnxdna.secure.control import AccessDenied, ControlPlane
from vnxdna.v2 import api as v2api, archive as v2arc

SECRET = "writer-pw-s3cret"


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("VNX_SECURE_HOME", "VNX_SECURE_TOKEN", "VNX_SECURE_SESSION", "VNX_SECURE_REQUIRE"):
        monkeypatch.delenv(k, raising=False)
    guard._CACHE.clear()
    h = tmp_path / "home"
    cp = ControlPlane.init(h)
    cp.add_principal("w", SECRET, ("writer",), scrypt_n=1 << 10)
    cp.add_principal("g", "guest-pw", ("guest",), scrypt_n=1 << 10)
    cp.check_integrity()
    return h, cp


def ctx(h, cp, user, pw, ses="s1"):
    return guard.SecureContext(h, cp.login(user, pw, ses), ses)


@pytest.fixture
def blob(tmp_path):
    p = tmp_path / "in.bin"
    p.write_bytes(hashlib.sha256(b"x").digest() * 40)
    return p


def test_default_unchanged(blob, tmp_path):
    guard.enforce("encode", tmp_path / "o.fa")  # no context, not required: no-op


def test_allowed_and_denied_sdk(home, blob, tmp_path):
    h, cp = home
    ok = ctx(h, cp, "w", SECRET)
    res = sdk.encode(blob, tmp_path / "ok.fa", secure=ok, allow_unencrypted=True)
    assert res.status == "SUCCESS" or getattr(res, "ok", True)
    bad = ctx(h, cp, "g", "guest-pw", "s2")
    with pytest.raises(AccessDenied):
        sdk.encode(blob, tmp_path / "no.fa", secure=bad, allow_unencrypted=True)
    assert not (tmp_path / "no.fa").exists()


@pytest.mark.parametrize("bad", [None, "token", object(), guard.SecureContext("/nonexistent", "t", "s")])
def test_required_blocks_everything(home, blob, tmp_path, monkeypatch, bad):
    monkeypatch.setenv("VNX_SECURE_REQUIRE", "1")
    for call in (lambda: sdk.encode(blob, tmp_path / "a.fa", secure=bad),
                 lambda: sdk.decode(blob, tmp_path / "a.vnx", secure=bad),
                 lambda: sdk.extract(blob, tmp_path / "d", secure=bad),
                 lambda: v1api.encode(blob, tmp_path / "b.fa"),
                 lambda: v1api.decode(blob, tmp_path / "b.v"),
                 lambda: v1api.restore(blob, tmp_path / "b.r"),
                 lambda: v2api.encode(blob, tmp_path / "c.fa"),
                 lambda: v2api.decode(blob, tmp_path / "c.v"),
                 lambda: v2api.restore(blob, tmp_path / "c.r"),
                 lambda: v2arc.restore_file(blob, tmp_path / "e.r")):
        with pytest.raises(AccessDenied):
            call()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["home", "in.bin"]


def test_env_context_gates_legacy_paths(home, blob, tmp_path, monkeypatch):
    h, cp = home
    c = ctx(h, cp, "g", "guest-pw")  # guest: denied by policy
    monkeypatch.setenv("VNX_SECURE_HOME", str(h)); monkeypatch.setenv("VNX_SECURE_TOKEN", c.token)
    monkeypatch.setenv("VNX_SECURE_SESSION", c.session)
    with pytest.raises(AccessDenied):
        v2api.restore(blob, tmp_path / "r")
    with pytest.raises(AccessDenied):
        v1api.encode(blob, tmp_path / "r.fa")


def test_forged_replayed_and_wrong_session(home, blob, tmp_path):
    h, cp = home
    c = ctx(h, cp, "w", SECRET)
    for forged in (guard.SecureContext(h, c.token + "x", c.session), guard.SecureContext(h, c.token, "other"),
                   guard.SecureContext(h, "", c.session)):
        with pytest.raises(AccessDenied):
            guard.enforce("encode", tmp_path / "o.fa", forged)


def test_no_secret_in_errors_or_audit(home, blob, tmp_path):
    h, cp = home
    c = ctx(h, cp, "g", "guest-pw")
    with pytest.raises(AccessDenied) as ei:
        guard.enforce("encode", tmp_path / "o.fa", c)
    assert c.token not in str(ei.value) and c.token not in repr(c)
    blob_text = "".join(p.read_text(errors="ignore") for p in h.rglob("*") if p.is_file() and p.suffix in (".log", ".jsonl", ".json"))
    assert c.token not in blob_text and SECRET not in blob_text


def test_unexpected_error_fails_closed(home, tmp_path, monkeypatch):
    h, cp = home
    c = ctx(h, cp, "w", SECRET)
    monkeypatch.setattr(ControlPlane, "authorize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(AccessDenied):
        guard.enforce("encode", tmp_path / "o.fa", c)


def test_cli_denied_exit_nonzero_no_output(home, blob, tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from vnxdna.commands.cli import app
    h, cp = home
    c = ctx(h, cp, "g", "guest-pw")
    monkeypatch.setenv("VNX_SECURE_HOME", str(h)); monkeypatch.setenv("VNX_SECURE_TOKEN", c.token)
    monkeypatch.setenv("VNX_SECURE_SESSION", c.session)
    out = tmp_path / "cli.fa"
    r = CliRunner().invoke(app, ["encode", str(blob), "-o", str(out), "--allow-unencrypted"])
    assert r.exit_code != 0 and not out.exists() and c.token not in (r.output or "")
    assert "SUCCESS" not in (r.output or "")
