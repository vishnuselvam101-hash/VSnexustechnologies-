"""V14 Phase 8: the Linux block device (``vnxd nbd``), docs/V14_DEVICE_INTERFACE.md.

Two groups:

* the NBD service over a Unix socket with a userspace client (no root, no kernel module): protocol, error status,
  fault path, management socket, kill-and-recover;
* the kernel block device (root + the ``nbd`` module): the principal acceptance test (partition, mkfs.ext4, mount,
  files, sync, umount, detach, re-attach over the same backend, verify every file's SHA-256), the fault path seen by
  the filesystem, and killing the server. These skip with the reason when the host cannot run them.

Every destructive tool call goes through ``nbd_support.run_tool`` / ``mounted``, which refuse any device that is not
an ``/dev/nbdN[pM]`` served by a ``vnxd`` process this test started.
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
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import nbd_support as ns  # tests/v14 is on sys.path (rootdir import mode)


@pytest.fixture(scope="module")
def vnxd() -> Path:
    try:
        built = ns.build_vnxd()
    except subprocess.TimeoutExpired:
        pytest.fail("building vnxd timed out")
    if built is None:
        pytest.skip("cargo is not installed: the vnxd crate cannot be built here")
    return built


@pytest.fixture()
def sockdir() -> Iterator[Path]:
    """A short directory for Unix sockets (sun_path holds 108 bytes; pytest's tmp_path can be longer)."""
    d = Path(tempfile.mkdtemp(prefix="vnxd-", dir="/tmp"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def kernel(vnxd: Path) -> Path:
    why = ns.kernel_blocker()
    if why is not None:
        pytest.skip(why)
    return vnxd


def _volume(tmp_path: Path, size: str = "8M", *extra: str) -> tuple[Path, Path]:
    vol, be = tmp_path / "vol", tmp_path / "backend"
    ns.create_volume(vol, be, size, *extra)
    return vol, be


def _serve(sockdir: Path, vol: Path, be: Path, *extra: str) -> tuple[ns.Service, Path]:
    sock = sockdir / "nbd.sock"
    svc = ns.Service("serve", vol, be, "--socket", str(sock), *extra)
    return svc, sock


# --------------------------------------------------------------------------------------------------------------
# userspace NBD clients (any host)


def test_cli_refuses_bad_usage(vnxd: Path, tmp_path: Path) -> None:
    with pytest.raises(ns.UnsafeDeviceError):
        ns.assert_own_device("/dev/vda", os.getpid())
    with pytest.raises(ns.UnsafeDeviceError):
        ns.run_tool(["true", "/dev/nbd9999"], "/dev/nbd9999", os.getpid())
    for argv in (["nbd"], ["nbd", "attach"], ["nbd", "create", "--volume", "v"], ["nbd", "frobnicate", "--volume", "v", "--backend", "b"],
                 ["nbd", "serve", "--volume", "v", "--backend", "b", "--cache", "sometimes"]):
        r = subprocess.run([str(vnxd), *argv], capture_output=True, text=True, timeout=60, cwd=tmp_path)
        assert r.returncode == 2, (argv, r.stderr)
    # attach refuses anything that is not /dev/nbdN before touching it
    vol, be = _volume(tmp_path)
    r = subprocess.run([str(vnxd), "nbd", "attach", "--volume", str(vol), "--backend", str(be), "--device", "/dev/vda"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "not an /dev/nbdN" in r.stderr
    # a volume is never created twice over the same directories
    r = subprocess.run([str(vnxd), "nbd", "create", "--volume", str(vol), "--backend", str(be), "--size", "8M"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1


def test_serve_protocol_round_trip(vnxd: Path, tmp_path: Path, sockdir: Path) -> None:
    vol, be = _volume(tmp_path)
    svc, sock = _serve(sockdir, vol, be)
    try:
        c = ns.NbdClient(sock)
        assert c.size == 8 << 20 and c.block_size == 4096
        assert c.flags & 0b101101 == 0b101101  # HAS_FLAGS, SEND_FLUSH, SEND_FUA, SEND_TRIM
        rng = random.Random(8)
        model = bytearray(c.size)
        for _ in range(60):
            off = rng.randrange(0, c.size - 70000)
            data = rng.randbytes(rng.choice([512, 4096, 65536, 69632, 1000]))
            assert c.write(off, data, flags=ns.FLAG_FUA if rng.random() < 0.2 else 0) == 0
            model[off:off + len(data)] = data
        assert c.flush() == 0
        for _ in range(40):
            off = rng.randrange(0, c.size - 70000)
            n = rng.choice([512, 4096, 70000])
            err, d = c.read(off, n)
            assert err == 0 and d == bytes(model[off:off + n])
        # precise error status, never silent success
        assert c.read(c.size - 512, 1024)[0] == ns.EINVAL
        assert c.write(c.size - 512, b"x" * 1024) == ns.ENOSPC
        assert c.call(ns.CMD_TRIM, c.size, 4096)[0] == ns.EINVAL
        assert c.call(ns.CMD_TRIM, 0, 4096)[0] == 0
        assert c.call(6, 0, 4096)[0] == ns.EINVAL  # WRITE_ZEROES is not advertised
        c.disconnect()
        # a second client sees the same bytes (acknowledged + flushed)
        c2 = ns.NbdClient(sock)
        off = 12345
        assert c2.read(off, 4096) == (0, bytes(model[off:off + 4096]))
        c2.disconnect()
    finally:
        assert svc.stop() == 0, svc.log()[-2000:]
    assert not sock.exists()


def test_serve_backend_faults_surface_as_errors(vnxd: Path, tmp_path: Path, sockdir: Path) -> None:
    vol, be = _volume(tmp_path)
    ctl = tmp_path / "faults"
    svc, sock = _serve(sockdir, vol, be, "--fault-control", str(ctl), "--cache", "none")
    try:
        c = ns.NbdClient(sock)
        good = os.urandom(4096)
        assert c.write(0, good) == 0 and c.flush() == 0
        ctl.write_text("get")
        assert c.read(0, 4096)[0] == ns.EIO
        ctl.write_text("put")
        assert c.write(4096, os.urandom(4096)) in (ns.EIO, ns.EPERM)
        ctl.write_text("sync")
        assert c.flush() != 0
        ctl.unlink()
        assert c.read(0, 4096) == (0, good), "data written before the fault is intact"
        deadline = time.monotonic() + 30
        while c.write(4096, b"\x07" * 4096) != 0:  # the backend is probed again after the probe interval
            assert time.monotonic() < deadline, "writes did not resume after the backend recovered"
            time.sleep(0.5)
        assert c.read(4096, 4096) == (0, b"\x07" * 4096)
        c.disconnect()
    finally:
        assert svc.stop() == 0
    names = [e["event"] for e in svc.events()]
    assert "stopped" in names


def test_management_socket(vnxd: Path, tmp_path: Path, sockdir: Path) -> None:
    vol, be = _volume(tmp_path)
    token = os.urandom(32).hex()
    policy = tmp_path / "policy"
    policy.write_text(f"admin {hashlib.sha256(token.encode()).hexdigest()}\n")
    policy.chmod(0o600)
    mgmt = sockdir / "mgmt.sock"
    svc, sock = _serve(sockdir, vol, be, "--mgmt", str(mgmt), "--policy", str(policy))
    try:
        ns.wait_for_socket(mgmt)
        assert oct(mgmt.stat().st_mode & 0o777) == "0o660"
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(30)
        s.connect(str(mgmt))
        f = s.makefile("rw")

        def call(i: int, op: str, args: dict) -> dict:
            req = {"args": args, "id": i, "kind": "request", "op": op, "proto": "vnx.mgmt", "v": 1}
            f.write(json.dumps(req, sort_keys=True, separators=(",", ":")) + "\n")
            f.flush()
            return json.loads(f.readline())

        assert call(1, "hello", {"client": "test", "versions": [1]})["ok"]
        assert call(2, "capacity", {})["error"]["code"] == "UNAUTHORIZED"
        assert call(3, "authenticate", {"token": token})["result"]["role"] == "admin"
        cap = call(4, "capacity", {})["result"]
        assert cap["capacity_bytes"] == 8 << 20 and cap["block_size"] == 4096
        c = ns.NbdClient(sock)
        assert c.write(0, b"\x01" * 4096) == 0
        h = call(5, "health", {})["result"]
        assert h["state"] == "ok" and h["detail"]["backend"]["backend"] == "healthy"
        st = call(6, "stats", {})["result"]["counters"]
        assert st["nbd_writes"] == 1 and st["nbd_connections"] == 1
        c.disconnect()
        assert call(7, "shutdown", {})["ok"]
        # the management session is still open: shutdown must not wait for it
        svc.proc.wait(timeout=60)
        f.close()
        s.close()
        assert svc.proc.returncode == 0
    finally:
        svc.stop()


def test_serve_kill_and_recover(vnxd: Path, tmp_path: Path, sockdir: Path) -> None:
    """SIGKILL while writes are in flight: every flushed write survives, every block holds old or new bytes only."""
    vol, be = _volume(tmp_path, "16M")
    svc, sock = _serve(sockdir, vol, be)
    blocks = 256
    flushed = {i: hashlib.sha256(b"A%d" % i).digest() * 128 for i in range(blocks)}
    try:
        c = ns.NbdClient(sock)
        for i, d in flushed.items():
            assert c.write(i * 4096 * 4, d) == 0
        assert c.flush() == 0
        later = {i: hashlib.sha256(b"B%d" % i).digest() * 128 for i in range(blocks)}
        for i, d in later.items():  # pipelined, unflushed
            c.send(ns.CMD_WRITE, i * 4096 * 4, 4096, d)
        time.sleep(0.05)
        svc.kill()
    finally:
        svc.kill()
    svc2, sock2 = _serve(sockdir, vol, be)
    try:
        opened = [e for e in svc2.events() if e["event"] == "volume_opened"]
        assert opened and opened[0]["damaged"] == 0
        c = ns.NbdClient(sock2)
        new = 0
        for i in range(blocks):
            err, d = c.read(i * 4096 * 4, 4096)
            assert err == 0
            assert d in (flushed[i], later[i]), f"block {i}: neither the flushed nor the later bytes"
            new += d == later[i]
        c.disconnect()
        # unflushed pipelined writes may or may not have landed: both counts are legal, the bound only documents it
        assert 0 <= new <= blocks
    finally:
        assert svc2.stop() == 0


# --------------------------------------------------------------------------------------------------------------
# the kernel block device (root + nbd module)


def _files(root: Path, seed: int) -> dict[str, str]:
    """Create a small tree of files (various sizes, a copy, a rename); returns {relative path: sha256}."""
    rng = random.Random(seed)
    out: dict[str, str] = {}
    (root / "docs" / "deep" / "er").mkdir(parents=True)
    sizes = [0, 1, 511, 4096, 4097, 65536, 200_000, 1 << 20, (3 << 20) + 17]
    for i, n in enumerate(sizes):
        p = root / ("docs/deep/er" if i % 3 == 0 else "docs" if i % 3 == 1 else ".") / f"file{i}.bin"
        p.write_bytes(rng.randbytes(n))
    subprocess.run(["cp", str(root / "docs/deep/er/file6.bin"), str(root / "copy-of-file6.bin")], check=True, timeout=120)
    (root / "file1.bin" if (root / "file1.bin").exists() else root / "docs/file1.bin").rename(root / "renamed.bin")
    (root / "notes.txt").write_text("VNX block device acceptance test\n" * 100)
    for p in root.rglob("*"):
        if p.is_file():
            out[str(p.relative_to(root))] = ns.sha256_file(p)
    return out


def _verify(root: Path, want: dict[str, str]) -> None:
    got = {str(p.relative_to(root)): ns.sha256_file(p) for p in root.rglob("*") if p.is_file() and "lost+found" not in p.parts}
    assert got == want


def test_kernel_ext4_files_survive_detach_and_reattach(kernel: Path, tmp_path: Path) -> None:
    """The principal acceptance test (master directive section 14, Stage A)."""
    vol, be = _volume(tmp_path, "128M")
    mnt = tmp_path / "mnt"
    svc = ns.Service("attach", vol, be, "--cache", "write-back")
    dev = svc.device
    try:
        assert svc.ready["size"] == 128 << 20 and svc.ready["block_size"] == 4096
        ns.run_tool(["sfdisk", "--quiet", "--no-reread", dev], dev, svc.pid, input="label: dos\n,,L\n")
        ns.reread_partitions(dev, svc.pid)
        part = dev + "p1"
        ns.wait_partition(part)
        ns.run_tool(["mkfs.ext4", "-q", "-F", "-L", "vnxtest", part], part, svc.pid)
        with ns.mounted(part, svc.pid, mnt) as mp:
            want = _files(mp, seed=14)
            subprocess.run(["sync", "-f", str(mp)], check=True, timeout=300)
        assert svc.stop() == 0, svc.log()[-3000:]
        tail = [e["event"] for e in svc.events()][-4:]  # the two middle events come from two threads, in any order
        assert tail[0] == "detaching" and tail[-1] == "detached" and sorted(tail[1:3]) == ["device_stopped", "server_ended"]
        last = svc.events()[-1]
        assert last["closed"] == "ok" and last["health"]["state"] == "ok"
        counters = last["stats"]["counters"]
        # the filesystem's barriers reached the device: FLUSH and FUA writes were served, none failed
        assert counters["nbd_flushes"] > 0 and counters["nbd_fua_writes"] > 0 and counters["nbd_errors"] == 0
        ns.wait_device_free(dev)
    finally:
        ns.umount(mnt)
        svc.stop()
    # a new service process over the same persisted backend
    svc2 = ns.Service("attach", vol, be)
    try:
        dev2 = svc2.device
        opened = [e for e in svc2.events() if e["event"] == "volume_opened"][0]
        assert opened["damaged"] == 0 and not opened["torn_tail"] and opened["records_replayed"] > 1
        part2 = dev2 + "p1"
        ns.wait_partition(part2)
        ns.run_tool(["e2fsck", "-f", "-n", part2], part2, svc2.pid)
        with ns.mounted(part2, svc2.pid, mnt, "ro") as mp:
            _verify(mp, want)
        assert svc2.stop() == 0
        ns.wait_device_free(dev2)
    finally:
        ns.umount(mnt)
        svc2.stop()


def _direct_read(path: Path, length: int = 4096) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
    try:
        buf = mmap.mmap(-1, length)
        n = os.readv(fd, [buf])
        return bytes(buf[:n])
    finally:
        os.close(fd)


def test_kernel_backend_faults_reach_the_filesystem_as_eio(kernel: Path, tmp_path: Path) -> None:
    vol, be = _volume(tmp_path, "64M")
    ctl = tmp_path / "faults"
    mnt = tmp_path / "mnt"
    svc = ns.Service("attach", vol, be, "--fault-control", str(ctl), "--cache", "none")
    dev = svc.device
    try:
        ns.run_tool(["mkfs.ext4", "-q", "-F", dev], dev, svc.pid)
        with ns.mounted(dev, svc.pid, mnt) as mp:
            payload = os.urandom(1 << 20)
            (mp / "kept.bin").write_bytes(payload)
            subprocess.run(["sync", "-f", str(mp)], check=True, timeout=300)
            ctl.write_text("get")
            with pytest.raises(OSError) as e:
                _direct_read(mp / "kept.bin")
            assert e.value.errno == 5  # EIO from the device, through the filesystem
            ns.assert_own_device(dev, svc.pid, may_be_mounted=True)
            with pytest.raises(OSError) as e:
                _direct_read(Path(dev))
            assert e.value.errno == 5
            ctl.write_text("put sync")
            fd = os.open(mp / "new.bin", os.O_WRONLY | os.O_CREAT, 0o644)
            try:
                with pytest.raises(OSError) as e:
                    os.write(fd, os.urandom(1 << 20))
                    os.fsync(fd)
                assert e.value.errno in (5, 30)  # EIO, or EROFS once ext4 aborted its journal
            finally:
                os.close(fd)
            ctl.unlink()
            # recovery (docs/V14_FAILURE_AND_RECOVERY_MODEL.md): reads work at once and clear the read streak; writes
            # stay refused (read-only device) until a write probe succeeds after the probe interval (5 s)
            assert _direct_read(mp / "kept.bin") == payload[:4096]
            time.sleep(6)
        assert svc.stop() == 0
        ns.wait_device_free(dev)
    finally:
        if ctl.exists():
            ctl.unlink()
        ns.umount(mnt)
        svc.stop()
    stats_ok = [e for e in svc.events() if e["event"] == "detached"]
    assert stats_ok, svc.log()[-2000:]
    # the data synced before the faults is intact after a repair run
    svc2 = ns.Service("attach", vol, be)
    try:
        dev2 = svc2.device
        ns.run_tool(["e2fsck", "-f", "-y", dev2], dev2, svc2.pid, check=False)
        with ns.mounted(dev2, svc2.pid, mnt, "ro") as mp:
            assert (mp / "kept.bin").read_bytes() == payload
        assert svc2.stop() == 0
    finally:
        ns.umount(mnt)
        svc2.stop()


def test_kernel_kill_server_and_recover(kernel: Path, tmp_path: Path) -> None:
    vol, be = _volume(tmp_path, "64M")
    mnt = tmp_path / "mnt"
    svc = ns.Service("attach", vol, be)
    dev = svc.device
    try:
        ns.run_tool(["mkfs.ext4", "-q", "-F", dev], dev, svc.pid)
        with ns.mounted(dev, svc.pid, mnt) as mp:
            (mp / "synced").mkdir()
            want = _files(mp / "synced", seed=99)
            subprocess.run(["sync", "-f", str(mp)], check=True, timeout=300)
            # unsynced writes in flight, then the server dies
            (mp / "unsynced.bin").write_bytes(os.urandom(4 << 20))
            svc.kill()
            ns.umount(mp)  # the filesystem sees I/O errors; the mount is removed
        ns.wait_device_free(dev, timeout=60)
    finally:
        ns.umount(mnt)
        svc.kill()
    svc2 = ns.Service("attach", vol, be)
    try:
        dev2 = svc2.device
        opened = [e for e in svc2.events() if e["event"] == "volume_opened"]
        assert opened and opened[0]["damaged"] == 0
        r = ns.run_tool(["e2fsck", "-f", "-y", dev2], dev2, svc2.pid, check=False)
        assert r.returncode in (0, 1, 2), r.stdout + r.stderr  # 1/2 = errors corrected
        with ns.mounted(dev2, svc2.pid, mnt, "ro") as mp:
            _verify_subset(mp / "synced", want)
        assert svc2.stop() == 0
        ns.wait_device_free(dev2)
    finally:
        ns.umount(mnt)
        svc2.stop()


def _verify_subset(root: Path, want: dict[str, str]) -> None:
    for rel, h in want.items():
        assert ns.sha256_file(root / rel) == h, rel


# --------------------------------------------------------------------------------------------------------------
# P8 review regressions


def test_stop_signal_works_when_stderr_is_gone(vnxd: Path, tmp_path: Path, sockdir: Path) -> None:
    """B1: a failed log write (stderr pipe closed) must never kill the signal handling: SIGTERM still stops."""
    vol, be = _volume(tmp_path)
    sock = sockdir / "nbd.sock"
    p = subprocess.Popen([str(vnxd), "nbd", "serve", "--volume", str(vol), "--backend", str(be), "--socket", str(sock)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        ns.wait_for_socket(sock)
        assert p.stderr is not None
        p.stderr.close()  # every later log line fails with EPIPE
        os.kill(p.pid, 15)
        p.wait(timeout=20)
        assert p.returncode in (0, 1)
    finally:
        if p.poll() is None:
            os.kill(p.pid, 9)
            p.wait(timeout=30)


def test_serve_does_not_leak_descriptors_per_connection(vnxd: Path, tmp_path: Path, sockdir: Path) -> None:
    """B2: every connection's descriptors are released when it ends."""
    vol, be = _volume(tmp_path)
    svc, sock = _serve(sockdir, vol, be)
    try:
        fds = Path(f"/proc/{svc.pid}/fd")
        ns.NbdClient(sock).disconnect()
        time.sleep(0.2)
        before = len(list(fds.iterdir()))
        for _ in range(60):
            ns.NbdClient(sock).disconnect()
        time.sleep(0.5)
        assert len(list(fds.iterdir())) <= before + 2
    finally:
        assert svc.stop() == 0


def test_failed_final_flush_is_a_failed_exit(vnxd: Path, tmp_path: Path, sockdir: Path) -> None:
    """B3 (serve): a stop whose final flush fails exits non-zero, never 0."""
    vol, be = _volume(tmp_path)
    ctl = tmp_path / "faults"
    svc, sock = _serve(sockdir, vol, be, "--fault-control", str(ctl), "--cache", "write-back")
    try:
        c = ns.NbdClient(sock)
        assert c.write(0, b"\x01" * 4096) == 0  # write-back: dirty in the cache
        ctl.write_text("put sync")
        c.s.close()
    finally:
        rc = svc.stop()
    assert rc == 1, svc.log()[-2000:]


def test_kernel_failed_detach_is_a_failed_exit(kernel: Path, tmp_path: Path) -> None:
    """B3 (attach): when the kernel cannot write its dirty buffers at detach, attach exits non-zero."""
    vol, be = _volume(tmp_path, "16M")
    ctl = tmp_path / "faults"
    svc = ns.Service("attach", vol, be, "--fault-control", str(ctl), "--cache", "none")
    dev = svc.device
    try:
        ns.assert_own_device(dev, svc.pid)
        ctl.write_text("put sync")
        fd = os.open(dev, os.O_WRONLY)
        try:
            os.write(fd, b"\x02" * 65536)  # buffered: written back only by the detach's fsync
        finally:
            os.close(fd)
        rc = svc.stop()
        assert rc == 1, svc.log()[-2000:]
        assert "device_sync_failed" in [e["event"] for e in svc.events()]
        ns.wait_device_free(dev)
    finally:
        if ctl.exists():
            ctl.unlink()
        svc.stop()
