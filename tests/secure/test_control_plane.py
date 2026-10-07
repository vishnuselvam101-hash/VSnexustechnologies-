"""Zero-trust request evaluation, detection, risk, idempotent containment, the state machine and recovery."""
from __future__ import annotations

import json
import threading

import pytest

from vnxdna.secure.contain import ContainmentState
from vnxdna.secure.control import AccessDenied, ControlPlane
from vnxdna.secure.detect import Detector
from vnxdna.secure.events import Finding, ManualClock, SecurityEvent
from vnxdna.secure.risk import assess, level
from vnxdna.secure.state import StateError, StateMachine


def _auth(cp, tok, ses, nonce, res="archive:a.vnx", op="read"):
    return cp.authorize(tok, ses, nonce(), cp.clock(), res, op)


def test_allowed_request_is_audited(cp, nonce):
    tok = cp.login("alice", "alice-pw", "s1")
    c = _auth(cp, tok, "s1", nonce)
    assert c.sub == "alice"
    ev = cp.audit.events()[-1]
    assert ev["result"] == "allow" and ev["evidence"]["rule"] == "reader"


@pytest.mark.parametrize("who,res,op,code", [("eve", "archive:a.vnx", "read", "policy"), ("alice", "archive:a.vnx", "write", "capability"),
                                             ("alice", "system", "admin", "capability"), ("ops", "deception:x", "admin", "policy")])
def test_denials_are_explicit_and_audited(cp, nonce, who, res, op, code):
    tok = cp.login(who, f"{who}-pw", "s1")
    with pytest.raises(AccessDenied) as e:
        _auth(cp, tok, "s1", nonce, res, op)
    assert e.value.code == code
    assert any(x["event_id"] == e.value.event_id and x["result"] == "deny" for x in cp.audit.events())


def test_integrity_state_gates_reads(cp, nonce):
    tok = cp.login("alice", "alice-pw", "s1")
    (cp.comp / "a.py").write_text("A = 2\n")
    cp.check_integrity()
    assert cp.integrity_state == "MODIFIED" and cp.sm.state == "ISOLATED"
    with pytest.raises(AccessDenied):
        _auth(cp, tok, "s1", nonce)


def test_brute_force_locks_login_and_revokes(cp):
    good = cp.login("alice", "alice-pw", "s-good")
    for i in range(5):
        with pytest.raises(AccessDenied):
            cp.login("alice", f"bad{i}", "s-x")
        cp.clock.advance(1)
    with pytest.raises(AccessDenied) as e:
        cp.login("alice", "alice-pw", "s-y")
    assert e.value.code == "login_locked"
    with pytest.raises(Exception):
        cp.tokens.validate(good)
    cp.clock.advance(901)
    cp.login("alice", "alice-pw", "s-z")           # the lock expires


def test_unknown_principal_counts_as_failure(cp):
    for _ in range(5):
        with pytest.raises(AccessDenied) as e:
            cp.login("nobody", "x", "s")
    assert e.value.code == "unknown_principal"
    assert any(f.detector == "auth_failures" for f in cp.findings)


def test_containment_is_idempotent(cp, nonce):
    tok = cp.login("alice", "alice-pw", "s1")
    with pytest.raises(AccessDenied):
        _auth(cp, tok, "s1", nonce, "system", "admin")
    snap = json.dumps(cp.cont.to_dict(), sort_keys=True)
    revoked = (set(cp.tokens.revoked_tokens), set(cp.tokens.revoked_sessions))
    # the same finding again changes nothing
    ev = cp.recent[cp.findings[-1].evidence[0]]
    again = cp._contain(cp.findings[-1], ev)
    assert all(not a.changed for a in again if a.action != "PRESERVE_EVIDENCE")
    assert json.dumps(cp.cont.to_dict(), sort_keys=True) == snap
    assert (set(cp.tokens.revoked_tokens), set(cp.tokens.revoked_sessions)) == revoked


def test_actions_record_why_evidence_and_policy(cp, nonce):
    tok = cp.login("alice", "alice-pw", "s1")
    with pytest.raises(AccessDenied):
        _auth(cp, tok, "s1", nonce, "system", "admin")
    acts = [e["evidence"]["action"] for e in cp.audit.events() if e["kind"] == "containment"]
    assert {a["action"] for a in acts} >= {"CONTAIN_SESSION", "REVOKE_TOKEN", "PRESERVE_EVIDENCE"}
    for a in acts:
        assert a["why"] and a["evidence"] and a["policy"].startswith("playbook:")


def test_rate_limit_and_session_binding(cp, nonce):
    cp.rate_per_min = 5
    tok = cp.login("alice", "alice-pw", "s1")
    for _ in range(5):
        _auth(cp, tok, "s1", nonce)
    with pytest.raises(AccessDenied) as e:
        _auth(cp, tok, "s1", nonce)
    assert e.value.code == "rate_limited"
    with pytest.raises(AccessDenied) as e:
        _auth(cp, tok, "other", nonce)
    assert e.value.code == "session_mismatch"


def test_risk_score_is_explainable():
    f = lambda sev, t=0.0: Finding("d", "authorization_violations", sev, "s", "r", ("e",), t)  # noqa: E731
    assert [level(x) for x in (0, 19, 20, 49, 50, 79, 80)] == ["LOW", "LOW", "MEDIUM", "MEDIUM", "HIGH", "HIGH", "CRITICAL"]
    a = assess("s", [f("MEDIUM"), f("HIGH"), f("LOW", -10_000)], now=0.0)
    assert a.score == 70 and a.level == "HIGH" and a.breakdown == {"authorization_violations": 70} and len(a.findings) == 2


