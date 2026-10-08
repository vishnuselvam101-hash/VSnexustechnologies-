"""System security state machine (VNX-Secure; docs/VNX_SECURE_SECURITY_MODEL.md §7).

    TRUSTED ─► DEGRADED ─► SUSPICIOUS ─► ISOLATED ─► INTEGRITY_CHECK ─► VERIFIED
                                                        │      ▲
                                                        ▼      │
                                                       RECOVERY

Escalation (to a more severe state) is always allowed. De-escalation is not: the only way back to a trusted state is
INTEGRITY_CHECK → VERIFIED, which the control plane takes only after the audit chain and the integrity snapshot verify.
An unknown state is treated as ISOLATED (fail closed).
"""
from __future__ import annotations

STATES = ("TRUSTED", "DEGRADED", "SUSPICIOUS", "ISOLATED", "INTEGRITY_CHECK", "RECOVERY", "VERIFIED")
SEVERITY = {"TRUSTED": 0, "VERIFIED": 0, "DEGRADED": 1, "SUSPICIOUS": 2, "ISOLATED": 3, "INTEGRITY_CHECK": 3, "RECOVERY": 3}
TRANSITIONS = {
    "TRUSTED": {"DEGRADED", "SUSPICIOUS", "ISOLATED", "INTEGRITY_CHECK"},
    "VERIFIED": {"TRUSTED", "DEGRADED", "SUSPICIOUS", "ISOLATED", "INTEGRITY_CHECK"},
    "DEGRADED": {"SUSPICIOUS", "ISOLATED", "INTEGRITY_CHECK"},
    "SUSPICIOUS": {"ISOLATED", "INTEGRITY_CHECK"},
    "ISOLATED": {"INTEGRITY_CHECK"},
    "INTEGRITY_CHECK": {"VERIFIED", "RECOVERY", "ISOLATED"},
    "RECOVERY": {"INTEGRITY_CHECK", "ISOLATED"},
}


class StateError(Exception):
    pass


class StateMachine:
    def __init__(self, state: str = "TRUSTED", history: list | None = None):
        self.state = state if state in STATES else "ISOLATED"
        self.history: list = list(history or [])

    def transition(self, to: str, reason: str, ts: float) -> bool:
        """Move to ``to``; returns False if already there. Raises :class:`StateError` for a forbidden transition."""
        if to == self.state:
            return False
        if to not in TRANSITIONS.get(self.state, ()):
            raise StateError(f"transition {self.state} -> {to} is not allowed")
        self.history.append({"from": self.state, "to": to, "reason": reason, "ts": ts})
        self.state = to
        return True

    def escalate(self, to: str, reason: str, ts: float) -> bool:
        """Move to ``to`` only if it is more severe than the current state (idempotent, never de-escalates)."""
        if self.state in ("INTEGRITY_CHECK", "RECOVERY"):
            # a new severe event during a check or recovery aborts it
            return self.transition("ISOLATED", reason, ts) if SEVERITY[to] >= 3 else False
        if SEVERITY[to] <= SEVERITY[self.state]:
            return False
        return self.transition(to, reason, ts)
