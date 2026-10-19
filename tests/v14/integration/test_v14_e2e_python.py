"""V14 Phase 9: the end-to-end rows driven through Python-side public interfaces (docs/V14_VALIDATION_PLAN.md §3).

* R18b: the ``vnx.codec-ipc/1`` worker process, as vnxd starts it (stdin/stdout lines);
* R22b: the ``vnx`` CLI with every native kernel forced off or pointed at a broken library;
* R23b: the SDK configuration loader and the worker's start-up checks;
* R25: the ``vnx`` CLI on the committed V5/V6 golden fixtures (SYNTHETIC SOFTWARE TEST data; reads are SIMULATED).

The expected outcomes were recorded in the plan before these tests ran.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vnxdna.device import protocol as pr

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "tests" / "fixtures"
VNX = "import sys; from vnxdna.commands import main; sys.argv = ['vnx'] + sys.argv[1:]; main()"
KERNELS = ("ALIGN", "CLUSTER", "ENGINE", "INTEGRITY", "ORIENT", "OUTERLIST", "READS", "RS", "STRAND")
DEV = "00112233445566778899aabbccddeeff"


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not (k.startswith("VNXDNA_") and ("BACKEND" in k or k.endswith("_LIB")))}
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
    env.update(extra)
    return env


def _vnx(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", VNX, *args], capture_output=True, text=True, env=env or _env(), timeout=600)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _reads(tmp: Path, src: Path) -> Path:
    out = tmp / (src.name.removesuffix(".gz"))
    out.write_bytes(gzip.decompress(src.read_bytes()))
    return out


# --------------------------------------------------------------------------------------------------------------
# R25 historical format compatibility

CASES = [("v5_0", "balanced"), ("v5_0", "archival"), ("v6_0", "stripes-seq"), ("v6_0", "max-recovery")]


@pytest.mark.parametrize("version,case", CASES)
def test_r25_historical_fixtures_decode_through_the_cli(tmp_path, version, case):
    manifest = json.loads((FIX / version / "manifest.json").read_text())["cases"][case]
    # the stored container verifies, and the SIMULATED reads decode to the recorded container bytes
    r = _vnx("verify", str(FIX / version / f"{case}.vnx"))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    out = tmp_path / "out.vnx"
    r = _vnx("decode", str(_reads(tmp_path, FIX / version / f"{case}.reads.fastq.gz")), "-o", str(out))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert _sha(out) == manifest["container_sha256"]


def test_r25_damaged_historical_reads_fail_without_output(tmp_path):
    lines = gzip.decompress((FIX / "v6_0" / "stripes-seq.reads.fastq.gz").read_bytes()).decode().splitlines(keepends=True)
    recs = [lines[i:i + 4] for i in range(0, len(lines), 4)]
    kept = [r for i, r in enumerate(recs) if i % 10 == 0]  # 90 % of the reads dropped (deterministic)
    src = tmp_path / "damaged.fastq"
    src.write_text("".join("".join(r) for r in kept))
    out = tmp_path / "out.vnx"
    r = _vnx("decode", str(src), "-o", str(out))
    assert r.returncode != 0, r.stdout[-2000:]
    assert not out.exists(), "a failed decode publishes nothing"


# --------------------------------------------------------------------------------------------------------------
# R22b native kernel failure

def test_r22b_native_kernels_off_or_broken_give_identical_bytes(tmp_path):
    reads = _reads(tmp_path, FIX / "v6_0" / "max-recovery.reads.fastq.gz")
    want = json.loads((FIX / "v6_0" / "manifest.json").read_text())["cases"]["max-recovery"]["container_sha256"]
    junk = tmp_path / "junk.so"
    junk.write_bytes(b"\x7fELF this is not a shared library")
    runs = {
        "auto": _env(),
        "reference": _env(**{f"VNXDNA_{k}_BACKEND": "reference" for k in KERNELS}),
        # a broken library path is tried first, then the normal search (or the reference) answers
        "junk-lib": _env(VNXDNA_NATIVE_LIB=str(junk), **{f"VNXDNA_{k}_LIB": str(junk) for k in KERNELS if k != "ALIGN"}),
    }
    for name, env in runs.items():
        out = tmp_path / f"{name}.vnx"
        r = _vnx("decode", str(reads), "-o", str(out), env=env)
        assert r.returncode == 0, name + ": " + r.stdout[-1500:] + r.stderr[-1500:]
        assert "Traceback" not in r.stderr, name
        assert _sha(out) == want, f"{name}: other bytes than the recorded container"


# --------------------------------------------------------------------------------------------------------------
# R18b codec-ipc worker protocol abuse

def _private(p: Path, mode: int) -> Path:
    p.chmod(mode)
    return p


@pytest.fixture
def worker(tmp_path):
    key = tmp_path / "key"
    key.write_text(bytes(range(32)).hex())
    _private(key, 0o600)
    staging = tmp_path / "staging"
    staging.mkdir()
    _private(staging, 0o700)
    p = subprocess.Popen([sys.executable, "-m", "vnxdna.device.worker", "--key-file", str(key), "--staging", str(staging)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env(),
                         cwd=staging)

    def ask(line: str) -> dict:
        p.stdin.write(line + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())

    yield ask, staging, p
    if p.poll() is None:
        p.stdin.close()
        assert p.wait(timeout=30) == 0
    p.stdout.close()
    p.stderr.close()


def _req(i: int, op: str, args: dict, proto: str = "vnx.codec-ipc") -> str:
    return pr.canonical(pr.request(proto, i, op, args))


def _raw(i: int, op: str, args: dict) -> str:
    """A request the builder would refuse (the worker must refuse it too)."""
    return pr.canonical({"args": args, "id": i, "kind": "request", "op": op, "proto": "vnx.codec-ipc", "v": 1})


def _code(resp: dict) -> str | None:
    return None if resp["ok"] else resp["error"]["code"]


def test_r18b_codec_ipc_worker_rejects_abuse(worker, tmp_path):
    ask, staging, proc = worker
    assert _code(ask(_req(1, "hello", {"client": "e2e", "versions": [1]}))) is None
    data = b"extent " * 100
    (staging / "in").write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    seal = {"job": 1, "device_id": DEV, "extent": 3, "generation": 1, "input": "in", "input_sha256": sha, "output": "out"}
    cases = [
        ("not json", "PROTOCOL"),
        ("[1,2,3]", "PROTOCOL"),
        (_req(2, "hello", {"client": "e2e", "versions": [1]}, proto="vnx.mgmt"), "PROTOCOL"),
        (_req(3, "hello", {"client": "e2e", "versions": [1]}).replace('"v":1', '"v":2'), "VERSION_MISMATCH"),
        (_raw(4, "seal_extent", {**seal, "input": "../in"}), "PROTOCOL"),
        (_raw(5, "seal_extent", {**seal, "output": "/tmp/x"}), "PROTOCOL"),
        (_raw(6, "seal_extent", {**seal, "input_sha256": "0" * 64}), "CORRUPTION"),
        (_raw(7, "seal_extent", {**seal, "input": "missing"}), "NOT_FOUND"),
        (_raw(8, "encode_extent", {"job": 1, "extent": 0, "generation": 1, "input": "in", "input_sha256": sha,
                                    "output": "o2", "options": {}}), "PROTOCOL"),
        ('{"a":' * 40 + "1" + "}" * 40, "PROTOCOL"),
        ('{"x":"' + "a" * (1 << 20) + '"}', "PROTOCOL"),
    ]
    for line, want in cases:
        assert _code(ask(line)) == want, line[:80]
    assert proc.poll() is None, "the worker keeps serving"
    r = ask(_req(9, "seal_extent", seal))
    assert _code(r) is None and (staging / "out").stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in staging.iterdir()) == ["in", "out"], "nothing else was written"


def test_r18b_worker_requires_hello_first(worker):
    """Regression for defect V14-P9-D1: the worker served operations before hello (contract §6)."""
    ask, staging, _ = worker
    (staging / "in").write_bytes(b"x" * 64)
    seal = {"job": 1, "device_id": DEV, "extent": 0, "generation": 1, "input": "in",
            "input_sha256": hashlib.sha256(b"x" * 64).hexdigest(), "output": "out"}
    assert _code(ask(_req(1, "seal_extent", seal))) == "PROTOCOL"
    assert _code(ask(_req(2, "key_check", {"device_id": DEV}))) == "PROTOCOL"
    assert _code(ask(_req(3, "hello", {"client": "e2e", "versions": [7]}))) == "VERSION_MISMATCH"
    assert _code(ask(_req(4, "seal_extent", seal))) == "PROTOCOL", "a failed hello negotiates nothing"
    assert _code(ask(_req(5, "hello", {"client": "e2e", "versions": [1]}))) is None
    assert _code(ask(_req(6, "seal_extent", seal))) is None


# --------------------------------------------------------------------------------------------------------------
# R23b configuration corruption (SDK + worker start)

def test_r23b_sdk_and_worker_config_corruption(tmp_path):
    from vnxdna.core.errors import VNXConfigurationError
    from vnxdna.sdk.config import load_config

    bad = {"unknown-section.json": json.dumps({"archiv": {}}), "unknown-key.json": json.dumps({"decode": {"bandd": 3}}),
           "malformed.json": '{"decode": {"band": 3', "not-object.json": "[1, 2]"}
    for name, text in bad.items():
        p = tmp_path / name
        p.write_text(text)
        with pytest.raises((VNXConfigurationError, ValueError)):
            load_config(p)
    staging = tmp_path / "staging"
    staging.mkdir()
    _private(staging, 0o700)
    keys = {"short": "abcd", "passphrase": "correct horse battery staple" * 3, "readable": bytes(range(32)).hex()}
    for name, text in keys.items():
        k = tmp_path / f"key-{name}"
        k.write_text(text)
        _private(k, 0o644 if name == "readable" else 0o600)
        r = subprocess.run([sys.executable, "-m", "vnxdna.device.worker", "--key-file", str(k), "--staging", str(staging)],
                           input="", capture_output=True, text=True, env=_env(), timeout=60)
        assert r.returncode == 2 and r.stderr.strip(), name
    open_staging = tmp_path / "open"
    open_staging.mkdir()
    _private(open_staging, 0o755)
    k = tmp_path / "key-ok"
    k.write_text(bytes(range(32)).hex())
    _private(k, 0o600)
    r = subprocess.run([sys.executable, "-m", "vnxdna.device.worker", "--key-file", str(k), "--staging", str(open_staging)],
                       input="", capture_output=True, text=True, env=_env(), timeout=60)
    assert r.returncode == 2, "a staging directory others can read is refused"
    assert list(staging.iterdir()) == [] and list(open_staging.iterdir()) == []
