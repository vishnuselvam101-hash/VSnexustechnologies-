# V14 validation plan (Phase 9)

Master directive §19 (integrated testing + fault injection) and §20 (performance sequence);
[V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P9: "the §19 end-to-end matrix through the public device interface;
acceptance: full suite + E2E matrix green, benchmarks reproducible from committed commands, no silent corruption in
any fault run".

**Status.** Written in the P9 preparation pass on `build/v14` b69e70f, while P6-P8 were still being built, then
completed after `build/v14` e231eaa (P6 A-F, P7 media adapters, P8 NBD block device) was merged into `work/v14-p9`:
every row that needed P7 or P8 now runs against the merged code (the M-rows over the real P7 adapters, the B-rows
through a real `/dev/nbdN` attached by `vnxd nbd attach`). One row stays pending: R24-hw needs a Raspberry Pi (P10/P11).
Evidence categories: B (software storage device), C (electronic-backend persistence: image/file backends on the
host's disk); M02 and M04 also D (SIMULATED channel / SIMULATED lab, TEST-ONLY codec). Nothing here touches DNA. The
block-device rows touch only `/dev/nbdN` devices this run attached over disposable images (§5).

## 1. Rule behind every row

No request ever returns bytes other than the last completed write of that range, and no failure is reported as
success. Every row checks its result against an **oracle** (a byte array holding the last completed write of every
byte, zeros where nothing was written). A request that fails must have no effect (the contract's FAILED state: the
previous mapping is unchanged), except where the row records another outcome (write-back STAGED data before a flush).

## 2. Interfaces driven

| interface | what it is at b69e70f | rows |
|---|---|---|
| vnxd I/O queue | `IoQueue` -> `BlockIo` (+ VNX-RAM leases) -> `ExtentCache` (none / write-through / write-back) -> `Volume` (journal, generations, recovery) -> `ResilientMedia` (retries, health) -> media adapter. This is the top of the storage stack below the host-facing block device: P8 puts the NBD server on exactly this queue | R01-R24 |
| vnx.mgmt protocol | `authz::MgmtSession` over the running device (the session the P8 management socket serves) | R18 |
| vnx.codec-ipc worker | `python -m vnxdna.device.worker`, the process vnxd starts for sealing (and, from P6, for the codec) | R18b, R22, R23b |
| SDK / CLI | `vnx` (V4+ CLI, `vnxdna.commands`), `vnxdna.sdk.config` | R22b, R23b, R25 |
| media adapters (P7) | the `MediaAdapter` trait (docs/V14_CONTRACTS.md §7) | M01-M04 |
| public block device (P8) | `/dev/nbdN` exported by vnxd | B01-B09, R24-hw |

The R-rows use the media adapters that exist at b69e70f: `FaultMedia` (in-memory, with fault rules), `LatencyMedia`
(modelled latency) and, for the real-crash row R11b, a durable file-per-generation adapter defined in the test file
(write + fsync + rename + directory fsync; a fixture, not P7's image adapter).

## 3. The matrix

Expected outcomes were written into the tests and this table before the rows were first run (one exception, R08,
§7). `status`: **run** = runs now and must pass; **pending (phase)** = written against the documented interface and
skipped/ignored with a reason starting "needs P7/P8 integration" (or "needs P10/P11 hardware", or "needs the real
codec worker") until the lead turns it on (§5). A pending row is never a pass. The registry
`tests/v14/integration/v14_e2e_matrix.py` holds the same rows with their implementation;
`tests/v14/integration/test_v14_e2e_registry.py` fails if the registry, this table and the code disagree, or if a run
row is skipped or a pending row lacks its reason.

| id | row | interface | fault injection | expected outcome (preregistered) | depends on | status |
|---|---|---|---|---|---|---|
| R01 | sequential read/write | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | none (3 cache modes, restart) | identical bytes before and after flush + restart; no orphans; no leaked I/O memory | P2-P5 | run |
| R02 | random read/write | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | seeded unaligned requests (3 seeds x 3 cache modes) | every read equals the oracle | P2-P5 | run |
| R03 | small files (sub-block writes) | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | 1-511 B writes at seeded offsets | every read equals the oracle (read-modify-write) | P2-P3 | run |
| R04 | large files and request limits | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | 8 MiB requests over 2048 extents; 64 MiB + 1; past the end | exact readback (multi-transaction); INVALID_ARGUMENT and OUT_OF_RANGE at submission, no effect | P2 | run |
| R05 | partial overwrites | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | overlapping unaligned writes across extent boundaries | every read equals the oracle; one live generation per extent | P2-P3 | run |
| R06 | concurrency | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | 4 submitters x 4 outstanding on a 16-extent hot region (5 cache/read-ahead configs) | every read equals the oracle replayed in queue sequence order | P2-P5 | run |
| R06b | concurrency under backend faults | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | 40 seeded FaultMedia rules (11 fault kinds) during R06 | successful reads equal the oracle; failed writes have no effect; typed errors; clean after recovery | P2-P5 | run |
| R07 | cache hit/miss | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | restart (cold cache), repeated reads, write after cached read | first read miss, second hit; no stale read after a write | P3 | run |
| R08 | dirty eviction + failed writeback | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | write-back, 4-extent cache quota; Put unavailable during flush | evictions write back; failed flush reported, data stays dirty and readable; retried flush durable | P3-P4 | run |
| R09 | flush/sync | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | restart without flush; Sync busy | write-through VERIFIED durable; write-back STAGED lost without flush, old bytes intact; sync failure fails the flush | P2-P3 | run |
| R10 | device restart | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | drained stop + restart; stop without drain behind a slow backend | clean replay; queued requests SHUTTING_DOWN with no effect; device = oracle of completed requests | P2-P5 | run |
| R11a | process crash at every commit stage | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | volume crash hook at 5 stages of a 3-extent write | all 3 extents old before the durable map record, all new after; torn record reported; orphans collected | P2 | run |
| R11b | real process crash (SIGKILL) | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over a durable file backend | SIGKILL of a writer process, 4 rounds (write-through, write-back) | every extent holds a complete version, never older than the last durable one (VERIFIED, or flushed in write-back) | P2 | run |
| R12 | journal recovery | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) + journal file | torn tail; one copy damaged; both copies of a middle record | tail reported and cut; repaired; CORRUPTION refusal with the backend untouched | P2 | run |
| R13 | missing/corrupt extents | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | backend object deleted / bit-flipped | CORRUPTION on every read of the damaged extent, never other bytes; listed at restart and by scrub; rewrite repairs | P2-P4 | run |
| R14 | duplicate/reordered reads | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | 40 identical concurrent reads + shuffled extent reads; one bit-rot read | every successful read equals the oracle; at most the faulted read fails, with CORRUPTION | P2-P5 | run |
| R15 | out of space | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | max_stored_bytes = 8 extents | NO_SPACE (ENOSPC) beyond the bound, nothing changes; overwrite at the bound succeeds or NO_SPACE | P2 | run |
| R16 | backend timeouts + deadlines | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | Put timeout once / always; queue deadline behind a slow write | single timeout absorbed; persistent -> TIMEOUT then READ_ONLY, reads keep working; deadline -> TIMEOUT, no effect | P4-P5 | run |
| R17 | failed verification | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | silent corruption, wrong receipt, torn put, corrupt+ack lost | CORRUPTION; none of the 3 extents of the request changes; no orphans | P2-P4 | run |
| R18 | unauthorized requests (management + sealing) | vnx.mgmt protocol sessions over the device; sealed volume | no token, reader token, 3 wrong tokens, wrong device key, tampered sealed object | PROTOCOL before hello; UNAUTHORIZED without role; reader cannot shut down; cut off after 3 failures; wrong key UNAUTHORIZED; tampered object CORRUPTION | P4 | run |
| R18b | codec-ipc worker protocol abuse | vnx.codec-ipc/1 worker process (stdin/stdout) | malformed JSON, wrong proto/version, path names, wrong SHA-256, oversize line | typed error response for each, worker keeps serving, nothing written outside staging | P4 | run |
| R19 | memory exhaustion | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | 512 KiB VNX-RAM budget, request over the io quota, 8 competing requests | INVALID_ARGUMENT for the oversize request; competing requests wait and succeed; peaks within quotas | P3 | run |
| R20 | saturation | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | 1 worker, depth 4, 10 ms backend, 40 try_submit | excess RESOURCE_EXHAUSTED, counted; queue <= 4; accepted writes complete | P5 | run |
| R21 | cancellation | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | 9 requests cancelled behind a 50 ms write | CANCELLED at once, no effect, no leaked memory; queue keeps serving | P2-P5 | run |
| R22 | native/worker failure at the sealing boundary | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) + sealing worker process | worker exits / answers garbage / hangs | typed BACKEND_UNAVAILABLE/TIMEOUT/PROTOCOL; no device, nothing stored, no journal left, no staging residue | P4 | run |
| R22b | native codec kernel failure | vnx CLI (decode) + SDK | every kernel forced to the reference; junk library paths | byte-identical container (same SHA-256) as the native run; no crash | P6 | run |
| R23 | configuration corruption (service) | vnxd policy/queue/RAM config, volume dir, journal | garbage policy, open modes, symlink, out-of-range configs, foreign backend, superblock damaged | each refused with its recorded typed error before any data moves | P2-P4 | run |
| R23b | configuration corruption (SDK/worker) | SDK config loader; codec-ipc worker start | unknown key, malformed JSON, bad key file, key file readable by others | VNXConfigurationError / exit 2 with a reason; nothing written | P4/P6 | run |
| R24 | Pi-class memory budget (SIMULATED on this host) | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) | 1 MiB extents, 32 MiB budget, 2 workers | correct data; every subsystem peak within quota; total within budget | P3/P10 | run |
| R24-hw | Pi memory constraints on the real board | public block device (/dev/nbdN exported by vnxd, P8) on a Raspberry Pi | memory pressure, kill + restart | budgets of docs/V14_RASPBERRY_PI_PROFILE.md met on hardware | P8/P10/P11 | pending (P8/P10/P11) |
| R25 | historical format compatibility | vnx CLI (decode/verify) on committed V5/V6 fixtures | clean fixtures; reads with 90 % of the records dropped | every fixture decodes to its recorded SHA-256; the damaged one fails with a non-zero exit and no output | P6 | run |
| M01 | file-image media adapter | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over the P7 ImageMedia | conformance + workload + restart + reopen | as R02 + R10; nothing damaged or orphaned | P7 | run |
| M02 | DNA-simulator media adapter | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over the P7 DnaSimMedia (TEST-ONLY codec) | SIMULATED substitution channel; restart + reopen | oracle bytes on every read (channel errors corrected or retried), never other bytes | P7 | run |
| M03 | fault-injection media adapter | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over the P7 seeded FaultMedia | FaultPlan::lying, 2 % per op kind | successful reads equal the oracle; failed writes no effect; no INTERNAL; clean after the plan is cleared | P7 | run |
| M04 | physical-lab interface adapter (SIMULATED lab) | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over the P7 LabMedia + SimulatedLab | seeded synthesis/sequencing failures (retried) | oracle bytes on every read, after restart too; nothing damaged | P7 | run |
| M05 | DNA-simulator media adapter over the real codec worker (SIMULATED channel) | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over the P7 DnaSimMedia driven by the production VNX-DNA codec worker (vnxdna.device.dnasim) | SIMULATED illumina-like substitution channel; conformance + workload + restart + reopen | oracle bytes on every read (the SIMULATED channel's errors are corrected or retried), never other bytes; nothing damaged; a fresh adapter over the same store still matches the oracle | P7 | pending (real codec worker: opt-in) |
| M06 | physical-lab interface adapter over the real codec worker (SIMULATED lab) | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over the P7 LabMedia + SimulatedLab driven by the production VNX-DNA codec worker (vnxdna.device.dnasim) | seeded synthesis/sequencing failures (retried) in the SIMULATED lab; conformance + workload + restart + reopen | oracle bytes on every read, after restart too; nothing damaged; a fresh adapter over the same lab and catalogue still matches the oracle | P7 | pending (real codec worker: opt-in) |
| B01 | sequential + random R/W on the block device | public block device (/dev/nbdN exported by vnxd, P8) | O_DIRECT I/O, seeded; detach + attach | a new device reads zeros; every read equals the oracle, also after re-attach | P8 | run |
| B02 | filesystem: small/large files, partial overwrite | public block device (/dev/nbdN exported by vnxd, P8) + ext4 (write-back cache) | mkfs, mount, files, umount, detach, attach, e2fsck -n | fsck clean; every file's SHA-256 equal after re-attach | P8 | run |
| B03 | flush/sync through the host | public block device (/dev/nbdN exported by vnxd, P8) (write-back cache) | fsync, then SIGKILL of vnxd (no final flush) | fsynced data survives | P8 | run |
| B04 | device restart | public block device (/dev/nbdN exported by vnxd, P8) | 3 detach/attach cycles (none / write-through / write-back cache) | device bytes equal the oracle at every attach; nothing damaged, no torn tail | P8 | run |
| B05 | service crash under filesystem load | public block device (/dev/nbdN exported by vnxd, P8) + ext4 (write-back cache) | SIGKILL of vnxd during 4 fsyncing writers | nothing damaged; e2fsck repairs at most; every fsynced file intact | P8 | run |
| B06 | out of space to the host | public block device (/dev/nbdN exported by vnxd, P8) + ext4 | write past the end; filesystem filled | ENOSPC, nothing changes; ENOSPC to the writer, earlier files intact, fsck clean | P8 | run |
| B07 | backend failure to the host | public block device (/dev/nbdN exported by vnxd, P8) with --fault-control | backend put/sync, then get, failing | EIO for the failed write and read; committed data readable, never other bytes | P7/P8 | run |
| B08 | unauthorized management over the socket | vnx.mgmt Unix socket of the attached service | no token, reader token, 3 wrong tokens, admin | UNAUTHORIZED; reader cannot shut down; cut off after 3 failures; admin shutdown detaches cleanly (exit 0) | P8 | run |
| B09 | queue-depth saturation on the block device | public block device (/dev/nbdN exported by vnxd, P8) | 1-32 concurrent O_DIRECT writers | no errors, no data mismatch, no NBD errors counted | P8 | run |

Totals: 46 rows; 43 run (30 in Rust, 13 in Python: 4 SDK/CLI/worker rows and 9 block-device rows), 3 pending (R24-hw,
needs Pi hardware; M05 and M06, need the real codec worker: opt-in).

**V15 note (M05 and M06).** M05 and M06 are the V15 real-codec variants of the M02 and M04 rows: the same DNA-simulator
and simulated-lab workloads, but driven by the production VNX-DNA codec worker (`vnxdna.device.dnasim`) instead of the
TEST-ONLY toy codec. They are opt-in rows: they need `VNXD_REAL_CODEC=1` and `VNXD_TEST_PYTHON` pointing at a Python
that can import `vnxdna.device.dnasim`; without that environment the test prints its reason and returns. Everything they
do is SIMULATED: no DNA is synthesised, stored or sequenced. They are pending because they need that opt-in environment,
and they have NOT been run: in a plain run they are ignored, and a pending row is never counted as a pass. The §7.2
results below are the 2026-10-19 run of the then 44-row matrix and are not re-measured for M05/M06.

## 4. Determinism

* All data, offsets, lengths and fault schedules come from SplitMix64 (`Rng` in e2e_matrix.rs) or `random.Random`
  with fixed seeds recorded in each test; the same seed gives the same requests on every host.
* Fault injection is deterministic in WHAT is injected (FaultMedia rules fire on the n-th matching operation; crash
  hooks fire at a named commit stage; journal edits at seeded byte positions; SIGKILL after a seeded number of
  acknowledged writes). Under concurrency (R06b, R14) WHICH request meets the fault depends on thread timing, so those
  rows check an outcome that holds for every interleaving: the oracle is replayed in the queue's sequence order (the
  queue starts conflicting requests in that order), failed writes have no effect, and every successful read equals the
  oracle at its sequence point.
* Soak: `VNX_E2E_SEEDS=N` (or `tools/v14-e2e.sh --soak N`) repeats R06 and R06b over N seeds.

## 5. How the P7/P8 rows run (and their safety rules)

* **P7 (M01-M04):** `p7_adapter(kind, dir)` in `e2e_matrix.rs` builds the merged adapters: `file-image`
  (`ImageMedia::open_or_create`), `dna-sim` (`DnaSimMedia` over an image store, TEST-ONLY `ToyCodec`, SIMULATED
  channel `toy-sub-1000`), `fault-injection` (`FaultMedia::seeded` over an image; M03 sets `FaultPlan::lying`) and
  `lab` (`LabMedia` over the `SimulatedLab` of `vnx.dna-lab/1`). The real VNX-DNA codec behind the DNA adapters is
  covered by P7's `tests/media_worker.rs`. M04 was preregistered as "every operation a typed error" for an adapter with
  no equipment; P7 delivered a simulated lab instead, so M04's expected outcome was re-registered (oracle bytes through
  the simulated lab, after restart too) BEFORE its first run.
