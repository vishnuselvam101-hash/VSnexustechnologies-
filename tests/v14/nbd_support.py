"""Helpers for the V14 block-device tests: build ``vnxd``, run the service, a userspace NBD client, and the
safety checks every destructive tool call goes through.

Safety rules (docs/V14_DEVICE_INTERFACE.md section 9), enforced here in code:

* ``mkfs``/``sfdisk``/``mount``/``e2fsck``/raw I/O only ever run on a path that matches ``/dev/nbdN`` or
  ``/dev/nbdNpM`` whose whole device is served by a ``vnxd`` process THIS test started (``/sys/block/nbdN/pid``
  equals that process's pid) and that is not mounted (except for the I/O done on a mount we made);
* every service is stopped and every mount we made is unmounted in ``finally`` blocks, and only exact PIDs we
  spawned are signalled.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CRATE = ROOT / "src/vnxdna/native/rust/vnxd"
VNXD = CRATE / "target/release/vnxd"

NBDMAGIC = 0x4E42444D41474943
IHAVEOPT = 0x49484156454F5054
OPT_REPLY_MAGIC = 0x3E889045565A9
REQUEST_MAGIC = 0x25609513
REPLY_MAGIC = 0x67446698
CMD_READ, CMD_WRITE, CMD_DISC, CMD_FLUSH, CMD_TRIM = 0, 1, 2, 3, 4
FLAG_FUA = 1
EPERM, EIO, EINVAL, ENOSPC = 1, 5, 22, 28

_DEV_RE = re.compile(r"^/dev/nbd(\d{1,4})(p\d{1,3})?$")


class UnsafeDeviceError(RuntimeError):
    """A destructive operation was refused: the target is not a device this run attached (never an assert, so the
    checks survive ``python -O``)."""


def _require(ok: bool, msg: str) -> None:
    if not ok:
        raise UnsafeDeviceError(msg)


def build_vnxd() -> Path | None:
    """Build the release binary once; None when cargo is not installed."""
    cargo = shutil.which("cargo")
    if cargo is None:
        return None
    r = subprocess.run([cargo, "build", "--release", "--offline", "--locked", "--bin", "vnxd"], cwd=CRATE,
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError("cargo build failed:\n" + r.stderr[-4000:])
    return VNXD


def kernel_blocker() -> str | None:
    """Why the kernel E2E tests cannot run here (None when they can)."""
    if os.geteuid() != 0:
        return "the kernel block-device tests need root (CAP_SYS_ADMIN for the NBD ioctls, mount, mkfs)"
    if not Path("/sys/module/nbd").exists():
        return "the nbd kernel module is not loaded (the founder/operator runs: modprobe nbd max_part=8)"
    for tool in ("mkfs.ext4", "e2fsck", "mount", "umount", "sfdisk"):
        if shutil.which(tool) is None:
            return f"{tool} is not installed"
    return None


# --------------------------------------------------------------------------------------------------------------
# safety checks


def mounted_sources() -> set[str]:
    with open("/proc/self/mounts", encoding="utf-8") as f:
        return {line.split()[0] for line in f if line.strip()}


def assert_own_device(dev: str, pid: int, *, may_be_mounted: bool = False) -> None:
    """Refuse anything but an NBD device (or one of its partitions) served by process ``pid`` and not mounted."""
    m = _DEV_RE.match(dev)
    if m is None:
        raise UnsafeDeviceError(f"refusing {dev!r}: not an /dev/nbdN[pM] path")
    whole = f"nbd{m.group(1)}"
    pid_file = Path(f"/sys/block/{whole}/pid")
    owner = pid_file.read_text().strip() if pid_file.exists() else None
    _require(owner == str(pid), f"refusing {dev}: served by {owner!r}, not by our vnxd process {pid}")
    if not may_be_mounted:
        mounted = {s for s in mounted_sources() if s == f"/dev/{whole}" or s.startswith(f"/dev/{whole}p")}
        _require(not mounted, f"refusing {dev}: {sorted(mounted)} mounted")


def run_tool(argv: list[str], dev: str, pid: int, *, timeout: float = 300, check: bool = True,
             may_be_mounted: bool = False, input: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a destructive tool on ``dev`` after the ownership check (``dev`` must be an argument of ``argv``)."""
    _require(dev in argv, "the checked device must be the tool's target")
    assert_own_device(dev, pid, may_be_mounted=may_be_mounted)
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input)
    if check and r.returncode != 0:
        raise AssertionError(f"{argv} failed ({r.returncode}): {r.stdout[-2000:]} {r.stderr[-2000:]}")
    return r


