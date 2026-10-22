"""AP-17: the VNX-DNA core must work with no network at all.

Two guards live here:

* a runtime guard - the socket API is blocked (``socket.socket.connect``,
  ``socket.create_connection`` and ``socket.getaddrinfo`` all raise) while the
  container round trip, the stream round trip, the conformance vectors and the
  security monitor tick run;
* a static guard - an AST scan of ``src/vnxdna`` rejects the network-capable
  modules, and every ``socket`` import has to be listed with a reason in
  ``tests/v15/ap17_network_allowlist.txt``.

``tools/offline-check.sh`` runs the suite inside a network namespace that has no
route off the machine.
"""
from __future__ import annotations

import ast
import os
import socket
import subprocess
from pathlib import Path

import pytest

from vnxdna import api
from vnxdna.channel import ChannelConfig
from vnxdna.pipeline import stream as stream_module
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.pipeline.stream import StreamOptions, decode_stream, encode_stream
from vnxdna.secure.audit import AuditLog
from vnxdna.secure.events import ManualClock, SecurityEvent
from vnxdna.secure.monitor import HmacSigner, Monitor, Policy, build_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "vnxdna"
ALLOWLIST_PATH = Path(__file__).with_name("ap17_network_allowlist.txt")
OFFLINE_CHECK = REPO_ROOT / "tools" / "offline-check.sh"

# The modules the library must never pull in: each one can reach the network.
NETWORK_MODULES = ("urllib.request", "http.client", "requests", "httpx", "aiohttp", "ftplib", "smtplib")

# Obviously fake keys, used only to exercise the authenticated paths.
SIGNING_KEY = b"k" * 32
AUDIT_KEY = b"a" * 32

PAYLOAD = bytes(range(256)) * 16          # 4 KiB, deterministic


class OfflineViolation(OSError):
    """Raised by the offline guard when something tries to reach the network."""


# ------------------------------------------------------------------ the guard

@pytest.fixture(autouse=True)
def offline_guard(monkeypatch):
    """Block every outbound socket call and prove that the block is in force."""
    def _blocked(*args, **kwargs):
        raise OfflineViolation("outbound network access is not allowed in the AP-17 tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)

    with pytest.raises(OfflineViolation):
        socket.getaddrinfo("vnx-dna.invalid", 80)
    with pytest.raises(OfflineViolation):
        socket.create_connection(("vnx-dna.invalid", 80))
    yield


# ------------------------------------------------------------------ the scanner

def _imported_modules(path: Path) -> set[str]:
    """Every module named by an import statement in ``path`` (dotted names kept whole)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # a relative import names no top-level module
                continue
            module = node.module or ""
            names.add(module)
            for alias in node.names:
                # "from urllib import request" imports urllib.request
                names.add(f"{module}.{alias.name}")
    return names


def _imports(names: set[str], module: str) -> bool:
    """True when ``names`` pulls in ``module`` (or something inside it)."""
    return any(name == module or name.startswith(f"{module}.") for name in names)


def _imports_socket(names: set[str]) -> bool:
    return _imports(names, "socket")


def _library_socket_users() -> set[str]:
    """The files under src/ that import the socket module."""
    users = set()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if _imports_socket(_imported_modules(path)):
            users.add(path.relative_to(REPO_ROOT).as_posix())
    return users


def _parse_allowlist(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """The enforced and the informational entries of the allow-list."""
    if not path.is_file():
        raise AssertionError(f"the AP-17 network allow-list is missing: {path}")
    enforced: dict[str, str] = {}
    informational: dict[str, str] = {}
    target = enforced
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "section:" in line:
                target = informational if "informational" in line else enforced
            continue
        relative, separator, reason = line.partition("|")
        relative = relative.strip()
        reason = reason.strip()
        if not separator or not relative or not reason:
            raise AssertionError(f"malformed allow-list line: {line!r} (expected '<path> | <reason>')")
        target[relative] = reason
    return enforced, informational


# ------------------------------------------------------------------ static guard

def test_scanner_sees_every_network_import(tmp_path):
    """The scanner itself is checked, so a broken scanner cannot pass silently."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import socket\n"
        "from socket import socket as sock\n"
        "import urllib.request\n"
        "from urllib import request\n"
        "from http.client import HTTPConnection\n"
        "import requests\n"
        "import httpx\n"
        "import aiohttp\n"
        "import ftplib\n"
        "import smtplib\n",
        encoding="utf-8",
    )
    names = _imported_modules(planted)
    assert _imports_socket(names)
    for module in NETWORK_MODULES:
        assert _imports(names, module), f"the scanner missed {module}"

    clean = tmp_path / "clean.py"
    clean.write_text("import json\nfrom pathlib import Path\n", encoding="utf-8")
    assert not _imports_socket(_imported_modules(clean))
    for module in NETWORK_MODULES:
        assert not _imports(_imported_modules(clean), module)


