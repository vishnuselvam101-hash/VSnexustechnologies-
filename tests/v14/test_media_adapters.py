"""V14 Phase 7: the DNA-simulator codec worker (``vnxdna.device.dnasim``) and the media adapters of ``vnxd`` over the
real VNX-DNA codec. Everything here is SIMULATED: no DNA is synthesised, stored or sequenced."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from vnxdna.device import dnasim as ds
from vnxdna.device import protocol as pr
from vnxdna.device.contracts import DeviceError

ROOT = Path(__file__).resolve().parents[2]
CRATE = ROOT / "src/vnxdna/native/rust/vnxd"


def _req(i: int, op: str, args: dict) -> str:
    return pr.canonical(pr.request("vnx.codec-ipc", i, op, args))


def _result(line: str) -> dict:
    v = json.loads(line)
    assert v["ok"], v
    return v["result"]


def _code(line: str) -> str:
    return json.loads(line)["error"]["code"]


@pytest.fixture()
def staged(tmp_path):
    tmp_path.chmod(0o700)
    w = ds.CodecWorker(tmp_path)
    assert _result(w.handle(_req(0, "hello", {"client": "t", "versions": [1]})))["version"] == 1  # contract §6: hello first
    return w, tmp_path


def _encode(w, d: Path, data: bytes, extent: int = 3, generation: int = 1, job: int = 2) -> bytes:
    (d / f"in{job}").write_bytes(data)
    args = {"job": job, "extent": extent, "generation": generation, "input": f"in{job}",
            "input_sha256": hashlib.sha256(data).hexdigest(), "output": f"pool{job}", "options": {}}
    r = _result(w.handle(_req(job, "encode_extent", args)))
    pool = (d / f"pool{job}").read_bytes()
    assert r == {"job": job, "output_sha256": hashlib.sha256(pool).hexdigest(), "output_bytes": len(pool)}
    return pool


def _decode_args(job: int, inp: str, data: bytes, options: dict, extent: int = 3, generation: int = 1) -> dict:
    return {"job": job, "extent": extent, "generation": generation, "input": inp, "output": f"out{job}",
            "expect_sha256": hashlib.sha256(data).hexdigest(), "expect_bytes": len(data), "options": options}


def test_encode_decode_round_trip_with_and_without_a_simulated_channel(staged):
    w, d = staged
    assert _result(w.handle(_req(1, "hello", {"client": "t", "versions": [1]}))) == {"server": "vnxdna.device.dnasim",
                                                                                   "version": 1}
    data = os.urandom(5000)
    pool = _encode(w, d, data)
    h, body = ds.parse_pool(pool)
    assert h["evidence"] == "SIMULATED" and h["format"] == "vnx.dna-pool/1" and h["body"] == "strands"
    assert (h["extent"], h["generation"]) == (3, 1) and body.startswith(b">")
    assert h["record"]["sha256"] == hashlib.sha256(data).hexdigest()
    for job, opt in ((10, {}), (11, {"channel": {"model": "illumina-like", "seed": 7}})):
        _result(w.handle(_req(job, "decode_extent", _decode_args(job, "pool2", data, opt))))
        assert (d / f"out{job}").read_bytes() == data
    assert not list(d.glob(".dsimw-*")), "scratch directories are removed"


def test_the_worker_refuses_moved_tampered_or_mismatched_pools(staged):
    w, d = staged
    data = os.urandom(3000)
    pool = _encode(w, d, data)
    assert _code(w.handle(_req(3, "decode_extent", _decode_args(3, "pool2", data, {}, extent=4)))) == "CORRUPTION"
    assert _code(w.handle(_req(4, "decode_extent", _decode_args(4, "pool2", data, {}, generation=2)))) == "CORRUPTION"
    wrong = _decode_args(5, "pool2", data, {})
    wrong["expect_sha256"] = "0" * 64
    assert _code(w.handle(_req(5, "decode_extent", wrong))) == "CORRUPTION"
    assert not (d / "out5").exists(), "bytes that did not verify are never written"
    h, body = ds.parse_pool(pool)
    flipped = body.replace(b"A", b"C")
    (d / "bad").write_bytes(ds.build_pool({**h, "body_sha256": hashlib.sha256(flipped).hexdigest()}, flipped))
    assert _code(w.handle(_req(6, "decode_extent", _decode_args(6, "bad", data, {})))) == "CORRUPTION"
    (d / "rot").write_bytes(pool[:-3] + b"TTT")
    assert _code(w.handle(_req(7, "decode_extent", _decode_args(7, "rot", data, {})))) == "CORRUPTION"


@pytest.mark.parametrize("options", [
    {"x": 1}, {"channel": {"model": "illumina-like"}}, {"channel": {"model": "../etc/passwd", "seed": 1}},
    {"channel": {"model": "illumina-like", "seed": -1}}, {"channel": {"model": "no-such-model", "seed": 1}},
    {"channel": {"model": "illumina-like\n", "seed": 1}}, {"channel": {"model": "illumina-like@9.9.9", "seed": 1}},
])
def test_bad_channel_options_are_invalid_argument(staged, options):
    w, d = staged
    data = b"x" * 100
    _encode(w, d, data)
    v = json.loads(w.handle(_req(8, "decode_extent", _decode_args(8, "pool2", data, options))))
    assert v["error"]["code"] == "INVALID_ARGUMENT" and v["error"]["message"].startswith(ds.OPTIONS)


def test_a_file_named_like_a_model_in_the_working_directory_is_never_read(staged, monkeypatch):
    w, d = staged
    data = os.urandom(800)
    _encode(w, d, data)
    (d / "illumina-like").write_text("{not a model}")      # planted next to the worker (its cwd is the staging dir)
    monkeypatch.chdir(d)
    _result(w.handle(_req(9, "decode_extent", _decode_args(9, "pool2", data, {"channel": {"model": "illumina-like",
                                                                                          "seed": 2}}))))
    assert (d / "out9").read_bytes() == data


def test_worker_resource_failures_are_retryable_not_damage(staged, monkeypatch):
    from vnxdna.core.errors import VNXOutputError
    from vnxdna.pipeline import stream
    w, d = staged
    data = os.urandom(500)
    _encode(w, d, data)

    def full(*a, **k):
        raise VNXOutputError("No space left on device")
    monkeypatch.setattr(stream, "decode_extent", full)
    assert _code(w.handle(_req(12, "decode_extent", _decode_args(12, "pool2", data, {})))) == "BACKEND_UNAVAILABLE"
    monkeypatch.setattr(stream, "encode_extent", full)
    (d / "e2").write_bytes(data)
    args = {"job": 13, "extent": 0, "generation": 1, "input": "e2", "input_sha256": hashlib.sha256(data).hexdigest(),
            "output": "o13", "options": {}}
    assert _code(w.handle(_req(13, "encode_extent", args))) == "BACKEND_UNAVAILABLE"


def test_a_channel_is_never_applied_to_reads_and_encode_options_are_strict(staged):
    w, d = staged
    data = b"y" * 300
    pool = _encode(w, d, data)
    h, body = ds.parse_pool(pool)
    reads = body  # FASTA strands presented as reads
    (d / "reads").write_bytes(ds.build_pool({**h, "body": "reads", "body_sha256": hashlib.sha256(reads).hexdigest()},
                                            reads))
    opt = {"channel": {"model": "clean", "seed": 1}}
    assert _code(w.handle(_req(9, "decode_extent", _decode_args(9, "reads", data, opt)))) == "INVALID_ARGUMENT"
    _result(w.handle(_req(10, "decode_extent", _decode_args(10, "reads", data, {}))))
    (d / "e").write_bytes(data)
    args = {"job": 11, "extent": 0, "generation": 1, "input": "e", "input_sha256": hashlib.sha256(data).hexdigest(),
            "output": "o11", "options": {"profile": "x"}}
    assert _code(w.handle(_req(11, "encode_extent", args))) == "INVALID_ARGUMENT"


def test_the_codec_worker_holds_no_key_and_refuses_sealing(staged):
    w, _ = staged
    assert _code(w.handle(_req(1, "key_check", {"device_id": "00" * 16}))) == "PROTOCOL"


def test_the_simulated_channel_is_seeded(staged):
    w, d = staged
    data = os.urandom(2000)
    _encode(w, d, data)
    from vnxdna.simulation import engine, registry
    h, body = ds.parse_pool((d / "pool2").read_bytes())
    (d / "s.fasta").write_bytes(body)
    model = registry.load_model("illumina-like")
    outs = []
    for seed, name in ((5, "a.fastq"), (5, "b.fastq"), (6, "c.fastq")):
        engine.simulate_file(d / "s.fasta", d / name, model, seed, fmt="fastq")
        outs.append((d / name).read_bytes())
    assert outs[0] == outs[1] and outs[0] != outs[2]


@pytest.mark.parametrize("damage", ["magic", "header", "keys", "label", "body"])
def test_parse_pool_is_strict(damage):
    body = b">s\nACGT\n"
    rec = {k: (0 if t is int else "x") for k, t in ds._RECORD.items()}
    h = {"format": ds.FORMAT, "evidence": "SIMULATED", "extent": 0, "generation": 1, "body": "strands",
         "body_sha256": hashlib.sha256(body).hexdigest(), "record": rec}
    good = ds.build_pool(h, body)
    assert ds.parse_pool(good) == (h, body)
    bad = {
        "magic": b"VNXPOOL/2\n" + good[len(ds.POOL_MAGIC):],
        "header": ds.POOL_MAGIC + b"{not json}\n" + body,
        "keys": ds.build_pool({**h, "extra": 1}, body),
        "label": ds.build_pool({**h, "evidence": "MEASURED"}, body),
        "body": good + b"A",
    }[damage]
    with pytest.raises(DeviceError) as e:
        ds.parse_pool(bad)
    assert e.value.device_code == "CORRUPTION"


def test_the_worker_refuses_a_shared_staging_directory(tmp_path):
    tmp_path.chmod(0o755)
    r = subprocess.run([sys.executable, "-m", "vnxdna.device.dnasim", "--staging", str(tmp_path)], input="",
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert r.returncode == 2 and "group or others" in r.stderr


def test_scratch_of_dead_workers_is_swept(tmp_path):
    tmp_path.chmod(0o700)
    dead = tmp_path / ".dsimw-7fffffff-abc"     # no such pid
    dead.mkdir()
    (dead / "x").write_bytes(b"left over")
    live = tmp_path / f".dsimw-{os.getpid():x}-abc"
    live.mkdir()
    ds.CodecWorker(tmp_path)
    assert not dead.exists() and live.exists()


def test_vnxd_media_adapters_over_the_vnxdna_codec(tmp_path):
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("cargo is not installed: the vnxd crate cannot be built here")
    env = {**os.environ, "VNXD_TEST_PYTHON": sys.executable,
           "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    r = subprocess.run([cargo, "test", "--release", "--offline", "--locked", "--test", "media_worker", "--", "--ignored"],
                       cwd=CRATE, env=env, capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "2 passed" in r.stdout
