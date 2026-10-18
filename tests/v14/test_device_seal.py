"""V14 Phase 4: the sealed-extent format, the codec-ipc worker, and sealing end to end with ``vnxd``."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from vnxdna.device import protocol as pr
from vnxdna.device import seal as sl
from vnxdna.device import worker as wk
from vnxdna.device.contracts import DeviceError

ROOT = Path(__file__).resolve().parents[2]
CRATE = ROOT / "src/vnxdna/native/rust/vnxd"
KEY = bytes(range(32))
DEV = bytes.fromhex("00112233445566778899aabbccddeeff")


def test_seal_round_trip_and_layout():
    plain = b"extent bytes " * 300
    s = sl.seal(KEY, DEV, 7, 3, plain)
    assert len(s) == sl.HEADER.size + len(plain) + sl.TAG_BYTES and s[:4] == b"VNXS"
    assert sl.open_sealed(KEY, DEV, 7, 3, s) == plain
    assert sl.seal(KEY, DEV, 7, 3, plain) != s, "a fresh salt per object"


def test_known_answer_matches_an_independent_computation():
    salt = bytes(16)
    s = sl._seal_with_salt(KEY, DEV, 1, 2, b"hello", salt)
    kcv = HKDF(algorithm=hashes.SHA256(), length=32, salt=DEV, info=b"VNX-DNA/14 key check").derive(KEY)
    header = b"VNXS" + struct.pack("<HH", 1, 1) + salt + kcv[:8] + struct.pack("<Q", 5)
    binding = DEV + struct.pack(">QQ", 1, 2)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"VNX-DNA/14 extent key" + binding).derive(KEY)
    want = header + AESGCM(key).encrypt(b"VNX-DNA/14\x00\x01", b"hello", header + b"VNX-DNA/14 sealed extent" + binding)
    assert s == want


@pytest.mark.parametrize("change", ["extent", "generation", "device", "byte", "length", "version"])
def test_anything_moved_or_altered_is_refused(change):
    s = bytearray(sl.seal(KEY, DEV, 4, 9, b"x" * 100))
    args = [KEY, DEV, 4, 9]
    expect = "CORRUPTION"
    if change == "extent":
        args[2] = 5
    elif change == "generation":
        args[3] = 8
    elif change == "device":
        args[1] = bytes(16)         # the key check is per device: rejected before the tag, as damage
    elif change == "byte":
        s[60] ^= 1
    elif change == "length":
        s = s[:-1]
    else:
        s[4] = 2
        expect = "VERSION_MISMATCH"
    with pytest.raises(DeviceError) as e:
        sl.open_sealed(args[0], args[1], args[2], args[3], bytes(s))
    assert e.value.device_code == expect


def test_the_wrong_key_or_a_tampered_key_check_is_refused_as_corruption():
    s = sl.seal(KEY, DEV, 0, 1, b"data")
    with pytest.raises(DeviceError) as e:
        sl.open_sealed(bytes(32), DEV, 0, 1, s)
    assert e.value.device_code == "CORRUPTION" and "key check" in e.value.device_message
    t = bytearray(s)
    t[26] ^= 1      # inside the key check
    with pytest.raises(DeviceError) as e:
        sl.open_sealed(KEY, DEV, 0, 1, bytes(t))
    assert e.value.device_code == "CORRUPTION"


def _req(i, op, args):
    return pr.canonical(pr.request("vnx.codec-ipc", i, op, args))


def test_worker_protocol(tmp_path):
    w = wk.Worker(KEY, tmp_path)
    ok = json.loads(w.handle(_req(1, "hello", {"client": "t", "versions": [1]})))
    assert ok["result"] == {"server": "vnxdna.device.worker", "version": 1}
    kc = json.loads(w.handle(_req(2, "key_check", {"device_id": DEV.hex()})))["result"]["key_check"]
    assert bytes.fromhex(kc) == sl.key_check(KEY, DEV)
    data = os.urandom(5000)
    (tmp_path / "in").write_bytes(data)
    args = {"job": 3, "device_id": DEV.hex(), "extent": 2, "generation": 1, "input": "in",
            "input_sha256": hashlib.sha256(data).hexdigest(), "output": "out"}
    r = json.loads(w.handle(_req(3, "seal_extent", args)))["result"]
    sealed = (tmp_path / "out").read_bytes()
    assert r == {"job": 3, "output_sha256": hashlib.sha256(sealed).hexdigest(), "output_bytes": len(sealed)}
    assert oct((tmp_path / "out").stat().st_mode & 0o777) == "0o600"
    (tmp_path / "in2").write_bytes(sealed)
    args2 = {**args, "input": "in2", "input_sha256": hashlib.sha256(sealed).hexdigest(), "output": "out2"}
    json.loads(w.handle(_req(4, "open_extent", args2)))
    assert (tmp_path / "out2").read_bytes() == data

    def code(line):
        return json.loads(line)["error"]["code"]

    assert code(w.handle(_req(5, "seal_extent", {**args, "input_sha256": "0" * 64, "output": "o5"}))) == "CORRUPTION"
    assert code(w.handle(_req(6, "seal_extent", {**args, "output": "out"}))) == "INVALID_ARGUMENT"   # exists already
    assert code(w.handle('{"proto":"vnx.codec-ipc","v":1,"kind":"request","id":7,"op":"seal_extent","args":'
                         '{"job":1,"device_id":"' + DEV.hex() + '","extent":0,"generation":1,"input":"../etc/passwd",'
                         '"input_sha256":"' + "0" * 64 + '","output":"o"}}')) == "PROTOCOL"
    assert code(w.handle(_req(8, "encode_extent", {"job": 1, "extent": 0, "generation": 1, "input": "in",
                                                  "input_sha256": "0" * 64, "output": "o8", "options": {}}))) == "PROTOCOL"
    assert code(w.handle("not json")) == "PROTOCOL"


def test_worker_refuses_a_key_others_can_read(tmp_path):
    k = tmp_path / "key"
    k.write_text(KEY.hex())
    k.chmod(0o644)
    with pytest.raises(DeviceError) as e:
        wk.load_private_key(str(k))
    assert e.value.device_code == "UNAUTHORIZED"
    k.chmod(0o600)
    assert wk.load_private_key(str(k)) == KEY
    link = tmp_path / "link"
    link.symlink_to(k)
    with pytest.raises(DeviceError):
        wk.load_private_key(str(link))
    # a passphrase-like raw key (32 printable bytes) is not a generated key
    weak = tmp_path / "weak"
    weak.write_bytes(b"correct horse battery staple 123")
    weak.chmod(0o600)
    with pytest.raises(DeviceError) as e:
        wk.load_private_key(str(weak))
    assert e.value.device_code == "INVALID_ARGUMENT"


def test_worker_refuses_a_shared_staging_directory_and_symlinked_inputs(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o777)
    shared.chmod(0o777)
    with pytest.raises(DeviceError) as e:
        wk.check_staging(shared)
    assert e.value.device_code == "UNAUTHORIZED"
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    wk.check_staging(private)
    (tmp_path / "secret").write_bytes(b"not for the worker")
    (private / "in").symlink_to(tmp_path / "secret")
    w = wk.Worker(KEY, private)
    args = {"job": 1, "device_id": DEV.hex(), "extent": 0, "generation": 1, "input": "in",
            "input_sha256": hashlib.sha256(b"not for the worker").hexdigest(), "output": "out"}
    r = json.loads(w.handle(_req(1, "seal_extent", args)))
    assert r["ok"] is False and r["error"]["code"] == "NOT_FOUND" and not (private / "out").exists()
    r = subprocess.run([sys.executable, "-m", "vnxdna.device.worker", "--key-file", str(tmp_path / "missing"),
                        "--staging", str(tmp_path)], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert r.returncode == 2 and "missing" in r.stderr


def test_vnxd_seals_through_the_python_worker(tmp_path):
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("cargo is not installed: the vnxd crate cannot be built here")
    keys = []
    for name, key in (("k1", KEY), ("k2", bytes(range(1, 33)))):
        p = tmp_path / name
        p.write_text(key.hex())
        p.chmod(0o600)
        keys.append(str(p))
    env = {**os.environ, "VNXD_TEST_PYTHON": sys.executable, "VNXD_TEST_KEY": keys[0], "VNXD_TEST_KEY2": keys[1],
           "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    r = subprocess.run([cargo, "test", "--release", "--offline", "--locked", "--test", "seal_worker", "--", "--ignored"],
                       cwd=CRATE, env=env, capture_output=True, text=True, timeout=1200)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "1 passed" in r.stdout
