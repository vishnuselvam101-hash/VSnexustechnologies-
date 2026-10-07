"""Audit chain: every edit, insertion, deletion, reordering, truncation or replacement is detected (fail closed)."""
from __future__ import annotations

import json

import pytest

from vnxdna.secure.audit import AuditIntegrityError, AuditLog, verify_file, verify_lines
from vnxdna.secure.events import SecurityEvent

KEY = b"k" * 32


def _ev(i: int) -> SecurityEvent:
    return SecurityEvent(f"ev-{i}", 1000.0 + i, "s", "alice", "archive:a", "read", "allow", evidence={"i": i})


def _log(tmp_path, n=6):
    log = AuditLog(KEY, tmp_path / "audit.log")
    for i in range(n):
        log.append(_ev(i))
    return log


def test_clean_chain_verifies_with_and_without_anchor(tmp_path):
    log = _log(tmp_path)
    assert log.verify().status == "VERIFIED"
    assert verify_file(log.path, KEY).status == "VERIFIED"
    assert verify_file(log.path, KEY, log.anchor).count == 6


@pytest.mark.parametrize("mutate", ["edit", "insert", "delete", "swap", "forge", "torn"])
def test_tampering_is_detected(tmp_path, mutate):
    log = _log(tmp_path)
    lines = log.path.read_bytes().splitlines()
    if mutate == "edit":
        rec = json.loads(lines[2])
        rec["event"]["principal"] = "mallory"
        lines[2] = json.dumps(rec).encode()
    elif mutate == "insert":
        lines.insert(3, lines[3])
    elif mutate == "delete":
        del lines[1]
    elif mutate == "swap":
        lines[1], lines[2] = lines[2], lines[1]
    elif mutate == "forge":
        rec = json.loads(lines[4])
        rec["mac"] = "00" * 32
        lines[4] = json.dumps(rec).encode()
    elif mutate == "torn":
        lines[-1] = lines[-1][:-10]          # a write cut short
    log.path.write_bytes(b"\n".join(lines) + (b"" if mutate == "torn" else b"\n"))
    v = verify_file(log.path, KEY, log.anchor)
    assert v.status == "FAILED"
    with pytest.raises(AuditIntegrityError):
        AuditLog(KEY, log.path, log.anchor)


def test_tail_truncation_needs_the_anchor(tmp_path):
    log = _log(tmp_path)
    lines = log.path.read_bytes().splitlines()
    log.path.write_bytes(b"\n".join(lines[:4]) + b"\n")
    assert verify_file(log.path, KEY).status == "VERIFIED"          # documented limitation without an anchor
    assert verify_file(log.path, KEY, log.anchor).status == "FAILED"


def test_deleted_log_and_wrong_key(tmp_path):
    log = _log(tmp_path)
    anchor = log.anchor
    assert verify_file(log.path, b"x" * 32).status == "FAILED"
    log.path.unlink()
    assert verify_file(log.path, KEY, anchor).status == "FAILED"
    assert verify_file(log.path, KEY).status == "UNKNOWN"


def test_replaced_log_with_same_length_fails_against_anchor(tmp_path):
    log = _log(tmp_path, 3)
    anchor = log.anchor
    other = AuditLog(KEY, tmp_path / "other.log")
    for i in range(3):
        other.append(_ev(i + 100))
    assert verify_file(other.path, KEY, anchor).status == "FAILED"


def test_memory_chain_and_short_key():
    log = AuditLog(KEY)
    log.append(_ev(0))
    assert verify_lines(log.lines, KEY).status == "VERIFIED"
    with pytest.raises(ValueError):
        AuditLog(b"short")
