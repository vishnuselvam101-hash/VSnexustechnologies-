"""Automatic containment: explicit, idempotent action objects (VNX-Secure; docs/VNX_SECURE_SECURITY_MODEL.md §6).

Containment protects VNX; it never acts on the attacker's systems. Every action records why it happened (the
finding and risk assessment), which evidence triggered it and which playbook rule applied. Applying the same action
twice changes nothing the second time (``changed=False``), so repeated events leave the state consistent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

ACTIONS = ("RATE_LIMIT", "REQUIRE_REAUTHENTICATION", "CONTAIN_SESSION", "REVOKE_TOKEN", "REVOKE_PRINCIPAL_TOKENS",
           "LOCK_PRINCIPAL_LOGIN", "QUARANTINE_INPUT", "LOCK_RESOURCE", "ISOLATE_WORKLOAD", "PRESERVE_EVIDENCE",
           "ENTER_RECOVERY")
# playbook: risk level of the subject -> actions (documented in the security model, §6)
PLAYBOOK = {
    "LOW": (),
    "MEDIUM": ("RATE_LIMIT", "PRESERVE_EVIDENCE"),
    "HIGH": ("RATE_LIMIT", "REQUIRE_REAUTHENTICATION", "CONTAIN_SESSION", "REVOKE_TOKEN", "QUARANTINE_INPUT",
             "PRESERVE_EVIDENCE"),
    "CRITICAL": ("RATE_LIMIT", "REQUIRE_REAUTHENTICATION", "CONTAIN_SESSION", "REVOKE_TOKEN", "REVOKE_PRINCIPAL_TOKENS",
                 "QUARANTINE_INPUT", "LOCK_RESOURCE", "ISOLATE_WORKLOAD", "PRESERVE_EVIDENCE"),
}
RATE_LIMITED_PER_MIN = 10
LOGIN_LOCK_SECONDS = 900.0


@dataclass(frozen=True)
class ActionRecord:
    action: str
    target: str
    changed: bool
    why: str            # the finding / assessment that caused it
    evidence: tuple     # event ids
    policy: str         # the playbook rule applied

    def to_dict(self) -> dict:
        return {"action": self.action, "target": self.target, "changed": self.changed, "why": self.why,
                "evidence": list(self.evidence), "policy": self.policy}


class NetworkIsolator(Protocol):
    """Interface to a network-isolation mechanism (firewall, service mesh, cgroup). This build ships only
    :class:`RecordingIsolator`, which records the request: no network configuration is changed."""

    def isolate(self, workload: str, reason: str) -> None: ...


class RecordingIsolator:
    def __init__(self):
        self.requests: list = []

    def isolate(self, workload: str, reason: str) -> None:
        self.requests.append((workload, reason))


@dataclass
class ContainmentState:
    contained_sessions: set = field(default_factory=set)
    reauth_principals: set = field(default_factory=set)
    rate_limited: dict = field(default_factory=dict)        # session -> requests per minute
    login_locked: dict = field(default_factory=dict)        # principal -> locked until (timestamp)
    locked_resources: set = field(default_factory=set)
    isolated_workloads: set = field(default_factory=set)
    quarantined: dict = field(default_factory=dict)         # sha256 -> {"resource", "reason", "copy"}

    def to_dict(self) -> dict:
        return {"contained_sessions": sorted(self.contained_sessions), "reauth_principals": sorted(self.reauth_principals),
                "rate_limited": dict(sorted(self.rate_limited.items())), "login_locked": dict(sorted(self.login_locked.items())),
                "locked_resources": sorted(self.locked_resources), "isolated_workloads": sorted(self.isolated_workloads),
                "quarantined": dict(sorted(self.quarantined.items()))}

    @classmethod
    def from_dict(cls, d: dict) -> "ContainmentState":
        return cls(set(d["contained_sessions"]), set(d["reauth_principals"]), dict(d["rate_limited"]), dict(d["login_locked"]),
                   set(d["locked_resources"]), set(d["isolated_workloads"]), dict(d["quarantined"]))

    def _add(self, s: set, v: str) -> bool:
        if v in s:
            return False
        s.add(v)
        return True

    def contain_session(self, ses: str) -> bool:
        return self._add(self.contained_sessions, ses)

    def require_reauth(self, principal: str) -> bool:
        return self._add(self.reauth_principals, principal)

    def rate_limit(self, ses: str, per_min: int = RATE_LIMITED_PER_MIN) -> bool:
        if self.rate_limited.get(ses, 1 << 30) <= per_min:
            return False
        self.rate_limited[ses] = per_min
        return True

    def lock_login(self, principal: str, until: float) -> bool:
        if self.login_locked.get(principal, float("-inf")) >= until:
            return False
        self.login_locked[principal] = until
        return True

    def lock_resource(self, resource: str) -> bool:
        return self._add(self.locked_resources, resource)

    def isolate_workload(self, workload: str) -> bool:
        return self._add(self.isolated_workloads, workload)

    def quarantine(self, sha256: str, info: dict) -> bool:
        if sha256 in self.quarantined:
            return False
        self.quarantined[sha256] = info
        return True
