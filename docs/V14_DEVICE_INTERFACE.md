# V14 device interface: the Linux block device

Phase 8 specification (master directive §14 Stage A, A' and B; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P8; decision
D3 of [V14_LANGUAGE_AND_RUNTIME_STRATEGY.md](V14_LANGUAGE_AND_RUNTIME_STRATEGY.md)). Implementation:
`src/vnxdna/native/rust/vnxd/src/{nbd,nbdsys,nbdserve,nbdctl,dirmedia}.rs`; tests: `tests/v14/test_device_nbd.py`
and the unit tests of those modules; measurements: `experiments/v14/device/p8-nbd/`.

Evidence category B (a software block device on this host) over C (an electronic file backend). Nothing here involves
DNA; a DNA-simulator backend (P7) behind the same device is category D.

## 1. What exists

| stage | status | what |
|---|---|---|
| A: kernel block device | **done** | `/dev/nbdN` through the kernel NBD driver; `vnxd` is the NBD server and attaches the device itself (no `nbd-client`) |
| A': ublk | **not built** (design + gap in §11) | `ublk_drv` loads on this kernel; the io_uring server is not written |
| B: portable userspace API | documented (§12) | the same device served on a Unix socket to any NBD client; the SDK/CLI volume of V13 is a separate format |
| C: external hardware controller | P11 | not in this phase |

A device is a **volume** (journal directory) over a **backend** (media adapter); the device depends only on the
`MediaAdapter` trait. Default backend (`--media image`): the P7 `ImageMedia` (`vnx.media-image/1`,
docs/V14_MEDIA_ADAPTERS.md §3): immutable object files with a header naming the generation and its SHA-256, written
temp + fsync + `link(2)` + directory fsync, checked on every read. It is the backend the device should run over: it is
persisted, integrity-checked below the volume's own SHA-256 check, and the one the media phase tested. `--media dir`
keeps `DirMedia`, the P8 interim adapter (the same commit procedure without the object header). Other adapters (the
DNA simulator, sealing) plug in behind the same trait in P9.

## 2. Commands

```
vnxd nbd create --volume DIR --backend DIR --size N [--block-size 4096] [--extent-blocks 16]
vnxd nbd attach --volume DIR --backend DIR [--device /dev/nbdN | auto] [options]
                (--media image|dir on every command; image is the default)
vnxd nbd serve  --volume DIR --backend DIR --socket PATH [options]

options: --cache none|write-through|write-back (default write-through)   --ram N (VNX-RAM budget, default 256M)
         --read-only   --request-timeout-s N (default 30)   --kernel-timeout-s N (default 120; >= 2 x request + 10; 0 refused)
         --mgmt SOCKET --policy FILE (management socket, §7)   --fault-control FILE (fault drills, §5)
         --no-readback (skip the volume's read-back verification of new generations)
         --workers N --queue-depth N (I/O queue; default sized from the backend)   --max-inflight N (default 128)
         --max-inflight-bytes N (default 64M, >= 32M)   --repliers N (default 8)
         --low-memory (preset: --ram 96M --workers 2 --queue-depth 16 --max-inflight 16 --max-inflight-bytes 32M
                       --repliers 2; explicit options override it; V14_RASPBERRY_PI_PROFILE.md §3)
```

`create` refuses an existing journal and a non-empty backend. `attach` prints one JSON line `{"event":"attached",
"device":"/dev/nbdN",...}` on stdout once the kernel reports the device as served by this process
(`/sys/block/nbdN/pid`); `serve` prints `{"event":"serving",...}`. Every event (volume opened with its recovery report,
signals, detach steps, errors, the final health and counters) is one canonical-JSON line on stderr. Exit status: 0
only after a requested stop (signal or management `shutdown`) in which everything succeeded; 1 on any error, including
a failed final flush, a failed fsync of the device at detach (the kernel could not write its dirty buffers), an
`NBD_DO_IT` error (kernel timeout), a connection that ended without a requested detach, or a failed device health;
2 on usage errors. Logging never panics: a closed stderr does not stop the service from handling signals.

## 3. The NBD protocol subset

Server and client sides of the **fixed newstyle** negotiation (the old style is never offered):

