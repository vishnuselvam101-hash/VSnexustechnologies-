# V14 Phase 9 report: integrated validation

Date 2026-10-19. Branch `work/v14-p9` from `build/v14` b69e70f (preparation pass, 2026-10-18), with `build/v14`
e231eaa (P6 A-F, P7 media adapters, P8 NBD block device, P11/P13 docs) merged in. Scope:
[V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P9 and master directive §19 (integrated testing + fault injection).
Matrix, expected outcomes and per-row results: [V14_VALIDATION_PLAN.md](V14_VALIDATION_PLAN.md).

Evidence categories: B (software storage device, including `/dev/nbdN` on this host) and C (image/file backends on the
host's disk); the DNA-simulator and simulated-lab rows are D (SIMULATED, TEST-ONLY codec). Nothing is E or F: no DNA
was synthesised, stored or sequenced. No benchmark numbers are taken in P9 (the host was shared during the
preparation pass; the lead runs the benchmark suite next, [V14_BENCHMARK_REPORT.md](V14_BENCHMARK_REPORT.md) is a
skeleton with the commands only).

## Implemented

* **The §19 matrix, 44 rows**, each with interface, fault-injection mechanism, preregistered expected outcome and the
  phase it depends on (plan §3); a registry (`tests/v14/integration/v14_e2e_matrix.py`) that a test keeps identical to
  the plan table and to the code, and that fails if a running row is skipped or ignored.
* **Rust rows** (`src/vnxdna/native/rust/vnxd/tests/e2e_matrix.rs`, 30): the whole stack (I/O queue, VNX-RAM,
  cache in 3 modes, volume + journal, fallback, media) checked against a byte oracle: sequential/random/small/large
  I/O, partial overwrites, concurrency (replayed in queue order) with and without 11 kinds of backend faults, cache
  hit/miss, dirty eviction and failed writeback, flush/durability, restarts, crash hooks at every commit stage, a real
  SIGKILL over a durable file backend (write-through and write-back), journal damage, missing/corrupt extents,
  duplicate/reordered reads, out of space, timeouts and deadlines, lying backends, management authz and sealing,
  memory exhaustion, saturation, cancellation, sealing-worker failure, configuration corruption, a Pi-class memory
  budget (SIMULATED on this host), and the four P7 adapters (image, DNA simulator, seeded fault injection, simulated
  lab).
* **Python rows** (`tests/v14/integration/`, 13): the codec-ipc worker under protocol abuse, native kernels forced
  off or pointed at a broken library (byte-identical containers), SDK/worker configuration corruption, V5/V6 golden
  fixtures through the `vnx` CLI, and nine rows through a real `/dev/nbdN` (O_DIRECT I/O, ext4 with small/large files
  and partial overwrites, fsync surviving a SIGKILL, restarts in 3 cache modes, SIGKILL under 4 fsyncing writers,
  ENOSPC, backend EIO, management-socket authz, queue depth 1-32), all through P8's safety helpers.
* **Runner** `tools/v14-e2e.sh [--integration] [--soak N] [--out DIR]`: Rust rows, then the Python rows (whose
  registry test checks the Rust log), a summary with every skipped row by name and the nbd teardown check.

## Defects found and fixed (details and reproducers: plan §7.3)

| id | severity | fix |
|---|---|---|
| D1 | low | the codec-ipc workers (`vnxdna.device.worker` and the P7 codec worker that subclasses it) served operations before `hello`: now PROTOCOL until a hello succeeds; two unit tests that skipped hello now send it |
| D2 | medium | `Volume::create` left its journal when the device-marker write failed, wedging the directory (retries BACKEND_UNAVAILABLE "File exists", open refused): create now undoes the marker and the journal |
| D3 | low (test) | the vnxd cargo flake (1 in 30 / 1 in 12 runs): `balance::tests::failures_are_counted_...` read metrics that a worker updates after sending the result; the test now polls |
| D4 | low (test) | `io::tests::backpressure_cancellation_timeout_and_barrier` raced the reaper (deadline `now`) and, on failure, hung in the queue's drop (P7's debug-build hang report): deadline that passes while queued, plus an open-on-drop guard on the gated tests |

Every fix has a test that failed before it (D3/D4 with a deterministic delay injected into a scratch copy). No test was
weakened, skipped or deleted.

## Tests and exact results (2026-10-19, idle host, root with the nbd module)

| check | result |
|---|---|
| `tools/v14-e2e.sh --integration` | Rust 30 passed / 0 failed / 0 ignored; Python 22 passed / 1 skipped (R24-hw); 0 nbd devices attached, 0 nbd mounts after |
| `tools/v14-e2e.sh --integration --soak 20` | same, R06/R06b over 20 seeds: PASS |
| matrix | **44 rows: 43 pass, 0 fail, 1 skipped (R24-hw, needs Pi hardware)** |
| `cargo test --release --offline --locked`, `RUSTFLAGS=-D warnings` (vnxd) | **238 passed**, 0 failed, 3 ignored (gate 207; +30 E2E rows, +1 D2 test); engine 25, integrity 5 |
| full suite (8 workers, systemd scope) | **4,851 passed, 5 skipped, 0 failed**, 343 s (gate 4,829 / 4 / 0; +22 tests; the 5th skip is R24-hw) |
| static, fuzz, sanitizers | plan §8: -D warnings and ruff clean; fuzz smoke 0 crashes; all 7 C kernels PASS under gcc/clang ASan+UBSan and UBSan trap with canaries; one intermittent gcc-ASan hang in a fork-based decode test (not reproduced; recorded) |

## Remaining work and limitations

* R24-hw: memory constraints on a real Raspberry Pi (P10/P11). R24 is a SIMULATED budget on this host only.
* The DNA adapters in M02/M04 run on the TEST-ONLY `ToyCodec`; the real VNX-DNA codec behind them is covered by P7's
  `tests/media_worker.rs`, not by this matrix.
* `vnxd nbd` has no backend storage bound option, so backend NO_SPACE is validated at the queue level (R15) only.
* The intermittent ASan fork hang (plan §8) is unexplained; `ProcessPoolExecutor` with the fork start method in a
  threaded process is the suspect.
* Benchmarks: none in P9; the separated per-layer benchmark run is the lead's next step.

## Acceptance (P9 criteria of V14_MASTER_PLAN)

| criterion | result |
|---|---|
| full suite + E2E matrix green | PASS (4,851 / 5 / 0; matrix 43/44 with the Pi row skipped for hardware) |
| §19 matrix through the public device interface | PASS (B01-B09 through `/dev/nbdN`; the rest through the vnxd queue, protocols, worker and CLI) |
| no silent corruption in any fault run | PASS (every row checks a byte oracle; no read returned other bytes, no failure was reported as success) |
| benchmarks reproducible from committed commands, layers separate | skeleton + commands committed; numbers pending (lead) |

Phase 9: **PASS** for validation; the benchmark report stays open for the lead's run.
