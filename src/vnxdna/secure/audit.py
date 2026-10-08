"""Append-only, HMAC-chained security audit log (VNX-Secure; docs/VNX_SECURE_SECURITY_MODEL.md §6).

Each line is ``{"seq", "event", "mac"}`` with ``mac_i = HMAC-SHA256(K_audit, mac_{i-1} || seq || canonical(event))`` and
``mac_{-1}`` = 32 zero bytes. Editing, inserting, deleting or reordering any line breaks every later MAC. Removing lines from
the *end* leaves a valid shorter chain; that is detected only against an anchor (the count and last MAC, kept in a
separate HMAC'd file and in the control-plane state). Without the key, a writer cannot forge MACs, but anyone with write
access can still delete the whole file: the verdict is then FAILED or UNKNOWN, never VERIFIED.

A log that does not verify is not trusted: :class:`AuditLog` refuses to append to it (fail closed) until an operator
moves it aside (``vnx security recover`` keeps it as evidence and starts a new chain anchored to the old one's head).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .events import SecurityEvent, canonical

ZERO = bytes(32)
MAX_LINE = 1 << 20


class AuditIntegrityError(Exception):
    """The audit chain did not verify; it must not be trusted or extended."""


def _mac(key: bytes, prev: bytes, seq: int, event: dict) -> bytes:
    return hmac.new(key, prev + seq.to_bytes(8, "big") + canonical(event), hashlib.sha256).digest()


@dataclass(frozen=True)
class AuditVerdict:
    status: str             # VERIFIED, FAILED (tampered/malformed) or UNKNOWN (no log / no anchor to compare)
    count: int              # lines that verified before the first failure
    head: str               # hex MAC of the last verified line
    first_bad: int | None
    reason: str

    def to_dict(self) -> dict:
        return {"status": self.status, "count": self.count, "head": self.head, "first_bad": self.first_bad, "reason": self.reason}


def verify_lines(lines: list[bytes], key: bytes, anchor: tuple[int, str] | None = None) -> AuditVerdict:
    prev, n = ZERO, 0
    for i, raw in enumerate(lines):
        try:
            if len(raw) > MAX_LINE:
                raise ValueError("line too long")
            rec = json.loads(raw)
            if not isinstance(rec, dict) or set(rec) != {"seq", "event", "mac"}:
                raise ValueError("bad record shape")
            seq, ev, mac = rec["seq"], rec["event"], rec["mac"]
            if not isinstance(seq, int) or isinstance(seq, bool) or seq != i or not isinstance(ev, dict) or not isinstance(mac, str):
                raise ValueError("bad sequence number or field types")
            SecurityEvent.from_dict(ev)
            want = _mac(key, prev, seq, ev)
            if not hmac.compare_digest(want.hex(), mac):
                return AuditVerdict("FAILED", n, prev.hex(), i, f"line {i}: MAC mismatch (edited, inserted, reordered or forged)")
        except (ValueError, TypeError, UnicodeDecodeError) as e:
            return AuditVerdict("FAILED", n, prev.hex(), i, f"line {i}: malformed ({e})")
        prev, n = want, n + 1
    if anchor is not None:
        a_count, a_head = anchor
        if n < a_count:
            return AuditVerdict("FAILED", n, prev.hex(), n, f"log has {n} records but the anchor records {a_count} (truncated)")
        if n == a_count and not hmac.compare_digest(prev.hex(), a_head):
            return AuditVerdict("FAILED", n, prev.hex(), None, "head MAC differs from the anchor (replaced log)")
        if n > a_count:
            # the anchor is from an earlier point: the record at a_count-1 must match it
            p = ZERO
            for j in range(a_count):
                rec = json.loads(lines[j])
                p = _mac(key, p, j, rec["event"])
            if not hmac.compare_digest(p.hex(), a_head):
                return AuditVerdict("FAILED", n, prev.hex(), None, "anchor does not match the chain prefix")
        return AuditVerdict("VERIFIED", n, prev.hex(), None, "chain and anchor verified")
    return AuditVerdict("VERIFIED", n, prev.hex(), None, "chain verified (no anchor: tail truncation is not detectable)")


def _read_lines(path: Path) -> list[bytes]:
    data = path.read_bytes()
    if not data:
        return []
    if not data.endswith(b"\n"):
        # a torn final write is reported, not silently repaired
        return data.split(b"\n")
    return data[:-1].split(b"\n")


def verify_file(path: str | os.PathLike, key: bytes, anchor: tuple[int, str] | None = None) -> AuditVerdict:
    p = Path(path)
    if not p.exists():
        if anchor is not None and anchor[0] > 0:
            return AuditVerdict("FAILED", 0, ZERO.hex(), 0, "audit log is missing but the anchor records events")
        return AuditVerdict("UNKNOWN", 0, ZERO.hex(), None, "no audit log")
    return verify_lines(_read_lines(p), key, anchor)


class AuditLog:
    """Writer for one chain. ``path=None`` keeps the chain in memory (tests, simulator)."""

    def __init__(self, key: bytes, path: str | os.PathLike | None = None, anchor: tuple[int, str] | None = None,
                 fsync: bool = False):
        if len(key) < 32:
            raise ValueError("audit key must be at least 32 bytes")
        self.key, self.path, self.fsync = key, Path(path) if path is not None else None, fsync
        self.lines: list[bytes] = []
        if self.path is not None and self.path.exists():
            v = verify_file(self.path, key, anchor)
            if v.status != "VERIFIED":
                raise AuditIntegrityError(v.reason)
            self.count, self.head = v.count, bytes.fromhex(v.head)
        else:
            if anchor is not None and anchor[0] > 0:
                raise AuditIntegrityError("audit log is missing but the anchor records events")
            self.count, self.head = 0, ZERO

    def append(self, event: SecurityEvent) -> str:
        ev = event.to_dict()
        mac = _mac(self.key, self.head, self.count, ev)
        line = json.dumps({"seq": self.count, "event": ev, "mac": mac.hex()}, sort_keys=True, separators=(",", ":")).encode()
        if self.path is None:
            self.lines.append(line)
        else:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line + b"\n")
                if self.fsync:
                    os.fsync(fd)
            finally:
                os.close(fd)
        self.count, self.head = self.count + 1, mac
        return mac.hex()

    @property
    def anchor(self) -> tuple[int, str]:
        return self.count, self.head.hex()

    def verify(self, anchor: tuple[int, str] | None = None) -> AuditVerdict:
        anchor = anchor if anchor is not None else self.anchor
        if self.path is None:
            return verify_lines(self.lines, self.key, anchor)
        if not self.path.exists() and self.count == 0 and anchor[0] == 0:
            return AuditVerdict("VERIFIED", 0, ZERO.hex(), None, "empty chain (nothing recorded yet)")
        return verify_file(self.path, self.key, anchor)

    def events(self) -> list[dict]:
        lines = self.lines if self.path is None else (_read_lines(self.path) if self.path.exists() else [])
        return [json.loads(x)["event"] for x in lines]