* handshake flags FIXED_NEWSTYLE | NO_ZEROES; a client without FIXED_NEWSTYLE or with unknown flags is refused;
* options: `EXPORT_NAME`, `GO`, `INFO` (always answered with NBD_INFO_EXPORT and NBD_INFO_BLOCK_SIZE: minimum =
  preferred = the volume block size, maximum payload 32 MiB), `LIST`, `ABORT`; any other option (STARTTLS,
  STRUCTURED_REPLY, META_CONTEXT, ...) gets `NBD_REP_ERR_UNSUP` and negotiation continues. The export is named `vnx`;
  the empty name means the default export;
* limits: option payloads above 8 KiB, more than 64 options, or a negotiation not finished within 10 s end the
  connection (PROTOCOL);
* transmission flags: HAS_FLAGS, SEND_FLUSH, SEND_FUA, SEND_TRIM, and READ_ONLY with `--read-only`. Simple replies only.

| command | behaviour |
|---|---|
| READ | bound-checked, then a VNX-IO read; data follows a successful reply |
| WRITE | the payload is always consumed (the stream stays in step); refused on a read-only device (EPERM), past the end (ENOSPC), above 32 MiB (EOVERFLOW, the payload is drained in 64 KiB pieces, never buffered); else one VNX-IO write |
| WRITE + FUA | answered only after the write AND a following flush succeeded |
| FLUSH | a barrier in the queue: waits for every earlier request, then flushes the cache and syncs the backend |
| TRIM | bound-checked (EINVAL past the end), refused read-only (EPERM), otherwise answered success **without releasing space**: the protocol leaves trimmed data undefined, so keeping it is correct; reclaiming space needs an unmap record in the volume (not in V14) |
| DISC | stop reading; answer every outstanding request; flush; close |
| anything else (WRITE_ZEROES, CACHE, BLOCK_STATUS, ...: not advertised) | EINVAL |
| zero-length READ/WRITE | EINVAL |
| a command flag other than FUA (none other is negotiated) | EINVAL (a write's payload is still consumed) |
| a malformed request header | PROTOCOL: the connection is closed |

## 4. The kernel interface

Kernel: Linux 6.8.0-146-generic (Ubuntu 24.04), x86_64; `CONFIG_BLK_DEV_NBD=m`. Module loaded for this phase with
`modprobe nbd max_part=8` (the kernel rounds `max_part` to 15; `nbds_max` 16, the default): partitions appear as
`/dev/nbdNpM`. Privileges: root, or CAP_SYS_ADMIN for the NBD ioctls plus write access to `/dev/nbdN`
(`root:disk 0660` here); `modprobe` needs root.

Attach sequence (all on the main thread; the kernel ties a device's setup to the task that sent `NBD_SET_SOCK`):

1. open the volume and backend, start the I/O queue; create a Unix socket pair; a server thread negotiates on one end
   while the main thread runs the client side (`NBD_OPT_GO`) on the other and checks size, flags and block size;
2. pick the device: the one given, or every `/dev/nbdN` whose sysfs entry shows no serving pid and size 0, in order;
   `open` refuses any path that is not `/dev/nbdN`, a running device and a configured one;
3. `NBD_SET_SOCK` first: it is the claim, refused (EBUSY) for a device another task set up, and `auto` moves on;
4. `NBD_SET_BLKSIZE` (512..=4096), `NBD_SET_SIZE_BLOCKS`, `NBD_SET_FLAGS` (the negotiated transmission flags),
   `NBD_SET_TIMEOUT` (kernel timeout, default 120 s);
5. `NBD_DO_IT` blocks while the device runs; a helper thread announces `attached` when sysfs shows this pid.

Detach (SIGINT/SIGTERM, a management `shutdown`, or the connection ending; SIGHUP is blocked and only logged, so a
dropped terminal never detaches a device a filesystem may be using): a controller thread, holding the
lock that says whether the device is still ours, fsyncs `/dev/nbdN` (the kernel writes its dirty buffers of the device
and sends FLUSH) and sends `NBD_DISCONNECT`; the kernel sends DISC, the server answers everything and flushes, `NBD_DO_IT`
returns, the main thread clears the device (`NBD_CLEAR_QUE`, `NBD_CLEAR_SOCK`) under the same lock, the queue drains and
the store is flushed again. After the clear no ioctl reaches the device (another process may claim it next).

* stop signals are blocked in every thread before any thread starts and received by one `sigwait` thread, so a signal
  never interrupts `NBD_DO_IT` (which would drop the connection without the clean sequence);
* an error or panic after the claim releases the device through a drop guard; a guard on the attach scope also wakes
  the controller and shuts the server socket, so every scoped thread ends and the process exits; the server's
  connection is shut down on every way out of the server (return, error, panic), so the kernel never waits on a FLUSH
  nobody will answer;
* SIGKILL: the kernel sees the socket close, fails outstanding and later requests with EIO, and frees the device when
  its last opener closes (after the unmount). The volume recovers by journal replay on the next open: every commit is
  either fully applied or absent (docs/V14_VOLUME_AND_IO.md).

## 5. Request path and status

```
reader thread ── parse, bound-check ── IoQueue (bounded, conflict-ordered, deadlines, cancellation)
replier threads ── wait for the outcome ── one reply per request
IoQueue ─ BlockIo (VNX-RAM io quota) ─ [ExtentCache] ─ Volume ─ ResilientMedia ─ [FaultGate] ─ MediaAdapter
```

* **status**: every request gets exactly one reply; a failure carries the errno of its `ErrorCode`
  (docs/V14_CONTRACTS.md §5): backend failures, corruption and timeouts EIO, a read-only (degraded) backend EPERM,
  invalid requests EINVAL/ENOSPC/EOVERFLOW, a shutting-down queue ESHUTDOWN. The kernel driver turns any non-zero
  status into a block-layer I/O error, which the filesystem reports as EIO (or EROFS once ext4 aborts its journal).
  Never success for a failed operation; TRIM is the documented no-op;
* **bounds**: a request's payload is at most 32 MiB (the kernel driver's largest request); the payloads in flight on a
  connection are bounded by `max_inflight_bytes` (64 MiB) and their number by `max_inflight` (128): the reader stops
  reading the socket when either is reached (backpressure). VNX-IO then leases each request's working memory from the
  VNX-RAM `io` quota, sized so that one largest request always fits (`ram_config`);
* **scheduling and cancellation**: the queue orders conflicting requests (overlapping ranges, one writing) in arrival
  order and runs the others in parallel (docs/V14_LOAD_BALANCER.md); each NBD request has one deadline
  (`--request-timeout-s` from its arrival, also bounding the flush of a FUA write): not started by then, it completes
  with TIMEOUT (EIO); the kernel timeout must be at least twice that plus 10 s, so a started request (which runs to
  completion, with the backend's bounded retries) is answered before the kernel gives up; when the connection dies,
  requests not started are cancelled;
* **write-back cache** (`--cache write-back`): single-extent writes are acknowledged from memory and written at the
  next FLUSH/FUA (filesystems issue them); data a filesystem never flushed is lost with the process, as with any
  volatile write cache. Multi-extent writes always go through to the volume;
* **fault drills** (`--fault-control FILE`): while FILE contains words from `put get sync remove list all`, those
  media operations fail with BACKEND_UNAVAILABLE (read at every operation; an unreadable file fails closed). Used by
  the tests; never on unless asked.

## 6. Health and diagnostics

* management socket (`--mgmt PATH --policy FILE`, mode 0660, at most 8 sessions): the `vnx.mgmt/1` protocol with the
  P4 authorisation (docs/V14_SECURITY_MODEL.md §4): `hello`, `authenticate`, `capacity`, `health`, `stats`,
  `shutdown` (admin). An idle session never delays shutdown;
* `health`: `ok`, `degraded` (backend degraded/unavailable or damaged extents) or `failed` (a server thread
  panicked); detail: backend health, damaged extents, last error, requests in flight;
* `stats`: NBD counters (connections, requests, reads, writes, FUA writes, flushes, trims, bytes, errors, EIO,
  protocol errors, cancelled on close, peak in flight), queue, volume, cache, VNX-RAM and injected-fault counters. The
  same health and counters are written to the log when the device stops.

## 7. Recovery behaviour seen from the host

| event | host sees | after |
|---|---|---|
| backend read fails | EIO for that read | reads of other extents keep working |
| backend writes fail | EIO, then EPERM while the backend is unavailable (read-only device); ext4 aborts its journal | a write probe every 5 s; writes resume when it succeeds (the probe timer runs from the last failure) |
| clean detach | — | `/sys/block/nbdN/pid` gone, size 0 |
| `vnxd` killed | EIO for in-flight and later I/O; unmount succeeds with errors | re-attach replays the journal; `e2fsck -y` replays the ext4 journal; every file synced before the kill reads back with the same SHA-256 (test) |

## 8. Unsafe-boundary audit

`nbdsys.rs` is the only module with `unsafe` (the crate is `#![deny(unsafe_code)]`; the module opts out with a documented
`#![allow]`). Every call goes to the C library std already links; no new dependency.

| call | declaration | arguments and why they are valid | failure handling |
|---|---|---|---|
| `ioctl` (9 requests) | `fn ioctl(fd: c_int, request: c_ulong, ...) -> c_int` (variadic, as in libc) | `fd`: the `File` of `/dev/nbdN` borrowed for the call (cannot be closed meanwhile). Request numbers are `_IO(0xab, n)` from `include/uapi/linux/nbd.h` (no size, no direction). The third argument is always an integer by value: a socket fd (`NBD_SET_SOCK`, kept open by the caller until the device is cleared), a block size, a block count, flags, seconds, or 0. **No pointer is ever passed**, so the kernel never reads or writes our memory through these calls | `rc < 0` -> `last_os_error()` mapped: EBUSY BACKEND_BUSY, EPERM/EACCES UNAUTHORIZED, ENOENT/ENXIO/ENODEV NOT_FOUND, others BACKEND_UNAVAILABLE |
| `sigemptyset`, `sigaddset` | `fn(*mut SigSet[, c_int]) -> c_int` | pointer to an owned `SigSet` = `[u64; 16]` (128 bytes: `sigset_t` of glibc and musl on Linux, x86_64 and aarch64), `#[repr(C)]`, aligned; signal numbers 1, 2, 15 are valid on both architectures | cannot fail for valid pointers and numbers |
| `pthread_sigmask` | `fn(c_int, *const SigSet, *mut SigSet) -> c_int` | `SIG_BLOCK` = 0 (Linux generic ABI); the set lives in the caller's frame; old-set null (allowed by POSIX) | non-zero -> INTERNAL, the service does not start |
| `sigwait` | `fn(*const SigSet, *mut c_int) -> c_int` | both pointers to values in the caller's frame | non-zero -> INTERNAL (the signal thread ends; stop then comes from the management socket or the connection) |

Thread rules the code keeps: `NBD_SET_SOCK`, the configuration ioctls and `NBD_DO_IT` run on the main thread (the
kernel refuses `NBD_SET_SOCK` from another task while one is setting a device up); `fsync` and `NBD_DISCONNECT` from
the controller thread are allowed by the driver and run only while the device is still ours (one lock, checked).
Things deliberately not used: the netlink interface (not needed), `mmap`, raw `syscall`, any pointer argument.

## 9. Hazards and safety rules

* **Never put swap or the backend on the exported device.** The backend's writes go through the host's page cache;
  if the device they come from is itself waiting for memory to be written back (swap, or the backend directory on a
  filesystem mounted from an NBD device), the server can deadlock. `vnxd` refuses a volume or backend directory on a
  filesystem whose source is an `/dev/nbd*` or `/dev/ublkb*` device; swap is the operator's responsibility.
* Memory pressure: the server runs as an ordinary process; under heavy host memory pressure its allocations can
  stall the device (the kernel timeout, 120 s by default, then fails the connection). The bounded payload budget and
  VNX-RAM limit what the server itself holds.
* Detaching a mounted device fails the filesystem's later I/O; `vnxd` logs `detach_while_mounted` and proceeds
  (a stop signal must stop). Unmount first.
* Tests and drills only ever touch an `/dev/nbdN` that their own `vnxd` attached, over disposable files: before every
  `mkfs`, `sfdisk`, `blockdev`, `mount`, `e2fsck` or raw I/O the harness (`tests/v14/nbd_support.py`) checks the path
  pattern `/dev/nbdN[pM]`, that `/sys/block/nbdN/pid` is the pid of the `vnxd` it started, and that nothing of the device
  is mounted; teardown (unmount, stop, wait for the device to be free) runs in `finally` blocks; only exact PIDs are
  signalled. `vnxd` itself refuses any device path that is not `/dev/nbdN` and any device that is running or configured.
* The backend directory is locked (`.lock` with the owner's pid; a stale lock of a dead process is taken over).

## 10. Dependencies and platforms

* Runtime: Linux with the `nbd` module (or built in) for `attach`; any Linux for `serve`. Standard library only, no
  crates (decision D2). glibc or musl (`sigset_t` layout).
* Test tools (kernel tests only): util-linux (`sfdisk`, `blockdev`, `mount`, `umount`), e2fsprogs (`mkfs.ext4`,
  `e2fsck`), udev (`udevadm settle`). The kernel tests skip with the reason when not root, when the module is not
  loaded (`modprobe nbd max_part=8`) or a tool is missing; the socket tests run anywhere cargo is installed.
* Unsupported: non-Linux hosts (no NBD driver: use `serve` only where it builds, untested off Linux); kernels without
  NBD; containers without CAP_SYS_ADMIN or without `/dev/nbd*`; block sizes above 4096 (the driver's limit);
  partitions need `max_part` > 0 at module load. ARM64: cross-built and tested under qemu-user in P10
  (functional only; V14_BUILD_AND_DEPLOYMENT.md, V14_RASPBERRY_PI_PROFILE.md); not run on Pi hardware.

## 11. Stage A': ublk (design and gap)

Measured on this host: `modprobe ublk_drv` succeeds, creates `/dev/ublk-control` (char 10:261, root 0600), module
parameter `ublks_max` 64; `modprobe -r ublk_drv` unloads it (it was unloaded again after the check). Nothing else was
run: no ublk device was created and **no ublk numbers exist**.

What a std-only ublk server needs (the gap): an io_uring instance created with `IORING_SETUP_SQE128` (ublk control
commands live in the 80-byte command area of a 128-byte SQE) through `syscall(SYS_io_uring_setup)`, `mmap` of the SQ
ring, CQ ring and SQE array, and `io_uring_enter`; release/acquire ordering on the ring head/tail words shared with the
kernel; control commands on `/dev/ublk-control` (`ADD_DEV`, `SET_PARAMS` with logical block size and capacity,
`START_DEV`, `STOP_DEV`, `DEL_DEV`); per queue, `/dev/ublkcN` opened, its read-only I/O descriptor array mapped, one
`UBLK_IO_FETCH_REQ` per tag with a buffer, then `UBLK_IO_COMMIT_AND_FETCH_REQ` with each result; `START_DEV` only after
every queue has its fetches queued. The same `IoQueue` would serve the requests. That is several hundred lines of
`unsafe` (raw shared-memory rings, user buffers handed to the kernel by address) — a much larger audited surface than
NBD's integer-only ioctls — plus its own teardown rules (a dead server leaves `/dev/ublkbN` until `DEL_DEV`, or recovery
with `UBLK_F_USER_RECOVERY`). It was not started in P8 so that it is not half-built; the expected gain (no socket copy
per request) is unmeasured, and is only worth the surface once the P9 profile shows the NBD socket hop matters next to
the volume's per-extent fsyncs (the throughput in the P8 report is bound by the backend's fsyncs, not by the socket).

## 13. Measured (evidence category B on C)

`experiments/v14/device/p8-nbd/results.json` (commit 86c16c0, idle host, 8 vCPU, Linux 6.8.0-146): the acceptance
transcript (11 files, 19,148,626 bytes on ext4 in a partition of a 128 MiB device; all SHA-256 equal after detach and
re-attach; `e2fsck -f -n` exit 0) and raw-device O_DIRECT throughput, 64 MiB in 1 MiB requests, medians of 3:

| cache | write MiB/s | read MiB/s |
|---|---|---|
| none | 52.6 | 306.3 |
| write-through | 47.4 | 926.4 |
| write-back | 58.3 | 874.1 |

Writes are bound by the backend's per-generation fsyncs and the journal's fsync per transaction (1 MiB requests span
16 extents and always go through to the volume, so write-back does not change them); cached reads skip the backend's
read and SHA-256 check of the object. These are numbers of this host's virtual disk and page cache, not of any
storage medium, and say nothing about DNA.

## 12. Stage B: hosts without the kernel interface

The same device, without a kernel block device, is `vnxd nbd serve --socket PATH`: identical VNX-IO, volume, cache,
fallback, error status and flush/FUA semantics, reachable by any NBD client library (qemu, libnbd, the Python client in
`tests/v14/nbd_support.py`). It is **not** a block device: nothing in the kernel sees it.

The Python SDK/CLI (`vnxdna.sdk` `volume_create/volume_write/volume_read/volume_stat/volume_scrub`, `vnx volume ...`)
is the V13 codec volume (`vnx.volume/1`: byte-addressed, write-back buffer, extents encoded as archives at `flush`). Its
semantics match on the points the host relies on — a read returns the last write; a completed `flush` makes everything
before it durable; an extent commit is all-or-nothing; failures raise typed errors, never return other bytes — and
differ in format and granularity (no blocks, no FUA, no trim, one writer per volume). Bringing the SDK onto the V14
block volume (a client of `vnxd nbd serve` or of the management socket) is left to P9, after the P6 F SDK/CLI changes
are merged, so that P8 does not edit the SDK in parallel with them.
