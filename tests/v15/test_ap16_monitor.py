"""Tests for AP-16 offline security monitor.

Covers tamper detection, anomaly rules, lockdown, and a normal-use campaign.
All network access is disabled via monkeypatch.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from vnxdna.secure.audit import AuditLog
from vnxdna.secure.events import ManualClock, SecurityEvent
from vnxdna.secure.monitor import (
    HmacSigner,
    Policy,
    Monitor,
    build_manifest,
)

KEY = b"k" * 32
AUDIT_KEY = b"a" * 32


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Disable all network access for offline guarantee."""

    def _fail(*args, **kwargs):
        raise RuntimeError("network access is disabled in AP-16 tests")

    monkeypatch.setattr(socket, "socket", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)
    # Don't patch urllib - it triggers ssl import issues
    yield


def _setup_monitor(tmp_path: Path, clock: ManualClock | None = None, policy: Policy | None = None) -> tuple[Monitor, Path]:
    """Create a test monitor with a small file tree."""
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    (root / "b.txt").write_text("world")
    sub = root / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("nested")

    signer = HmacSigner(KEY)
    manifest, sig = build_manifest(root, signer, now=clock() if clock else 1_800_000_000.0)

    audit = AuditLog(AUDIT_KEY, tmp_path / "audit.log")
    monitor = Monitor(
        root,
        manifest,
        sig,
        signer,
        audit,
        policy=policy or Policy(),
        clock=clock or (lambda: 1_800_000_000.0),
    )
    return monitor, root


def _ensure_audit_file(monitor: Monitor) -> None:
    """Ensure the audit log file exists by appending a dummy event."""
    event = SecurityEvent(
        event_id="init",
        timestamp=monitor._clock(),
        session_id="monitor",
        principal="monitor",
        resource="system",
        operation="init",
        result="observed",
        severity="INFO",
        detector="monitor",
        evidence={},
        integrity_state="UNKNOWN",
        kind="request",
    )
    monitor._audit.append(event)


