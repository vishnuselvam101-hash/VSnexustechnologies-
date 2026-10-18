"""V14 Phase 9: the rows driven through the PUBLIC BLOCK DEVICE (P8) and the real-hardware row (P10/P11).

They are written against the documented interface (a Linux block device exported by the vnxd service, and a
service-control command) and are skipped until the integration lead provides both after merging P7/P8:

* ``VNX_E2E_DEVICE``: the device node exported by vnxd (``/dev/nbdN`` or ``/dev/ublkbN`` only, created by our own code
  over a disposable image under a lab scratch directory, master directive / docs/V14_STATE.md block-device rule);
* ``VNX_E2E_SERVICE``: a command taking one argument, ``start`` | ``stop`` | ``kill`` | ``restart`` | ``mgmt-socket``
  | ``limit-space <bytes>`` | ``backend-fail`` | ``backend-ok``, that controls the service behind that device and
  re-exports it on start (``mgmt-socket`` prints the vnx.mgmt socket path; ``limit-space`` restarts it with the
  backend bound);
* ``VNX_E2E_MNT``: an empty scratch directory for the filesystem rows; ``VNX_E2E_TOKENS``: a JSON file
  ``{"reader": "...", "admin": "..."}`` with the service's test tokens.

A skipped row is never a pass. Expected outcomes are recorded in docs/V14_VALIDATION_PLAN.md §3.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import socket
import subprocess
import threading
from pathlib import Path

import pytest

DEVICE = os.environ.get("VNX_E2E_DEVICE", "")
SERVICE = os.environ.get("VNX_E2E_SERVICE", "")
MNT = os.environ.get("VNX_E2E_MNT", "")
PENDING = "needs P7/P8 integration: set VNX_E2E_DEVICE and VNX_E2E_SERVICE (docs/V14_VALIDATION_PLAN.md §5)"

needs_device = pytest.mark.skipif(not (DEVICE and SERVICE), reason=PENDING)
needs_fs = pytest.mark.skipif(not (DEVICE and SERVICE and MNT), reason=PENDING + " and VNX_E2E_MNT")


def _dev() -> str:
    if not re.fullmatch(r"/dev/(nbd|ublkb)\d+", DEVICE):
        pytest.fail(f"refusing {DEVICE!r}: only /dev/nbdN or /dev/ublkbN exported by vnxd")
    return DEVICE


def _svc(*args: str) -> str:
    return subprocess.run([*SERVICE.split(), *args], check=True, capture_output=True, text=True, timeout=300).stdout.strip()


def _size(fd: int) -> int:
    return os.lseek(fd, 0, os.SEEK_END)


def _rw(fd: int, rng: random.Random, oracle: bytearray, ops: int, block: int = 4096) -> None:
    n = len(oracle) // block
    for _ in range(ops):
        b, k = rng.randrange(n), rng.randint(1, 16)
        k = min(k, n - b)
        off, ln = b * block, k * block
        if rng.random() < 0.4:
            assert os.pread(fd, ln, off) == bytes(oracle[off:off + ln]), f"read {off}+{ln}"
        else:
            data = rng.randbytes(ln)
            assert os.pwrite(fd, data, off) == ln
            oracle[off:off + ln] = data


def _snapshot(fd: int) -> bytearray:
    return bytearray(os.pread(fd, _size(fd), 0))


@needs_device
def test_b01_block_device_rw():
    fd = os.open(_dev(), os.O_RDWR)
    try:
        oracle = _snapshot(fd)
        rng = random.Random(9101)
        # sequential: the whole device in 1 MiB writes, read back
        for off in range(0, len(oracle), 1 << 20):
            data = rng.randbytes(min(1 << 20, len(oracle) - off))
            os.pwrite(fd, data, off)
            oracle[off:off + len(data)] = data
        os.fsync(fd)
        assert os.pread(fd, len(oracle), 0) == bytes(oracle)
        _rw(fd, rng, oracle, 2000)
        os.fsync(fd)
        assert os.pread(fd, len(oracle), 0) == bytes(oracle)
    finally:
        os.close(fd)


def _fs(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)


def _files(root: Path, rng: random.Random) -> dict[str, str]:
    out = {}
    for i in range(200):  # small files
        p = root / f"small-{i}"
        p.write_bytes(rng.randbytes(rng.randint(0, 4000)))
    for i in range(3):  # large files
        p = root / f"large-{i}"
        with open(p, "wb") as f:
            for _ in range(32):
                f.write(rng.randbytes(1 << 20))
    with open(root / "large-0", "r+b") as f:  # partial overwrite inside a large file
        f.seek(5 * (1 << 20) + 123)
        f.write(rng.randbytes(70000))
    for p in sorted(root.iterdir()):
        out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@needs_fs
def test_b02_filesystem_files():
    dev = _dev()
    _fs(["mkfs.ext4", "-F", "-q", dev])
    _fs(["mount", dev, MNT])
    try:
        want = _files(Path(MNT), random.Random(9102))
        _fs(["sync"])
    finally:
        _fs(["umount", MNT])
    _fs(["fsck.ext4", "-n", "-f", dev])
    _fs(["mount", dev, MNT])
    try:
        got = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(MNT).iterdir()) if p.is_file()}
        assert got == want
    finally:
        _fs(["umount", MNT])


@needs_device
def test_b03_fsync_survives_restart():
    fd = os.open(_dev(), os.O_RDWR)
    data = random.Random(9103).randbytes(1 << 20)
    os.pwrite(fd, data, 4096)
    os.fsync(fd)
    os.close(fd)
    _svc("restart")
    fd = os.open(_dev(), os.O_RDONLY)
    try:
        assert os.pread(fd, len(data), 4096) == data
    finally:
        os.close(fd)


@needs_device
def test_b04_device_restart():
    fd = os.open(_dev(), os.O_RDWR)
    _rw(fd, random.Random(9104), oracle := _snapshot(fd), 300)
    os.fsync(fd)
    os.close(fd)
    _svc("stop")
    _svc("start")
    fd = os.open(_dev(), os.O_RDONLY)
    try:
        assert os.pread(fd, len(oracle), 0) == bytes(oracle)
    finally:
        os.close(fd)


@needs_fs
def test_b05_service_kill_under_load():
    dev = _dev()
    _fs(["mkfs.ext4", "-F", "-q", dev])
    _fs(["mount", dev, MNT])
    rng = random.Random(9105)
    synced: dict[str, str] = {}
    stop = threading.Event()

    def writer() -> None:
        i = 0
        while not stop.is_set():
            data = rng.randbytes(rng.randint(1, 200000))
            p = Path(MNT) / f"f-{i}"
            try:
                with open(p, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
            except OSError:
                return
            synced[p.name] = hashlib.sha256(data).hexdigest()
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    threading.Event().wait(3)
    _svc("kill")
    stop.set()
    t.join(timeout=120)
    subprocess.run(["umount", "-l", MNT], capture_output=True, timeout=120)
    _svc("start")
    _fs(["fsck.ext4", "-p", "-f", dev])
    _fs(["mount", dev, MNT])
    try:
        for name, sha in synced.items():
            assert hashlib.sha256((Path(MNT) / name).read_bytes()).hexdigest() == sha, name
    finally:
        _fs(["umount", MNT])


@needs_device
def test_b06_out_of_space():
    _svc("limit-space", str(1 << 20))
    fd = os.open(_dev(), os.O_RDWR)
    try:
        before = os.pread(fd, 1 << 20, 0)
        with pytest.raises(OSError) as e:
            for off in range(0, _size(fd), 1 << 20):
                os.pwrite(fd, b"\x5a" * (1 << 20), off)
                os.fsync(fd)
        assert e.value.errno in (28, 5), e.value  # ENOSPC (EIO when the kernel folds it)
    finally:
        os.close(fd)
        _svc("restart")
    fd = os.open(_dev(), os.O_RDONLY)
    try:
        got = os.pread(fd, 1 << 20, 0)
        assert got in (before, b"\x5a" * (1 << 20)), "each block is the old or the new bytes"
    finally:
        os.close(fd)


@needs_device
def test_b07_backend_failure_is_eio():
    fd = os.open(_dev(), os.O_RDWR)
    try:
        data = random.Random(9107).randbytes(1 << 16)
        os.pwrite(fd, data, 0)
        os.fsync(fd)
        _svc("backend-fail")
        with pytest.raises(OSError) as e:
            os.pwrite(fd, b"\0" * (1 << 16), 1 << 20)
            os.fsync(fd)
        assert e.value.errno == 5
        assert os.pread(fd, 1 << 16, 0) == data, "committed data stays readable"
    finally:
        _svc("backend-ok")
        os.close(fd)


def _mgmt(path: str, lines: list[str]) -> list[dict]:
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(path)
        f = s.makefile("rw")
        out = []
        for line in lines:
            f.write(line + "\n")
            f.flush()
            out.append(json.loads(f.readline()))
        return out


@needs_device
def test_b08_mgmt_socket_authz():
    from vnxdna.device import protocol as pr

    tokens = json.loads(Path(os.environ["VNX_E2E_TOKENS"]).read_text())
    path = _svc("mgmt-socket")
    req = lambda i, op, a: pr.canonical(pr.request("vnx.mgmt", i, op, a))  # noqa: E731
    hello = req(1, "hello", {"client": "e2e", "versions": [1]})
    r = _mgmt(path, [hello, req(2, "capacity", {}), req(3, "shutdown", {})])
    assert [x["ok"] for x in r] == [True, False, False] and r[1]["error"]["code"] == "UNAUTHORIZED"
    r = _mgmt(path, [hello, req(2, "authenticate", {"token": tokens["reader"]}), req(3, "capacity", {}), req(4, "shutdown", {})])
    assert [x["ok"] for x in r] == [True, True, True, False] and r[3]["error"]["code"] == "UNAUTHORIZED"


@needs_device
def test_b09_queue_depth_saturation():
    fd = os.open(_dev(), os.O_RDWR)
    try:
        size = _size(fd)
        for qd in (1, 4, 16, 32):
            region = size // qd // 4096 * 4096

            errors: list[str] = []

            def work(i: int) -> None:
                rng = random.Random(9109 + 100 * qd + i)
                base = i * region
                try:
                    for _ in range(50):
                        off = base + rng.randrange(region // 4096) * 4096
                        data = rng.randbytes(4096)
                        os.pwrite(fd, data, off)
                        if os.pread(fd, 4096, off) != data:
                            errors.append(f"qd {qd} writer {i}: mismatch at {off}")
                except OSError as e:
                    errors.append(f"qd {qd} writer {i}: {e}")

            ts = [threading.Thread(target=work, args=(i,)) for i in range(qd)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            assert not errors, errors[:5]
    finally:
        os.close(fd)


@pytest.mark.skip(reason="needs P10/P11 hardware: a Raspberry Pi running the service (docs/V14_RASPBERRY_PI_PROFILE.md)")
def test_r24hw_pi_memory_on_hardware():
    raise AssertionError("runs only on the Pi: B01 + B05 under the Pi memory budget, with peak RSS recorded")
