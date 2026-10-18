"""``vnx.codec-ipc/1`` worker process (docs/V14_CONTRACTS.md §6, docs/V14_SECURITY_MODEL.md §3).

    python -m vnxdna.device.worker --key-file KEY --staging DIR

Reads one request per line on stdin and writes one response per line on stdout. Phase 4 serves ``hello``,
``key_check``, ``seal_extent`` and ``open_extent`` (sealing with :mod:`vnxdna.device.seal`); the codec operations
``encode_extent``/``decode_extent`` are served by the key-less codec worker :mod:`vnxdna.device.dnasim` (Phase 7) and
refused here (PROTOCOL).

* the key file is opened once without following symlinks; it must be a regular file owned by this user, not
  accessible by group or others, and hold 64 hex or 44 base64 characters (a generated key, never a passphrase);
  the key never leaves this process;
* the staging directory must be a real directory owned by this user with no group or other access (checked at
  start), so no other user can plant, swap or read files in it;
* files are only opened inside the staging directory, by protocol-validated names (never paths), without following
  symlinks; inputs are checked against their announced SHA-256 before use; outputs are created exclusively with
  mode 0600 (a partial output is removed);
* every failure is a typed error response (unexpected exceptions are INTERNAL); the worker exits only at end of
  input.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import Any

import base64
import binascii

from . import protocol as pr
from . import seal as sl
from .contracts import DeviceError

PROTO = "vnx.codec-ipc"
SERVER = "vnxdna.device.worker"
MAX_INPUT = 256 << 20


def _private(st: os.stat_result, what: str) -> None:
    if st.st_uid != os.getuid():
        raise DeviceError("UNAUTHORIZED", f"{what} is owned by uid {st.st_uid}, not by this user")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise DeviceError("UNAUTHORIZED", f"{what} is accessible by group or others (mode {stat.S_IMODE(st.st_mode):o})")


def load_private_key(path: str) -> bytes:
    """The 32-byte device key from a private key file (64 hex or 44 base64 characters, as generated)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        raise DeviceError("NOT_FOUND", f"key file {path}: {e.strerror}") from None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise DeviceError("INVALID_ARGUMENT", f"key file {path} is not a regular file")
        _private(st, f"key file {path}")
        raw = f.read(1025)
    text = raw.strip()
    key = None
    if len(text) == 64:
        try:
            key = bytes.fromhex(text.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            key = None
    elif len(text) == 44:
        try:
            key = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            key = None
    if key is None or len(key) != 32:
        raise DeviceError("INVALID_ARGUMENT", f"key file {path} must hold a generated key: 64 hex or 44 base64 characters")
    return key


def check_staging(path: Path) -> None:
    try:
        st = os.lstat(path)
    except OSError as e:
        raise DeviceError("NOT_FOUND", f"staging directory {path}: {e.strerror}") from None
    if not stat.S_ISDIR(st.st_mode):
        raise DeviceError("INVALID_ARGUMENT", f"staging {path} is not a directory (symlinks are refused)")
    _private(st, f"staging directory {path}")


class Worker:
    def __init__(self, key: bytes, staging: Path) -> None:
        self.key = key
        self.staging = staging

    def _read_input(self, name: str, sha: str) -> bytes:
        p = self.staging / name
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as e:
            raise DeviceError("NOT_FOUND", f"staging input {name}: {e.strerror}") from None
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise DeviceError("INVALID_ARGUMENT", f"staging input {name} is not a regular file")
            data = f.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            raise DeviceError("INVALID_ARGUMENT", f"staging input {name} exceeds {MAX_INPUT} bytes")
        if hashlib.sha256(data).hexdigest() != sha:
            raise DeviceError("CORRUPTION", f"staging input {name} does not match its announced SHA-256")
        return data

    def _write_output(self, name: str, data: bytes) -> dict[str, Any]:
        p = self.staging / name
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError as e:
            raise DeviceError("INVALID_ARGUMENT", f"staging output {name}: {e.strerror}") from None
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        except OSError as e:
            p.unlink(missing_ok=True)
            raise DeviceError("BACKEND_UNAVAILABLE", f"staging output {name}: {e.strerror}") from None
        return {"output_sha256": hashlib.sha256(data).hexdigest(), "output_bytes": len(data)}

    def run(self, op: str, a: dict[str, Any]) -> dict[str, Any]:
        if op == "hello":
            if pr.VERSION not in a["versions"]:
                raise DeviceError("VERSION_MISMATCH", f"{PROTO} versions {a['versions']}; supported [{pr.VERSION}]")
            return {"server": SERVER, "version": pr.VERSION}
        if op == "key_check":
            return {"key_check": sl.key_check(self.key, bytes.fromhex(a["device_id"])).hex()}
        if op in ("seal_extent", "open_extent"):
            data = self._read_input(a["input"], a["input_sha256"])
            device = bytes.fromhex(a["device_id"])
            fn = sl.seal if op == "seal_extent" else sl.open_sealed
            out = fn(self.key, device, a["extent"], a["generation"], data)
            if not out:
                raise DeviceError("CORRUPTION", "an empty extent cannot be reported (output_bytes >= 1)")
            return {"job": a["job"], **self._write_output(a["output"], out)}
        raise DeviceError("PROTOCOL", f"operation {op} is not served by this worker (codec operations: vnxdna.device.dnasim)")

    def handle(self, line: str) -> str:
        rid = 0
        try:
            v = pr.parse(line)
            req = pr.validate_request(PROTO, v)
            rid = v["id"]
            result = self.run(v["op"], v["args"])
            del req
            return pr.canonical(pr.ok_response(PROTO, rid, v["op"], result))
        except DeviceError as e:
            return pr.canonical(pr.error_response(PROTO, rid, e))
        except Exception as e:  # never die on one request: a typed INTERNAL error, the worker keeps serving
            return pr.canonical(pr.error_response(PROTO, rid, DeviceError("INTERNAL", f"{type(e).__name__}: {e}")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vnxdna.device.worker")
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--staging", required=True)
    args = ap.parse_args(argv)
    try:
        key = load_private_key(args.key_file)
    except DeviceError as e:
        print(f"vnxdna.device.worker: {e.device_message}", file=sys.stderr)
        return 2
    staging = Path(args.staging)
    try:
        check_staging(staging)
    except DeviceError as e:
        print(f"vnxdna.device.worker: {e.device_message}", file=sys.stderr)
        return 2
    w = Worker(key, staging)
    for line in sys.stdin:
        sys.stdout.write(w.handle(line.rstrip("\n")) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
