"""Safe local attack simulator and security benchmark (VNX-Secure; docs/VNX_SECURE_BENCHMARKS.md). SIMULATED.

Every scenario runs in a fresh in-memory control plane with a deterministic clock, against a real VNX-DNA archive and
a small "component" directory created in a private temporary directory. Attacks are synthetic requests made through
the same API a client would use; nothing opens a network connection or touches another system. For each scenario the
simulator verifies: detection, containment, credential revocation (where the playbook revokes), evidence preservation,
recovery, and the final integrity state; it also runs a benign workload to count false positives.

Latencies are wall-clock on this host for processing the triggering request (detection, scoring and containment run
synchronously inside it), plus the number of attack events and simulated seconds before detection.
"""
from __future__ import annotations

import hashlib
import os
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from ..archive import operations as ops
from .control import AccessDenied, ControlPlane, SecurityFailure
from .events import ManualClock, canonical
from .gate import SecureArchive

SCRYPT_N = 1 << 10          # simulator principals only: fast hashing; production default is 2^14
LABEL = "SIMULATED: synthetic attacks against a local, in-memory control plane; not a real-world security guarantee"


class Lab:
    def __init__(self, root: Path):
        self.root, self.clock, self.n = root, ManualClock(), 0
        comp = root / "component"
        comp.mkdir()
        for i in range(4):
            (comp / f"module{i}.py").write_text(f"# component {i}\nVALUE = {i}\n")
        self.component = comp
        data = root / "data"
        data.mkdir()
        (data / "report.txt").write_bytes(b"VNX-DNA synthetic report\n" * 400)
        (data / "table.csv").write_bytes(b"".join(f"{i},{i * i}\n".encode() for i in range(3000)))
        self.key = hashlib.sha256(b"vnx-secure simulator archive key").digest()
        self.archive = root / "vault.vnx"
        ops.build_archive([data], self.archive, ops.ArchiveOptions(key=self.key, chunk_size=4096), archive_id=bytes(16), salt=bytes(16))
        self.cp = ControlPlane(None, clock=self.clock, master_key=hashlib.sha256(b"vnx-secure simulator master").digest(),
                               integrity_roots=[comp])
        for name, roles in (("alice", ("reader",)), ("bob", ("writer",)), ("ops", ("admin",)), ("mallory", ("guest",))):
            self.cp.add_principal(name, f"{name}-correct-horse", roles, scrypt_n=SCRYPT_N)
        self.cp.check_integrity()
        self.vault = SecureArchive(self.cp, self.archive, key=self.key)

    def req(self, token: str, session: str, nonce: str | None = None) -> dict:
        self.n += 1
        return {"token": token, "session": session, "nonce": nonce or hashlib.sha256(f"nonce{self.n}".encode()).hexdigest()[:32],
                "ts": self.clock()}

    def login(self, name: str, session: str) -> str:
        return self.cp.login(name, f"{name}-correct-horse", session)


def _attempt(fn):
    t0 = time.perf_counter()
    try:
        fn()
        out = "allow"
    except (AccessDenied, SecurityFailure) as e:
        out = e.code
    return out, (time.perf_counter() - t0) * 1e3


class Probe:
    """Tracks one scenario's attack events until the first finding and its containment."""

    def __init__(self, lab: Lab):
        self.lab, self.events, self.t0 = lab, 0, lab.clock()
        self.detect_ms = self.detect_events = self.detect_sim_s = None
        self.contain_ms = None

    def hit(self, fn, dt: float = 1.0):
        before_f, before_a = len(self.lab.cp.findings), self.lab.cp.audit.anchor[0]
        out, ms = _attempt(fn)
        self.events += 1
        self.lab.clock.advance(dt)
        if self.detect_ms is None and len(self.lab.cp.findings) > before_f:
            self.detect_ms, self.detect_events, self.detect_sim_s = ms, self.events, self.lab.clock() - self.t0 - dt
            contained = any(e["kind"] == "containment" for e in self.lab.cp.audit.events()[before_a:])
            self.contain_ms = ms if contained else None
        return out


