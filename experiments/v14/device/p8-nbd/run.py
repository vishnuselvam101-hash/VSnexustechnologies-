"""P8 block-device acceptance transcript and raw-device throughput (docs/V14_PHASE8_REPORT.md).

Needs root, the nbd module (``modprobe nbd max_part=8``) and a built ``vnxd``
(``cargo build --release --offline --locked --manifest-path src/vnxdna/native/rust/vnxd/Cargo.toml``). Run:

    python experiments/v14/device/p8-nbd/run.py --work <scratch dir> --out experiments/v14/device/p8-nbd/results.json

Every device touched is an /dev/nbdN attached by a vnxd process this script started (checked before each tool call by
``tests/v14/nbd_support.py``); every service is stopped and every mount removed in ``finally`` blocks. Evidence category
B (software block device) over C (the file-per-generation backend on this host's disk); no DNA is involved.
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests" / "v14"))
import nbd_support as ns  # noqa: E402


def sh(*argv: str) -> str:
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=60).stdout.strip()


def files(root: Path, seed: int) -> dict[str, str]:
    rng = random.Random(seed)
    (root / "a" / "b").mkdir(parents=True)
    sizes = [0, 1, 511, 4096, 4097, 65536, 200_000, 1 << 20, (3 << 20) + 17, 7 << 20]
    for i, n in enumerate(sizes):
        d = root / ("a/b" if i % 2 else "a")
        (d / f"f{i}.bin").write_bytes(rng.randbytes(n))
    subprocess.run(["cp", str(root / "a/b/f9.bin"), str(root / "copy.bin")], check=True, timeout=120)
    return {str(p.relative_to(root)): ns.sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


def acceptance(work: Path) -> dict:
    vol, be, mnt = work / "acc/vol", work / "acc/backend", work / "acc/mnt"
    ns.create_volume(vol, be, "128M")
    t: dict[str, float] = {}
    out: dict = {"cache": "write-back", "capacity_bytes": 128 << 20, "block_size": 4096, "extent_bytes": 65536}
    t0 = time.monotonic()
    svc = ns.Service("attach", vol, be, "--cache", "write-back")
    t["attach_s"] = time.monotonic() - t0
    dev = svc.device
    out["device_first"] = dev
    try:
        ns.run_tool(["sfdisk", "--quiet", "--no-reread", dev], dev, svc.pid, input="label: dos\n,,L\n")
        ns.reread_partitions(dev, svc.pid)
        part = dev + "p1"
        ns.wait_partition(part)
        out["partition_table"] = ns.run_tool(["sfdisk", "--dump", dev], dev, svc.pid).stdout.strip().splitlines()[-1]
        t0 = time.monotonic()
        ns.run_tool(["mkfs.ext4", "-q", "-F", "-L", "vnxp8", part], part, svc.pid)
        t["mkfs_s"] = time.monotonic() - t0
        with ns.mounted(part, svc.pid, mnt) as mp:
            t0 = time.monotonic()
            want = files(mp, seed=8)
            out["bytes"] = sum(p.stat().st_size for p in mp.rglob("*") if p.is_file())
            subprocess.run(["sync", "-f", str(mp)], check=True, timeout=300)
            t["write_and_sync_s"] = time.monotonic() - t0
            out["df"] = sh("df", "-B1", "--output=fstype,size,used", str(mp)).splitlines()[-1].split()
        out["exit_first"] = svc.stop()
        ns.wait_device_free(dev)
        out["device_released_after_detach"] = ns.device_free(dev)
    finally:
        ns.umount(mnt)
        svc.stop()
    svc2 = ns.Service("attach", vol, be)
    try:
        dev2 = svc2.device
        out["device_second"] = dev2
        part2 = dev2 + "p1"
        ns.wait_partition(part2)
        r = ns.run_tool(["e2fsck", "-f", "-n", part2], part2, svc2.pid, check=False)
        out["e2fsck_exit"] = r.returncode
        with ns.mounted(part2, svc2.pid, mnt, "ro") as mp:
            t0 = time.monotonic()
            got = {str(p.relative_to(mp)): ns.sha256_file(p) for p in sorted(mp.rglob("*")) if p.is_file() and "lost+found" not in p.parts}
            t["verify_s"] = time.monotonic() - t0
        out["exit_second"] = svc2.stop()
        ns.wait_device_free(dev2)
    finally:
        ns.umount(mnt)
        svc2.stop()
    out["files"] = len(want)
    out["file_sha256"] = want
    out["all_hashes_match"] = got == want
    out["timings"] = {k: round(v, 3) for k, v in t.items()}
    return out


def direct_io(dev: str, pid: int, total: int, chunk: int, write: bool) -> float:
    ns.assert_own_device(dev, pid)
    fd = os.open(dev, (os.O_WRONLY if write else os.O_RDONLY) | os.O_DIRECT)
    buf = mmap.mmap(-1, chunk)
    buf.write(random.Random(1).randbytes(chunk))
    try:
        t0 = time.monotonic()
        for off in range(0, total, chunk):
            n = os.pwritev(fd, [buf], off) if write else os.preadv(fd, [buf], off)
            assert n == chunk
        if write:
            os.fsync(fd)
        return time.monotonic() - t0
    finally:
        os.close(fd)


def throughput(work: Path, reps: int, total: int) -> list[dict]:
    rows = []
    for cache in ("none", "write-through", "write-back"):
        for rep in range(reps):
            base = work / f"tp-{cache}-{rep}"
            shutil.rmtree(base, ignore_errors=True)
            vol, be = base / "vol", base / "backend"
            ns.create_volume(vol, be, "256M")
            svc = ns.Service("attach", vol, be, "--cache", cache)
            try:
                w = direct_io(svc.device, svc.pid, total, 1 << 20, True)
                r = direct_io(svc.device, svc.pid, total, 1 << 20, False)
                rows.append({"cache": cache, "rep": rep, "bytes": total, "write_s": round(w, 3), "read_s": round(r, 3),
                             "write_MiB_s": round(total / w / 2**20, 1), "read_MiB_s": round(total / r / 2**20, 1)})
                assert svc.stop() == 0
            finally:
                svc.stop()
            shutil.rmtree(base, ignore_errors=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--mib", type=int, default=64)
    a = ap.parse_args()
    why = ns.kernel_blocker()
    if why:
        sys.exit(f"cannot run: {why}")
    assert ns.VNXD.exists(), "build vnxd first"
    a.work.mkdir(parents=True, exist_ok=True)
    res: dict = {
        "provenance": {
            "commit": sh("git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"),
            "dirty": bool(sh("git", "-C", str(ROOT), "status", "--porcelain", "--", "src", "tests", "experiments")),
            "kernel": platform.release(),
            "machine": platform.machine(),
            "cpus": os.cpu_count(),
            "nbd_params": {p.name: p.read_text().strip() for p in sorted(Path("/sys/module/nbd/parameters").iterdir())},
            "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "evidence": "B (software block device over the kernel NBD driver) on C (file-per-generation backend, host disk)",
        },
        "acceptance": acceptance(a.work),
    }
    rows = throughput(a.work, a.reps, a.mib << 20)
    res["throughput_rows"] = rows
    res["throughput_median"] = {
        c: {k: statistics.median(r[k] for r in rows if r["cache"] == c) for k in ("write_MiB_s", "read_MiB_s")}
        for c in ("none", "write-through", "write-back")
    }
    left = [p.name for p in Path("/sys/block").iterdir() if p.name.startswith("nbd") and (p / "pid").exists()]
    res["post_run_attached_nbd"] = left
    res["post_run_nbd_mounts"] = [s for s in ns.mounted_sources() if s.startswith("/dev/nbd")]
    a.out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: res[k] for k in ("throughput_median", "post_run_attached_nbd")}, indent=1))
    print("acceptance: hashes match =", res["acceptance"]["all_hashes_match"])


if __name__ == "__main__":
    main()
