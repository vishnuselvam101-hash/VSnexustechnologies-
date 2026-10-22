"""Offline security monitor: integrity checks, access auditing, anomaly detection, and lockdown.

VNX-Secure AP-16: An always-on monitor that works with no network. Integrity checks against signed manifests,
audit of every access, anomaly rules, a tamper-evident hash-chained log, and a configurable lock-down.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .audit import AuditLog, AuditIntegrityError
from .events import SecurityEvent, canonical, SYSTEM_CLOCK


class Signer(Protocol):
    """Signature interface placeholder until AP-4; real signatures can be dropped in later."""

    def sign(self, data: bytes) -> bytes: ...

    def verify(self, data: bytes, sig: bytes) -> bool: ...


class HmacSigner:
    """HMAC-SHA256 signer (placeholder until AP-4). Key must be >= 32 bytes; uses constant-time compare."""

    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ValueError("HmacSigner key must be at least 32 bytes")
        self._key = key

    def sign(self, data: bytes) -> bytes:
        return hmac.new(self._key, data, hashlib.sha256).digest()

    def verify(self, data: bytes, sig: bytes) -> bool:
        expected = self.sign(data)
        return hmac.compare_digest(expected, sig)


@dataclass(frozen=True)
class Manifest:
    """Canonical manifest of a directory tree: relative path -> sha256 hex."""

    root: str
    files: dict[str, str]
    created: float

    def canonical(self) -> bytes:
        """Canonical JSON bytes (sorted keys, no whitespace)."""
        return canonical({"root": self.root, "files": self.files, "created": self.created})


def build_manifest(root: str | os.PathLike, signer: Signer, *, now: float) -> tuple[Manifest, bytes]:
    """Build a manifest of all files under root and sign it."""
    root_path = Path(root)
    files: dict[str, str] = {}
    for p in sorted(root_path.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root_path).as_posix()
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            files[rel] = h
    manifest = Manifest(root=root_path.as_posix(), files=files, created=now)
    sig = signer.sign(manifest.canonical())
    return manifest, sig


@dataclass(frozen=True)
class Finding:
    """A monitor finding; appended to the audit log as a SecurityEvent."""

    kind: str  # "modified" | "missing" | "added" | "bad-signature" | "auth-failures" | "read-burst" | "audit-broken"
    path: str | None
    detail: dict


@dataclass(frozen=True)
class Policy:
    """Monitor policy controlling lockdown triggers and anomaly thresholds."""

    lockdown_on: frozenset[str] = frozenset({"bad-signature", "audit-broken", "modified", "missing", "added"})
    max_auth_failures: int = 5
    window_s: float = 60.0
    max_read_bytes: int = 1 << 30


class Monitor:
    """Offline security monitor with integrity checks, access auditing, and sticky lockdown."""

    def __init__(
        self,
        root: str | os.PathLike,
        manifest: Manifest,
        signature: bytes,
        signer: Signer,
        audit: AuditLog,
        policy: Policy | None = None,
        clock: Callable[[], float] = SYSTEM_CLOCK,
    ):
        self._root = Path(root)
        self._manifest = manifest
        self._signature = signature
        self._signer = signer
        self._audit = audit
        self._policy = policy or Policy()
        self._clock = clock
        self._locked = False

        # Sliding windows for anomaly detection
        self._auth_failures: list[tuple[float, str]] = []  # (timestamp, actor)
        self._read_bytes: list[tuple[float, int]] = []  # (timestamp, nbytes)

    @property
    def locked(self) -> bool:
        return self._locked

    def _append_finding(self, finding: Finding) -> None:
        """Log a finding as a SecurityEvent to the audit chain."""
        event = SecurityEvent(
            event_id=f"finding-{finding.kind}-{int(self._clock() * 1000)}",
            timestamp=self._clock(),
            session_id="monitor",
            principal="monitor",
            resource=finding.path or "system",
            operation="integrity_check",
            result="observed",
            severity="HIGH" if finding.kind in self._policy.lockdown_on else "MEDIUM",
            detector="monitor",
            evidence={"kind": finding.kind, "detail": finding.detail},
            integrity_state="UNKNOWN",
            kind="finding",
        )
        self._audit.append(event)

        # Trigger lockdown if policy says so
        if finding.kind in self._policy.lockdown_on:
            self._locked = True

    def _prune_window(self, window: list[tuple[float, ...]], now: float, window_s: float) -> None:
        """Remove entries older than window_s."""
        cutoff = now - window_s
        while window and window[0][0] < cutoff:
            window.pop(0)

    def check_integrity(self) -> list[Finding]:
        """Verify all files against the manifest; detect modified, missing, added, and bad signature."""
        findings: list[Finding] = []

        # Verify manifest signature
        if not self._signer.verify(self._manifest.canonical(), self._signature):
            finding = Finding("bad-signature", None, {"reason": "manifest signature verification failed"})
            findings.append(finding)
            self._append_finding(finding)
            return findings  # If signature is bad, don't trust the manifest

        # Check for missing or modified files
        for rel_path, expected_hash in self._manifest.files.items():
            full_path = self._root / rel_path
            if not full_path.exists():
                finding = Finding("missing", rel_path, {"expected_hash": expected_hash})
                findings.append(finding)
                self._append_finding(finding)
            else:
                actual_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
                if actual_hash != expected_hash:
                    finding = Finding("modified", rel_path, {"expected_hash": expected_hash, "actual_hash": actual_hash})
                    findings.append(finding)
                    self._append_finding(finding)

        # Check for added files (files on disk not in manifest)
        for p in sorted(self._root.rglob("*")):
            if p.is_file():
                rel = p.relative_to(self._root).as_posix()
                if rel not in self._manifest.files:
                    finding = Finding("added", rel, {"hash": hashlib.sha256(p.read_bytes()).hexdigest()})
                    findings.append(finding)
                    self._append_finding(finding)

        return findings

    def record_access(self, actor: str, op: str, path: str, nbytes: int, ok: bool) -> list[Finding]:
        """Record an access attempt; enforce auth-failure and read-burst windows."""
        findings: list[Finding] = []
        now = self._clock()

        # Always audit the access
        event = SecurityEvent(
            event_id=f"access-{actor}-{op}-{int(now * 1000)}",
            timestamp=now,
            session_id=actor,
            principal=actor,
            resource=path,
            operation=op,
            result="allow" if ok else "deny",
            severity="INFO" if ok else "LOW",
            detector="monitor",
            evidence={"bytes": nbytes},
            integrity_state="UNKNOWN",
            kind="request",
        )
        self._audit.append(event)

        if not ok:
            # Track auth failures
            self._auth_failures.append((now, actor))
            self._prune_window(self._auth_failures, now, self._policy.window_s)
            failures_in_window = sum(1 for t, a in self._auth_failures if a == actor)
            if failures_in_window > self._policy.max_auth_failures:
                finding = Finding(
                    "auth-failures",
                    path,
                    {"actor": actor, "count": failures_in_window, "window_s": self._policy.window_s},
                )
                findings.append(finding)
                self._append_finding(finding)
        else:
            # Track read bytes for burst detection
            if op in ("read", "read_bytes", "export"):
                self._read_bytes.append((now, nbytes))
                self._prune_window(self._read_bytes, now, self._policy.window_s)
                total_bytes = sum(b for _, b in self._read_bytes)
                if total_bytes > self._policy.max_read_bytes:
                    finding = Finding(
                        "read-burst",
                        path,
                        {"total_bytes": total_bytes, "limit": self._policy.max_read_bytes, "window_s": self._policy.window_s},
                    )
                    findings.append(finding)
                    self._append_finding(finding)

        return findings

    def tick(self) -> list[Finding]:
        """One monitoring round: integrity check + audit chain verify + anomaly rules."""
        findings: list[Finding] = []

        # 1. Integrity check
        findings.extend(self.check_integrity())

        # 2. Audit chain verification
        try:
            verdict = self._audit.verify()
            if verdict.status != "VERIFIED":
                finding = Finding("audit-broken", None, {"status": verdict.status, "reason": verdict.reason})
                findings.append(finding)
                self._append_finding(finding)
        except AuditIntegrityError as e:
            finding = Finding("audit-broken", None, {"reason": str(e)})
            findings.append(finding)
            self._append_finding(finding)

        return findings

    def unlock(self, admin_token_ok: bool) -> None:
        """Unlock the monitor if admin token is valid."""
        if admin_token_ok:
            self._locked = False

    def lockdown_actions(self) -> list[str]:
        """Return the list of lockdown actions in effect."""
        if not self._locked:
            return []
        # Key wipe is only simulated in tests (no real key deletion)
        return ["read-only"]


def _demo() -> None:
    """Quick demo (not a test)."""
    import tempfile

    key = b"k" * 32
    audit_key = b"a" * 32

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "data"
        root.mkdir()
        (root / "a.txt").write_text("hello")
        (root / "b.txt").write_text("world")

        signer = HmacSigner(key)
        manifest, sig = build_manifest(root, signer, now=1_800_000_000.0)

        audit = AuditLog(audit_key, Path(td) / "audit.log")
        monitor = Monitor(root, manifest, sig, signer, audit, clock=lambda: 1_800_000_000.0)

        print("Initial integrity:", monitor.check_integrity())
        print("Locked:", monitor.locked)

        # Tamper
        (root / "a.txt").write_text("tampered")
        findings = monitor.check_integrity()
        print("After tamper:", findings)
        print("Locked:", monitor.locked)


if __name__ == "__main__":
    _demo()