def _finish(lab: Lab, name: str, probe: Probe, *, revoked: bool | None, contained: bool, extra: dict | None = None,
            archives: list | None = None, before_recover=None) -> dict:
    cp = lab.cp
    evidence = len(cp.evidence_mem) > 0 and all(hashlib.sha256(canonical(b)).hexdigest() == h for h, b in cp.evidence_mem)
    if before_recover:
        before_recover()
    t0 = time.perf_counter()
    rec = cp.recover(archives=archives, key=lab.key)
    recovery_ms = (time.perf_counter() - t0) * 1e3
    final = cp.check_integrity()
    audit = cp.audit.verify()
    ok = bool(probe.detect_ms is not None and contained and revoked is not False and evidence and rec["result"] == "VERIFIED"
              and final["status"] == "VERIFIED" and audit.status == "VERIFIED")
    return {"scenario": name, "pass": ok, "detected": probe.detect_ms is not None, "detector": _first_finding(cp),
            "events_to_detect": probe.detect_events, "sim_seconds_to_detect": probe.detect_sim_s,
            "detect_ms": _r(probe.detect_ms), "contain_ms": _r(probe.contain_ms), "contained": contained, "revoked": revoked,
            "evidence_preserved": evidence, "recovery": rec["result"], "recovery_ms": _r(recovery_ms),
            "final_integrity": final["status"], "audit_chain": audit.status, "audit_records": audit.count, **(extra or {})}


def _first_finding(cp) -> str | None:
    for e in cp.audit.events():
        if e["kind"] == "finding":
            return e["detector"]
    return None


def _r(x):
    return None if x is None else round(x, 3)


def _revoked(lab: Lab, token: str, session: str) -> bool:
    try:
        lab.cp.authorize(token, session, lab.req(token, session)["nonce"], lab.clock(), "archive:vault.vnx", "list")
        return False
    except AccessDenied as e:
        return e.code in ("token_revoked", "session_revoked", "principal_revoked", "bad_signature")


# ------------------------------------------------------------------------------------------------ scenarios
def s01_bruteforce(lab: Lab) -> dict:
    victim = lab.login("alice", "s-alice")
    p = Probe(lab)
    for i in range(6):
        p.hit(lambda i=i: lab.cp.login("alice", f"guess-{i}", "s-attacker"))
    locked = _attempt(lambda: lab.login("alice", "s-attacker2"))[0] == "login_locked"
    return _finish(lab, "credential brute-force", p, revoked=_revoked(lab, victim, "s-alice"), contained=locked,
                   extra={"login_locked_after": locked})


def s02_unauthorized_archive(lab: Lab) -> dict:
    t = lab.login("mallory", "s-m")
    p = Probe(lab)
    outs = [p.hit(lambda: lab.vault.read_file(lab.req(t, "s-m"), "data/report.txt")) for _ in range(6)]
    return _finish(lab, "unauthorized archive access", p, revoked=_revoked(lab, t, "s-m"), contained="s-m" in lab.cp.cont.contained_sessions,
                   extra={"data_returned": "allow" in outs})


def s03_privilege_escalation(lab: Lab) -> dict:
    t = lab.login("alice", "s-a")
    p = Probe(lab)
    p.hit(lambda: lab.cp.authorize(t, "s-a", lab.req(t, "s-a")["nonce"], lab.clock(), "system", "admin"))
    return _finish(lab, "privilege escalation", p, revoked=_revoked(lab, t, "s-a"), contained="s-a" in lab.cp.cont.contained_sessions)


def s04_malformed_input(lab: Lab) -> dict:
    bad = lab.root / "evil.vnx"
    bad.write_bytes(b"VNX4" + hashlib.sha256(b"junk").digest() * 64)
    evil = SecureArchive(lab.cp, bad, key=lab.key, resource="archive:vault.vnx")
    t = lab.login("alice", "s-a")
    p = Probe(lab)
    outs = [p.hit(lambda: evil.read_file(lab.req(t, "s-a"), "data/report.txt")) for _ in range(3)]
    sha = hashlib.sha256(bad.read_bytes()).hexdigest()
    q = sha in lab.cp.cont.quarantined
    copy = lab.root / "evil-renamed.vnx"
    copy.write_bytes(bad.read_bytes())          # the same bytes under another name are still refused
    t2 = lab.login("bob", "s-b")
    after = _attempt(lambda: SecureArchive(lab.cp, copy, key=lab.key).list(lab.req(t2, "s-b")))[0]
    return _finish(lab, "malformed input", p, revoked=_revoked(lab, t, "s-a"), contained=q and after == "quarantined",
                   extra={"outcomes": outs, "quarantined": q, "later_access": after})


