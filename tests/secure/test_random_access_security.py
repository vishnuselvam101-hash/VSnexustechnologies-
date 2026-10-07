"""VNX-DNA random access behind VNX-Secure: authorized, unauthorized, malformed, tampered, wrong key, truncated,
replayed, excessive rate. Every failure is explicit; no failure returns data."""
from __future__ import annotations

import hashlib

import pytest

from vnxdna.secure.control import AccessDenied, SecurityFailure
from vnxdna.secure.gate import SecureArchive

DATA = b"VNX-DNA synthetic report\n" * 400


def test_authorized_random_access(lab):
    t = lab.login("alice", "s-a")
    assert lab.vault.read_file(lab.req(t, "s-a"), "data/report.txt") == DATA
    assert len(lab.vault.read_chunk(lab.req(t, "s-a"), 0)) > 0
    assert lab.vault.verify(lab.req(t, "s-a"))
    assert lab.vault.locate(lab.req(t, "s-a"), "data/table.csv")["chunks"]


def test_unauthorized_random_access(lab):
    t = lab.login("mallory", "s-m")
    with pytest.raises(AccessDenied):
        lab.vault.read_chunk(lab.req(t, "s-m"), 0)


@pytest.mark.parametrize("index", [-1, 10_000, True, "0"])
def test_malformed_access_requests(lab, index):
    t = lab.login("alice", "s-a")
    with pytest.raises(SecurityFailure) as e:
        lab.vault.read_chunk(lab.req(t, "s-a"), index)
    assert e.value.code == "malformed"


def _tampered(lab, name, fn) -> SecureArchive:
    raw = bytearray(lab.archive.read_bytes())
    fn(raw)
    p = lab.root / name
    p.write_bytes(bytes(raw))
    return SecureArchive(lab.cp, p, key=lab.key)


@pytest.mark.parametrize("where", ["body", "index", "trailer", "header"])
def test_tampered_archive_fails_explicitly(lab, where):
    n = lab.archive.stat().st_size
    pos = {"body": 200, "index": n - 300, "trailer": n - 5, "header": 6}[where]
    arc = _tampered(lab, f"t-{where}.vnx", lambda r: r.__setitem__(pos, r[pos] ^ 0x01))
    t = lab.login("alice", "s-a")
    # random access checks what it reads: it either fails or returns exactly the original bytes, never wrong data
    try:
        assert arc.read_file(lab.req(t, "s-a"), "data/report.txt") == DATA
    except (SecurityFailure, AccessDenied):
        pass
    t2 = lab.login("bob", "s-b")
    with pytest.raises((SecurityFailure, AccessDenied)):      # a full verification always notices
        arc.verify(lab.req(t2, "s-b"))


def test_invalid_merkle_proof_is_refused(lab, monkeypatch):
    from vnxdna.archive import merkle
    monkeypatch.setattr(merkle, "verify_inclusion", lambda *a, **k: False)
    t = lab.login("alice", "s-a")
    with pytest.raises(SecurityFailure) as e:
        lab.vault.read_chunk(lab.req(t, "s-a"), 1)
    assert e.value.code == "integrity"


def test_wrong_key(lab):
    arc = SecureArchive(lab.cp, lab.archive, key=hashlib.sha256(b"wrong").digest())
    t = lab.login("alice", "s-a")
    with pytest.raises(SecurityFailure) as e:
        arc.read_file(lab.req(t, "s-a"), "data/report.txt")
    assert e.value.code == "key_mismatch"
    assert any(f.detector == "unexpected_key_usage" for f in lab.cp.findings)


@pytest.mark.parametrize("keep", [0, 10, 100, 0.5, 0.95])
def test_truncated_records(lab, keep):
    raw = lab.archive.read_bytes()
    n = int(len(raw) * keep) if isinstance(keep, float) else keep
    p = lab.root / f"trunc-{keep}.vnx"
    p.write_bytes(raw[:n])
    t = lab.login("alice", "s-a")
    with pytest.raises(SecurityFailure):
        SecureArchive(lab.cp, p, key=lab.key).read_file(lab.req(t, "s-a"), "data/report.txt")


def test_replayed_request(lab):
    t = lab.login("alice", "s-a")
    r = lab.req(t, "s-a")
    lab.vault.list(r)
    with pytest.raises(AccessDenied) as e:
        lab.vault.read_file(r, "data/report.txt")
    assert e.value.code == "replay"
    with pytest.raises(AccessDenied):          # the session is contained afterwards
        lab.vault.list(lab.req(t, "s-a"))


def test_excessive_request_rate(lab):
    lab.cp.rate_per_min = 20
    t = lab.login("alice", "s-a")
    codes = []
    for _ in range(25):
        try:
            lab.vault.list(lab.req(t, "s-a"))
            codes.append("ok")
        except AccessDenied as e:
            codes.append(e.code)
    assert codes[:20] == ["ok"] * 20 and set(codes[20:]) == {"rate_limited"}


def test_quarantined_bytes_are_refused_under_any_name(lab):
    bad = lab.root / "junk.vnx"
    bad.write_bytes(b"\x00" * 300)
    t = lab.login("alice", "s-a")
    for _ in range(3):
        with pytest.raises((SecurityFailure, AccessDenied)):
            SecureArchive(lab.cp, bad, key=lab.key, resource="archive:upload").list(lab.req(t, "s-a"))
    assert hashlib.sha256(bad.read_bytes()).hexdigest() in lab.cp.cont.quarantined
    other = lab.root / "renamed.vnx"
    other.write_bytes(bad.read_bytes())
    t2 = lab.login("bob", "s-b")
    with pytest.raises(AccessDenied) as e:
        SecureArchive(lab.cp, other, key=lab.key).list(lab.req(t2, "s-b"))
    assert e.value.code == "quarantined"


def test_security_overhead_is_measured(lab):
    from vnxdna.secure.simulator import overhead
    o = overhead(lab, reps=3)
    assert o["read_file_gated_ms"] > 0 and o["reps"] == 3
