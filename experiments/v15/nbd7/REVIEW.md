# V15 review: NBD kernel 7.0 readiness gate (work/v15-nbd7)

Reviewed commits f5a29df (announce an attached NBD device only once the kernel has sized it) and c3bda1e (readiness
rule and kernel matrix). Files: `src/vnxdna/native/rust/vnxd/src/nbdsys.rs` (`device_ready`, `ready_state`),
`src/vnxdna/native/rust/vnxd/src/nbdctl.rs` (`attach` readiness watcher), `tests/v14/test_device_nbd.py`.

## 1. Sizes that are not a multiple of 512

`ready_state` requires `size_bytes > 0 && size_bytes % 512 == 0 && seen_sectors == Some(size_bytes / 512)`, so a
size of 0 or an unaligned size is never ready, even when the rounded-down sector count matches. Valid volumes cannot
produce such a size: `Geometry::new` requires a power-of-two block size of at least 512. No extra check in `attach`.

## 2. Timeout path

If the device is not ready within 30 s while the device is still running, the watcher logs `attach_not_ready`
(device, pid, sectors, size) and requests a stop; the controller detaches, the claim is released, and `attach`
reports the failure. The watcher already returns right after announcing `attached`, so a healthy device never
falls through to the timeout branch.

## 3. Unit coverage of the pure rule

The existing `ready_needs_our_pid_and_the_full_size` covers the Linux 7.0 window (pid published, capacity 0), a
missing or foreign pid, one sector short, missing sysfs size and size 0. Added
`ready_rejects_unaligned_sizes_and_any_sector_mismatch`: exact match at 512 B and 64 MiB, seven unaligned sizes
(1, 511, 513, 1023, 4097, 64 MiB + 1, 64 MiB + 511), one sector too many and too few, and a stale pid.

## 4. Stop requested during the readiness check

`running` is cleared only after `NBD_DO_IT` returns, so a stop requested while `device_ready` reads sysfs was not
seen before `announce("attached")`. The watcher now re-checks `stop.requested()` after a positive readiness check and
returns without announcing a device that is being detached.

## Verdict

Approve for merge into build/v15 with the two changes above (one guard in `nbdctl.rs`, one test in `nbdsys.rs`).