def test_library_imports_no_network_module():
    """No file under src/ may import a module that can reach the network."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        names = _imported_modules(path)
        hits = sorted(module for module in NETWORK_MODULES if _imports(names, module))
        if hits:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = hits
    assert not offenders, f"network-capable imports in the library: {offenders}"


def test_socket_imports_are_allowlisted():
    """A socket import under src/ is only allowed when it is listed with a reason."""
    enforced, _ = _parse_allowlist(ALLOWLIST_PATH)
    users = _library_socket_users()
    unexpected = sorted(users - set(enforced))
    assert not unexpected, (
        f"socket imported outside the allow-list: {unexpected}; add each one to {ALLOWLIST_PATH.name} with a reason"
    )
    stale = sorted(set(enforced) - users)
    assert not stale, f"allow-list entries that no longer import socket: {stale}"


def test_allowlist_entries_are_real_files_with_reasons():
    enforced, informational = _parse_allowlist(ALLOWLIST_PATH)
    for relative, reason in enforced.items():
        target = REPO_ROOT / relative
        assert target.is_file(), f"allow-list entry does not exist: {relative}"
        assert reason, f"allow-list entry without a reason: {relative}"
        assert _imports_socket(_imported_modules(target)), f"allow-list entry does not import socket: {relative}"
    for relative, reason in informational.items():
        assert (REPO_ROOT / relative).is_file(), f"informational entry does not exist: {relative}"
        assert reason, f"informational entry without a reason: {relative}"


# ------------------------------------------------------------------ runtime guard

def test_container_round_trip_through_the_simulated_channel(tmp_path):
    """store -> encode -> simulated channel -> decode -> restore, with the socket API blocked."""
    source = tmp_path / "payload.bin"
    source.write_bytes(PAYLOAD)

    report = api.pipeline(
        source,
        tmp_path / "restored.bin",
        work_dir=tmp_path / "work",
        channel=ChannelConfig(seed=17, dropout_rate=0.05),
        key=SIGNING_KEY,
    )

    assert report["bytes_identical"] is True
    assert report["container_roundtrip_identical"] is True
    assert report["steps"]["decode"]["status"] in {"SUCCESS", "RECOVERED"}
    assert (tmp_path / "restored.bin").read_bytes() == PAYLOAD


def test_container_range_read_offline(tmp_path):
    """A byte range is read back through ContainerReader.read_range, with the socket API blocked."""
    source = tmp_path / "payload.bin"
    source.write_bytes(PAYLOAD)
    container = tmp_path / "payload.vxdna"
    api.store(source, container, key=SIGNING_KEY)
    api.encode(container, tmp_path / "payload.fasta")
    api.decode(tmp_path / "payload.fasta", tmp_path / "payload.decoded.vxdna")

    api.extract(tmp_path / "payload.decoded.vxdna", tmp_path / "slice.bin", start=100, end=356, key=SIGNING_KEY)

    assert (tmp_path / "slice.bin").read_bytes() == PAYLOAD[100:356]


def test_stream_round_trip_offline(tmp_path):
    """encode_stream -> decode_stream, with the socket API blocked."""
    source = tmp_path / "stream.bin"
    source.write_bytes(PAYLOAD)
    options = StreamOptions(extent_bytes=4096, workers=1, dna=DNAOptions())
    manifest = tmp_path / "stream.json"

    encode_stream(source, tmp_path / "stream.fasta", options=options, manifest=manifest)
    decode_stream(tmp_path / "stream.fasta", manifest, tmp_path / "stream.out", overwrite=True)

    assert (tmp_path / "stream.out").read_bytes() == PAYLOAD

    # The AP-14 lane adds a range read to the stream API; use it when it is there.
    read_range = getattr(stream_module, "read_range", None)
    if read_range is not None:
        returned = read_range(tmp_path / "stream.fasta", manifest, 100, 64)
        data = returned[0] if isinstance(returned, tuple) else returned
        assert data == PAYLOAD[100:164]


def test_monitor_tick_offline(tmp_path):
    """One monitoring round runs with the socket API blocked."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    (root / "b.txt").write_text("world")

    clock = ManualClock(start=1_800_000_000.0)
    signer = HmacSigner(SIGNING_KEY)
    manifest, signature = build_manifest(root, signer, now=clock())
    audit = AuditLog(AUDIT_KEY, tmp_path / "audit.log")
    monitor = Monitor(root, manifest, signature, signer, audit, policy=Policy(), clock=clock)

    audit.append(SecurityEvent(
        event_id="init",
        timestamp=clock(),
        session_id="monitor",
        principal="monitor",
        resource="system",
        operation="init",
        result="observed",
        severity="INFO",
        detector="monitor",
        evidence={},
        integrity_state="UNKNOWN",
        kind="request",
    ))

    findings = monitor.tick()
    assert findings == [], f"an untouched tree must not raise findings: {findings}"
    assert monitor.locked is False

    clock.advance(1.0)
    (root / "a.txt").write_text("tampered")
    findings = monitor.tick()
    assert any(finding.kind == "modified" and finding.path == "a.txt" for finding in findings), findings


