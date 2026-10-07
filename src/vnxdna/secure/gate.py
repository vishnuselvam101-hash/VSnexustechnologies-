"""Secure access to VNX-DNA archives (VNX-Secure; docs/VNX_SECURE_ARCHITECTURE.md §4).

The gate does not re-implement random access: it calls the existing archive layer (``vnxdna.archive``), which already
fails closed on corrupted bodies, wrong keys (AES-256-GCM tag), bad Merkle proofs and malformed tables. The gate adds,
in front of every call: authorization by the control plane, a quarantine check on the archive's bytes, and the
conversion of every archive-layer failure into an audited :class:`~.control.SecurityFailure` (never partial data).
``read_chunk`` additionally checks the chunk's Merkle inclusion proof against the manifest root before returning it.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..archive import container as ct
from ..archive import merkle
from ..archive import operations as ops
from ..core.errors import VNXConfigurationError, VNXFormatError, VNXIntegrityError, VNXKeyError, VNXResourceError, VNXUnsupportedVersionError
from .control import AccessDenied, ControlPlane, SecurityFailure
from .sandbox import SandboxError, run_limited

_CODES = ((VNXKeyError, "key_mismatch"), (VNXIntegrityError, "integrity"), (VNXUnsupportedVersionError, "malformed"),
          (VNXFormatError, "malformed"), (VNXResourceError, "malformed"), (VNXConfigurationError, "malformed"))


def file_sha256(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class SecureArchive:
    """One archive behind the control plane. ``resource`` defaults to ``archive:<file name>``."""

    def __init__(self, cp: ControlPlane, path: str | os.PathLike, *, key: bytes | None = None, resource: str | None = None,
                 sandbox: bool = False, mem_bytes: int = 1 << 30, cpu_seconds: int = 30):
        self.cp, self.path, self.key = cp, Path(path), key
        self.resource = resource or f"archive:{self.path.name}"
        # sandbox=True: list/verify/locate parse the archive in a resource-limited child process (sandbox.py)
        self.sandbox, self.limits = sandbox, {"mem_bytes": mem_bytes, "cpu_seconds": cpu_seconds}
        self._sha_key, self._sha_val = None, ""

    def _call(self, req: dict, operation: str, fn):
        claims = self.cp.authorize(req["token"], req["session"], req["nonce"], req["ts"], self.resource, operation)
        try:
            sha = self._sha()
        except OSError as e:
            ev = self.cp.observe_failure(claims, req["session"], self.resource, operation, "malformed", f"unreadable: {e}")
            raise SecurityFailure("malformed", "archive unreadable", ev)
        if sha in self.cp.cont.quarantined:
            ev, _ = self.cp.record(kind="request", result="deny", session_id=req["session"], principal=claims.sub, resource=self.resource,
                                   operation=operation, evidence={"code": "quarantined", "input_sha256": sha, "tid": claims.tid})
            raise AccessDenied("quarantined", "this archive's bytes are quarantined", ev.event_id)
        try:
            return fn()
        except Exception as e:
            code = next((c for t, c in _CODES if isinstance(e, t)), None)
            if isinstance(e, SandboxError):
                code = "malformed"
            if code is None:
                if isinstance(e, (ValueError, KeyError, IndexError, OverflowError, MemoryError, EOFError)):
                    code = "malformed"
                else:
                    raise
            ev = self.cp.observe_failure(claims, req["session"], self.resource, operation, code, f"{type(e).__name__}: {e}",
                                         input_sha256=sha, input_path=str(self.path))
            raise SecurityFailure(code, f"{type(e).__name__}: {e}", ev) from None

    def _sha(self) -> str:
        """SHA-256 of the archive file, cached by (device, inode, size, mtime_ns, ctime_ns): a large archive is hashed once,
        not on every request; any change to the file (or its replacement) recomputes it."""
        st = os.stat(self.path)
        key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if self._sha_key != key:
            self._sha_val, self._sha_key = file_sha256(self.path), key
        return self._sha_val

    def _open(self, require_key: bool = False):
        return ct.open_container(self.path, key=self.key, require_key=require_key, allow_unencrypted=self.key is None)

    def _run(self, fn, *args, **kw):
        if self.sandbox:
            return run_limited(fn, *args, **self.limits, **kw)
        return fn(*args, **kw)

    def list(self, req: dict) -> list:
        return self._call(req, "list", lambda: self._run(ops.list_container, self.path, key=self.key, allow_unencrypted=self.key is None))

    def verify(self, req: dict) -> dict:
        return self._call(req, "verify", lambda: self._run(ops.verify_container, self.path, key=self.key, allow_unencrypted=self.key is None))

    def locate(self, req: dict, name: str) -> dict:
        return self._call(req, "locate", lambda: self._run(ops.locate, self.path, name, key=self.key, allow_unencrypted=self.key is None))

    def read_file(self, req: dict, name: str) -> bytes:
        def go():
            c = self._open(require_key=True)
            rec = c.file(name)
            data = b"".join(ops.iter_file(c, rec))
            if hashlib.sha256(data).digest() != bytes(rec.sha256):
                raise VNXIntegrityError(f"{name}: file hash mismatch")
            return data
        return self._call(req, "read", go)

    def read_chunk(self, req: dict, index: int) -> bytes:
        def go():
            c = self._open(require_key=True)
            leaves = ct.leaf_hashes(c.chunk_table_bytes)
            n = len(leaves)
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < n:
                raise VNXFormatError(f"chunk index {index!r} out of range")
            proof = merkle.inclusion_proof(leaves, index)
            if not merkle.verify_inclusion(leaves[index], index, n, proof, bytes.fromhex(c.manifest["integrity"]["merkle_root"])):
                raise VNXIntegrityError(f"chunk {index}: Merkle inclusion proof does not verify")
            return ops.read_chunk(c, index)
        return self._call(req, "read", go)