def s05_replay(lab: Lab) -> dict:
    t = lab.login("alice", "s-a")
    r = lab.req(t, "s-a")
    first = _attempt(lambda: lab.vault.list(r))[0]
    p = Probe(lab)
    p.hit(lambda: lab.vault.list(dict(r, ts=lab.clock())), dt=0.5)
    return _finish(lab, "replay", p, revoked=_revoked(lab, t, "s-a"), contained="s-a" in lab.cp.cont.contained_sessions,
                   extra={"original_request": first})


def s06_token_misuse(lab: Lab) -> dict:
    stolen = lab.login("alice", "s-a")
    p = Probe(lab)
    p.hit(lambda: lab.vault.read_file(lab.req(stolen, "s-x"), "data/report.txt"))
    return _finish(lab, "token misuse", p, revoked=_revoked(lab, stolen, "s-a"), contained="s-x" in lab.cp.cont.contained_sessions)


def s07_integrity_tamper(lab: Lab) -> dict:
    target = lab.component / "module2.py"
    good = target.read_bytes()
    target.write_bytes(good + b"import os; os.system('true')  # injected\n")
    p = Probe(lab)
    p.hit(lambda: lab.cp.check_integrity())
    isolated = lab.cp.sm.state == "ISOLATED"
    t = lab.login("alice", "s-a")
    blocked = _attempt(lambda: lab.vault.list(lab.req(t, "s-a")))[0]
    early = lab.cp.recover(key=lab.key)["result"]          # must not verify while the component is still modified
    return _finish(lab, "integrity tampering", p, revoked=None, contained=isolated and blocked != "allow" and early == "FAILED",
                   before_recover=lambda: target.write_bytes(good),
                   extra={"isolated": isolated, "request_while_isolated": blocked, "recover_before_restore": early})


def s08_request_rate(lab: Lab) -> dict:
    t = lab.login("alice", "s-a")
    p = Probe(lab)
    outs = [p.hit(lambda: lab.vault.list(lab.req(t, "s-a")), dt=0.2) for _ in range(140)]
    limited = outs.count("rate_limited")
    return _finish(lab, "abnormal request rate", p, revoked=None, contained=limited > 0, extra={"rate_limited_requests": limited})


