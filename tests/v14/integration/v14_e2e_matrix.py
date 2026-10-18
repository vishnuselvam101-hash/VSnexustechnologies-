"""The V14 §19 end-to-end matrix: one entry per row of docs/V14_VALIDATION_PLAN.md §3.

Every row names the public interface it drives, how the fault is injected, the outcome recorded BEFORE it was run,
the phase whose deliverable it needs, and where it is implemented:

* ``rust:<fn>``: a test in ``src/vnxdna/native/rust/vnxd/tests/e2e_matrix.rs`` (the integrated vnxd stack driven
  through its I/O queue);
* ``py:<file>::<fn>``: a test in this directory.

``status`` is ``run`` (runs now and must pass) or ``pending`` (written against the documented interface, skipped or
ignored with a reason starting "needs P7/P8 integration" or "needs P10/P11 hardware" until the lead turns it on).
A pending row is never counted as a pass. test_v14_e2e_registry.py keeps this table, the plan and the code in step.
"""
from __future__ import annotations

from typing import NamedTuple


class Row(NamedTuple):
    id: str
    title: str
    interface: str
    fault: str
    expected: str
    depends: str
    impl: str
    status: str  # "run" | "pending"


IOQ = "vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media)"
BLK = "public block device (/dev/nbdN exported by vnxd, P8)"

