# V14 validation plan (Phase 9)

Master directive §19 (integrated testing + fault injection) and §20 (performance sequence);
[V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P9: "the §19 end-to-end matrix through the public device interface;
acceptance: full suite + E2E matrix green, benchmarks reproducible from committed commands, no silent corruption in
any fault run".

**Status of this document.** P9 validates the INTEGRATED system, and the parts it validates (P6 codec work, P7 media
adapters, P8 block device) were still being built in parallel when it was written (base `build/v14` b69e70f). This is
the P9 preparation and first pass: the full matrix with its expected outcomes, a harness that runs every row that can
run on b69e70f, and the rows that need P7/P8 written against the documented interfaces and switched off with an
explicit reason. P9 is complete only when the lead has merged P6-P8, turned the pending rows on (§5) and the whole
matrix is green. Evidence categories: B (software storage device) and C (electronic-backend persistence, a durable
file backend on the host's disk); the DNA-simulator rows (M02) are D (SIMULATED channel). Nothing here touches DNA,
a real disk or a block device.

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
skipped/ignored with a reason starting "needs P7/P8 integration" (or "needs P10/P11 hardware") until the lead turns
it on (§5). A pending row is never a pass. The registry `tests/v14/integration/v14_e2e_matrix.py` holds the same rows
with their implementation; `tests/v14/integration/test_v14_e2e_registry.py` fails if the registry, this table and the
code disagree, or if a run row is skipped or a pending row lacks its reason.

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
| M01 | file-image media adapter | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over the P7 adapter | conformance + workload + restart | as R02 + R10 | P7 | pending (P7) |
| M02 | DNA-simulator media adapter | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over the P7 adapter | SIMULATED channel loss | oracle bytes or a typed error, never other bytes | P7 | pending (P7) |
| M03 | fault-injection media adapter | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over the P7 adapter | the adapter's seeded fault config | the R13-R17 outcomes | P7 | pending (P7) |
| M04 | physical-interface media adapter | vnxd I/O queue (IoQueue/BlockIo over cache, volume, fallback, media) over the P7 adapter | no hardware | every operation a typed error; no device is created | P7 | pending (P7) |
| B01 | sequential + random R/W on the block device | public block device (/dev/nbdN exported by vnxd, P8) | O_DIRECT pread/pwrite, seeded | every read equals the oracle | P8 | pending (P8) |
| B02 | filesystem: small/large files, partial overwrite | public block device (/dev/nbdN exported by vnxd, P8) + ext4 | mkfs, mount, files, umount, fsck | fsck clean; every file's SHA-256 equal after remount | P8 | pending (P8) |
| B03 | flush/sync through the host | public block device (/dev/nbdN exported by vnxd, P8) | fsync + BLKFLSBUF, service restart | fsynced data survives a service restart | P8 | pending (P8) |
| B04 | device restart | public block device (/dev/nbdN exported by vnxd, P8) | service stop/start, reconnect | device bytes unchanged | P8 | pending (P8) |
| B05 | service crash under filesystem load | public block device (/dev/nbdN exported by vnxd, P8) + ext4 | SIGKILL of vnxd during writes | fsck clean after reconnect; fsynced files intact | P8 | pending (P8) |
| B06 | out of space to the host | public block device (/dev/nbdN exported by vnxd, P8) | backend bound smaller than the device | ENOSPC to the writer, no corruption | P8 | pending (P8) |
| B07 | backend failure to the host | public block device (/dev/nbdN exported by vnxd, P8) over the P7 fault adapter | backend unavailable | EIO to the host; reads of committed data keep working; no wrong bytes | P7/P8 | pending (P7/P8) |
| B08 | unauthorized management over the socket | vnx.mgmt Unix socket of the running service | no token / reader token / other uid | UNAUTHORIZED; shutdown only for admin | P8 | pending (P8) |
| B09 | queue-depth saturation on the block device | public block device (/dev/nbdN exported by vnxd, P8) | 1-32 concurrent writers | no errors, no data mismatch, bounded service RSS | P8 | pending (P8) |

Totals: 44 rows; 30 run now (26 in Rust, 4 in Python), 14 pending (4 need P7, 9 need P8, 1 needs Pi hardware).

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

## 5. Turning the pending rows on (integration lead)

* **P7 (M01-M04, B07):** in `src/vnxdna/native/rust/vnxd/tests/e2e_matrix.rs`, make `p7_adapter(kind, dir)` return
  the merged adapters (`file-image`, `dna-sim`, `fault-injection`, `physical`), each over a fresh backend in `dir`, and
  delete the four `#[ignore = "needs P7/P8 integration: ..."]` lines; change their status to `run` in the registry
  and in §3.
* **P8 (B01-B09):** export a disposable device from vnxd and provide a service-control command; set
  `VNX_E2E_DEVICE=/dev/nbdN` (only nbd/ublk devices vnxd created are accepted), `VNX_E2E_SERVICE="<cmd>"` (arguments
  `start|stop|kill|restart|mgmt-socket|limit-space <bytes>|backend-fail|backend-ok`, see the module docstring of
  test_v14_e2e_blockdev.py), `VNX_E2E_MNT=<empty scratch dir>`, `VNX_E2E_TOKENS=<json file>`; then change the rows'
  status to `run`. Teardown of the device stays P8's job.
* **R24-hw:** only on the Raspberry Pi (P10/P11); desktop numbers are never Pi numbers.
* Run `tools/v14-e2e.sh --integration` (§6): the registry test then requires every Rust row to pass and none to be
  ignored.

## 6. Runner

```
WT=/root/vnx-dna-lab/v14-p9 VNX_LAB_GOVERN=systemd PY=/root/vnx-dna-lab/.venv/bin/python tools/v14-e2e.sh            # now
WT=... VNX_LAB_GOVERN=systemd PY=... VNX_E2E_DEVICE=/dev/nbd0 VNX_E2E_SERVICE=... tools/v14-e2e.sh --integration   # after P7/P8
WT=... VNX_LAB_GOVERN=systemd PY=... tools/v14-e2e.sh --soak 20                                                     # soak
```

It runs the Rust rows (`cargo test --release --offline --locked --test e2e_matrix`), then `pytest
tests/v14/integration` (whose registry test checks the Rust log), and writes `summary.txt` (commit, host, counts, every
skipped/ignored row by name, known defects) under `--out` (default `$TMPDIR/vnx-e2e/<UTC time>`). Exit 0 only when
both halves pass. The rows are also part of the normal gates: `cargo test` runs e2e_matrix and the full pytest suite
collects tests/v14/integration.

## 7. First pass (2026-10-18, base b69e70f, lab host shared with 7 other jobs)

FIRST_PASS_PLACEHOLDER

## 8. Static analysis, fuzzing, sanitizers, concurrency (§19)

CHECKS_PLACEHOLDER
