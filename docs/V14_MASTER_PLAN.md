# V14 master plan

Phase 0 document (master directive §2, §22, §24). It orders the V14 work into phases P0–P13, each with what it
delivers and the acceptance criteria that must pass before the next phase starts. The codec/performance detail of
P6 is in [V14_PLAN.md](V14_PLAN.md) (the earlier V14 brief); the architecture is in
[V14_ARCHITECTURE.md](V14_ARCHITECTURE.md); the language decisions in
[V14_LANGUAGE_AND_RUNTIME_STRATEGY.md](V14_LANGUAGE_AND_RUNTIME_STRATEGY.md).

## Rules for every phase

* Branch `build/v14`; commits authored `vishnuselvam101-hash`; no merge to main, no tag, no release.
* The full suite never drops below the gate (4,454 passed / 4 skipped / 0 failed at 58171b8) and no test is
  weakened, skipped or removed. Existing archive formats keep decoding.
* No placeholder modules, empty directories, mocked success or docs describing unimplemented behaviour as done.
* Every number comes from a committed results file with its command, host and load; channel results are SIMULATED.
* Each phase ends with `docs/V14_PHASE<k>_REPORT.md` (implemented, tested, exact results, benchmark changes, known
  defects, remaining work, acceptance PASS/FAIL), an update of `docs/V14_STATE.md`, and one short founder note.

## Phases

| phase | delivers | acceptance criteria |
|---|---|---|
| **P0** audit + baseline + docs | repo audit, suite + A/B + pool-scaling baseline, V14_MASTER_PLAN, V14_ARCHITECTURE, V14_BASELINE, V14_LANGUAGE_AND_RUNTIME_STRATEGY, V14_DEPENDENCY_GRAPH | suite reproduces the V13 gate; A/B ratios reproduce the V13 report within CIs or the difference is explained; pool-scaling reference committed; every `_PENDING` cell of V14_PLAN filled from committed files |
| **P1** contracts + core storage model | Rust crate `vnxd` (lib + bin): block addresses, generation ids, write states, error classes, persisted record formats with CRC-32, media-adapter trait, `vnx.mgmt/1` + `vnx.codec-ipc/1` schemas; Python `vnxdna.device` (layer 6) mirroring the schemas | `cargo test` (-D warnings, overflow checks) green; record encode/decode round-trips and rejects every corrupted byte; Python and Rust agree on every schema (cross-language golden tests); layer test includes `device` |
| **P2** Volume + VNX-IO | block map, extent allocation, generations, journal, transactions, recovery, GC; LBA I/O with split/coalesce, RMW, bounded queues, ordering, flush, cancellation, timeouts | unaligned, overlapping, concurrent and cancelled I/O tests; crash before/during/after each commit stage recovers to a defined state; out-of-space is a typed error; image of a random workload equals a reference byte model |
| **P3** VNX-RAM + Cache | budgets, quotas, pools, backpressure, accounting, leak detection; read-through cache, write staging, dirty tracking, write-through default, optional write-back; molecular-memory research interface (simulator + conformance) | memory stays within limits under saturation (measured peak); cancellation releases buffers; no stale read under concurrent writes; dirty eviction and interrupted writeback tests; buffers do not leak across security contexts |
| **P4** Fallback + Secure | retry classes, backend health, degraded mode, crash recovery orchestration; authorisation, sealing at the extent boundary, integrity records, audit; V14_SECURITY_MODEL, V14_FAILURE_AND_RECOVERY_MODEL | fault-injection matrix (crash, partial write, unavailable backend, corrupt metadata, failed verification) gives the recorded expected outcome every time; unauthorised requests refused and audited; no unverified data returned |
| **P5** Load Balancer | priority queues, worker pools, fairness, per-backend concurrency limits | no deadlock under the concurrency stress tests; single- vs multi-worker benchmark (queue latency, utilisation, throughput, memory) committed; the chosen default is justified by that file |
| **P6** VNX-DNA integration + optimisation | brief workstreams A–C, E–F: linear pool decode, native pass 2, safe scheduler, bench suite, config/security; codec worker for the simulator adapter | V14_PLAN gates: byte-identical outputs, 0 false successes, differential + determinism tests, sanitizers + fuzz; tier claims only when the CI lower bound clears them |
| **P7** media adapters | A DNA simulator over codec IPC, B electronic image backend, C fault injection, D physical interface spec (V14_MEDIA_ADAPTER_SPEC) | one conformance suite passes on A, B and C; B persists across restarts; C reproduces each injected fault deterministically; D sends no command to any equipment |
| **P8** Linux block device | NBD Stage A (`/dev/nbdN`), ublk Stage A' if Stage A passes, Stage B userspace API; V14_DEVICE_INTERFACE | on a disposable image: fdisk → mkfs.ext4 → mount → copy files → umount → restart → mount → byte-identical readback; flush/FUA honoured; teardown always runs; only devices vnxd created are touched |
| **P9** integrated validation | the §19 end-to-end matrix through the public device interface; V14_BENCHMARK_REPORT with codec, I/O, cache, scheduler, backend and simulator numbers kept separate | full suite + E2E matrix green; benchmarks reproducible from committed commands; no silent corruption in any fault run |
| **P10** ARM64 portability | aarch64 cross-build of `vnxd` and the C kernels, tests under qemu-user; V14_RASPBERRY_PI_PROFILE, V14_BUILD_AND_DEPLOYMENT | cross-built tests pass under emulation (functional only, no performance numbers); Pi acceptance recorded as INCOMPLETE until real hardware runs it |
| **P11** hardware + boot engineering | V14_BOOT_ARCHITECTURE, external-controller study (USB MSC/UASP vs NVMe), physical DNA device design with researched parts | every hardware statement sourced; buildable-now / custom / lab-only classification; no claim that the device exists |
| **P12** acceptance review | V14_IMPLEMENTATION_REPORT; completed / incomplete / hardware-dependent list; the 18 criteria of directive §22 each PASS, FAIL or INCOMPLETE with evidence | independent review of commits, tests, benchmarks and claims; full suite green on the final SHA |
| **P13** V15 plan | V15_FINALIZATION_PLAN (directive §23) | each V15 item has a method, an evidence target and the V14 file it starts from |

## Host constraints that shape the plan

* This host (x86_64, Linux 6.8) has the `nbd` and `ublk_drv` modules and root, so P8 runs here.
* There is no ARM64 toolchain, no qemu-aarch64 and no Raspberry Pi; P10 installs the cross tools from the Ubuntu
  archive, and Pi acceptance cannot pass in V14 without hardware.
* No DNA laboratory: evidence categories E and F are out of reach; adapter D is specification + simulator only.