def test_conformance_vectors_pass_offline():
    """The spec conformance vectors run with the socket API blocked."""
    from typer.testing import CliRunner

    from vnxdna.commands.cli import app

    result = CliRunner().invoke(app, ["conformance"])
    assert result.exit_code == 0, f"conformance failed offline: {result.output}"


# ------------------------------------------------------------------ the runner

def _run_offline_check(args: list[str], extra_env: dict[str, str] | None = None):
    env = dict(os.environ)
    env.pop("VNX_UNSHARE", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(OFFLINE_CHECK), *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _stub_unshare(tmp_path: Path, body: str) -> str:
    stub = tmp_path / "unshare"
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    return str(stub)


def test_offline_check_script_is_present_and_executable():
    assert OFFLINE_CHECK.is_file(), f"the offline runner is missing: {OFFLINE_CHECK}"
    assert os.access(OFFLINE_CHECK, os.X_OK), f"the offline runner is not executable: {OFFLINE_CHECK}"


def test_offline_check_exits_2_when_unshare_is_missing(tmp_path):
    result = _run_offline_check([], {"VNX_UNSHARE": str(tmp_path / "no-such-unshare")})
    assert result.returncode == 2, result.stderr
    assert "namespace" in result.stderr.lower(), result.stderr


def test_offline_check_exits_2_when_the_namespace_cannot_be_created(tmp_path):
    stub = _stub_unshare(tmp_path, "#!/bin/sh\nexit 1\n")
    result = _run_offline_check([], {"VNX_UNSHARE": stub})
    assert result.returncode == 2, result.stderr
    assert "namespace" in result.stderr.lower(), result.stderr


def test_offline_check_runs_the_suite_in_a_network_namespace(tmp_path):
    record = tmp_path / "argv.txt"
    stub = _stub_unshare(tmp_path, f'#!/bin/sh\nprintf "%s\\n" "$@" > {record}\nexit 0\n')
    result = _run_offline_check(["tests/v15", "-k", "ap17"], {"VNX_UNSHARE": stub})
    assert result.returncode == 0, result.stderr

    argv = record.read_text(encoding="utf-8").splitlines()
    joined = "\n".join(argv)
    assert "--net" in argv and "--map-root-user" in argv, argv
    assert "-m pytest" in joined, joined
    assert "-o addopts=" in joined, joined
    assert "-p no:cacheprovider" in joined, joined
    assert "-n 8" in joined, joined
    assert "PYTHONPATH=src" in joined, joined
    assert "OPENBLAS_NUM_THREADS=1" in joined, joined
    assert "tests/v15" in argv and "-k" in argv and "ap17" in argv, argv


def test_offline_check_default_target_skips_the_nbd_device_test(tmp_path):
    record = tmp_path / "argv.txt"
    stub = _stub_unshare(tmp_path, f'#!/bin/sh\nprintf "%s\\n" "$@" > {record}\nexit 0\n')
    result = _run_offline_check([], {"VNX_UNSHARE": stub})
    assert result.returncode == 0, result.stderr

    argv = record.read_text(encoding="utf-8").splitlines()
    assert "tests" in argv, argv
    assert "--ignore=tests/v14/test_device_nbd.py" in argv, argv
