"""`vnx-dna cloud` client and CLI, against an in-process fake of the Cloud API (no network)."""
from __future__ import annotations

import io
import json
import stat
import urllib.error
import urllib.request
import zipfile

import pytest
from typer.testing import CliRunner

from vnxdna import cloud
from vnxdna.cli import app
from vnxdna.cloud import CloudClient, CloudUnavailableError
from vnxdna.core.taxonomy import AuthenticationError, IntegrityError, InvalidInputError

KEY = "vnx_sk_test"


class FakeAPI:
    """Records requests and answers like the service: jobs succeed at once, decode returns the uploaded files."""

    def __init__(self):
        self.requests: list[urllib.request.Request] = []
        self.uploads: dict[str, bytes] = {}
        self.fail_decode = False
        self.n = 0

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if req.get_header("Authorization") != f"Bearer {KEY}":
            return self._error(req, 401, "missing or invalid API key")
        path, method = req.full_url.split("example.test", 1)[1], req.get_method()
        if path == "/v1/me":
            return self._ok({"name": "Gamma Labs", "plan": "trial", "trial_ends": "2026-10-18T00:00:00Z",
                             "feedback_given": False, "usage": {"jobs_last_24h": 1, "month_input_bytes": 10},
                             "limits": {"daily_jobs": 100, "monthly_input_bytes": 1 << 30}})
        if path.startswith("/v1/jobs/") and method == "POST":
            kind = path.rsplit("/", 1)[1]
            if kind == "encode":
                self.uploads = _parse_files(req)
            self.n += 1
            return self._ok({"id": f"j_{kind}{self.n}", "kind": kind, "status": "queued"})
        if path.startswith("/v1/jobs/j_") and path.endswith("/files/recovered.zip"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for name, data in self.uploads.items():
                    z.writestr(name, data)
            return io.BytesIO(buf.getvalue())
        if path.startswith("/v1/jobs/j_"):
            jid = path.split("/")[3]
            if "decode" in jid and self.fail_decode:
                return self._ok({"id": jid, "kind": "decode", "status": "failed", "error": "insufficient redundancy"})
            return self._ok({"id": jid, "kind": jid[2:8], "status": "succeeded", "files": []})
        if path == "/v1/feedback":
            body = json.loads(req.data)
            assert body["rating"] in range(1, 6)
            return self._ok({"id": "f_1", "message": "thank you"})
        return self._error(req, 404, "not found")

    @staticmethod
    def _ok(obj):
        return io.BytesIO(json.dumps(obj).encode())

    @staticmethod
    def _error(req, code, message):
        raise urllib.error.HTTPError(req.full_url, code, "err", {},
                                     io.BytesIO(json.dumps({"error": {"status": code, "message": message}}).encode()))


def _parse_files(req) -> dict[str, bytes]:
    boundary = req.get_header("Content-type").split("boundary=")[1].encode()
    out = {}
    for part in req.data.split(b"--" + boundary):
        head, _, body = part.partition(b"\r\n\r\n")
        if b'filename="' in head:
            out[head.split(b'filename="')[1].split(b'"')[0].decode()] = body[:-2]
    return out


@pytest.fixture()
def api(monkeypatch, tmp_path):
    fake = FakeAPI()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("VNXDNA_CLOUD_URL", "https://example.test")
    monkeypatch.delenv("VNXDNA_CLOUD_KEY", raising=False)
    return fake


def test_no_key_is_an_authentication_error(api):
    with pytest.raises(AuthenticationError, match="cloud login"):
        CloudClient()


def test_http_errors_map_to_exit_codes(api):
    assert isinstance(cloud._api_error(401, b'{"error":{"message":"x"}}'), AuthenticationError)
    assert isinstance(cloud._api_error(403, b"{}"), AuthenticationError)
    assert isinstance(cloud._api_error(422, b'{"error":{"message":"bad"}}'), InvalidInputError)
    assert isinstance(cloud._api_error(429, b""), CloudUnavailableError)
    assert isinstance(cloud._api_error(502, b"<html>"), CloudUnavailableError)
    with pytest.raises(AuthenticationError):
        CloudClient(key="vnx_sk_wrong").me()


def test_unreachable_service(monkeypatch, tmp_path):
    def down(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    with pytest.raises(CloudUnavailableError, match="connection refused"):
        CloudClient(key=KEY, url="https://example.test", opener=down).me()


def test_job_ids_are_validated_before_any_request(api):
    c = CloudClient(key=KEY)
    for bad in ("../v1/me", "j_../../x", "x" * 50, "j_"):
        with pytest.raises(InvalidInputError):
            c.job(bad)
    assert api.requests == []


def test_wait_raises_on_a_failed_job(api):
    api.fail_decode = True
    c = CloudClient(key=KEY)
    with pytest.raises(IntegrityError, match="insufficient redundancy"):
        c.wait(c.decode("standard", from_job="j_sim1")["id"], sleep=lambda s: None)


def test_save_key_is_private_and_checked(api):
    with pytest.raises(InvalidInputError):
        cloud.save_key("sk-not-ours")
    path = cloud.save_key(KEY)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert CloudClient().key == KEY


def test_cli_login_me_roundtrip_feedback(api, tmp_path, monkeypatch):
    run = CliRunner()
    r = run.invoke(app, ["cloud", "login", "--api-key", KEY])
    assert r.exit_code == 0, r.output
    assert "Gamma Labs" in r.output and "trial ends" in r.output
    r = run.invoke(app, ["cloud", "me"])
    assert r.exit_code == 0 and "plan trial" in r.output and "not sent yet" in r.output
    (tmp_path / "a.bin").write_bytes(b"\x00\x01payload")
    (tmp_path / "b.txt").write_text("hello dna")
    monkeypatch.chdir(tmp_path)
    r = run.invoke(app, ["cloud", "roundtrip", "a.bin", "b.txt", "-o", "out"])
    assert r.exit_code == 0, r.output
    assert "2 file(s) byte-identical" in r.output and "SIMULATED" in r.output
    assert (tmp_path / "out" / "a.bin").read_bytes() == b"\x00\x01payload"
    r = run.invoke(app, ["cloud", "feedback", "--rating", "4", "--would-pay", "maybe", "--use-case", "lab archives",
                         "--comments", ""])
    assert r.exit_code == 0 and "thank you" in r.output


def test_cli_roundtrip_detects_a_wrong_recovery(api, tmp_path, monkeypatch):
    api_upload = api.__call__

    def tamper(req, timeout=None):
        resp = api_upload(req, timeout)
        if req.full_url.endswith("/v1/jobs/encode"):
            api.uploads = {name: data + b"!" for name, data in api.uploads.items()}
        return resp
    monkeypatch.setattr(urllib.request, "urlopen", tamper)
    (tmp_path / "a.bin").write_bytes(b"original")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["cloud", "roundtrip", "a.bin"], env={"VNXDNA_CLOUD_KEY": KEY})
    assert r.exit_code == 1 and "differ from the originals" in r.output
    assert not (tmp_path / "recovered" / "a.bin").exists()


def test_cli_bad_key_exits_4(api):
    r = CliRunner().invoke(app, ["cloud", "me"], env={"VNXDNA_CLOUD_KEY": "vnx_sk_wrong"})
    assert r.exit_code == 4 and "AUTHENTICATION_FAILED" in r.output
