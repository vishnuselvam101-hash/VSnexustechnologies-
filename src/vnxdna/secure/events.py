"""Structured security events and findings (VNX-Secure; docs/VNX_SECURE_SECURITY_MODEL.md §2).

Events are plain data with a canonical JSON form, so detection over a recorded event stream is reproducible and the
audit chain (:mod:`.audit`) can authenticate them byte for byte.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
RESULTS = ("allow", "deny", "error", "observed")


def canonical(obj) -> bytes:
    """Canonical JSON bytes (sorted keys, no whitespace, ASCII) of a JSON-able object."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


class Clock(Protocol):
    def __call__(self) -> float: ...


class ManualClock:
    """A deterministic clock for tests, the attack simulator and replay: time moves only when ``advance`` is called."""

    def __init__(self, start: float = 1_800_000_000.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("a clock cannot go backwards")
        self.t += seconds
        return self.t


SYSTEM_CLOCK: Callable[[], float] = time.time


@dataclass(frozen=True)
class SecurityEvent:
    """One observed security-relevant fact: a request decision, an authentication attempt, an integrity result, ..."""
    event_id: str
    timestamp: float
    session_id: str
    principal: str
    resource: str
    operation: str
    result: str
    severity: str = "INFO"
    detector: str = ""
    evidence: dict = field(default_factory=dict)
    response: tuple = ()
    integrity_state: str = "UNKNOWN"
    kind: str = "request"

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")
        if self.result not in RESULTS:
            raise ValueError(f"unknown result {self.result!r}")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["response"] = list(self.response)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SecurityEvent":
        d = dict(d)
        d["response"] = tuple(d.get("response", ()))
        return cls(**d)


@dataclass(frozen=True)
class Finding:
    """A detector's conclusion over one or more events. ``severity`` drives the risk score (:mod:`.risk`)."""
    detector: str
    category: str
    severity: str
    subject: str            # the session (or principal, prefixed "principal:") the finding is about
    reason: str
    evidence: tuple         # event_ids
    timestamp: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["evidence"] = list(self.evidence)
        return d
