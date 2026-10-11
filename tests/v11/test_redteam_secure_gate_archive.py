"""V11-RT-03 (LOW): ``sdk.archive`` / ``vnx archive`` builds a container from plaintext files but did not pass the
opt-in VNX-Secure gate that ``sdk.encode`` (operation ``encode`` -> policy ``write``) applies; with
``VNX_SECURE_REQUIRE=1`` or a guest context it wrote the container. SYNTHETIC SOFTWARE TEST data."""
import pytest
from typer.testing import CliRunner

from vnxdna.commands.cli import app
from vnxdna.core import guard
from vnxdna.sdk import api as sdk
from vnxdna.secure.control import AccessDenied, ControlPlane


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("VNX_SECURE_HOME", "VNX_SECURE_TOKEN", "VNX_SECURE_SESSION", "VNX_SECURE_REQUIRE"):
        monkeypatch.delenv(k, raising=False)
    guard._CACHE.clear()
    cp = ControlPlane.init(tmp_path / "home")
    cp.add_principal("w", "writer-pw-test", ("writer",), scrypt_n=1 << 10)
    cp.add_principal("g", "guest-pw-test", ("guest",), scrypt_n=1 << 10)
    return tmp_path / "home", cp


@pytest.fixture
def blob(tmp_path):
    p = tmp_path / "in.bin"
    p.write_bytes(b"plaintext " * 100)
    return p


def test_archive_is_gated(home, blob, tmp_path, monkeypatch):
    h, cp = home
    writer = guard.SecureContext(h, cp.login("w", "writer-pw-test", "s2"), "s2")
    assert sdk.archive([blob], tmp_path / "w.vnx", secure=writer).status == "SUCCESS"
    guest = guard.SecureContext(h, cp.login("g", "guest-pw-test", "s1"), "s1")
    with pytest.raises(AccessDenied):                  # (a denial may also tighten the system state)
        sdk.archive([blob], tmp_path / "g.vnx", secure=guest)
    monkeypatch.setenv("VNX_SECURE_REQUIRE", "1")
    with pytest.raises(AccessDenied):
        sdk.archive([blob], tmp_path / "r.vnx")
    r = CliRunner().invoke(app, ["archive", str(blob), str(tmp_path / "c.vnx")])
    assert r.exit_code != 0
    assert not (tmp_path / "g.vnx").exists() and not (tmp_path / "r.vnx").exists() and not (tmp_path / "c.vnx").exists()


def test_archive_default_unchanged(blob, tmp_path, monkeypatch):
    for k in ("VNX_SECURE_TOKEN", "VNX_SECURE_SESSION", "VNX_SECURE_REQUIRE"):
        monkeypatch.delenv(k, raising=False)
    assert sdk.archive([blob], tmp_path / "a.vnx").status == "SUCCESS"