* **P8 (B01-B09):** each row creates a disposable volume + image backend under pytest's `tmp_path`, attaches it with
  `vnxd nbd attach` through P8's `tests/v14/nbd_support.py` (`Service`, `run_tool`, `mounted`, `assert_own_device`,
  `wait_device_free`) and follows docs/V14_DEVICE_INTERFACE.md §9: every mkfs/e2fsck/mount and every raw O_DIRECT open
  first asserts that the path is `/dev/nbdN` served by the vnxd PID this test started and not mounted; services are
  stopped and mounts removed in `finally`; only our own PIDs are signalled. B05 uses `--kernel-timeout-s 20` so the
  kernel fails the I/O it still holds after the SIGKILL within 20 s instead of 120 s. On a host without root or the
  nbd module the rows skip with the kernel blocker as reason (never counted as passed). The runner prints the number of
  attached nbd devices and nbd mounts at the end (must be 0).
* **R24-hw:** only on the Raspberry Pi (P10/P11); desktop numbers are never Pi numbers.

## 6. Runner

```
PY=/root/vnx-dna-lab/.venv/bin/python CARGO_BUILD_JOBS=8 tools/v14-e2e.sh --integration              # whole matrix
PY=/root/vnx-dna-lab/.venv/bin/python CARGO_BUILD_JOBS=8 tools/v14-e2e.sh --integration --soak 20    # + R06/R06b x 20 seeds
```

