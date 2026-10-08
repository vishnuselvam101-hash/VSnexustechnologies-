"""Fuzzing (hypothesis) of every VNX-Secure input parser: policies, tokens, audit lines, event records, archives behind
the gate. Property: malformed input is rejected with the documented exception type; nothing else escapes, and nothing
malformed is accepted as valid."""
from __future__ import annotations

import json
import os

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vnxdna.secure.audit import verify_lines
from vnxdna.secure.control import AccessDenied, SecurityFailure
from vnxdna.secure.detect import Detector
from vnxdna.secure.events import ManualClock, SecurityEvent
from vnxdna.secure.gate import SecureArchive
from vnxdna.secure.identity import AuthError, Principal, TokenAuthority
from vnxdna.secure.policy import Policy, PolicyError

json_values = st.recursive(st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=20),
                           lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=10), c, max_size=4), max_leaves=20)
N = int(os.environ.get("VNX_SECURE_FUZZ_EXAMPLES", "300"))     # a campaign sets e.g. 20000
FUZZ = settings(max_examples=N, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


@FUZZ
@given(st.binary(max_size=400) | json_values.map(lambda v: json.dumps(v).encode()))
def test_policy_parser(data):
    try:
        p = Policy.from_json(data)
    except PolicyError:
        return
    assert Policy.from_dict(p.to_dict()).to_dict() == p.to_dict()


@FUZZ
@given(st.text(max_size=300))
def test_token_parser(s):
    ta = TokenAuthority(b"k" * 32, ManualClock())
    try:
        ta.validate("vnxt1." + s)
    except AuthError:
        return
    raise AssertionError("a random token validated")


@FUZZ
@given(st.binary(max_size=200), st.integers(0, 200))
def test_token_bitflips(junk, pos):
    clk = ManualClock()
    ta = TokenAuthority(b"k" * 32, clk)
    tok, _ = ta.issue(Principal("alice", ("reader",)), "s1")
    b = bytearray(tok.encode())
    b[pos % len(b)] ^= 1 + (junk[0] % 255 if junk else 0)
    try:
        mutated = b.decode()
    except UnicodeDecodeError:
        return
    if mutated == tok:
        return
    try:
        ta.validate(mutated)
    except AuthError:
        return
    # a flip in base64 padding-free tail bits can decode to the same bytes; then the claims must be unchanged
    assert ta.validate(mutated) == ta.validate(tok)


@FUZZ
@given(st.lists(st.binary(max_size=200) | json_values.map(lambda v: json.dumps(v).encode()), max_size=5))
def test_audit_line_parser(lines):
    v = verify_lines(lines, b"k" * 32)
    assert v.status in ("VERIFIED", "FAILED")
    if lines:
        assert v.status == "FAILED"


events = st.builds(lambda kind, result, code, ses, pr, res, op, dt: (kind, result, code, ses, pr, res, op, dt),
                   st.sampled_from(["auth", "request", "integrity", "config"]), st.sampled_from(["allow", "deny", "error", "observed"]),
                   st.sampled_from(["ok", "bad_secret", "policy", "capability", "replay", "malformed", "integrity", "key_mismatch",
                                    "session_mismatch", "token_revoked", "rate_limited", ""]),
                   st.sampled_from(["", "s1", "s2"]), st.sampled_from(["", "alice", "bob"]),
                   st.sampled_from(["archive:a", "deception:x", "system", ""]), st.sampled_from(["read", "list", "admin", "login"]),
                   st.floats(0, 120))


@FUZZ
@given(st.lists(events, max_size=60))
def test_event_processing_is_total_and_deterministic(evs):
    def run():
        d, t, out = Detector(), 0.0, []
        for i, (kind, result, code, ses, pr, res, op, dt) in enumerate(evs):
            t += dt
            ev = SecurityEvent(f"e{i}", t, ses, pr, res, op, result, evidence={"code": code}, kind=kind)
            out += [f.to_dict() for f in d.feed(ev)]
        return out
    a = run()
    assert a == run()
    assert all(f["severity"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL") for f in a)


@settings(max_examples=max(60, N // 50), deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.integers(0, 10**9), st.integers(1, 255), st.sampled_from(["list", "verify", "read"]))
def test_archive_mutations_never_return_wrong_data(lab, pos, xor, op):
    raw = bytearray(lab.archive.read_bytes())
    raw[pos % len(raw)] ^= xor
    p = lab.root / "m.vnx"
    p.write_bytes(bytes(raw))
    lab.clock.advance(3600)           # a fresh rate/risk/lock window per example
    lab.n += 1
    ses = f"s{pos}-{lab.n}"
    t = lab.login("ops" if op != "read" else "alice", ses)
    arc = SecureArchive(lab.cp, p, key=lab.key, resource=f"archive:m{pos}")
    try:
        r = {"list": lambda: arc.list(lab.req(t, ses)), "verify": lambda: arc.verify(lab.req(t, ses)),
             "read": lambda: arc.read_file(lab.req(t, ses), "data/report.txt")}[op]()
    except (SecurityFailure, AccessDenied):
        return
    if op == "read":
        assert r == b"VNX-DNA synthetic report\n" * 400