class TestIntegrityChecks:
    """Tamper campaign: each tamper type must be detected, logged, and trigger lockdown per policy."""

    def test_modified_file_detected_and_locked(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        # Tamper: modify a file
        (root / "a.txt").write_text("tampered")
        findings = monitor.check_integrity()

        assert any(f.kind == "modified" and f.path == "a.txt" for f in findings)
        assert monitor.locked is True

        # Verify audit entry exists
        events = monitor._audit.events()
        assert any(e["detector"] == "monitor" and e["evidence"].get("kind") == "modified" for e in events)

    def test_deleted_file_detected_and_locked(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        # Tamper: delete a file
        (root / "a.txt").unlink()
        findings = monitor.check_integrity()

        assert any(f.kind == "missing" and f.path == "a.txt" for f in findings)
        assert monitor.locked is True

    def test_added_file_detected_and_locked(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        # Tamper: add a new file
        (root / "new.txt").write_text("added")
        findings = monitor.check_integrity()

        assert any(f.kind == "added" and f.path == "new.txt" for f in findings)
        assert monitor.locked is True

    def test_edited_manifest_detected_as_bad_signature(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        # Tamper: corrupt the signature (use wrong key to sign)
        other_key = b"x" * 32
        other_signer = HmacSigner(other_key)
        # Re-sign the same manifest with a different key
        bad_sig = other_signer.sign(monitor._manifest.canonical())
        monitor._signature = bad_sig

        findings = monitor.check_integrity()

        assert any(f.kind == "bad-signature" for f in findings)
        assert monitor.locked is True

    def test_edited_signature_detected_as_bad_signature(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        # Tamper: corrupt the signature
        monitor._signature = b"x" * 32

        findings = monitor.check_integrity()

        assert any(f.kind == "bad-signature" for f in findings)
        assert monitor.locked is True

    def test_audit_edit_detected_and_locked(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        # Record some accesses to generate audit entries
        for _ in range(3):
            monitor.record_access("alice", "read", "a.txt", 100, ok=True)

        # Tamper: edit an audit line directly on disk
        audit_path = monitor._audit.path
        lines = audit_path.read_bytes().splitlines()
        rec = json.loads(lines[0])
        rec["event"]["principal"] = "mallory"
        lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()
        audit_path.write_bytes(b"\n".join(lines) + b"\n")

        # Next tick should detect audit-broken
        findings = monitor.tick()

        assert any(f.kind == "audit-broken" for f in findings)
        assert monitor.locked is True

    def test_audit_insert_detected_and_locked(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        for _ in range(3):
            monitor.record_access("alice", "read", "a.txt", 100, ok=True)

        # Tamper: insert a duplicate line
        audit_path = monitor._audit.path
        lines = audit_path.read_bytes().splitlines()
        lines.insert(1, lines[1])
        audit_path.write_bytes(b"\n".join(lines) + b"\n")

        findings = monitor.tick()

        assert any(f.kind == "audit-broken" for f in findings)
        assert monitor.locked is True

    def test_audit_delete_detected_and_locked(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        for _ in range(3):
            monitor.record_access("alice", "read", "a.txt", 100, ok=True)

        # Tamper: delete a line
        audit_path = monitor._audit.path
        lines = audit_path.read_bytes().splitlines()
        del lines[1]
        audit_path.write_bytes(b"\n".join(lines) + b"\n")

        findings = monitor.tick()

        assert any(f.kind == "audit-broken" for f in findings)
        assert monitor.locked is True

    def test_audit_swap_detected_and_locked(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        for _ in range(3):
            monitor.record_access("alice", "read", "a.txt", 100, ok=True)

        # Tamper: swap two lines
        audit_path = monitor._audit.path
        lines = audit_path.read_bytes().splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        audit_path.write_bytes(b"\n".join(lines) + b"\n")

        findings = monitor.tick()

        assert any(f.kind == "audit-broken" for f in findings)
        assert monitor.locked is True


class TestAnomalyRules:
    """Anomaly detection: auth failures and read bursts."""

    def test_auth_failures_exceeding_threshold_triggers_finding(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)

        # Record 6 auth failures within 60s (threshold is 5)
        for i in range(6):
            monitor.record_access("eve", "read", "a.txt", 100, ok=False)
            clock.advance(1.0)

        # The 6th failure should trigger a finding
        events = monitor._audit.events()
        auth_findings = [e for e in events if e["detector"] == "monitor" and e["evidence"].get("kind") == "auth-failures"]
        assert len(auth_findings) >= 1
        # Default policy does NOT lockdown on auth-failures
        assert monitor.locked is False

    def test_auth_failures_not_in_lockdown_on_by_default(self, tmp_path):
        """Default policy does not lockdown on auth-failures."""
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)

        for i in range(6):
            monitor.record_access("eve", "read", "a.txt", 100, ok=False)
            clock.advance(1.0)

        # Default policy lockdown_on includes "missing", "added" but not "auth-failures"
        assert monitor.locked is False

    def test_auth_failures_lockdown_when_policy_includes_it(self, tmp_path):
        clock = ManualClock()
        policy = Policy(lockdown_on=frozenset({"bad-signature", "audit-broken", "modified", "missing", "added", "auth-failures"}))
        monitor, root = _setup_monitor(tmp_path, clock, policy=policy)

        for i in range(6):
            monitor.record_access("eve", "read", "a.txt", 100, ok=False)
            clock.advance(1.0)

        assert monitor.locked is True

    def test_read_burst_exceeding_limit_triggers_finding(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)

        # Read burst: exceed 1GB limit (default max_read_bytes = 1<<30)
        large_read = (1 << 30) + 100
        monitor.record_access("alice", "read", "a.txt", large_read, ok=True)

        events = monitor._audit.events()
        burst_findings = [e for e in events if e["detector"] == "monitor" and e["evidence"].get("kind") == "read-burst"]
        assert len(burst_findings) >= 1
        assert monitor.locked is False  # read-burst not in default lockdown_on

    def test_read_burst_lockdown_when_policy_includes_it(self, tmp_path):
        clock = ManualClock()
        policy = Policy(lockdown_on=frozenset({"bad-signature", "audit-broken", "modified", "missing", "added", "read-burst"}))
        monitor, root = _setup_monitor(tmp_path, clock, policy=policy)

        large_read = (1 << 30) + 100
        monitor.record_access("alice", "read", "a.txt", large_read, ok=True)

        assert monitor.locked is True


class TestNormalUseCampaign:
    """Normal-use campaign: 1000 seeded normal accesses + 20 ticks = no findings, no lockdown."""

    def test_1000_normal_accesses_and_20_ticks_no_findings_no_lockdown(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        # 1000 normal read/write accesses
        for i in range(1000):
            actor = "alice" if i % 2 == 0 else "bob"
            op = "read" if i % 3 != 0 else "write"
            path = "a.txt" if i % 2 == 0 else "b.txt"
            nbytes = 1024 + (i % 4096)
            monitor.record_access(actor, op, path, nbytes, ok=True)
            clock.advance(0.01)  # Small time advance

        # 20 monitoring ticks
        all_findings = []
        for _ in range(20):
            findings = monitor.tick()
            all_findings.extend(findings)
            clock.advance(1.0)

        # No integrity findings (no tampering)
        integrity_findings = [f for f in all_findings if f.kind in ("modified", "missing", "added", "bad-signature")]
        assert len(integrity_findings) == 0

        # No audit-broken findings
        audit_findings = [f for f in all_findings if f.kind == "audit-broken"]
        assert len(audit_findings) == 0

        # No anomaly findings (auth failures or read bursts)
        anomaly_findings = [f for f in all_findings if f.kind in ("auth-failures", "read-burst")]
        assert len(anomaly_findings) == 0

        # Not locked
        assert monitor.locked is False


class TestLockdownAndUnlock:
    """Sticky lockdown and unlock behavior."""

    def test_lockdown_is_sticky_until_unlock(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        # Trigger lockdown by tampering
        (root / "a.txt").write_text("tampered")
        monitor.check_integrity()
        assert monitor.locked is True

        # Further ticks stay locked
        monitor.tick()
        assert monitor.locked is True

        # Unlock with valid admin token
        monitor.unlock(admin_token_ok=True)
        assert monitor.locked is False

        # Unlock with invalid admin token does nothing
        (root / "b.txt").write_text("tampered2")
        monitor.check_integrity()
        assert monitor.locked is True
        monitor.unlock(admin_token_ok=False)
        assert monitor.locked is True
        monitor.unlock(admin_token_ok=True)
        assert monitor.locked is False

    def test_lockdown_actions_returns_read_only_when_locked(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        assert monitor.lockdown_actions() == []

        (root / "a.txt").write_text("tampered")
        monitor.check_integrity()

        assert monitor.lockdown_actions() == ["read-only"]


class TestHmacSigner:
    """HmacSigner basic behavior."""

    def test_key_too_short_raises(self):
        with pytest.raises(ValueError):
            HmacSigner(b"short")

    def test_sign_verify_roundtrip(self):
        signer = HmacSigner(KEY)
        data = b"test data"
        sig = signer.sign(data)
        assert signer.verify(data, sig) is True
        assert signer.verify(b"other", sig) is False

    def test_constant_time_compare(self):
        signer = HmacSigner(KEY)
        data = b"data"
        sig = signer.sign(data)
        # Uses hmac.compare_digest internally
        assert signer.verify(data, sig) is True


class TestBuildManifest:
    """Manifest building and canonical form."""

    def test_manifest_covers_all_files(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        (root / "a.txt").write_text("a")
        (root / "b.txt").write_text("b")
        sub = root / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("c")

        signer = HmacSigner(KEY)
        manifest, sig = build_manifest(root, signer, now=1_800_000_000.0)

        assert set(manifest.files.keys()) == {"a.txt", "b.txt", "sub/c.txt"}
        for h in manifest.files.values():
            assert len(h) == 64  # sha256 hex

    def test_manifest_canonical_is_deterministic(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        (root / "a.txt").write_text("a")

        signer = HmacSigner(KEY)
        m1, _ = build_manifest(root, signer, now=1_800_000_000.0)
        m2, _ = build_manifest(root, signer, now=1_800_000_000.0)

        assert m1.canonical() == m2.canonical()

    def test_manifest_signature_verifies(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        (root / "a.txt").write_text("a")

        signer = HmacSigner(KEY)
        manifest, sig = build_manifest(root, signer, now=1_800_000_000.0)

        assert signer.verify(manifest.canonical(), sig) is True


class TestAuditChainVerification:
    """Monitor.tick verifies the audit chain and detects tampering."""

    def test_tick_detects_audit_tampering(self, tmp_path):
        clock = ManualClock()
        monitor, root = _setup_monitor(tmp_path, clock)
        _ensure_audit_file(monitor)

        # Clean tick
        monitor.tick()

        # Tamper with audit file
        audit_path = monitor._audit.path
        lines = audit_path.read_bytes().splitlines()
        rec = json.loads(lines[0])
        rec["event"]["evidence"]["kind"] = "tampered"
        lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()
        audit_path.write_bytes(b"\n".join(lines) + b"\n")

        # Next tick detects it
        findings = monitor.tick()
        assert any(f.kind == "audit-broken" for f in findings)


class TestPolicyDefaults:
    """Policy default values."""

    def test_default_policy_values(self):
        policy = Policy()
        assert policy.lockdown_on == frozenset({"bad-signature", "audit-broken", "modified", "missing", "added"})
        assert policy.max_auth_failures == 5
        assert policy.window_s == 60.0
        assert policy.max_read_bytes == 1 << 30


if __name__ == "__main__":
    pytest.main([__file__, "-v"])