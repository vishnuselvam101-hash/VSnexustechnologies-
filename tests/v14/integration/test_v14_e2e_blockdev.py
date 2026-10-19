"""V14 Phase 9: the rows driven through the PUBLIC BLOCK DEVICE (P8) and the real-hardware row (P10/P11).

Every row creates a disposable volume + image backend under pytest's tmp_path, attaches it with ``vnxd nbd attach``
(through tests/v14/nbd_support.py, which picks a free /dev/nbdN) and drives /dev/nbdN with O_DIRECT I/O, ext4, the
management socket or the fault-control file. Safety (docs/V14_DEVICE_INTERFACE.md §9): every destructive tool runs
through ``ns.run_tool``/``ns.mounted`` (the device must be served by the vnxd process this test started and not be
mounted); raw I/O asserts the same ownership first; every service is stopped and every mount removed in ``finally``;
only exact PIDs we started are signalled. Rows skip (with the kernel blocker as reason) on hosts without the NBD
interface. Expected outcomes: docs/V14_VALIDATION_PLAN.md §3.
"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
import random
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/v14: the P8 helpers
import nbd_support as ns  # noqa: E402

BS = 4096


@pytest.fixture(scope="module")
def kernel() -> Path:
    built = ns.build_vnxd()
    if built is None:
        pytest.skip("cargo is not installed: the vnxd crate cannot be built here")
    why = ns.kernel_blocker()
    if why is not None:
        pytest.skip(why)
    return built


@pytest.fixture()
def sockdir() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="vnxe2e-", dir="/tmp"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _volume(tmp: Path, size: str) -> tuple[Path, Path]:
    vol, be = tmp / "vol", tmp / "backend"
    ns.create_volume(vol, be, size)
    return vol, be


class Direct:
    """O_DIRECT I/O on our own device (ownership asserted at open)."""

    def __init__(self, dev: str, pid: int) -> None:
        ns.assert_own_device(dev, pid)
        self.fd = os.open(dev, os.O_RDWR | os.O_DIRECT)
        self.size = os.lseek(self.fd, 0, os.SEEK_END)

    def write(self, off: int, data: bytes) -> None:
        buf = mmap.mmap(-1, len(data))
        buf.write(data)
        assert os.pwritev(self.fd, [buf], off) == len(data)

    def read(self, off: int, n: int) -> bytes:
        buf = mmap.mmap(-1, n)
        assert os.preadv(self.fd, [buf], off) == n
        return bytes(buf)

    def sync(self) -> None:
        os.fsync(self.fd)

    def close(self) -> None:
        os.close(self.fd)


def _random_io(d: Direct, rng: random.Random, oracle: bytearray, ops: int) -> None:
    n = len(oracle) // BS
    for _ in range(ops):
        b = rng.randrange(n)
        k = min(rng.randint(1, 16), n - b)
        off, ln = b * BS, k * BS
        if rng.random() < 0.4:
            assert d.read(off, ln) == bytes(oracle[off:off + ln]), f"read {off}+{ln}"
        else:
            data = rng.randbytes(ln)
            d.write(off, data)
            oracle[off:off + ln] = data


def _stop(svc: ns.Service) -> None:
    assert svc.stop() == 0, svc.log()[-3000:]
    ns.wait_device_free(svc.device)


def test_b01_block_device_rw(kernel, tmp_path):
    vol, be = _volume(tmp_path, "32M")
    svc = ns.Service("attach", vol, be)
    try:
        d = Direct(svc.device, svc.pid)
        oracle = bytearray(d.size)
        assert d.read(0, d.size) == bytes(oracle), "a new device reads as zeros"
        rng = random.Random(9101)
        for off in range(0, d.size, 1 << 20):  # sequential
            data = rng.randbytes(1 << 20)
            d.write(off, data)
            oracle[off:off + len(data)] = data
        d.sync()
        assert d.read(0, d.size) == bytes(oracle)
        _random_io(d, rng, oracle, 1500)  # random
        d.sync()
        assert d.read(0, d.size) == bytes(oracle)
        d.close()
        _stop(svc)
    finally:
        svc.stop()
    svc = ns.Service("attach", vol, be)
    try:
        d = Direct(svc.device, svc.pid)
        assert d.read(0, d.size) == bytes(oracle), "after detach + attach"
        d.close()
        _stop(svc)
    finally:
        svc.stop()


def _files(root: Path, rng: random.Random) -> dict[str, str]:
    for i in range(150):
        (root / f"small-{i}").write_bytes(rng.randbytes(rng.randint(0, 4000)))
    for i in range(3):
        with open(root / f"large-{i}", "wb") as f:
            for _ in range(8):
                f.write(rng.randbytes(1 << 20))
    with open(root / "large-0", "r+b") as f:  # partial overwrite inside a large file
        f.seek(5 * (1 << 20) + 123)
        f.write(rng.randbytes(70000))
    return {p.name: ns.sha256_file(p) for p in sorted(root.iterdir()) if p.is_file()}


def _verify(root: Path, want: dict[str, str]) -> None:
    got = {p.name: ns.sha256_file(p) for p in sorted(root.iterdir()) if p.is_file()}
    assert got == want


def test_b02_filesystem_files(kernel, tmp_path):
    vol, be = _volume(tmp_path, "96M")
    mnt = tmp_path / "mnt"
    svc = ns.Service("attach", vol, be, "--cache", "write-back")
    try:
        dev = svc.device
        ns.run_tool(["mkfs.ext4", "-q", "-F", dev], dev, svc.pid)
        with ns.mounted(dev, svc.pid, mnt) as mp:
            want = _files(mp, random.Random(9102))
            subprocess.run(["sync", "-f", str(mp)], check=True, timeout=300)
        _stop(svc)
    finally:
        ns.umount(mnt)
        svc.stop()
    svc = ns.Service("attach", vol, be)
    try:
        dev = svc.device
        ns.run_tool(["e2fsck", "-f", "-n", dev], dev, svc.pid)
        with ns.mounted(dev, svc.pid, mnt, "ro") as mp:
            _verify(mp, want)
        _stop(svc)
    finally:
        ns.umount(mnt)
        svc.stop()


def test_b03_fsync_survives_restart(kernel, tmp_path):
    """Write-back cache: data the host fsynced (NBD FLUSH/FUA) survives a stop by SIGKILL, i.e. without the clean
    detach's final flush."""
    vol, be = _volume(tmp_path, "16M")
    svc = ns.Service("attach", vol, be, "--cache", "write-back")
    data = random.Random(9103).randbytes(1 << 20)
    try:
        d = Direct(svc.device, svc.pid)
        d.write(BS, data)
        d.sync()
        d.close()
        svc.kill()
        ns.wait_device_free(svc.device, timeout=60)
    finally:
        svc.kill()
    svc = ns.Service("attach", vol, be)
    try:
        d = Direct(svc.device, svc.pid)
        assert d.read(BS, len(data)) == data
        d.close()
        _stop(svc)
    finally:
        svc.stop()