It runs the Rust rows (`cargo test --release --offline --locked --test e2e_matrix`), then `pytest
tests/v14/integration` (whose registry test checks the Rust log), and writes `summary.txt` (commit, host, counts, every
skipped/ignored row by name, known defects, the nbd teardown check) under `--out` (default `$TMPDIR/vnx-e2e/<UTC time>`). Exit 0 only when
both halves pass. Block-device rows need root and the nbd module; with `VNX_LAB_GOVERN=systemd` the steps run in a
systemd scope (CPUQuota 200 %, MemoryMax 6G). The rows are also part of the normal gates: `cargo test` runs e2e_matrix and the full pytest suite
collects tests/v14/integration.

## 7. Results

### 7.1 First pass (2026-10-18, base b69e70f, host shared with 7 build jobs)

26 Rust rows and 4 Python rows ran and passed; 14 rows were pending (P7/P8/Pi). Two defects were found (§7.3). One
expectation changed after a first run: R08 first allowed either a successful retried flush or the documented
read-only degraded mode; it was tightened (a 20 ms health-probe interval, then the retried flush MUST succeed and the
data MUST be durable). R22 was strengthened with the D2 fix (no journal left). No expectation was weakened.

### 7.2 Integrated run (2026-10-19, `work/v14-p9` = b69e70f work + `build/v14` e231eaa, host otherwise idle)

