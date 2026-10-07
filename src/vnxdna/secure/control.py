"""The VNX-Secure control plane (docs/VNX_SECURE_ARCHITECTURE.md).

Every request is evaluated on identity (token), session binding, replay, rate, containment, capability, policy,
integrity state, risk and the system security state; anything not explicitly allowed is denied. Every decision is an
audited :class:`~.events.SecurityEvent`; detectors turn events into findings, the risk score into a level, and the
playbook into idempotent containment actions. Recovery returns to VERIFIED only through a passing integrity check.

``home=None`` keeps all state in memory (tests, the attack simulator). With a home directory the state is persisted:

    secure.key           32-byte master key (mode 0600); sub-keys are HMAC-SHA256(master, "vnx-secure/<label>")
    policy.json          current policy; policy.good.json the known-good copy (its MAC is in the state)
    principals.json      principals and their scrypt secret hashes (MAC'd)
    baseline.json        authenticated integrity baseline
    audit.log            HMAC-chained audit log (anchor in the state)
    state.json           security state, containment, revocations, epoch (MAC'd; a bad MAC means ISOLATED)
    evidence/, quarantine/
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import threading
from functools import wraps
from collections import OrderedDict, deque
from pathlib import Path

from . import integrity as integ
from .audit import AuditIntegrityError, AuditLog
from .contain import LOGIN_LOCK_SECONDS, PLAYBOOK, ActionRecord, ContainmentState, RecordingIsolator
from .detect import Detector, Thresholds
from .events import SYSTEM_CLOCK, Finding, SecurityEvent, canonical
from .identity import AuthError, Principal, ReplayGuard, TokenAuthority, check_secret, hash_secret
from .policy import DEFAULT_POLICY, Policy, PolicyError
from .risk import assess
from .state import StateError, StateMachine

DEFAULT_RATE_PER_MIN = 600
PRIVILEGED = {"admin": "admin", "recover": "admin", "write": "writer"}
MAX_EVIDENCE_COPY = 64 << 20
_DUMMY = hash_secret("unused", n=1 << 14, salt=bytes(16))


def _locked(fn):
    @wraps(fn)
    def w(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return w


class AccessDenied(Exception):
    def __init__(self, code: str, reason: str, event_id: str = ""):
        super().__init__(f"{code}: {reason}")
        self.code, self.reason, self.event_id = code, reason, event_id


class SecurityFailure(Exception):
    """A security-relevant operation failed (integrity, malformed input, wrong key): explicit failure, never data."""

    def __init__(self, code: str, reason: str, event_id: str = ""):
        super().__init__(f"{code}: {reason}")
        self.code, self.reason, self.event_id = code, reason, event_id


def subkey(master: bytes, label: str) -> bytes:
    return hmac.new(master, b"vnx-secure/" + label.encode(), hashlib.sha256).digest()


def _write_private(path: Path, data: bytes, mode: int = 0o600) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


class ControlPlane:
    def __init__(self, home: str | os.PathLike | None = None, *, clock=SYSTEM_CLOCK, master_key: bytes | None = None,
                 policy: dict | None = None, thresholds: Thresholds | None = None, integrity_roots: list | None = None,
                 isolator=None, token_ttl: float = 900.0, rate_per_min: int = DEFAULT_RATE_PER_MIN):
        self.home = Path(home) if home is not None else None
        self._lock = threading.RLock()      # one decision at a time: detection, containment and the audit chain are sequential
        self.clock, self.isolator, self.rate_per_min = clock, isolator or RecordingIsolator(), rate_per_min
        self.alerts: list = []
        if self.home is not None:
            master_key = (self.home / "secure.key").read_bytes()
        if master_key is None or len(master_key) != 32:
            raise ValueError("a 32-byte master key is required")
        self.master = master_key
        self.k_state, self.k_audit, self.k_integ = (subkey(master_key, x) for x in ("state", "audit", "integrity"))
        self.detector = Detector(thresholds)
        self.findings: list[Finding] = []
        self.recent: OrderedDict = OrderedDict()
        self.evidence_mem: list = []
        self.requests: dict = {}                       # session -> deque of allowed-request timestamps
        st = self._load_state()
        self.epoch, self.seq = st["epoch"], st["seq"]
        self.sm = StateMachine(st["state"], st["history"])
        self.cont = ContainmentState.from_dict(st["containment"])
        self.tokens = TokenAuthority(subkey(master_key, f"token/{self.epoch}"), clock, ttl=token_ttl,
                                     revoked_tokens=set(st["revoked_tokens"]), revoked_sessions=set(st["revoked_sessions"]),
                                     revoked_principals=dict(st["revoked_principals"]), counter=st["token_counter"])
        self.replay = ReplayGuard(clock)
        self.integrity_state = st["integrity_state"]
        self.good_policy_mac = st["good_policy_mac"]
        self.principals = self._load_principals()
        try:
            self.policy = Policy.from_dict(policy if policy is not None else self._load_policy())
        except (PolicyError, ValueError, OSError) as e:
            # fail closed: no rule allows anything until recovery restores the known-good policy
            self.alert(f"policy does not load: {e}")
            self.policy = Policy([])
            st["state"] = "ISOLATED" if st["state"] not in ("ISOLATED",) else st["state"]
            self.sm = StateMachine(st["state"], st["history"] + [{"from": "?", "to": "ISOLATED", "reason": "policy unreadable",
                                                                    "ts": clock()}])
        if self.home is None:
            self.baseline = integ.make_baseline(integrity_roots, self.k_integ, clock()) if integrity_roots else None
            self.good_policy = self.policy.to_dict()
            self.good_policy_mac = self._mac_policy(self.good_policy)
        else:
            bp = self.home / "baseline.json"
            self.baseline = json.loads(bp.read_text()) if bp.exists() else None
        anchor = tuple(st["audit_anchor"]) if st["audit_anchor"] else None
        try:
            self.audit = AuditLog(self.k_audit, self.home / "audit.log" if self.home else None, anchor)
            self.audit_trusted = True
        except AuditIntegrityError as e:
            # ALERT + DO NOT TRUST: keep running in memory, isolated, until an operator recovers
            self.alert(f"audit log does not verify: {e}")
            self.audit, self.audit_trusted = AuditLog(self.k_audit, None), False
            self.sm.escalate("ISOLATED", f"audit chain failed: {e}", clock())
        if st.get("_bad_mac"):
            self.alert("state file does not authenticate; entering ISOLATED")
            self.sm.escalate("ISOLATED", "state file MAC mismatch", clock())

    # ------------------------------------------------------------------ persistence
    @classmethod
    def init(cls, home: str | os.PathLike, *, integrity_roots: list | None = None, policy: dict | None = None,
             admin: tuple[str, str] | None = None, clock=SYSTEM_CLOCK) -> "ControlPlane":
        """Create a new VNX-Secure home: key, known-good policy, integrity baseline. Refuses to overwrite."""
        home = Path(home)
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (home / "secure.key").exists():
            raise FileExistsError(f"{home} is already initialised")
        _write_private(home / "secure.key", os.urandom(32))
        (home / "evidence").mkdir(mode=0o700, exist_ok=True)
        (home / "quarantine").mkdir(mode=0o700, exist_ok=True)
        pol = Policy.from_dict(policy or DEFAULT_POLICY).to_dict()
        _write_private(home / "policy.json", (json.dumps(pol, indent=1, sort_keys=True) + "\n").encode())
        _write_private(home / "policy.good.json", (json.dumps(pol, indent=1, sort_keys=True) + "\n").encode())
        master = (home / "secure.key").read_bytes()
        k_integ = subkey(master, "integrity")
        roots = [Path(r) for r in (integrity_roots if integrity_roots is not None else [Path(__file__).resolve().parents[1]])]
        roots.append(home / "policy.json")
        _write_private(home / "baseline.json", integ.dumps(integ.make_baseline(roots, k_integ, clock())).encode())
        cp = cls(home, clock=clock)
        cp.good_policy_mac = cp._mac_policy(pol)
        if admin:
            cp.add_principal(admin[0], admin[1], ("admin",))
        cp.check_integrity()
        if cp.sm.state == "TRUSTED" and cp.integrity_state == "VERIFIED":
            cp.sm.transition("INTEGRITY_CHECK", "initial baseline", clock())
            cp.sm.transition("VERIFIED", "initial baseline verified", clock())
        cp.save()
        return cp

    def _mac(self, obj) -> str:
        return hmac.new(self.k_state, canonical(obj), hashlib.sha256).hexdigest()

    def _mac_policy(self, pol: dict) -> str:
        return hmac.new(self.k_state, b"policy" + canonical(pol), hashlib.sha256).hexdigest()

    def _fresh_state(self) -> dict:
        return {"version": 1, "epoch": 0, "seq": 0, "state": "TRUSTED", "history": [], "containment": ContainmentState().to_dict(),
                "revoked_tokens": [], "revoked_sessions": [], "revoked_principals": {}, "token_counter": 0,
                "integrity_state": "UNKNOWN", "good_policy_mac": "", "audit_anchor": None}

    def _load_state(self) -> dict:
        if self.home is None or not (self.home / "state.json").exists():
            return self._fresh_state()
        try:
            d = json.loads((self.home / "state.json").read_text())
            body, mac = d["body"], d["mac"]
            if hmac.compare_digest(self._mac(body), mac):
                return body
        except (OSError, ValueError, KeyError, TypeError):
            pass
        st = self._fresh_state()
        st["state"], st["_bad_mac"] = "ISOLATED", True
        return st

    def save(self) -> None:
        if self.home is None:
            return
        body = {"version": 1, "epoch": self.epoch, "seq": self.seq, "state": self.sm.state, "history": self.sm.history[-200:],
                "containment": self.cont.to_dict(), "revoked_tokens": sorted(self.tokens.revoked_tokens),
                "revoked_sessions": sorted(self.tokens.revoked_sessions), "revoked_principals": self.tokens.revoked_principals,
                "token_counter": self.tokens.counter, "integrity_state": self.integrity_state,
                "good_policy_mac": self.good_policy_mac, "audit_anchor": list(self.audit.anchor) if self.audit_trusted else None}
        _write_private(self.home / "state.json", json.dumps({"body": body, "mac": self._mac(body)}, sort_keys=True).encode())
        _write_private(self.home / "principals.json", json.dumps({"body": self.principals, "mac": self._mac(self.principals)},
                                                                sort_keys=True).encode())

    def _load_principals(self) -> dict:
        if self.home is None or not (self.home / "principals.json").exists():
            return {}
        d = json.loads((self.home / "principals.json").read_text())
        if not hmac.compare_digest(self._mac(d["body"]), d["mac"]):
            self.alert("principals file does not authenticate: no principal can log in")
            return {}
        return d["body"]

    def _load_policy(self) -> dict:
        text = (self.home / "policy.json").read_text() if self.home else None
        if text is None:
            return DEFAULT_POLICY
        return json.loads(text)

    # ------------------------------------------------------------------ events
    def alert(self, msg: str) -> None:
        self.alerts.append(msg)

    @_locked
    def record(self, *, kind: str, result: str, session_id: str = "", principal: str = "", resource: str = "", operation: str = "",
               severity: str = "INFO", detector: str = "", evidence: dict | None = None, response: tuple = ()) -> tuple[SecurityEvent, list]:
        """Append one event to the audit chain, run detection and containment; returns (event, action records)."""
        self.seq += 1
        ev = SecurityEvent(f"ev-{self.epoch}-{self.seq:08d}", self.clock(), session_id, principal, resource, operation, result, severity,
                           detector, dict(evidence or {}), tuple(response), self.integrity_state, kind)
        self.audit.append(ev)
        self.recent[ev.event_id] = ev
        while len(self.recent) > 10_000:
            self.recent.popitem(last=False)
        actions = []
        for f in self.detector.feed(ev):
            self.findings.append(f)
            actions += self._contain(f, ev)
        return ev, actions

    def _sub(self, kind: str, msg: str, **kw) -> SecurityEvent:
        """An internal event (finding, containment, recovery): audited, not fed back into detection."""
        self.seq += 1
        for k in ("session_id", "principal", "resource", "operation"):
            kw.setdefault(k, "")
        ev = SecurityEvent(f"ev-{self.epoch}-{self.seq:08d}", self.clock(), result="observed", kind=kind, integrity_state=self.integrity_state,
                           evidence={"message": msg, **kw.pop("evidence", {})}, **kw)
        self.audit.append(ev)
        self.recent[ev.event_id] = ev
        return ev

    def _contain(self, f: Finding, ev: SecurityEvent) -> list[ActionRecord]:
        now = self.clock()
        ra = assess(f.subject, self.findings, now)
        self._sub("finding", f.reason, session_id=ev.session_id, principal=ev.principal, resource=ev.resource, severity=f.severity,
                  detector=f.detector, evidence={"finding": f.to_dict(), "risk": ra.to_dict()})
        rule = f"playbook:{ra.level}"
        why = f"{f.detector} ({f.severity}): {f.reason}; risk {ra.score} = {ra.breakdown} -> {ra.level}"
        out: list[ActionRecord] = []

        def act(action: str, target: str, changed: bool) -> None:
            out.append(ActionRecord(action, target, bool(changed), why, f.evidence, rule))

        ses, pr = ev.session_id, ev.principal
        for a in PLAYBOOK[ra.level]:
            if a == "RATE_LIMIT" and ses:
                act(a, ses, self.cont.rate_limit(ses))
            elif a == "REQUIRE_REAUTHENTICATION" and pr:
                act(a, pr, self.cont.require_reauth(pr) | self.tokens.revoke_principal(pr))
            elif a == "CONTAIN_SESSION" and ses:
                act(a, ses, self.cont.contain_session(ses) | self.tokens.revoke_session(ses))
            elif a == "REVOKE_TOKEN" and ev.evidence.get("tid"):
                act(a, ev.evidence["tid"], self.tokens.revoke_token(ev.evidence["tid"]))
            elif a == "REVOKE_PRINCIPAL_TOKENS" and pr:
                act(a, pr, self.tokens.revoke_principal(pr))
                act("LOCK_PRINCIPAL_LOGIN", pr, self.cont.lock_login(pr, now + LOGIN_LOCK_SECONDS))
            elif a == "QUARANTINE_INPUT" and ev.evidence.get("input_sha256"):
                act(a, ev.evidence["input_sha256"], self._quarantine(ev))
            elif a == "LOCK_RESOURCE" and ev.resource and not ev.resource.startswith("deception:"):
                act(a, ev.resource, self.cont.lock_resource(ev.resource))
            elif a == "ISOLATE_WORKLOAD" and ses:
                changed = self.cont.isolate_workload(ses)
                if changed:
                    self.isolator.isolate(ses, why)
                act(a, ses, changed)
            elif a == "PRESERVE_EVIDENCE":
                act(a, self._preserve(f, ra, ev), True)
        if f.detector == "auth_failures" and pr:
            act("LOCK_PRINCIPAL_LOGIN", pr, self.cont.lock_login(pr, now + LOGIN_LOCK_SECONDS))
        if f.category == "integrity_failures" and f.detector in ("integrity_monitor", "config_change"):
            act("ENTER_RECOVERY", "system", self.sm.escalate("ISOLATED", why, now))
            act("PRESERVE_EVIDENCE", self._preserve(f, ra, ev), True)
        elif f.detector == "config_change":
            act("ENTER_RECOVERY", "system", self.sm.escalate("ISOLATED", why, now))
        elif ra.level == "CRITICAL":
            self.sm.escalate("DEGRADED", why, now)
        for r in out:
            self._sub("containment", r.why, session_id=ses, principal=pr, resource=r.target, operation=r.action, detector=f.detector,
                      evidence={"action": r.to_dict()}, response=(r.action,))
        self.save()
        return out

    def _preserve(self, f: Finding, ra, ev: SecurityEvent) -> str:
        bundle = {"finding": f.to_dict(), "risk": ra.to_dict(), "trigger": ev.to_dict(),
                  "events": [self.recent[e].to_dict() for e in f.evidence if e in self.recent],
                  "audit_head": self.audit.anchor[1], "audit_count": self.audit.anchor[0], "state": self.sm.state}
        data = canonical(bundle)
        h = hashlib.sha256(data).hexdigest()
        if self.home is None:
            self.evidence_mem.append((h, bundle))
        else:
            p = self.home / "evidence" / f"{ev.event_id}-{h[:16]}.json"
            if not p.exists():
                _write_private(p, data, 0o400)
        self._sub("evidence", f"evidence bundle {h}", evidence={"sha256": h, "finding": f.detector})
        return h

    def _quarantine(self, ev: SecurityEvent) -> bool:
        sha, src = ev.evidence["input_sha256"], ev.evidence.get("input_path")
        copy = None
        if self.home is not None and src and Path(src).is_file() and Path(src).stat().st_size <= MAX_EVIDENCE_COPY:
            dst = self.home / "quarantine" / sha
            if not dst.exists():
                shutil.copyfile(src, dst)
                os.chmod(dst, 0o400)
            copy = str(dst)
        return self.cont.quarantine(sha, {"resource": ev.resource, "event": ev.event_id, "copy": copy})

    # ------------------------------------------------------------------ identity
    @_locked
    def add_principal(self, name: str, secret: str, roles: tuple, *, scrypt_n: int = 1 << 14) -> None:
        if not name or not isinstance(name, str) or len(name) > 128:
            raise ValueError("bad principal name")
        self.principals[name] = {"roles": sorted(roles), "secret": hash_secret(secret, n=scrypt_n)}
        self._sub("admin", f"principal {name} set", principal=name, evidence={"roles": sorted(roles)})
        self.save()

    @_locked
    def login(self, name: str, secret: str, session_id: str) -> str:
        now = self.clock()
        rec = self.principals.get(name)
        base = dict(kind="auth", session_id=session_id, principal=name, operation="login")
        if self.cont.login_locked.get(name, float("-inf")) > now:
            ev, _ = self.record(result="deny", evidence={"code": "login_locked"}, **base)
            raise AccessDenied("login_locked", "too many failed authentications; try later", ev.event_id)
        ok = check_secret(secret, rec["secret"] if rec else _DUMMY)
        if not rec or not ok:
            code = "unknown_principal" if not rec else "bad_secret"
            ev, _ = self.record(result="deny", evidence={"code": code}, **base)
            raise AccessDenied(code, "authentication failed", ev.event_id)
        try:
            token, claims = self.tokens.issue(Principal(name, tuple(rec["roles"])), session_id)
        except AuthError as e:
            ev, _ = self.record(result="deny", evidence={"code": e.reason}, **base)
            raise AccessDenied(e.reason, str(e), ev.event_id)
        self.cont.reauth_principals.discard(name)
        self.record(result="allow", evidence={"code": "ok", "tid": claims.tid}, **base)
        self.save()
        return token

    # ------------------------------------------------------------------ authorization
    @_locked
    def authorize(self, token: str, session_id: str, nonce: str, ts: float, resource: str, operation: str):
        """Zero-trust request evaluation; returns the token claims or raises :class:`AccessDenied` (always audited)."""
        base = dict(kind="request", session_id=session_id, resource=resource, operation=operation)

        def deny(code: str, reason: str, principal: str = "", **ev) -> None:
            e, _ = self.record(result="deny", principal=principal, evidence={"code": code, **ev}, **base)
            raise AccessDenied(code, reason, e.event_id)

        try:
            c = self.tokens.validate(token)
        except AuthError as e:
            deny(e.reason, str(e))
        if c.ses != session_id:
            deny("session_mismatch", "token was issued to another session", c.sub, tid=c.tid)
        try:
            self.replay.check(nonce, ts)
        except AuthError as e:
            deny(e.reason, str(e), c.sub, tid=c.tid)
        now = self.clock()
        q = self.requests.setdefault(session_id, deque())
        while q and q[0] <= now - 60.0:
            q.popleft()
        if len(q) >= self.cont.rate_limited.get(session_id, self.rate_per_min):
            deny("rate_limited", "request rate limit", c.sub, tid=c.tid)
        if resource in self.cont.locked_resources:
            deny("resource_locked", f"{resource} is locked pending recovery", c.sub, tid=c.tid)
        need = PRIVILEGED.get(operation)
        if need and need not in c.caps:
            deny("capability", f"operation {operation!r} needs capability {need!r}", c.sub, tid=c.tid)
        risk = assess(session_id, self.findings, now).level
        d = self.policy.evaluate(roles=c.caps, resource=resource, operation=operation, integrity=self.integrity_state,
                                 risk=risk, state=self.sm.state)
        if not d.allowed:
            deny("policy", d.reason, c.sub, tid=c.tid, rule=d.rule)
        q.append(now)
        self.record(result="allow", principal=c.sub, evidence={"code": "ok", "tid": c.tid, "rule": d.rule}, **base)
        return c

    @_locked
    def observe_failure(self, claims, session_id: str, resource: str, operation: str, code: str, error: str, **evidence) -> str:
        """Record a failed operation after authorization (malformed input, integrity failure, wrong key)."""
        ev, _ = self.record(kind="request", result="error", session_id=session_id, principal=claims.sub if claims else "",
                            resource=resource, operation=operation,
                            evidence={"code": code, "error": error[:500], **({"tid": claims.tid} if claims else {}), **evidence})
        return ev.event_id

    # ------------------------------------------------------------------ integrity and recovery
    @_locked
    def check_integrity(self) -> dict:
        audit = self.audit.verify() if self.audit_trusted else None
        snap = integ.check(self.baseline, self.k_integ)
        pol_ok = self._mac_policy(self.policy.to_dict()) == self.good_policy_mac if self.good_policy_mac else False
        status = snap["status"]
        if audit is None or audit.status != "VERIFIED":
            status = "FAILED"
        elif not pol_ok and status == "VERIFIED":
            status = "MODIFIED"
        self.integrity_state = status
        report = {"status": status, "snapshot": {k: snap[k] for k in ("status", "counts", "reason", "items")},
                  "audit": audit.to_dict() if audit else {"status": "FAILED", "reason": "audit log not trusted"},
                  "policy_known_good": pol_ok, "scope": integ.SCOPE}
        self.record(kind="integrity", result="allow" if status == "VERIFIED" else "deny", resource="system", operation="integrity",
                    evidence={"status": status, "snapshot": snap["status"], "audit": report["audit"]["status"], "policy_known_good": pol_ok})
        self.save()
        return report

    @_locked
    def recover(self, archives: list | None = None, key: bytes | None = None) -> dict:
        """Defensive recovery: never trusts the current state; returns to VERIFIED only after verification passes."""
        now = self.clock()
        steps = []
        if self.sm.state != "INTEGRITY_CHECK":
            try:
                self.sm.transition("INTEGRITY_CHECK", "recovery requested", now)
            except StateError:
                self.sm.transition("ISOLATED", "recovery requested from an unexpected state", now)
                self.sm.transition("INTEGRITY_CHECK", "recovery requested", now)
        # 1. audit chain: an untrusted chain is preserved as evidence and a new chain is anchored to it
        v = self.audit.verify() if self.audit_trusted else None
        if v is None or v.status != "VERIFIED":
            kept = None
            if self.home is not None and (self.home / "audit.log").exists():
                kept = self.home / "evidence" / f"audit-untrusted-{self.epoch}-{int(now)}.log"
                os.replace(self.home / "audit.log", kept)
            self.audit, self.audit_trusted = AuditLog(self.k_audit, self.home / "audit.log" if self.home else None), True
            self._sub("recovery", "audit chain was not trusted; new chain started", evidence={"kept": str(kept) if kept else None,
                                                                                               "verdict": v.to_dict() if v else None})
            steps.append({"step": "audit", "result": "RESET", "kept": str(kept) if kept else None})
        else:
            steps.append({"step": "audit", "result": "VERIFIED", "count": v.count})
        # 2. policy: restore the known-good policy if the current one differs
        if self.good_policy_mac and self._mac_policy(self.policy.to_dict()) != self.good_policy_mac:
            good = json.loads((self.home / "policy.good.json").read_text()) if self.home else self.good_policy
            if self._mac_policy(good) != self.good_policy_mac:
                steps.append({"step": "policy", "result": "FAILED", "reason": "known-good policy copy does not authenticate"})
                return self._recovery_failed(steps, "known-good policy copy does not authenticate")
            self.policy = Policy.from_dict(good)
            if self.home:
                _write_private(self.home / "policy.json", (json.dumps(good, indent=1, sort_keys=True) + "\n").encode())
            steps.append({"step": "policy", "result": "RESTORED"})
        else:
            steps.append({"step": "policy", "result": "VERIFIED"})
        # 3. credentials: rotate the token key (every token of the old epoch is dead), reset replay and rate state
        self.epoch += 1
        self.tokens = TokenAuthority(subkey(self.master, f"token/{self.epoch}"), self.clock, ttl=self.tokens.ttl)
        self.replay, self.requests = ReplayGuard(self.clock), {}
        steps.append({"step": "credentials", "result": "ROTATED", "epoch": self.epoch})
        # 4. archives (optional): verify each; a failing one stays locked
        for a in archives or []:
            from ..archive.operations import verify_container
            res = "VERIFIED"
            try:
                verify_container(a, key=key, allow_unencrypted=key is None)
            except Exception as e:  # noqa: BLE001 - any failure keeps the archive locked
                res = f"FAILED: {type(e).__name__}"
            steps.append({"step": "archive", "path": str(a), "result": res})
            if res == "VERIFIED":
                self.cont.locked_resources.discard(f"archive:{Path(a).name}")
        # 5. integrity snapshot (components, policy, audit) must verify
        rep = self.check_integrity()
        steps.append({"step": "integrity", "result": rep["status"], "reason": rep["snapshot"]["reason"]})
        if rep["status"] != "VERIFIED" or self.sm.state != "INTEGRITY_CHECK":
            return self._recovery_failed(steps, "integrity snapshot did not verify; reinstall from a trusted source and re-baseline")
        kept_q, kept_locks = dict(self.cont.quarantined), set(self.cont.locked_resources)
        self.cont = ContainmentState(quarantined=kept_q, locked_resources=kept_locks)
        self.findings, self.detector = [], Detector(self.detector.t)
        self.sm.transition("VERIFIED", "recovery verified", self.clock())
        self._sub("recovery", "recovered to VERIFIED", evidence={"steps": steps})
        self.save()
        return {"result": "VERIFIED", "state": self.sm.state, "epoch": self.epoch, "steps": steps}

    def _recovery_failed(self, steps: list, reason: str) -> dict:
        if self.sm.state == "INTEGRITY_CHECK":
            self.sm.transition("RECOVERY", reason, self.clock())
        self.sm.escalate("ISOLATED", reason, self.clock())
        self._sub("recovery", f"recovery did not verify: {reason}", evidence={"steps": steps})
        self.save()
        return {"result": "FAILED", "state": self.sm.state, "epoch": self.epoch, "steps": steps, "reason": reason}

    # ------------------------------------------------------------------ reporting
    @_locked
    def status(self) -> dict:
        return {"state": self.sm.state, "epoch": self.epoch, "integrity": self.integrity_state, "audit_trusted": self.audit_trusted,
                "audit_records": self.audit.anchor[0], "principals": sorted(self.principals), "containment": self.cont.to_dict(),
                "revoked": {"tokens": len(self.tokens.revoked_tokens), "sessions": len(self.tokens.revoked_sessions),
                            "principals": sorted(self.tokens.revoked_principals)},
                "findings_since_recovery": len(self.findings), "alerts": list(self.alerts),
                "transitions": self.sm.history[-10:]}