def s09_malicious_metadata(lab: Lab) -> dict:
    bad = lab.root / "vault-meta.vnx"
    raw = bytearray(lab.archive.read_bytes())
    raw[len(raw) // 2] ^= 0x40
    raw[-40] ^= 0x01
    bad.write_bytes(bytes(raw))
    evil = SecureArchive(lab.cp, bad, key=lab.key)
    t = lab.login("alice", "s-a")
    p = Probe(lab)
    outs = [p.hit(lambda: evil.read_file(lab.req(t, "s-a"), "data/report.txt")) for _ in range(3)]
    res = _finish(lab, "malicious archive metadata", p, revoked=_revoked(lab, t, "s-a"),
                  contained=evil.resource in lab.cp.cont.locked_resources, archives=[bad],
                  extra={"outcomes": outs, "data_returned": "allow" in outs})
    res["archive_still_locked_after_recovery"] = evil.resource in lab.cp.cont.locked_resources
    return res


def s10_compromised_session(lab: Lab) -> dict:
    t = lab.login("alice", "s-a")
    lab.vault.list(lab.req(t, "s-a"))
    p = Probe(lab)
    p.hit(lambda: lab.cp.authorize(t, "s-a", lab.req(t, "s-a")["nonce"], lab.clock(), "deception:finance-2026-keys.vnx", "read"))
    relogin = _attempt(lambda: lab.login("alice", "s-new"))[0]
    return _finish(lab, "compromised session (deception hit)", p, revoked=_revoked(lab, t, "s-a"),
                   contained="s-a" in lab.cp.cont.isolated_workloads and relogin == "login_locked",
                   extra={"workload_isolated": "s-a" in lab.cp.cont.isolated_workloads, "relogin": relogin,
                          "network_isolation_requests": len(lab.cp.isolator.requests)})


SCENARIOS = (s01_bruteforce, s02_unauthorized_archive, s03_privilege_escalation, s04_malformed_input, s05_replay,
             s06_token_misuse, s07_integrity_tamper, s08_request_rate, s09_malicious_metadata, s10_compromised_session)


def benign(lab: Lab, minutes: int = 30) -> dict:
    """Legitimate use: three users, normal operations and occasional honest mistakes (one wrong password, one missing file)."""
    toks = {u: lab.login(u, f"s-{u}") for u in ("alice", "bob", "ops")}
    n = denied = 0
    lab.clock.advance(1)
    _attempt(lambda: lab.cp.login("bob", "typo", "s-bob2"))
    for minute in range(minutes):
        for u in ("alice", "bob"):
            for op in ("list", "verify", "read", "chunk"):
                r = lab.req(toks[u], f"s-{u}")
                fn = {"list": lambda: lab.vault.list(r), "verify": lambda: lab.vault.verify(r),
                      "read": lambda: lab.vault.read_file(r, "data/table.csv"), "chunk": lambda: lab.vault.read_chunk(r, minute % 3)}[op]
                out, _ = _attempt(fn)
                n += 1
                denied += out != "allow"
                lab.clock.advance(5)
        r = lab.req(toks["ops"], "s-ops")
        out, _ = _attempt(lambda: lab.cp.authorize(toks["ops"], "s-ops", r["nonce"], r["ts"], "system", "admin"))
        n += 1
        denied += out != "allow"
        if minute % 14 == 13:
            for u in ("alice", "bob", "ops"):
                toks[u] = lab.login(u, f"s-{u}")
        lab.clock.advance(15)
    findings = len(lab.cp.findings)
    return {"requests": n, "denied": denied, "findings": findings, "false_positive_rate": findings / n, "state": lab.cp.sm.state}


def overhead(lab: Lab, reps: int = 30) -> dict:
    t = lab.login("alice", "s-bench")
    direct, gated, cdirect, cgated = [], [], [], []
    from ..archive import container as ct
    for i in range(reps):
        t0 = time.perf_counter()
        c = ct.open_container(lab.archive, key=lab.key, require_key=True)
        b"".join(ops.iter_file(c, c.file("data/table.csv")))
        direct.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        lab.vault.read_file(lab.req(t, "s-bench"), "data/table.csv")
        gated.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        c = ct.open_container(lab.archive, key=lab.key, require_key=True)
        ops.read_chunk(c, i % 3)
        cdirect.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        lab.vault.read_chunk(lab.req(t, "s-bench"), i % 3)
        cgated.append(time.perf_counter() - t0)
        lab.clock.advance(1)
    md, mg, cd, cg = (statistics.median(x) * 1e3 for x in (direct, gated, cdirect, cgated))
    return {"read_file_direct_ms": round(md, 3), "read_file_gated_ms": round(mg, 3), "read_file_overhead_ms": round(mg - md, 3),
            "read_chunk_direct_ms": round(cd, 3), "read_chunk_gated_ms": round(cg, 3), "read_chunk_overhead_ms": round(cg - cd, 3),
            "archive_bytes": lab.archive.stat().st_size, "reps": reps,
            "audit_bytes_per_request": round(sum(len(x) + 1 for x in lab.cp.audit.lines) / max(1, lab.cp.audit.count), 1)}


def run(scenarios=SCENARIOS, *, with_benign: bool = True, with_overhead: bool = True) -> dict:
    out = {"label": LABEL, "scenarios": [], "load1": round(os.getloadavg()[0], 2)}
    tracemalloc.start()
    for s in scenarios:
        with tempfile.TemporaryDirectory(prefix="vnx-secure-sim-") as d:
            out["scenarios"].append(s(Lab(Path(d))))
    if with_benign:
        with tempfile.TemporaryDirectory(prefix="vnx-secure-sim-") as d:
            out["benign"] = benign(Lab(Path(d)))
    if with_overhead:
        with tempfile.TemporaryDirectory(prefix="vnx-secure-sim-") as d:
            out["overhead"] = overhead(Lab(Path(d)))
    out["peak_python_heap_bytes"] = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    sc = out["scenarios"]
    out["summary"] = {"scenarios": len(sc), "passed": sum(s["pass"] for s in sc), "detected": sum(s["detected"] for s in sc),
                      "missed_attack_rate": 1 - sum(s["detected"] for s in sc) / max(1, len(sc)),
                      "detect_ms_median": _r(statistics.median([s["detect_ms"] for s in sc if s["detect_ms"] is not None] or [0])),
                      "contain_ms_median": _r(statistics.median([s["contain_ms"] for s in sc if s["contain_ms"] is not None] or [0])),
                      "recovery_ms_median": _r(statistics.median([s["recovery_ms"] for s in sc])),
                      "false_positive_rate": out.get("benign", {}).get("false_positive_rate")}
    return out