def test_b04_device_restart(kernel, tmp_path):
    vol, be = _volume(tmp_path, "16M")
    oracle = bytearray(16 << 20)
    for rnd in range(3):
        svc = ns.Service("attach", vol, be, "--cache", ("none", "write-through", "write-back")[rnd])
        try:
            d = Direct(svc.device, svc.pid)
            assert d.read(0, d.size) == bytes(oracle), f"restart {rnd}"
            _random_io(d, random.Random(9104 + rnd), oracle, 300)
            d.close()
            _stop(svc)
        finally:
            svc.stop()
        opened = [e for e in svc.events() if e["event"] == "volume_opened"][0]
        assert opened["damaged"] == 0 and not opened["torn_tail"]


def test_b05_service_kill_under_load(kernel, tmp_path):
    vol, be = _volume(tmp_path, "64M")
    mnt = tmp_path / "mnt"
    synced: dict[str, str] = {}
    # short kernel timeout: after the kill, I/O the kernel still holds fails within 20 s, not the default 120 s
    svc = ns.Service("attach", vol, be, "--cache", "write-back", "--request-timeout-s", "5", "--kernel-timeout-s", "20")
    try:
        dev = svc.device
        ns.run_tool(["mkfs.ext4", "-q", "-F", dev], dev, svc.pid)
        with ns.mounted(dev, svc.pid, mnt) as mp:
            subprocess.run(["sync", "-f", str(mp)], check=True, timeout=300)
            stop = threading.Event()

            def writer(k: int) -> None:
                rng = random.Random(9105 + k)
                i = 0
                while not stop.is_set():
                    data = rng.randbytes(rng.randint(1, 200000))
                    p = mp / f"w{k}-{i}"
                    try:
                        with open(p, "wb") as f:
                            f.write(data)
                            f.flush()
                            os.fsync(f.fileno())
                        ns.fsync_dir(mp)  # never leaks its fd when the fsync fails (a leaked one kept the device attached)
                    except OSError:
                        return
                    synced[p.name] = hashlib.sha256(data).hexdigest()
                    i += 1

            ts = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
            for t in ts:
                t.start()
            time.sleep(3)
            svc.kill()  # SIGKILL of our own vnxd PID while fsyncs are in flight
            stop.set()
            for t in ts:
                t.join(timeout=180)
            ns.umount(mp)
        ns.wait_device_free(dev, timeout=180)
    finally:
        ns.umount(mnt)
        svc.kill()
    assert len(synced) > 5, "the writers made progress before the kill"
    svc = ns.Service("attach", vol, be)
    try:
        dev = svc.device
        opened = [e for e in svc.events() if e["event"] == "volume_opened"][0]
        assert opened["damaged"] == 0
        r = ns.run_tool(["e2fsck", "-f", "-y", dev], dev, svc.pid, check=False)
        assert r.returncode in (0, 1, 2), r.stdout + r.stderr
        with ns.mounted(dev, svc.pid, mnt, "ro") as mp:
            for name, sha in synced.items():
                assert ns.sha256_file(mp / name) == sha, name
        _stop(svc)
    finally:
        ns.umount(mnt)
        svc.stop()


