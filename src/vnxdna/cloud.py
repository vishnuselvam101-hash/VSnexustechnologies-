"""Client for the VNX-DNA Cloud API (https://api.vsnexustechnologies.com), standard library only.

Every channel result the service returns is SIMULATED: no DNA is synthesised or sequenced.
The API key comes from ``$VNXDNA_CLOUD_KEY`` or the file written by ``vnx-dna cloud login``
(``~/.config/vnxdna/cloud.json``, mode 600); ``$VNXDNA_CLOUD_URL`` overrides the service address.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .core.taxonomy import AuthenticationError, ConfigurationError, IntegrityError, InvalidInputError

DEFAULT_URL = "https://api.vsnexustechnologies.com"
KEY_PREFIX = "vnx_sk_"
_TIMEOUT_S = 120


class CloudUnavailableError(ConfigurationError):
    """The service could not be reached, is rate limiting, or answered with a server error."""

    category = "CLOUD_UNAVAILABLE"


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "vnxdna" / "cloud.json"


def save_key(key: str, url: Optional[str] = None) -> Path:
    key = key.strip()
    if not key.startswith(KEY_PREFIX):
        raise InvalidInputError(f"that is not a VNX-DNA Cloud API key (keys start with {KEY_PREFIX})")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"api_key": key, **({"url": url} if url else {})}, handle)
    os.chmod(path, 0o600)
    return path


def _saved() -> dict[str, Any]:
    try:
        return json.loads(config_path().read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ConfigurationError(f"cannot read {config_path()}: {error}") from None


class CloudClient:
    def __init__(self, key: Optional[str] = None, url: Optional[str] = None,
                 opener: Optional[Callable[..., Any]] = None):
        saved = _saved() if key is None or url is None else {}
        self.key = key or os.environ.get("VNXDNA_CLOUD_KEY") or saved.get("api_key")
        self.url = (url or os.environ.get("VNXDNA_CLOUD_URL") or saved.get("url") or DEFAULT_URL).rstrip("/")
        self._open = opener or urllib.request.urlopen
        if not self.key:
            raise AuthenticationError("no API key: run `vnx-dna cloud login` or set VNXDNA_CLOUD_KEY "
                                      "(request a free 7-day trial at vishnu@vsnexustechnologies.com)")

    # transport ---------------------------------------------------------------------------------------------------
    def _request(self, method: str, path: str, body: Optional[bytes] = None, ctype: Optional[str] = None) -> bytes:
        req = urllib.request.Request(self.url + path, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("User-Agent", "vnx-dna-cli")
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with self._open(req, timeout=_TIMEOUT_S) as resp:
                return resp.read()
        except urllib.error.HTTPError as error:
            raise _api_error(error.code, error.read()) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise CloudUnavailableError(f"cannot reach {self.url}: {reason}") from None

    def _json(self, method: str, path: str, **kw: Any) -> Any:
        return json.loads(self._request(method, path, **kw) or b"null")

    def _post_form(self, path: str, fields: dict[str, Any], files: dict[str, list[Path]]) -> Any:
        body, ctype = _multipart(fields, files)
        return self._json("POST", path, body=body, ctype=ctype)

    # API ---------------------------------------------------------------------------------------------------------
    def me(self) -> Any:
        return self._json("GET", "/v1/me")

    def options(self) -> Any:
        return self._json("GET", "/v1/options")

    def encode(self, files: list[Path], profile: str) -> Any:
        return self._post_form("/v1/jobs/encode", {"profile": profile}, {"files": files})

    def simulate(self, model: str, coverage: float, seed: int, from_job: Optional[str] = None,
                 strands: Optional[Path] = None) -> Any:
        fields = {"model": model, "coverage": coverage, "seed": seed, "from_job": from_job}
        return self._post_form("/v1/jobs/simulate", fields, {"strands": [strands] if strands else []})

    def decode(self, decoder: str, from_job: Optional[str] = None, reads: Optional[Path] = None) -> Any:
        return self._post_form("/v1/jobs/decode", {"decoder": decoder, "from_job": from_job},
                               {"reads": [reads] if reads else []})

    def jobs(self, limit: int = 50) -> Any:
        return self._json("GET", f"/v1/jobs?limit={int(limit)}")

    def job(self, job_id: str) -> Any:
        return self._json("GET", f"/v1/jobs/{_job_id(job_id)}")

    def delete(self, job_id: str) -> Any:
        return self._json("DELETE", f"/v1/jobs/{_job_id(job_id)}")

    def download(self, job_id: str, name: str) -> bytes:
        return self._request("GET", f"/v1/jobs/{_job_id(job_id)}/files/{name}")

    def feedback(self, rating: int, would_pay: str, use_case: str, comments: str) -> Any:
        body = json.dumps({"rating": rating, "would_pay": would_pay, "use_case": use_case,
                           "comments": comments}).encode()
        return self._json("POST", "/v1/feedback", body=body, ctype="application/json")

    def wait(self, job_id: str, timeout_s: float = 3600, poll_s: float = 2.0,
             sleep: Callable[[float], None] = time.sleep) -> Any:
        """Poll until the job finishes; a failed job raises :class:`IntegrityError` with the service's reason."""
        deadline = time.monotonic() + timeout_s
        while True:
            job = self.job(job_id)
            if job["status"] == "succeeded":
                return job
            if job["status"] in ("failed", "deleted"):
                reason = job.get("error") or (job.get("result") or {}).get("error") or job["status"]
                raise IntegrityError(f"job {job_id} {job['status']}: {reason}", details=job)
            if time.monotonic() > deadline:
                raise CloudUnavailableError(f"job {job_id} still {job['status']} after {timeout_s:.0f} s; "
                                            f"check later with `vnx-dna cloud job {job_id}`")
            sleep(poll_s)


def _job_id(job_id: str) -> str:
    if not (job_id.startswith("j_") and job_id[2:].isalnum() and len(job_id) <= 40):
        raise InvalidInputError(f"not a job id: {job_id!r} (job ids look like j_1a2b3c...)")
    return job_id


def _api_error(status: int, raw: bytes) -> Exception:
    try:
        message = json.loads(raw)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = raw.decode("utf-8", "replace")[:300] or f"HTTP {status}"
    if status in (401, 403):
        return AuthenticationError(message)
    if status == 429 or status >= 500:
        return CloudUnavailableError(f"{message} (HTTP {status})")
    return InvalidInputError(message)


def _multipart(fields: dict[str, Any], files: dict[str, list[Path]]) -> tuple[bytes, str]:
    boundary = "vnxdna-" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        if value is None:
            continue
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, paths in files.items():
        for path in paths:
            try:
                data = Path(path).read_bytes()
            except OSError as error:
                raise InvalidInputError(f"cannot read {path}: {error.strerror or error}") from None
            filename = Path(path).name.replace('"', "_")
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                         f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
