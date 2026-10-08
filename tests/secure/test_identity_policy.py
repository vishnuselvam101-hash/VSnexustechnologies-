"""Identity, tokens, replay protection and the deny-by-default policy."""
from __future__ import annotations

import pytest

from vnxdna.secure.events import ManualClock
from vnxdna.secure.identity import AuthError, Principal, ReplayGuard, TokenAuthority, check_secret, hash_secret
from vnxdna.secure.policy import DEFAULT_POLICY, Policy, PolicyError

KEY = b"t" * 32


def test_secret_hashing():
    rec = hash_secret("pw", n=1 << 10)
    assert check_secret("pw", rec) and not check_secret("pW", rec)
    assert not check_secret("pw", {**rec, "n": 2})
    assert not check_secret("pw", {"alg": "md5"})
    with pytest.raises(ValueError):
        hash_secret("pw", n=1000)


def test_token_lifecycle():
    clk = ManualClock()
    ta = TokenAuthority(KEY, clk, ttl=60)
    tok, c = ta.issue(Principal("alice", ("reader",)), "s1")
    assert ta.validate(tok) == c
    clk.advance(61)
    with pytest.raises(AuthError) as e:
        ta.validate(tok)
    assert e.value.reason == "expired"


@pytest.mark.parametrize("how,reason", [("token", "token_revoked"), ("session", "session_revoked"), ("principal", "principal_revoked")])
def test_revocation(how, reason):
    clk = ManualClock()
    ta = TokenAuthority(KEY, clk)
    tok, c = ta.issue(Principal("alice", ("reader",)), "s1")
    {"token": lambda: ta.revoke_token(c.tid), "session": lambda: ta.revoke_session("s1"),
     "principal": lambda: ta.revoke_principal("alice")}[how]()
    assert {"token": lambda: ta.revoke_token(c.tid), "session": lambda: ta.revoke_session("s1"),
            "principal": lambda: ta.revoke_principal("alice")}[how]() is False      # idempotent
    with pytest.raises(AuthError) as e:
        ta.validate(tok)
    assert e.value.reason == reason


def test_principal_revocation_spares_later_tokens():
    clk = ManualClock()
    ta = TokenAuthority(KEY, clk)
    ta.issue(Principal("alice"), "s1")
    ta.revoke_principal("alice")
    clk.advance(1)
    tok, _ = ta.issue(Principal("alice"), "s2")
    ta.validate(tok)


@pytest.mark.parametrize("bad", ["", "vnxt1.", "vnxt1.a.b.c", "vnxt1.!!!.abc", "x" * 5000, "vnxt2.abc.def"])
def test_malformed_tokens(bad):
    with pytest.raises(AuthError):
        TokenAuthority(KEY, ManualClock()).validate(bad)


def test_forged_and_cross_key_tokens():
    clk = ManualClock()
    tok, _ = TokenAuthority(KEY, clk).issue(Principal("alice", ("reader",)), "s1")
    with pytest.raises(AuthError) as e:
        TokenAuthority(b"u" * 32, clk).validate(tok)
    assert e.value.reason == "bad_signature"
    head, payload, mac = tok.split(".")
    import base64
    import json
    d = json.loads(base64.urlsafe_b64decode(payload + "=="))
    d["caps"] = ["admin"]
    forged = f"{head}.{base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()}.{mac}"
    with pytest.raises(AuthError):
        TokenAuthority(KEY, clk).validate(forged)


def test_replay_guard():
    clk = ManualClock()
    g = ReplayGuard(clk, window=600, skew=120)
    g.check("n" * 16, clk())
    for nonce, ts, reason in (("n" * 16, clk(), "replay"), ("m" * 16, clk() - 500, "stale_request"), ("short", clk(), "malformed_nonce")):
        with pytest.raises(AuthError) as e:
            g.check(nonce, ts)
        assert e.value.reason == reason
    with pytest.raises(ValueError):
        ReplayGuard(clk, window=100, skew=120)


def _eval(p, **kw):
    base = dict(roles=("reader",), resource="archive:a.vnx", operation="read", integrity="VERIFIED", risk="LOW", state="TRUSTED")
    return p.evaluate(**{**base, **kw})


def test_default_policy_is_deny_by_default():
    p = Policy.from_dict(DEFAULT_POLICY)
    assert _eval(p).allowed
    assert not _eval(p, roles=("guest",)).allowed
    assert not _eval(p, operation="write").allowed
    assert not _eval(p, resource="deception:x").allowed
    assert not _eval(p, roles=("admin",), resource="deception:x", operation="admin").allowed      # explicit deny wins
    assert not _eval(p, integrity="MODIFIED").allowed
    assert not _eval(p, risk="HIGH").allowed
    assert not _eval(p, state="ISOLATED").allowed
    assert not _eval(p, state="NOPE").allowed
    assert _eval(p, roles=("admin",), resource="system", operation="recover", integrity="FAILED", state="ISOLATED").allowed
    assert not _eval(Policy([])).allowed


@pytest.mark.parametrize("bad", [
    [], {"version": 2, "rules": []}, {"version": 1, "rules": {}}, {"version": 1, "rules": [{"id": "x"}]},
    {"version": 1, "rules": [{"id": "x", "effect": "maybe", "roles": ["r"], "resources": ["*"], "operations": ["read"]}]},
    {"version": 1, "rules": [{"id": "x", "effect": "allow", "roles": [], "resources": ["*"], "operations": ["read"]}]},
    {"version": 1, "rules": [{"id": "x", "effect": "allow", "roles": ["r"], "resources": ["*"], "operations": ["fly"]}]},
    {"version": 1, "rules": [{"id": "x", "effect": "allow", "roles": ["r"], "resources": ["*"], "operations": ["read"], "extra": 1}]},
    {"version": 1, "rules": [{"id": "x", "effect": "allow", "roles": ["r"], "resources": ["*"], "operations": ["read"], "max_risk": "X"}]},
    {"version": 1, "rules": [{"id": "x", "effect": "allow", "roles": ["r"], "resources": ["*"], "operations": ["read"]}] * 2},
])
def test_policy_validation(bad):
    with pytest.raises(PolicyError):
        Policy.from_dict(bad)


def test_policy_round_trip_and_bad_json():
    p = Policy.from_dict(DEFAULT_POLICY)
    assert Policy.from_dict(p.to_dict()).to_dict() == p.to_dict()
    with pytest.raises(PolicyError):
        Policy.from_json(b"{not json")