`tools/v14-e2e.sh --integration` (results in the lab: `/root/vnx-dna-lab/results/v14-p9-e2e-integration/`):

| part | result |
|---|---|
| Rust rows (R01-R24, R06b, R11a/b, M01-M04) | 30 passed, 0 failed, 0 ignored |
| Python rows (R18b, R22b, R23b, R25, B01-B09) + registry checks | 22 passed, 0 failed, 1 skipped (R24-hw: needs Pi hardware) |
| teardown | 0 nbd devices attached, 0 nbd mounts after the run |

`--integration --soak 20` (R06 and R06b over 20 seeds each; `/root/vnx-dna-lab/results/v14-p9-e2e-soak20/`): 30 Rust
rows passed (194 s), Python rows 22 passed / 1 skipped, teardown clean.

**Matrix (as of that run): 44 rows: 43 pass, 0 fail, 1 skipped (R24-hw, needs Pi hardware).** M05 and M06 were added
after this run and are pending (see the V15 note in §3). No fault run returned bytes other than
the last completed write (no silent corruption), and no failure was reported as success.

### 7.3 Defects found

| id | severity | where | what | reproducer | status |
|---|---|---|---|---|---|
| D1 | low | `vnxdna.device.worker` (and its subclass, the P7 codec worker `vnxdna.device.dnasim`) | served `seal_extent`/`open_extent`/`key_check` (and codec ops) before `hello`, against docs/V14_CONTRACTS.md §6. vnxd always sends hello first, so it was a conformance gap only | `test_v14_e2e_python.py::test_r18b_worker_requires_hello_first` (was a strict xfail) | FIXED: `Worker.handle` refuses every op but hello with PROTOCOL until a hello succeeded (a failed hello negotiates nothing). Two existing unit tests that skipped hello now send it (test_device_seal symlink case, test_media_adapters `staged` fixture) |
| D2 | medium | `vnxd::volume::Volume::create` | when the device-marker put/sync failed, the journal stayed: every retry failed BACKEND_UNAVAILABLE "File exists" (a retryable code for a permanent state) and open refused the directory, which stayed wedged until removed by hand | `volume::tests::a_failed_create_leaves_nothing_behind_and_can_be_retried` (failed before the fix); R22 | FIXED: create removes the marker (best effort) and the journal, then fsyncs the directory, before returning the error |
| D3 | low (test) | `vnxd::balance::tests::failures_are_counted_as_failures_and_ready_results_can_be_taken_by_workers` | the cargo flake (1 failure in 30 release runs at the merge; 1 in 12 seen by the lead): a balancer worker counts a task AFTER sending its result, and the test read the counters right after `wait()`: `left: (0, 2) right: (1, 2)` | adding a 20 ms sleep after `(job.run)()` makes it fail every time | FIXED in the test (poll the counters up to 10 s); the product behaviour (metrics may lag a delivered result by microseconds) is documented in the test |
| D4 | low (test) | `vnxd::io::tests::backpressure_cancellation_timeout_and_barrier` | (P7's debug-build hang under load) the test submitted a write with deadline `now` and then expected the queue to be full; the reaper resolves an expired request at once and frees its place, so under load `try_submit` succeeded, the assertion panicked, and the unwinding drop of the queue joined workers blocked in the test's gate: a HANG instead of a failure | sleeping 30 ms before the `try_submit` fails every time | FIXED: a 500 ms deadline that passes while the write waits behind the barrier (checked), and an `OpenOnDrop` guard on the three gated tests so a failing assertion fails instead of hanging. 20/20 debug runs of the io + balance tests clean afterwards |

Not a defect, recorded: the NBD service has no option for a backend storage bound, so the volume's NO_SPACE is
exercised at the queue level (R15) and B06 covers ENOSPC from the device end and from a full filesystem.

## 8. Static analysis, fuzzing, sanitizers, concurrency (§19)

Short, governed runs (budgets as in CI); raw logs stay in the lab scratch area, results summarised here.

| check | scope | result |
|---|---|---|
| static: `RUSTFLAGS=-D warnings` (clippy is not installed with rustc 1.75 here) | vnxd, all targets, debug and release | clean (0 warnings) |
| static: ruff | tests/v14, src/vnxdna/device | clean |
| fuzz smoke (fuzz/run.sh, 45 s each, 2026-10-18; the C kernels are unchanged since) | native reads, align; atheris py-decode, py-superblock, py-container, py-vxs | 0 crashes (379 k, 25 k, 3 k, 3.05 M, 172 k, 111 k runs) |
| sanitizers (gcc ASan+UBSan, clang ASan+UBSan, clang UBSan trap; each kernel's tests + differential stress fuzz + an ASan canary that must fire) | align, reads (2026-10-18); rs, cluster, strand (+libFuzzer 90 s), orient, outerlist (+libFuzzer 31 s) (2026-10-19) | all PASS, 0 mismatches against the reference, canaries fired. Orient: the first run's gcc-ASan pass HUNG in `tests/v13/test_spec_projection.py::test_option_validation_and_end_to_end` (a `ProcessPoolExecutor` decode: the queue-feeder thread blocked in a pipe send while both forked workers waited in recv; 35 min at 0 % CPU, killed); the clang passes of the same run and a complete rerun passed. Not reproduced; recorded as an intermittent fork-in-a-threaded-process hazard under the ASan runtime (Python 3.12 warns about fork with threads), not as a codec defect |
| Rust engine / integrity crates (P6) | `cargo test --release` | 25 / 5 passed (safe Rust; not under the C sanitizer scripts) |
| concurrency: randomized E2E rows | R06 (5 cache/read-ahead configs) and R06b (11 fault kinds) x 20 seeds | PASS (both before and after the merge) |
| concurrency: vnxd suite in a loop | `cargo test --release` x 30 at the merge commit, idle host + the sanitizer jobs | 29 clean, 1 failure = D3 (fixed); the debug io + balance tests x 20 after the D3/D4 fixes: 20/20 clean |
| block-device teardown | after every runner run and the full suite | 0 attached `/dev/nbd*` (`/sys/block/nbd*/pid`), 0 nbd mounts |