def test_state_machine():
    sm = StateMachine()
    assert sm.escalate("SUSPICIOUS", "x", 0) and not sm.escalate("DEGRADED", "x", 0)
    with pytest.raises(StateError):
        sm.transition("VERIFIED", "shortcut", 0)
    with pytest.raises(StateError):
        sm.transition("TRUSTED", "shortcut", 0)
    sm.transition("INTEGRITY_CHECK", "x", 0)
    assert sm.escalate("CRITICAL" if False else "ISOLATED", "new attack", 0) and sm.state == "ISOLATED"
    assert StateMachine("BOGUS").state == "ISOLATED"


def test_recovery_requires_verified_integrity(cp, nonce):
    good = (cp.comp / "a.py").read_text()
    (cp.comp / "a.py").write_text("A = 666\n")
    cp.check_integrity()
    r = cp.recover()
    assert r["result"] == "FAILED" and cp.sm.state == "ISOLATED"
    (cp.comp / "a.py").write_text(good)
    old = cp.login("alice", "alice-pw", "s-old")
    r = cp.recover()
    assert r["result"] == "VERIFIED" and cp.sm.state == "VERIFIED" and cp.epoch == 2
    with pytest.raises(Exception):
        cp.tokens.validate(old)                     # credentials rotated
    cp.clock.advance(1)
    tok = cp.login("alice", "alice-pw", "s-new")
    _auth(cp, tok, "s-new", nonce)
    assert cp.audit.verify().status == "VERIFIED"


def test_persistent_home_round_trip_and_tamper(tmp_path):
    clk = ManualClock()
    cp = ControlPlane.init(tmp_path / "h", integrity_roots=[], admin=("ops", "ops-pw"), clock=clk)
    assert cp.sm.state == "VERIFIED"
    cp2 = ControlPlane(tmp_path / "h", clock=clk)
    assert cp2.sm.state == "VERIFIED" and cp2.audit_trusted and "ops" in cp2.principals
    st = json.loads((tmp_path / "h" / "state.json").read_text())
    st["body"]["state"] = "TRUSTED"
    st["body"]["containment"]["locked_resources"] = []
    (tmp_path / "h" / "state.json").write_text(json.dumps(st))
    assert ControlPlane(tmp_path / "h", clock=clk).sm.state == "ISOLATED"        # MAC mismatch -> fail closed
    with pytest.raises(FileExistsError):
        ControlPlane.init(tmp_path / "h")


def test_corrupted_audit_log_is_not_trusted(tmp_path):
    clk = ManualClock()
    ControlPlane.init(tmp_path / "h", integrity_roots=[], clock=clk)
    p = tmp_path / "h" / "audit.log"
    p.write_bytes(p.read_bytes().replace(b'"result":"allow"', b'"result":"deny"', 1))
    cp = ControlPlane(tmp_path / "h", clock=clk)
    assert not cp.audit_trusted and cp.sm.state == "ISOLATED" and cp.alerts
    assert cp.check_integrity()["status"] == "FAILED"
    r = cp.recover()
    assert r["result"] == "VERIFIED" and r["steps"][0]["result"] == "RESET"
    assert list((tmp_path / "h" / "evidence").glob("audit-untrusted-*.log"))


def test_concurrent_requests_keep_a_consistent_chain(cp):
    toks = {i: cp.login("alice", "alice-pw", f"s{i}") for i in range(8)}
    errors = []

    def worker(i):
        for k in range(25):
            try:
                cp.authorize(toks[i], f"s{i}", f"nonce-{i:04d}-{k:020d}", cp.clock(), "archive:a.vnx", "read")
            except Exception as e:  # noqa: BLE001
                errors.append(e)
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    v = cp.audit.verify()
    assert v.status == "VERIFIED" and v.count == cp.audit.count
    ids = [e["event_id"] for e in cp.audit.events()]
    assert len(ids) == len(set(ids))


def test_detection_is_deterministic_on_replay(cp, nonce):
    tok = cp.login("alice", "alice-pw", "s1")
    for k in range(3):
        try:
            _auth(cp, tok, "s1", nonce, "deception:x" if k == 2 else "archive:a.vnx")
        except AccessDenied:
            pass
    events = [SecurityEvent.from_dict(e) for e in cp.audit.events() if e["kind"] in ("auth", "request", "integrity")]
    runs = []
    for _ in range(2):
        d = Detector()
        runs.append([f.to_dict() for ev in events for f in d.feed(ev)])
    assert runs[0] == runs[1] == [f.to_dict() for f in cp.findings]


def test_containment_state_serialises():
    c = ContainmentState()
    c.contain_session("s")
    c.quarantine("ab" * 32, {"resource": "r"})
    assert ContainmentState.from_dict(json.loads(json.dumps(c.to_dict()))).to_dict() == c.to_dict()


def test_forged_token_spray_is_detected(cp, nonce):
    for i in range(5):
        with pytest.raises(AccessDenied):
            cp.authorize(f"vnxt1.forged{i}.sig", "s-spray", nonce(), cp.clock(), "archive:a.vnx", "read")
    assert any(f.detector == "invalid_tokens" and f.subject == "s-spray" for f in cp.findings)
    assert "s-spray" in cp.cont.contained_sessions