def test_b06_out_of_space(kernel, tmp_path):
    """Past the end of the device: ENOSPC, nothing changes. A filesystem filled to the last block: ENOSPC to the
    writer, earlier files intact, fsck clean. (A backend storage bound is not a vnxd nbd option; NO_SPACE from the
    volume is covered at the queue level by R15.)"""
    vol, be = _volume(tmp_path, "32M")
    mnt = tmp_path / "mnt"
    svc = ns.Service("attach", vol, be)
    try:
        dev = svc.device
        d = Direct(dev, svc.pid)
        last = d.read(d.size - BS, BS)
        with pytest.raises(OSError) as e:
            d.write(d.size, b"\x5a" * BS)
        assert e.value.errno == 28
        assert d.read(d.size - BS, BS) == last
        d.close()
        ns.run_tool(["mkfs.ext4", "-q", "-F", dev], dev, svc.pid)
        with ns.mounted(dev, svc.pid, mnt) as mp:
            keep = random.Random(9106).randbytes(1 << 20)
            (mp / "keep").write_bytes(keep)
            with pytest.raises(OSError) as e:
                with open(mp / "fill", "wb") as f:
                    while True:
                        f.write(b"\xa5" * (1 << 20))
                        f.flush()
            assert e.value.errno == 28
            subprocess.run(["sync", "-f", str(mp)], check=True, timeout=300)
            assert (mp / "keep").read_bytes() == keep
        ns.run_tool(["e2fsck", "-f", "-n", dev], dev, svc.pid)
        _stop(svc)
    finally:
        ns.umount(mnt)
        svc.stop()


def test_b07_backend_failure_is_eio(kernel, tmp_path):
    vol, be = _volume(tmp_path, "16M")
    ctl = tmp_path / "faults"
    svc = ns.Service("attach", vol, be, "--fault-control", str(ctl), "--cache", "none")
    try:
        d = Direct(svc.device, svc.pid)
        data = random.Random(9107).randbytes(1 << 16)
        d.write(0, data)
        d.sync()
        ctl.write_text("put sync")
        with pytest.raises(OSError) as e:
            d.write(1 << 20, b"\x01" * (1 << 16))
            d.sync()
        assert e.value.errno == 5
        assert d.read(0, 1 << 16) == data, "committed data stays readable"
        ctl.write_text("get")
        with pytest.raises(OSError) as e:
            d.read(0, BS)
        assert e.value.errno == 5, "a failed backend read is EIO, never other bytes"
        ctl.unlink()
        assert d.read(0, 1 << 16) == data
        d.close()
        svc.stop()
        ns.wait_device_free(svc.device)
    finally:
        if ctl.exists():
            ctl.unlink()
        svc.stop()


