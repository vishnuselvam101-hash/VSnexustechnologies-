"""Deterministic first-generation detectors (VNX-Secure; docs/VNX_SECURE_SECURITY_MODEL.md §5).

Each detector is a fixed rule over the event stream: the same events in the same order give the same findings
(no learning, no randomness, no wall clock). Windows are measured on event timestamps. Each rule's threshold is
documented next to it and in the security model; a finding names the events that triggered it.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .events import Finding, SecurityEvent

AUTH_FAIL = ("bad_secret", "unknown_principal", "bad_signature", "malformed_token", "expired")


@dataclass(frozen=True)
class Thresholds:
    auth_failures: int = 5            # failed authentications per principal (or source session) in `window`
    authz_failures: int = 5           # denied requests per session in `window`
    rate: int = 120                   # requests per session in `rate_window`
    rate_window: float = 60.0
    distinct_resources: int = 50      # distinct resources per session in `window` (enumeration)
    malformed_repeat: int = 3         # malformed inputs per session in `window` before the finding escalates to HIGH
    integrity_repeat: int = 2         # integrity failures per session in `window` before escalating to CRITICAL
    window: float = 300.0


class Detector:
    """Feeds events one at a time; returns the findings each event triggers. State is bounded by the windows."""

    def __init__(self, thresholds: Thresholds | None = None):
        self.t = thresholds or Thresholds()
        self.hist: dict = defaultdict(deque)     # (rule, key) -> deque[(ts, event_id, value)]
        self.fired: dict = {}                    # (rule, key) -> ts of last finding (one finding per window per rule)
        self.sessions: dict = {}                 # session -> principal bound at first sight

    def _push(self, rule: str, key: str, ev: SecurityEvent, window: float, value: str = "") -> deque:
        q = self.hist[(rule, key)]
        q.append((ev.timestamp, ev.event_id, value))
        while q and q[0][0] < ev.timestamp - window:
            q.popleft()
        return q

    def _once(self, rule: str, key: str, ts: float, window: float) -> bool:
        last = self.fired.get((rule, key))
        if last is not None and ts - last < window:
            return False
        self.fired[(rule, key)] = ts
        return True

    def feed(self, ev: SecurityEvent) -> list[Finding]:
        out: list[Finding] = []
        t, ses = self.t, ev.session_id or f"principal:{ev.principal}"

        def find(det: str, cat: str, sev: str, reason: str, evidence) -> None:
            out.append(Finding(det, cat, sev, ses, reason, tuple(evidence), ev.timestamp))

        code = ev.evidence.get("code", "")
        # 1. repeated failed authentication (brute force / credential stuffing)
        if ev.kind == "auth" and ev.result == "deny" and code in AUTH_FAIL:
            q = self._push("auth", ev.principal or ses, ev, t.window)
            if len(q) >= t.auth_failures and self._once("auth", ev.principal or ses, ev.timestamp, t.window):
                find("auth_failures", "authentication_anomalies", "HIGH",
                     f"{len(q)} failed authentications for {ev.principal!r} in {t.window:.0f}s", [x[1] for x in q])
        # 1b. invalid tokens presented on requests (forgery / token guessing), per claimed session
        if ev.kind == "request" and ev.result == "deny" and code in ("bad_signature", "malformed_token", "expired"):
            q = self._push("token", ses, ev, t.window)
            if len(q) >= t.auth_failures and self._once("token", ses, ev.timestamp, t.window):
                find("invalid_tokens", "authentication_anomalies", "HIGH",
                     f"{len(q)} requests with invalid tokens in {t.window:.0f}s", [x[1] for x in q])
        # 2. revoked / replayed / misbound credentials
        if code in ("token_revoked", "session_revoked", "principal_revoked"):
            find("revoked_credential_use", "authentication_anomalies", "HIGH", f"use of a revoked credential ({code})", [ev.event_id])
        if code in ("replay", "stale_request"):
            find("replay", "authentication_anomalies", "HIGH", f"replay-like request ({code})", [ev.event_id])
        if code == "session_mismatch":
            find("token_misuse", "authentication_anomalies", "HIGH", "token presented for a different session than it was issued to",
                 [ev.event_id])
        # 3. unusual session transitions: a session id reused by a different principal
        if ev.session_id and ev.principal and ev.kind in ("request", "auth") and ev.result == "allow":
            bound = self.sessions.setdefault(ev.session_id, ev.principal)
            if bound != ev.principal:
                find("session_transition", "authentication_anomalies", "HIGH",
                     f"session {ev.session_id} bound to {bound!r} used by {ev.principal!r}", [ev.event_id])
        if ev.kind != "request":
            if ev.kind == "integrity" and ev.result in ("deny", "error"):
                find("integrity_monitor", "integrity_failures", "CRITICAL", f"integrity check failed: {ev.evidence.get('status')}",
                     [ev.event_id])
            if ev.kind == "config" and ev.result != "allow":
                find("config_change", "policy_violations", "HIGH", "unexpected configuration change", [ev.event_id])
            return out
        # 4. request rate
        q = self._push("rate", ses, ev, t.rate_window)
        if len(q) > t.rate and self._once("rate", ses, ev.timestamp, t.rate_window):
            find("request_rate", "rate_anomalies", "MEDIUM", f"{len(q)} requests in {t.rate_window:.0f}s (limit {t.rate})",
                 [x[1] for x in list(q)[-5:]])
        # 5. abnormal access pattern: enumeration of many distinct resources
        q = self._push("resources", ses, ev, t.window, ev.resource)
        if len({x[2] for x in q}) > t.distinct_resources and self._once("resources", ses, ev.timestamp, t.window):
            find("access_pattern", "authorization_violations", "MEDIUM",
                 f"{len({x[2] for x in q})} distinct resources in {t.window:.0f}s", [x[1] for x in list(q)[-5:]])
        if ev.resource.startswith("deception:"):
            find("deception", "deception_hits", "CRITICAL", f"access to deception resource {ev.resource}", [ev.event_id])
        if ev.result == "deny":
            if code == "capability":
                find("privilege_escalation", "authorization_violations", "HIGH",
                     f"operation {ev.operation!r} beyond the token's capabilities", [ev.event_id])
            if code in ("policy", "capability"):
                q = self._push("authz", ses, ev, t.window)
                if len(q) >= t.authz_failures and self._once("authz", ses, ev.timestamp, t.window):
                    find("authz_failures", "authorization_violations", "HIGH", f"{len(q)} denied requests in {t.window:.0f}s",
                         [x[1] for x in q])
        if code == "key_mismatch":
            find("unexpected_key_usage", "integrity_failures", "HIGH", "archive opened with a key it was not sealed with", [ev.event_id])
        if ev.result == "error":
            if code == "malformed":
                q = self._push("malformed", ses, ev, t.window)
                find("malformed_input", "input_anomalies", "HIGH" if len(q) >= t.malformed_repeat else "MEDIUM",
                     f"malformed input ({len(q)} in {t.window:.0f}s): {ev.evidence.get('error', '')[:120]}", [ev.event_id])
            if code == "integrity":
                q = self._push("tamper", ses, ev, t.window)
                find("tamper", "integrity_failures", "CRITICAL" if len(q) >= t.integrity_repeat else "HIGH",
                     f"integrity failure on {ev.resource}: {ev.evidence.get('error', '')[:120]}", [ev.event_id])
        return out
