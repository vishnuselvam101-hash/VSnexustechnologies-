"""Attack simulator (SIMULATED), the `vnx security` CLI, crypto registry, deception and the VNX-RAM / VNX-Q interfaces."""
from __future__ import annotations

import json
import os
import socket

import pytest
from typer.testing import CliRunner

from vnxdna.commands.cli import app
from vnxdna.secure import simulator
from vnxdna.secure.crypto_agility import AES256GCM, aead, registry
from vnxdna.secure.deception import DeceptionEnvironment
from vnxdna.secure.vnxq import NotAvailable
from vnxdna.secure.vnxram import MemoryAccessDenied, SoftwareMemoryGuard


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("VNX-Secure tests must not open network connections")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_every_scenario_detects_contains_and_recovers(no_network):
    r = simulator.run(with_benign=True, with_overhead=False)
    assert r["label"].startswith("SIMULATED")
    for s in r["scenarios"]:
        assert s["pass"], s
        assert s.get("data_returned") in (None, False)
    assert r["summary"]["passed"] == r["summary"]["scenarios"] == 10
    assert r["benign"]["findings"] == 0 and r["benign"]["denied"] == 0


def test_simulator_is_reproducible():
    keys = ("scenario", "pass", "detector", "events_to_detect", "sim_seconds_to_detect", "audit_records", "recovery")
    a = simulator.run(simulator.SCENARIOS[:4], with_benign=False, with_overhead=False)
    b = simulator.run(simulator.SCENARIOS[:4], with_benign=False, with_overhead=False)
    assert [{k: s[k] for k in keys} for s in a["scenarios"]] == [{k: s[k] for k in keys} for s in b["scenarios"]]


def test_cli_lifecycle(tmp_path, monkeypatch):
    run = CliRunner().invoke
    h = str(tmp_path / "h")
    monkeypatch.setenv("ADM", "admin-secret")
    r = run(app, ["security", "init", "--home", h, "--admin", "ops", "--admin-secret-env", "ADM"])
    assert r.exit_code == 0, r.output
    assert os.stat(tmp_path / "h" / "secure.key").st_mode & 0o777 == 0o600
    for cmd in (["status"], ["audit", "verify"], ["integrity"], ["sessions"], ["policy"], ["crypto"], ["audit", "show"]):
        r = run(app, ["security", *cmd, "--home", h] if cmd != ["crypto"] else ["security", "crypto"])
        assert r.exit_code == 0, (cmd, r.output)
    log = tmp_path / "h" / "audit.log"
    log.write_bytes(log.read_bytes().replace(b'"ops"', b'"eve"', 1))
    assert run(app, ["security", "audit", "verify", "--home", h]).exit_code == 1
    assert run(app, ["security", "status", "--home", h]).exit_code == 1          # ISOLATED
    r = run(app, ["security", "recover", "--home", h])
    assert r.exit_code == 0 and json.loads(r.output)["result"] == "VERIFIED"
    assert run(app, ["security", "status", "--home", h]).exit_code == 0
    assert run(app, ["security", "status", "--home", str(tmp_path / "none")]).exit_code == 3
    bad = tmp_path / "p.json"
    bad.write_text('{"version": 1, "rules": [{"id": "x"}]}')
    assert run(app, ["security", "policy", "--check", str(bad)]).exit_code == 3


def test_crypto_registry_is_honest():
    reg = registry()
    assert {a["status"] for a in reg} <= {"IMPLEMENTED", "PLANNED", "EXPERIMENTAL"}
    assert all(a["source"] == "none in this build" for a in reg if a["status"] == "PLANNED")
    k, n = b"k" * 32, b"n" * 12
    assert aead("AES-256-GCM").open(k, n, AES256GCM().seal(k, n, b"data", b"ad"), b"ad") == b"data"
    with pytest.raises(NotImplementedError):
        aead("ML-KEM-768 (FIPS 203)")


def test_deception_is_names_only():
    d = DeceptionEnvironment()
    assert all(x.startswith("deception:") for x in d.listing(["archive:a"]) if x != "archive:a")
    assert d.synthetic_content("x").startswith(b"VNX-SECURE SYNTHETIC DECOY")
    with pytest.raises(ValueError):
        DeceptionEnvironment(("archive:real",))


def test_vnxram_reference_contract():
    g = SoftwareMemoryGuard(b"m" * 32)
    g.secure_memory_region("r", 64, "alice")
    g.write("r", "alice", 0, b"hello")
    assert g.read("r", "alice", 0, 5) == b"hello"
    with pytest.raises(MemoryAccessDenied):
        g.read("r", "bob", 0, 5)
    g.grant("r", "bob", {"read"})
    assert g.read("r", "bob", 0, 5) == b"hello"
    g.revoke_memory_access("r", "bob")
    with pytest.raises(MemoryAccessDenied):
        g.read("r", "bob", 0, 5)
    g.regions["r"][0] ^= 1                      # tamper behind the guard's back
    assert g.verify_memory_integrity("r") == "FAILED"
    with pytest.raises(MemoryAccessDenied):
        g.read("r", "alice", 0, 5)
    g.quarantine_memory_region("r", "tamper")
    assert not g.authorize_memory_access("r", "alice", "read")
    with pytest.raises(MemoryAccessDenied):
        g.write("r", "alice", 60, b"overflow")


def test_vnxq_is_architectural_only():
    with pytest.raises(NotImplementedError):
        NotAvailable().authorize_qpu_job("alice", {})