@contextmanager
def mounted(dev: str, pid: int, mountpoint: Path, *opts: str) -> Iterator[Path]:
    mountpoint.mkdir(parents=True, exist_ok=True)
    _require(mountpoint.is_dir() and not any(mountpoint.iterdir()), "mount point must be an empty directory we own")
    args = ["mount"] + (["-o", ",".join(opts)] if opts else []) + [dev, str(mountpoint)]
    run_tool(args, dev, pid)
    try:
        yield mountpoint
    finally:
        umount(mountpoint)


def umount(mountpoint: Path) -> None:
    """Unmount a mount point we made (retrying a busy one); a lazy unmount only as the last resort."""
    target = str(mountpoint.resolve())
    for _ in range(20):
        if not is_mountpoint(target):
            return
        r = subprocess.run(["umount", target], capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return
        time.sleep(0.25)
    subprocess.run(["umount", "-l", target], capture_output=True, text=True, timeout=60)


def is_mountpoint(path: str) -> bool:
    with open("/proc/self/mounts", encoding="utf-8") as f:
        return any(line.split()[1] == path for line in f if line.strip())


def reread_partitions(dev: str, pid: int) -> None:
    """BLKRRPART on our device; udev may hold it open for a moment after a change event (EBUSY): settle, retry."""
    for _ in range(20):
        if shutil.which("udevadm"):
            subprocess.run(["udevadm", "settle", "--timeout=10"], capture_output=True, timeout=30)
        r = run_tool(["blockdev", "--rereadpt", dev], dev, pid, check=False)
        if r.returncode == 0:
            return
        assert "busy" in r.stderr.lower(), r.stderr
        time.sleep(0.25)
    raise AssertionError(f"{dev}: partition table re-read stayed busy")


def wait_partition(dev: str, timeout: float = 15.0) -> None:
    t0 = time.monotonic()
    while not Path(dev).exists():
        assert time.monotonic() - t0 < timeout, f"{dev} did not appear"
        time.sleep(0.1)


def device_free(dev: str) -> bool:
    m = _DEV_RE.match(dev)
    assert m is not None
    n = m.group(1)
    return not Path(f"/sys/block/nbd{n}/pid").exists() and Path(f"/sys/block/nbd{n}/size").read_text().strip() == "0"


def fsync_dir(path: Path) -> None:
    """fsync a directory; the descriptor is closed whatever happens (a leaked one keeps a mount busy)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def device_holders(dev: str) -> list[str]:
    """Why a device whose server is gone is still configured: the kernel frees it only when its last opener closes
    (an open fd, or a mounted filesystem, including one lazily unmounted but still in use)."""
    m = _DEV_RE.match(dev)
    assert m is not None
    name = f"nbd{m.group(1)}"
    out = [f"ext4 superblock of {name} still alive"] if Path(f"/sys/fs/ext4/{name}").exists() else []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            for fd in (p / "fd").iterdir():
                try:
                    if os.readlink(fd).startswith(f"/dev/{name}"):
                        out.append(f"pid {p.name} ({(p / 'comm').read_text().strip()}) has {dev} open")
                except OSError:
                    pass
        except OSError:
            pass
    return out


def wait_device_free(dev: str, timeout: float = 30.0) -> None:
    t0 = time.monotonic()
    while not device_free(dev):
        if time.monotonic() - t0 >= timeout:
            raise AssertionError(f"{dev} still attached after {timeout} s; holders: {device_holders(dev) or 'none found'}")
        time.sleep(0.1)


# --------------------------------------------------------------------------------------------------------------
# the service


class Service:
    """One ``vnxd nbd attach|serve`` process (started by us; only its exact PID is ever signalled)."""

    def __init__(self, mode: str, volume: Path, backend: Path, *extra: str, ready_timeout: float = 60.0) -> None:
        argv = [str(VNXD), "nbd", mode, "--volume", str(volume), "--backend", str(backend), *extra]
        self.log_path = volume.parent / f"vnxd-{mode}-{time.monotonic_ns()}.log"
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=self._log)
        self.pid = self.proc.pid
        self.ready: dict = {}
        want = {"attach": "attached", "serve": "serving"}[mode]
        assert self.proc.stdout is not None
        t0 = time.monotonic()
        buf = b""
        while True:
            left = ready_timeout - (time.monotonic() - t0)
            if left <= 0 or self.proc.poll() is not None:
                self.kill()
                raise AssertionError(f"vnxd {mode} not ready: {self.log()[-3000:]}")
            r, _, _ = select.select([self.proc.stdout], [], [], min(left, 0.5))
            if not r:
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                ev = json.loads(line)
                if ev.get("event") == want:
                    self.ready = ev
                    return

    @property
    def device(self) -> str:
        return str(self.ready["device"])

    def log(self) -> str:
        if not self._log.closed:
            self._log.flush()
        return self.log_path.read_text(errors="replace")

    def events(self) -> list[dict]:
        out = []
        for line in self.log().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def stop(self, timeout: float = 120.0) -> int:
        """SIGTERM (clean detach), then wait; SIGKILL our own PID only if it does not exit in time."""
        if self.proc.poll() is None:
            os.kill(self.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()
        self._log.close()
        return self.proc.returncode

    def kill(self) -> None:
        if self.proc.poll() is None:
            os.kill(self.pid, signal.SIGKILL)
            self.proc.wait(timeout=60)
        if not self._log.closed:
            self._log.close()


def create_volume(volume: Path, backend: Path, size: str, *extra: str) -> dict:
    r = subprocess.run([str(VNXD), "nbd", "create", "--volume", str(volume), "--backend", str(backend), "--size", size, *extra],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------------------------------------------
# a userspace NBD client (fixed newstyle, NBD_OPT_GO, simple replies)


class NbdClient:
    def __init__(self, path: Path, name: str = "") -> None:
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(120)
        self.s.connect(str(path))
        self.handle = 0
        magic, opt, hflags = struct.unpack(">QQH", self._recv(18))
        assert (magic, opt) == (NBDMAGIC, IHAVEOPT) and hflags & 1
        self.s.sendall(struct.pack(">I", 3))  # fixed newstyle + no zeroes
        data = struct.pack(">I", len(name)) + name.encode() + struct.pack(">HH", 1, 3)
        self.s.sendall(struct.pack(">QII", IHAVEOPT, 7, len(data)) + data)
        self.size = self.flags = self.block_size = -1
        while True:
            m, o, t, n = struct.unpack(">QIII", self._recv(20))
            assert m == OPT_REPLY_MAGIC and o == 7
            d = self._recv(n)
            if t == 1:
                break
            assert t == 3, f"GO refused: {t:#x} {d!r}"
            kind = struct.unpack(">H", d[:2])[0]
            if kind == 0:
                self.size, self.flags = struct.unpack(">QH", d[2:])
            elif kind == 3:
                _, self.block_size, _ = struct.unpack(">III", d[2:])

    def _recv(self, n: int) -> bytes:
        b = bytearray()
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise ConnectionError("server closed the connection")
            b += c
        return bytes(b)

    def send(self, cmd: int, offset: int, length: int, data: bytes = b"", flags: int = 0) -> int:
        self.handle += 1
        self.s.sendall(struct.pack(">IHHQQI", REQUEST_MAGIC, flags, cmd, self.handle, offset, length) + data)
        return self.handle

    def reply(self, data_len: int = 0) -> tuple[int, int, bytes]:
        magic, error, handle = struct.unpack(">IIQ", self._recv(16))
        assert magic == REPLY_MAGIC
        return handle, error, (self._recv(data_len) if error == 0 and data_len else b"")

    def call(self, cmd: int, offset: int, length: int, data: bytes = b"", flags: int = 0) -> tuple[int, bytes]:
        h = self.send(cmd, offset, length, data, flags)
        rh, err, d = self.reply(length if cmd == CMD_READ else 0)
        assert rh == h
        return err, d

    def write(self, offset: int, data: bytes, flags: int = 0) -> int:
        return self.call(CMD_WRITE, offset, len(data), data, flags)[0]

    def read(self, offset: int, length: int) -> tuple[int, bytes]:
        return self.call(CMD_READ, offset, length)

    def flush(self) -> int:
        return self.call(CMD_FLUSH, 0, 0)[0]

    def disconnect(self) -> None:
        self.send(CMD_DISC, 0, 0)
        try:
            while self.s.recv(4096):
                pass
        except OSError:
            pass
        self.s.close()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wait_for_socket(path: Path, timeout: float = 30.0) -> None:
    t0 = time.monotonic()
    while not path.exists():
        assert time.monotonic() - t0 < timeout
        time.sleep(0.05)
