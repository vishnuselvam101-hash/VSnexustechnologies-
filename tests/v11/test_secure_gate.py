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


# ---------------------------------------------------------------- security-review fixes (bypass tests)
@pytest.fixture
def arts(tmp_path, blob):
    """V1/V2 containers and read files built with the gate off."""
    d = tmp_path / "arts"
    d.mkdir()
    v1api.store(blob, d / "a1.vxdna")
    v1api.encode(d / "a1.vxdna", d / "a1.fa")
    v2api.store(blob, d / "a2.vxdna", workers=1)
    v2api.encode(d / "a2.vxdna", d / "a2.fa", workers=1)
    return d


def _env(monkeypatch, c):
    monkeypatch.setenv("VNX_SECURE_HOME", str(c.home)); monkeypatch.setenv("VNX_SECURE_TOKEN", c.token)
    monkeypatch.setenv("VNX_SECURE_SESSION", c.session)


def _plaintext_calls(d, o):
    kw = dict(options=v2api.DecodeOptionsV2(), workers=1, overwrite=False, temp_dir=None)
    return {
        "v2.recover": lambda: v2api.recover(d / "a2.fa", o / "1", workers=1),
        "v2._recover_v2": lambda: v2api._recover_v2(d / "a2.fa", o / "2", key=None, **kw),
        "v2.restore(reads)": lambda: v2api.restore(d / "a2.fa", o / "3", workers=1),
        "v2.extract(container)": lambda: v2api.extract(d / "a2.vxdna", o / "4", offset=0, length=8),
        "v2.extract(reads)": lambda: v2api.extract(d / "a2.fa", o / "5", offset=0, length=8, workers=1),
        "v2._extract_reads": lambda: v2api._extract_reads(d / "a2.fa", o / "6", offset=0, length=8, chunk=None, key=None,
                                                          dna_index=None, **kw),
        "v2.archive.extract_range": lambda: v2arc.extract_range(d / "a2.vxdna", o / "7", offset=0, length=8),
        "v2.archive.restore_file": lambda: v2arc.restore_file(d / "a2.vxdna", o / "8", workers=1),
        "v1.extract(container)": lambda: v1api.extract(d / "a1.vxdna", o / "9", chunk=0),
        "v1.extract(reads)": lambda: v1api.extract(d / "a1.fa", o / "10", start=0, end=8),
        "v1.recover": lambda: v1api.recover(d / "a1.fa", o / "11"),
    }


def test_every_plaintext_path_is_gated(home, arts, tmp_path, monkeypatch):
    h, cp = home
    _env(monkeypatch, ctx(h, cp, "g", "guest-pw"))  # guest: no read/extract rights
    o = tmp_path / "out"
    o.mkdir()
    for name, call in _plaintext_calls(arts, o).items():
        with pytest.raises(AccessDenied):
            call()
            pytest.fail(f"{name} returned plaintext without the gate")
    assert list(o.iterdir()) == []


def test_every_plaintext_path_allowed_for_reader(home, arts, tmp_path, monkeypatch, blob):
    h, cp = home
    _env(monkeypatch, ctx(h, cp, "w", SECRET))
    o = tmp_path / "out"
    o.mkdir()
    for name, call in _plaintext_calls(arts, o).items():
        assert call()["status"] == "SUCCESS", name
    assert (o / "1").read_bytes() == blob.read_bytes() == (o / "8").read_bytes()
    assert (o / "7").read_bytes() == blob.read_bytes()[:8]


@pytest.mark.parametrize("partial", [("VNX_SECURE_TOKEN",), ("VNX_SECURE_SESSION",), ("VNX_SECURE_HOME", "VNX_SECURE_TOKEN")])
def test_partial_env_config_denies(home, arts, tmp_path, monkeypatch, partial):
    h, cp = home
    c = ctx(h, cp, "w", SECRET)
    vals = {"VNX_SECURE_HOME": str(h), "VNX_SECURE_TOKEN": c.token, "VNX_SECURE_SESSION": c.session}
    for k in partial:
        monkeypatch.setenv(k, vals[k])
    for call in (lambda: v2arc.restore_file(arts / "a2.vxdna", tmp_path / "p1", workers=1),
                 lambda: v1api.encode(arts / "a1.vxdna", tmp_path / "p2.fa"),
                 lambda: sdk.list_entries(arts / "a2.vxdna")):
        with pytest.raises(AccessDenied) as ei:
            call()
        assert ei.value.code == "partial_context" and c.token not in str(ei.value)
    assert not (tmp_path / "p1").exists() and not (tmp_path / "p2.fa").exists()