ROWS: tuple[Row, ...] = (
    Row("R01", "sequential read/write", IOQ, "none (3 cache modes, restart)",
        "identical bytes before and after flush + restart; no orphans; no leaked I/O memory", "P2-P5",
        "rust:r01_sequential_rw", "run"),
    Row("R02", "random read/write", IOQ, "seeded unaligned requests (3 seeds x 3 cache modes)",
        "every read equals the oracle", "P2-P5", "rust:r02_random_rw", "run"),
    Row("R03", "small files (sub-block writes)", IOQ, "1-511 B writes at seeded offsets",
        "every read equals the oracle (read-modify-write)", "P2-P3", "rust:r03_small_writes", "run"),
    Row("R04", "large files and request limits", IOQ, "8 MiB requests over 2048 extents; 64 MiB + 1; past the end",
        "exact readback (multi-transaction); INVALID_ARGUMENT and OUT_OF_RANGE at submission, no effect", "P2",
        "rust:r04_large_requests_and_limits", "run"),
    Row("R05", "partial overwrites", IOQ, "overlapping unaligned writes across extent boundaries",
        "every read equals the oracle; one live generation per extent", "P2-P3", "rust:r05_partial_overwrites", "run"),
    Row("R06", "concurrency", IOQ, "4 submitters x 4 outstanding on a 16-extent hot region (5 cache/read-ahead configs)",
        "every read equals the oracle replayed in queue sequence order", "P2-P5",
        "rust:r06_concurrent_readers_writers", "run"),
    Row("R06b", "concurrency under backend faults", IOQ, "40 seeded FaultMedia rules (11 fault kinds) during R06",
        "successful reads equal the oracle; failed writes have no effect; typed errors; clean after recovery",
        "P2-P5", "rust:r06b_concurrent_io_under_backend_faults", "run"),
    Row("R07", "cache hit/miss", IOQ, "restart (cold cache), repeated reads, write after cached read",
        "first read miss, second hit; no stale read after a write", "P3", "rust:r07_cache_hit_miss", "run"),
    Row("R08", "dirty eviction + failed writeback", IOQ, "write-back, 4-extent cache quota; Put unavailable during flush",
        "evictions write back; failed flush reported, data stays dirty and readable; retried flush durable", "P3-P4",
        "rust:r08_dirty_eviction_and_failed_writeback", "run"),
    Row("R09", "flush/sync", IOQ, "restart without flush; Sync busy",
        "write-through VERIFIED durable; write-back STAGED lost without flush, old bytes intact; sync failure fails the flush",
        "P2-P3", "rust:r09_flush_and_durability", "run"),
    Row("R10", "device restart", IOQ, "drained stop + restart; stop without drain behind a slow backend",
        "clean replay; queued requests SHUTTING_DOWN with no effect; device = oracle of completed requests", "P2-P5",
        "rust:r10_device_restart", "run"),
    Row("R11a", "process crash at every commit stage", IOQ, "volume crash hook at 5 stages of a 3-extent write",
        "all 3 extents old before the durable map record, all new after; torn record reported; orphans collected",
        "P2", "rust:r11a_crash_at_every_commit_stage_is_atomic", "run"),
    Row("R11b", "real process crash (SIGKILL)", IOQ + " over a durable file backend", "SIGKILL of a writer process, 4 rounds (write-through, write-back)",
        "every extent holds a complete version, never older than the last durable one (VERIFIED, or flushed in write-back)", "P2",
        "rust:r11b_sigkill_during_writes_keeps_acknowledged_data", "run"),
    Row("R12", "journal recovery", IOQ + " + journal file", "torn tail; one copy damaged; both copies of a middle record",
        "tail reported and cut; repaired; CORRUPTION refusal with the backend untouched", "P2",
        "rust:r12_journal_recovery", "run"),
    Row("R13", "missing/corrupt extents", IOQ, "backend object deleted / bit-flipped",
        "CORRUPTION on every read of the damaged extent, never other bytes; listed at restart and by scrub; rewrite repairs",
        "P2-P4", "rust:r13_missing_and_corrupt_extents", "run"),
    Row("R14", "duplicate/reordered reads", IOQ, "40 identical concurrent reads + shuffled extent reads; one bit-rot read",
        "every successful read equals the oracle; at most the faulted read fails, with CORRUPTION", "P2-P5",
        "rust:r14_duplicate_and_reordered_reads", "run"),
    Row("R15", "out of space", IOQ, "max_stored_bytes = 8 extents",
        "NO_SPACE (ENOSPC) beyond the bound, nothing changes; overwrite at the bound succeeds or NO_SPACE", "P2",
        "rust:r15_out_of_space", "run"),
    Row("R16", "backend timeouts + deadlines", IOQ, "Put timeout once / always; queue deadline behind a slow write",
        "single timeout absorbed; persistent -> TIMEOUT then READ_ONLY, reads keep working; deadline -> TIMEOUT, no effect",
        "P4-P5", "rust:r16_backend_timeouts_and_deadlines", "run"),
    Row("R17", "failed verification", IOQ, "silent corruption, wrong receipt, torn put, corrupt+ack lost",
        "CORRUPTION; none of the 3 extents of the request changes; no orphans", "P2-P4",
        "rust:r17_failed_verification_is_refused_atomically", "run"),
    Row("R18", "unauthorized requests (management + sealing)", "vnx.mgmt protocol sessions over the device; sealed volume",
        "no token, reader token, 3 wrong tokens, wrong device key, tampered sealed object",
        "PROTOCOL before hello; UNAUTHORIZED without role; reader cannot shut down; cut off after 3 failures; "
        "wrong key UNAUTHORIZED; tampered object CORRUPTION", "P4", "rust:r18_unauthorized_requests", "run"),
    Row("R18b", "codec-ipc worker protocol abuse", "vnx.codec-ipc/1 worker process (stdin/stdout)",
        "malformed JSON, wrong proto/version, path names, wrong SHA-256, oversize line",
        "typed error response for each, worker keeps serving, nothing written outside staging", "P4",
        "py:test_v14_e2e_python.py::test_r18b_codec_ipc_worker_rejects_abuse", "run"),
    Row("R19", "memory exhaustion", IOQ, "512 KiB VNX-RAM budget, request over the io quota, 8 competing requests",
        "INVALID_ARGUMENT for the oversize request; competing requests wait and succeed; peaks within quotas", "P3",
        "rust:r19_memory_exhaustion", "run"),
    Row("R20", "saturation", IOQ, "1 worker, depth 4, 10 ms backend, 40 try_submit",
        "excess RESOURCE_EXHAUSTED, counted; queue <= 4; accepted writes complete", "P5",
        "rust:r20_saturation_backpressure", "run"),
    Row("R21", "cancellation", IOQ, "9 requests cancelled behind a 50 ms write",
        "CANCELLED at once, no effect, no leaked memory; queue keeps serving", "P2-P5", "rust:r21_cancellation", "run"),
    Row("R22", "native/worker failure at the sealing boundary", IOQ + " + sealing worker process",
        "worker exits / answers garbage / hangs",
        "typed BACKEND_UNAVAILABLE/TIMEOUT/PROTOCOL; no device, nothing stored, no journal left, no staging residue", "P4",
        "rust:r22_sealing_worker_failure", "run"),
    Row("R22b", "native codec kernel failure", "vnx CLI (decode) + SDK", "every kernel forced to the reference; junk library paths",
        "byte-identical container (same SHA-256) as the native run; no crash", "P6",
        "py:test_v14_e2e_python.py::test_r22b_native_kernels_off_or_broken_give_identical_bytes", "run"),
    Row("R23", "configuration corruption (service)", "vnxd policy/queue/RAM config, volume dir, journal",
        "garbage policy, open modes, symlink, out-of-range configs, foreign backend, superblock damaged",
        "each refused with its recorded typed error before any data moves", "P2-P4", "rust:r23_config_corruption", "run"),
    Row("R23b", "configuration corruption (SDK/worker)", "SDK config loader; codec-ipc worker start",
        "unknown key, malformed JSON, bad key file, key file readable by others",
        "VNXConfigurationError / exit 2 with a reason; nothing written", "P4/P6",
        "py:test_v14_e2e_python.py::test_r23b_sdk_and_worker_config_corruption", "run"),
    Row("R24", "Pi-class memory budget (SIMULATED on this host)", IOQ, "1 MiB extents, 32 MiB budget, 2 workers",
        "correct data; every subsystem peak within quota; total within budget", "P3/P10",
        "rust:r24_pi_class_memory_budget", "run"),
    Row("R24-hw", "Pi memory constraints on the real board", BLK + " on a Raspberry Pi", "memory pressure, kill + restart",
        "budgets of docs/V14_RASPBERRY_PI_PROFILE.md met on hardware", "P8/P10/P11",
        "py:test_v14_e2e_blockdev.py::test_r24hw_pi_memory_on_hardware", "pending"),
    Row("R25", "historical format compatibility", "vnx CLI (decode/verify) on committed V5/V6 fixtures",
        "clean fixtures; reads with 90 % of the records dropped",
        "every fixture decodes to its recorded SHA-256; the damaged one fails with a non-zero exit and no output", "P6",
        "py:test_v14_e2e_python.py::test_r25_historical_fixtures_decode_through_the_cli", "run"),
    Row("M01", "file-image media adapter", IOQ + " over the P7 adapter", "conformance + workload + restart",
        "as R02 + R10", "P7", "rust:m01_file_image_adapter", "pending"),
    Row("M02", "DNA-simulator media adapter", IOQ + " over the P7 adapter", "SIMULATED channel loss",
        "oracle bytes or a typed error, never other bytes", "P7", "rust:m02_dna_simulator_adapter", "pending"),
    Row("M03", "fault-injection media adapter", IOQ + " over the P7 adapter", "the adapter's seeded fault config",
        "the R13-R17 outcomes", "P7", "rust:m03_fault_injection_adapter", "pending"),
    Row("M04", "physical-interface media adapter", IOQ + " over the P7 adapter", "no hardware",
        "every operation a typed error; no device is created", "P7", "rust:m04_physical_interface_adapter_refuses",
        "pending"),
    Row("B01", "sequential + random R/W on the block device", BLK, "O_DIRECT pread/pwrite, seeded",
        "every read equals the oracle", "P8", "py:test_v14_e2e_blockdev.py::test_b01_block_device_rw", "pending"),
    Row("B02", "filesystem: small/large files, partial overwrite", BLK + " + ext4", "mkfs, mount, files, umount, fsck",
        "fsck clean; every file's SHA-256 equal after remount", "P8",
        "py:test_v14_e2e_blockdev.py::test_b02_filesystem_files", "pending"),
    Row("B03", "flush/sync through the host", BLK, "fsync + BLKFLSBUF, service restart",
        "fsynced data survives a service restart", "P8", "py:test_v14_e2e_blockdev.py::test_b03_fsync_survives_restart",
        "pending"),
    Row("B04", "device restart", BLK, "service stop/start, reconnect", "device bytes unchanged", "P8",
        "py:test_v14_e2e_blockdev.py::test_b04_device_restart", "pending"),
    Row("B05", "service crash under filesystem load", BLK + " + ext4", "SIGKILL of vnxd during writes",
        "fsck clean after reconnect; fsynced files intact", "P8",
        "py:test_v14_e2e_blockdev.py::test_b05_service_kill_under_load", "pending"),
    Row("B06", "out of space to the host", BLK, "backend bound smaller than the device", "ENOSPC to the writer, no corruption",
        "P8", "py:test_v14_e2e_blockdev.py::test_b06_out_of_space", "pending"),
    Row("B07", "backend failure to the host", BLK + " over the P7 fault adapter", "backend unavailable",
        "EIO to the host; reads of committed data keep working; no wrong bytes", "P7/P8",
        "py:test_v14_e2e_blockdev.py::test_b07_backend_failure_is_eio", "pending"),
    Row("B08", "unauthorized management over the socket", "vnx.mgmt Unix socket of the running service",
        "no token / reader token / other uid", "UNAUTHORIZED; shutdown only for admin", "P8",
        "py:test_v14_e2e_blockdev.py::test_b08_mgmt_socket_authz", "pending"),
    Row("B09", "queue-depth saturation on the block device", BLK, "1-32 concurrent writers",
        "no errors, no data mismatch, bounded service RSS", "P8",
        "py:test_v14_e2e_blockdev.py::test_b09_queue_depth_saturation", "pending"),
)

PENDING_PREFIXES = ("needs P7/P8 integration", "needs P10/P11 hardware")