def test_b08_mgmt_socket_authz(kernel, tmp_path, sockdir):
    vol, be = _volume(tmp_path, "8M")
    reader, admin = os.urandom(32).hex(), os.urandom(32).hex()
    policy = tmp_path / "policy"
    policy.write_text(f"reader {hashlib.sha256(reader.encode()).hexdigest()}\nadmin {hashlib.sha256(admin.encode()).hexdigest()}\n")
    policy.chmod(0o600)
    mgmt = sockdir / "mgmt.sock"
    svc = ns.Service("attach", vol, be, "--mgmt", str(mgmt), "--policy", str(policy))
    try:
        ns.wait_for_socket(mgmt)

        def session(lines: list[tuple[str, dict]]) -> list[dict]:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(30)
                s.connect(str(mgmt))
                f = s.makefile("rw")
                out = []
                for i, (op, args) in enumerate([("hello", {"client": "e2e", "versions": [1]})] + lines):
                    req = {"args": args, "id": i + 1, "kind": "request", "op": op, "proto": "vnx.mgmt", "v": 1}
                    f.write(json.dumps(req, sort_keys=True, separators=(",", ":")) + "\n")
                    f.flush()
                    out.append(json.loads(f.readline()))
                return out[1:]

        code = lambda r: None if r["ok"] else r["error"]["code"]  # noqa: E731
        r = session([("capacity", {}), ("shutdown", {})])
        assert [code(x) for x in r] == ["UNAUTHORIZED", "UNAUTHORIZED"]
        r = session([("authenticate", {"token": reader}), ("capacity", {}), ("shutdown", {})])
        assert [code(x) for x in r] == [None, None, "UNAUTHORIZED"] and r[1]["result"]["capacity_bytes"] == 8 << 20
        r = session([("authenticate", {"token": "x" * 40})] * 3 + [("authenticate", {"token": admin})])
        assert [code(x) for x in r] == ["UNAUTHORIZED"] * 4, "cut off after 3 failures"
        assert svc.proc.poll() is None, "no refused request stopped the device"
        r = session([("authenticate", {"token": admin}), ("shutdown", {})])
        assert [code(x) for x in r] == [None, None]
        svc.proc.wait(timeout=120)
        assert svc.proc.returncode == 0
        ns.wait_device_free(svc.device)
    finally:
        svc.stop()


def test_b09_queue_depth_saturation(kernel, tmp_path):
    vol, be = _volume(tmp_path, "64M")
    svc = ns.Service("attach", vol, be)
    try:
        dev = svc.device
        ns.assert_own_device(dev, svc.pid)
        size = 64 << 20
        for qd in (1, 4, 16, 32):
            region = size // qd // BS * BS
            errors: list[str] = []

            def work(i: int) -> None:
                d = Direct(dev, svc.pid)
                rng = random.Random(9109 + 100 * qd + i)
                try:
                    for _ in range(40):
                        off = i * region + rng.randrange(region // BS) * BS
                        data = rng.randbytes(BS * rng.randint(1, 4) if region >= 4 * BS else BS)
                        off = min(off, i * region + region - len(data))
                        d.write(off, data)
                        if d.read(off, len(data)) != data:
                            errors.append(f"qd {qd} writer {i}: mismatch at {off}")
                except OSError as e:
                    errors.append(f"qd {qd} writer {i}: {e}")
                finally:
                    d.close()

            ts = [threading.Thread(target=work, args=(i,)) for i in range(qd)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            assert not errors, errors[:5]
        _stop(svc)
        counters = [e for e in svc.events() if e["event"] == "detached"][-1]["stats"]["counters"]
        assert counters["nbd_errors"] == 0
    finally:
        svc.stop()


@pytest.mark.skip(reason="needs P10/P11 hardware: a Raspberry Pi running the service (docs/V14_RASPBERRY_PI_PROFILE.md)")
def test_r24hw_pi_memory_on_hardware():
    raise AssertionError("runs only on the Pi: B01 + B05 under the Pi memory budget, with peak RSS recorded")
