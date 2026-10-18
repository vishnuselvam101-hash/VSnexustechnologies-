# V14 Phase 8 report: the Linux block device

Date 2026-10-18. Branch `work/v14-p8` from `build/v14` b69e70f, with `build/v14` 41fcc51 (P6 D/F, P7) merged in.
Scope: [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P8 (master directive §14 Stages A, A', B). Specification:
[V14_DEVICE_INTERFACE.md](V14_DEVICE_INTERFACE.md).

Evidence categories: B (a software block device, `/dev/nbdN`, on this host) over C (the electronic image backend). Nothing
is D, E or F: no DNA was involved in this phase.

## Implemented

* **Stage A, kernel block device** (`vnxd nbd create | attach | serve`):
  * `nbd.rs` implements the NBD protocol: fixed newstyle negotiation on both the server and the client side, with bounded parsing, and the transmission formats.
  * `nbdsys.rs` holds the `/dev/nbdN` ioctls and the signal mask. It is the crate's only `unsafe` module, audited in §8 of the spec.
  * `nbdserve.rs` is the request path:
    * a reader thread and reply threads;
    * a budget of payload bytes and requests in flight;
    * one deadline per request;
    * cancellation when the connection dies;
    * FUA handled as a write followed by a flush;
    * DISC: every outstanding request is answered, then the store is flushed.

    It sits over `IoQueue` → `BlockIo` (VNX-RAM) → `ExtentCache` → `Volume` → `ResilientMedia` → an optional `FaultGate` → the media adapter.
  * `nbdctl.rs` runs the lifecycle:
    * the service negotiates with itself over a socket pair, claims the device with `NBD_SET_SOCK`, configures it and runs `NBD_DO_IT`, all on the main thread;
    * a controller detaches cleanly (device fsync, `NBD_DISCONNECT`, drain, flush) on SIGINT/SIGTERM, a management `shutdown` or the end of the connection;
    * drop guards release the device on every way out;
    * the management socket runs the P4 authorisation.
* **Backend:** the device runs over the P7 `ImageMedia` by default (`--media image`). The P8 interim adapter `DirMedia` remains available as `--media dir`.
* **Fault drills:** `--fault-control FILE` makes the chosen media operations fail at run time.
* **Fix in the P4 fallback** (`fallback.rs`): an operation that succeeded while the backend was UNAVAILABLE restarted the probe interval. Under the steady reads of a mounted filesystem the device therefore stayed read-only for good. The interval now runs from the last failure that counts against the backend. It was found by the kernel fault test and has a regression test.
* **Stage A', ublk:** not built. This host loads `ublk_drv` and creates `/dev/ublk-control`; the module was unloaded again. The design and the gap are in §11 of the spec: an io_uring server with shared rings and user buffers is a much larger `unsafe` surface than NBD's integer-only ioctls. There are no ublk numbers.
* **Stage B:** documented in §12 of the spec. `vnxd nbd serve` offers the same device to userspace NBD clients without a kernel device. The SDK/CLI volume is the V13 format; moving it onto the V14 block volume is left to P9, because P6F changed the SDK in parallel. This is never called a block device.

Changes to shared files:
* `lib.rs`: module lines only; the merge conflict with P7 was resolved by keeping both lists.
* `bin/vnxd.rs`: an `nbd` dispatch, 3 lines.
* `fallback.rs`: the fix above and its test.
* `docs/V14_FAILURE_AND_RECOVERY_MODEL.md` §4: one sentence.
* `media.rs`, `io.rs` and `Cargo.toml` are unchanged, and no crate was added.

## End-to-end acceptance (root, nbd module)

The module was loaded with `modprobe nbd max_part=8`; the kernel rounds `max_part` to 15, and `nbds_max` is 16. The steps:
1. sfdisk writes one partition on a 128 MiB device;
2. `mkfs.ext4` on `/dev/nbdNp1`, then mount;
3. create and copy 11 files of 19,148,626 bytes in total, then sync and unmount;
4. detach with SIGTERM: exit 0, and the device is free;
5. a NEW `vnxd` process attaches over the same backend, `e2fsck -f -n` exits 0, and the filesystem is mounted read-only;
6. **the SHA-256 of every file matches** (`experiments/v14/device/p8-nbd/results.json`, and the test `test_kernel_ext4_files_survive_detach_and_reattach`).

The test also checks that the kernel's FLUSH and FUA writes reached the server and that none failed.

| test (tests/v14/test_device_nbd.py) | result |
|---|---|
| kernel: ext4 files survive detach + re-attach (above) | PASS |
| kernel: backend faults reach the filesystem as EIO (O_DIRECT read of a file and of the raw device → EIO; write + fsync → EIO/EROFS); the backend recovers; the data synced before is intact after `e2fsck -y` | PASS |
| kernel: `kill -9` of the server while mounted with unsynced writes → unmount, device freed by the kernel, new `vnxd` replays the journal (0 damaged extents), `e2fsck -y`, every synced file identical | PASS |
| kernel: a detach whose device fsync fails exits 1 (B3) | PASS |
| socket: protocol round trip against a byte model, precise errno (EINVAL/ENOSPC/EOVERFLOW/EPERM/EIO), TRIM, FUA, a second client | PASS |
| socket: faults → EIO/EPERM, recovery after the probe interval | PASS |
| socket: management socket (hello, auth, capacity, health, stats, shutdown with an open session) | PASS |
| socket: `kill -9` with pipelined writes → flushed blocks intact, the others old-or-new only | PASS |
| socket: SIGTERM with stderr closed (B1); no fd leak over 60 connections (B2); a failed final flush exits 1 (B3) | PASS |
| CLI usage errors; the harness refuses foreign devices | PASS |

Repeat runs of `test_device_nbd.py`:
* before the review fixes: 16 of 16 green with 2 pytest processes × 3 workers;
* after them: 4 of 4 green with `-n 8`.

The Python harness refuses a device unless its path matches `/dev/nbdN[pM]`, `/sys/block/nbdN/pid` is the pid of the `vnxd` that this test started, and none of it is mounted. These checks raise exceptions, so they also run under `python -O`. Teardown runs in `finally` blocks.

## Measured (B on C; idle host; `experiments/v14/device/p8-nbd/results.json`, commit 86c16c0)

Raw-device O_DIRECT, 64 MiB in 1 MiB requests, medians of 3:

| cache | write MiB/s | read MiB/s |
|---|---|---|
| none | 52.6 | 306.3 |
| write-through | 47.4 | 926.4 |
| write-back | 58.3 | 874.1 |

Writes are bound by the fsyncs of the backend and the journal. These are numbers of this host's disk and page cache, not of any medium.

## Tests and exact results

| check | result |
|---|---|
| `cargo test --release --offline --locked`, `RUSTFLAGS=-D warnings` (vnxd) | build clean; **207 passed**, 0 failed (179 unit, 2 CLI, 2 concurrency stress, 8 fault matrix, 4 golden, 12 media adapters; gate 178) |
| `ruff` (new tests, experiment) | clean |
| full suite (8 workers, systemd scope, idle host, root with the nbd module: kernel tests ran) | **4,723 passed, 4 skipped, 0 failed**, 318 s (gate 4,711 / 4 / 0; P8 adds 12) |

## Review

One independent review was run on b69e70f..5a1bd65. It confirmed the ioctl boundary: integer arguments only, the `_IO` numbers, the `sigset_t` size, the thread of `NBD_SET_SOCK`, and no ioctl after the clear. It also found no budget deadlock and judged the fallback fix correct. Its findings:

| finding | severity | fix |
|---|---|---|
| B1: `eprintln!` panics when stderr is gone. It killed the signal thread (SIGTERM was then ignored forever) or the controller (no disconnect). Reproduced | blocking | logging never panics; the stop is requested before logging; the signal thread loops; test |
| B2: `serve` kept a clone of every connection: one leaked fd per connection, then EMFILE and the backend marked unavailable. Reproduced | blocking | only live connections are kept; test |
| B3: `attach` exited 0 although the device failed (device fsync at detach, `NBD_DO_IT` error, an unrequested end, a server failure) | blocking | each is recorded and makes `attach` fail; tests (socket and kernel) |
| M1: a server thread that ended by `?` or a panic left the socket open, so the kernel waited on a FLUSH until its timeout (forever with timeout 0) | minor | the socket is shut down on every way out of `serve` and of the server thread; a kernel timeout of 0 is refused |
| M2: DirMedia lock races | minor | the lock is linked into place holding its pid; the remaining stale-takeover race is documented, and ImageMedia (which has its own lock) is the default |
| M3: idle clients could hold every `serve` slot | minor | 10 s negotiation timeout; unit test |
| M4: SIGHUP (a dropped SSH session) detached a mounted device | minor | SIGHUP is only logged |
| M5: the deadline did not cover the flush of a FUA write; the margin against the kernel timeout was not checked | minor | one deadline per request; kernel timeout ≥ 2 × request timeout + 10 s |
| M6: the harness safety checks were `assert`s (stripped by `-O`); `sfdisk --dump` bypassed the check | minor | they raise `UnsafeDeviceError`; everything goes through `run_tool`; test |
| M7: the backend-on-NBD check missed paths that do not exist yet and escaped mount points | minor | nearest existing ancestor; `\ooo` decoded; test |
| M8: the attach teardown has no kernel-free tests | minor | open: covered by the kernel tests only (they skip without root). A fake-device trait for CI is a P9 item |
| nits: unknown command flags accepted; a short read could put the reply stream out of step; stale in-flight gauge; `attach` left its management socket; timing margin of the fallback test; doc comment; an unasserted test variable | nit | EINVAL; EIO on a length mismatch; the gauge is decremented; the socket is removed; margins tripled; fixed |

The first full-suite run after the fixes failed one test: the kernel fault test's final detach exited 1. The cause is
not a regression. Writes to the device really failed during the fault window, and when one failed in the device's own
buffers the kernel reports it once to the detach fsync, which B3 now turns into exit 1. Which ext4 path hits the fault
decides whether that happens, so the test now accepts exactly two outcomes: exit 0, or exit 1 whose only failure is
that fsync error, with the store flushed and healthy in both cases. The rerun of the full suite passed.

Found by our own tests while P8 was being written: the fallback probe-timer bug above; `udevd` holding the device during `BLKRRPART` (the harness now settles udev and retries); log ordering between two threads (the assertion now checks the real invariant).

## Teardown

After every run, and after the final one: `ls /sys/block/nbd*/pid` shows nothing, `/proc/mounts` has no `nbd` entry, and `lsblk` shows every `nbd` device at 0B. The `nbd` module stays loaded (`modprobe -r nbd` removes it). `ublk_drv` is not loaded.

## Acceptance (P8 criteria of V14_MASTER_PLAN)

| criterion | result |
|---|---|
| on a disposable image: fdisk → mkfs.ext4 → mount → copy files → umount → restart → mount → byte-identical readback | PASS (SHA-256 of all 11 files; a new process over the same backend) |
| flush/FUA honoured | PASS (the kernel's FLUSH and FUA reach the server; FUA is answered after write + flush; FLUSH is a queue barrier) |
| teardown always runs | PASS (signal, management, connection end, error and panic paths; `kill -9` recovered; final check clean) |
| only devices vnxd created are touched | PASS (checked in `vnxd` and in the harness before every tool call) |
| Stage A' | NOT DONE: design and gap documented, no numbers |
| Stage B | documented (`serve` + SDK semantics); no SDK code change in P8 |

Phase 8: **Stage A PASS**; A' documented gap; B documented.