def test_home_alone_does_not_turn_the_gate_on(arts, tmp_path, monkeypatch, blob):
    monkeypatch.setenv("VNX_SECURE_HOME", str(tmp_path / "nowhere"))  # default home of the admin CLI only
    assert v2arc.restore_file(arts / "a2.vxdna", tmp_path / "r", workers=1)["status"] == "SUCCESS"


def _lock(h, resource):
    """Lock a resource from *another* control-plane instance (as the admin CLI would), persisted to the home."""
    other = ControlPlane(h)
    other.cont.lock_resource(resource)
    other.save()


def test_lock_follows_archive_id_through_rename_and_is_reread(home, arts, tmp_path, monkeypatch):
    import shutil
    h, cp = home
    _env(monkeypatch, ctx(h, cp, "w", SECRET))
    v2arc.extract_range(arts / "a2.vxdna", tmp_path / "warm", offset=0, length=4)   # the plane is now cached
    aid = guard.resource_of(arts / "a2.vxdna")
    assert aid.startswith("archive:") and not aid.startswith("archive:path:") and "a2" not in aid
    _lock(h, aid)
    shutil.copy(arts / "a2.vxdna", tmp_path / "renamed.bin")
    shutil.copy(arts / "a2.fa", tmp_path / "renamed.fa")
    for call in (lambda: v2arc.extract_range(tmp_path / "renamed.bin", tmp_path / "x1", offset=0, length=4),
                 lambda: v2api.restore(arts / "a2.vxdna", tmp_path / "x2", workers=1),
                 lambda: v2api.recover(tmp_path / "renamed.fa", tmp_path / "x3", workers=1)):    # reads: id known after decode
        with pytest.raises(AccessDenied) as ei:
            call()
        assert ei.value.code == "resource_locked"
    assert not any((tmp_path / n).exists() for n in ("x1", "x2", "x3"))


def test_legacy_name_lock_and_revocation_are_reread(home, arts, tmp_path, monkeypatch):
    h, cp = home
    c = ctx(h, cp, "w", SECRET)
    _env(monkeypatch, c)
    v2arc.extract_range(arts / "a2.vxdna", tmp_path / "warm", offset=0, length=4)
    _lock(h, "archive:a2.vxdna")                       # name-keyed lock (ArchiveGate / older homes) is still honoured
    with pytest.raises(AccessDenied) as ei:
        v2arc.extract_range(arts / "a2.vxdna", tmp_path / "y1", offset=0, length=4)
    assert ei.value.code == "resource_locked"
    other = ControlPlane(h)
    other.tokens.revoke_session(c.session)
    other.save()
    with pytest.raises(AccessDenied):
        v1api.extract(arts / "a1.vxdna", tmp_path / "y2", chunk=0)
    assert not (tmp_path / "y1").exists() and not (tmp_path / "y2").exists()


def test_sdk_metadata_reads_are_gated(home, tmp_path, blob):
    from vnxdna.archive.operations import ArchiveOptions, build_archive
    h, cp = home
    a = tmp_path / "a.vnx"
    build_archive([blob], a, ArchiveOptions())
    bad, ok = ctx(h, cp, "g", "guest-pw", "s2"), ctx(h, cp, "w", SECRET)
    calls = {"inspect": lambda s: sdk.inspect(a, secure=s, allow_unencrypted=True),
             "verify": lambda s: sdk.verify(a, secure=s, allow_unencrypted=True),
             "list": lambda s: sdk.list_entries(a, secure=s, allow_unencrypted=True),
             "locate": lambda s: sdk.locate(a, "in.bin", secure=s, allow_unencrypted=True)}
    for name, call in calls.items():
        with pytest.raises(AccessDenied):
            call(bad)
        assert call(ok).status in ("SUCCESS", "VERIFIED"), name
    assert guard.resource_of(a).startswith("archive:") and "path:" not in guard.resource_of(a)


def test_manifest_stamp_is_not_the_package_version():
    import vnxdna
    from vnxdna.core.version import MANIFEST_ENCODER_VERSION
    from vnxdna.v4 import container as ct
    assert ct.__version__ == vnxdna.__version__ and ct.MANIFEST_ENCODER_VERSION == MANIFEST_ENCODER_VERSION == "6.0.0.dev0"
