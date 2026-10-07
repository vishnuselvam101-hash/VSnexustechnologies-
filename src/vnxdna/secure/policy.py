"""Deny-by-default authorization policy (VNX-Secure; docs/VNX_SECURE_SECURITY_MODEL.md §4).

A request is allowed only if some ``allow`` rule matches it and no ``deny`` rule does (explicit deny wins). A rule
matches on role, resource pattern (``fnmatch``) and operation, and additionally requires the current integrity state
to be in ``integrity`` and the subject's risk level to be at most ``max_risk``. The system security state gates every
rule (:data:`STATE_OPERATIONS`). Policies are JSON, validated strictly; anything unknown is rejected, not ignored.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass

from .risk import LEVELS

OPERATIONS = ("list", "verify", "locate", "read", "extract", "write", "admin", "recover")
INTEGRITY_STATES = ("VERIFIED", "MODIFIED", "UNKNOWN", "FAILED")
# operations permitted in each system security state, before any rule is considered
STATE_OPERATIONS = {
    "TRUSTED": set(OPERATIONS),
    "VERIFIED": set(OPERATIONS),
    "DEGRADED": {"list", "verify", "locate", "read", "extract", "admin", "recover"},
    "SUSPICIOUS": {"list", "verify", "admin", "recover"},
    "ISOLATED": {"admin", "recover"},
    "INTEGRITY_CHECK": {"recover"},
    "RECOVERY": {"recover"},
}
MAX_RULES = 1000


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    effect: str
    roles: tuple
    resources: tuple
    operations: tuple
    integrity: tuple = ("VERIFIED",)
    max_risk: str = "MEDIUM"

    def matches(self, roles: tuple, resource: str, operation: str) -> bool:
        return ((("*" in self.roles) or any(r in self.roles for r in roles))
                and operation in self.operations
                and any(fnmatch.fnmatchcase(resource, p) for p in self.resources))


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule: str
    reason: str

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "rule": self.rule, "reason": self.reason}


class Policy:
    def __init__(self, rules: list[Rule], version: int = 1):
        self.rules, self.version = rules, version

    @classmethod
    def from_dict(cls, d) -> "Policy":
        if not isinstance(d, dict) or set(d) != {"version", "rules"} or d["version"] != 1:
            raise PolicyError("policy must be {'version': 1, 'rules': [...]}")
        if not isinstance(d["rules"], list) or len(d["rules"]) > MAX_RULES:
            raise PolicyError("rules must be a list of at most 1000 rules")
        rules, ids = [], set()
        for i, r in enumerate(d["rules"]):
            if not isinstance(r, dict):
                raise PolicyError(f"rule {i}: not an object")
            extra = set(r) - {"id", "effect", "roles", "resources", "operations", "integrity", "max_risk"}
            if extra:
                raise PolicyError(f"rule {i}: unknown fields {sorted(extra)}")
            try:
                rule = Rule(id=r["id"], effect=r["effect"], roles=tuple(r["roles"]), resources=tuple(r["resources"]),
                            operations=tuple(r["operations"]), integrity=tuple(r.get("integrity", ("VERIFIED",))),
                            max_risk=r.get("max_risk", "MEDIUM"))
            except (KeyError, TypeError) as e:
                raise PolicyError(f"rule {i}: {e}")
            if not isinstance(rule.id, str) or not rule.id or rule.id in ids:
                raise PolicyError(f"rule {i}: id must be a unique non-empty string")
            if rule.effect not in ("allow", "deny"):
                raise PolicyError(f"rule {rule.id}: effect must be allow or deny")
            for name, vals in (("roles", rule.roles), ("resources", rule.resources), ("operations", rule.operations)):
                if not vals or not all(isinstance(v, str) and v and len(v) <= 512 for v in vals):
                    raise PolicyError(f"rule {rule.id}: {name} must be non-empty strings")
            if any(o not in OPERATIONS for o in rule.operations):
                raise PolicyError(f"rule {rule.id}: unknown operation")
            if not rule.integrity or any(s not in INTEGRITY_STATES for s in rule.integrity):
                raise PolicyError(f"rule {rule.id}: unknown integrity state")
            if rule.max_risk not in LEVELS:
                raise PolicyError(f"rule {rule.id}: unknown risk level")
            ids.add(rule.id)
            rules.append(rule)
        return cls(rules)

    @classmethod
    def from_json(cls, text: str | bytes) -> "Policy":
        try:
            return cls.from_dict(json.loads(text))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:
            raise PolicyError(f"policy is not valid JSON: {e}")

    def to_dict(self) -> dict:
        return {"version": 1, "rules": [{"id": r.id, "effect": r.effect, "roles": list(r.roles), "resources": list(r.resources),
                                         "operations": list(r.operations), "integrity": list(r.integrity), "max_risk": r.max_risk}
                                        for r in self.rules]}

    def evaluate(self, *, roles: tuple, resource: str, operation: str, integrity: str, risk: str, state: str) -> Decision:
        if state not in STATE_OPERATIONS:
            return Decision(False, "-", f"unknown security state {state!r}: deny and require re-authentication")
        if operation not in STATE_OPERATIONS[state]:
            return Decision(False, "-", f"operation {operation!r} is not permitted in state {state}")
        allow = None
        for r in self.rules:
            if not r.matches(roles, resource, operation):
                continue
            if r.effect == "deny":
                return Decision(False, r.id, f"explicit deny by rule {r.id}")
            if allow is None and integrity in r.integrity and LEVELS.index(risk) <= LEVELS.index(r.max_risk):
                allow = r
        if allow is None:
            return Decision(False, "-", "no allow rule matches (deny by default, or integrity/risk conditions not met)")
        return Decision(True, allow.id, f"allowed by rule {allow.id}")


DEFAULT_POLICY = {
    "version": 1,
    "rules": [
        {"id": "deny-deception", "effect": "deny", "roles": ["*"], "resources": ["deception:*"],
         "operations": list(OPERATIONS)},
        {"id": "reader", "effect": "allow", "roles": ["reader", "writer"], "resources": ["archive:*"],
         "operations": ["list", "verify", "locate", "read", "extract"], "integrity": ["VERIFIED"], "max_risk": "MEDIUM"},
        {"id": "writer", "effect": "allow", "roles": ["writer"], "resources": ["archive:*"], "operations": ["write"],
         "integrity": ["VERIFIED"], "max_risk": "LOW"},
        {"id": "admin", "effect": "allow", "roles": ["admin"], "resources": ["*"], "operations": ["admin", "recover", "verify", "list"],
         "integrity": list(INTEGRITY_STATES), "max_risk": "HIGH"},
    ],
}
