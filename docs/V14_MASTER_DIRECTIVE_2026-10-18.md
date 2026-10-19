# VNX ULTIMATE V14 — Complete Technical Build Master Directive (founder, pasted 2026-10-18)

GOVERNING SCOPE for V14. The earlier brief (notes/V14-BRIEF-2026-10-18.md: stream-pool linear decode, native pass 2, safe scheduler, volume hardening, bench suite, config/security, V15 plan) stays in force as the detailed spec for the codec/performance work (Phase 6 below) and for the volume hardening in Phase 2/4. Condensed from the founder's paste; every requirement kept.

V14 = final planned technical build. V15 = final planned research-grade build.
Principle: research first, inspect the real repo, implement working functionality, verify every layer, optimise measured bottlenecks, keep all existing formats and functionality.

## 1. Mission
VNX = a complete, modular, hybrid-language storage platform with DNA as the long-term medium. Target product: an external storage device the OS sees as a standard block device (capacity, partition, format, mount, copy, read/write, normal apps).
Modules, each with purpose, implementation, interface, tests and measurable acceptance criteria:
- VNX-DNA: encoding, decoding, strand layout, addressing, sync, ECC, reconstruction.
- VNX-RAM: electronic working-memory manager for the controller + a separate molecular-memory research interface.
- VNX-IO: logical block requests, address translation, async I/O, ordering, data movement.
- VNX Cache: read cache, write staging, dirty data, prefetch, consistency.
- VNX Load Balancer: scheduling, workers, queues, media-operation coordination.
- VNX Fallback: errors, recovery, redundancy, retries, degraded mode, fault containment.
- VNX-Secure: encryption, authN, authZ, integrity, audit.
- VNX Volume: logical volumes, extents, metadata, transactions, journal, recovery, GC.
- VNX Device Controller: host-facing block interface, later a hardware controller.
- VNX Media Adapter: simulator, electronic test backend, fault-injection backend, future physical DNA adapters.
- VNX Management: config, capacity, monitoring, diagnostics, lifecycle.
- VNX SDK and CLI.
- VNX Boot Architecture (future firmware-accessible storage).
- VNX Research Framework: reproducible benchmarks, error models, protocols, evidence.
NO empty dirs, placeholder classes, fake device files, mocked success, or docs claiming unimplemented features. A simulator is fine only if labelled as one and implementing the real contract.

## 2. Phase 0 investigation (before code)
V13 reported: build/v13-final f7586b9, 4,454 passed / 4 skipped / 0 failed (now released as v13.0.0 = 58171b8). Verify, don't trust.
Inspect: cwd/branch/HEAD/status; uncommitted + untracked files; V13 RC + release history; Python/Rust/C/C++ components; native extensions, build scripts, dependency versions, compiler settings; encoder/decoder/ECC/strand layout; volume/metadata/journal/cache/scheduler/security; SDKs, CLIs, config formats, archive compatibility; fuzz/fault-injection/sanitizer tests and benchmark artifacts; Linux kernel features, CPU arch, CPU count, memory limits.
Produce before major implementation: docs/V14_MASTER_PLAN.md, docs/V14_ARCHITECTURE.md, docs/V14_BASELINE.md, docs/V14_LANGUAGE_AND_RUNTIME_STRATEGY.md, docs/V14_DEPENDENCY_GRAPH.md.
Reproduce the suite and benchmark V13 where possible. Never reset, overwrite unrelated changes, force-push, merge to main, tag or publish. Work on a V14 dev branch. If the repo is unavailable: stop and report; never fabricate an audit.

## 3. Hybrid-language strategy
Not all Python. Choose by measured performance, memory safety, OS integration, portability, ecosystem, maintainability. Few, well-defined native interfaces.
3.1 Rust = primary systems/control plane for new performance-sensitive parts: block-request orchestration, logical I/O + scheduling, transaction/volume logic where migration is justified, bounded async queues + cancellation, memory-pool management, cache metadata/consistency, media-adapter abstractions, recovery coordination, device-controller userspace service, config validation/diagnostics, management interfaces. Established crates allowed (async, serialisation, logging, crypto, errors, OS interfaces). Prefer safe code; document every unsafe boundary. Don't rewrite proven code just to add Rust; migrate incrementally.
3.2 C/C++: keep/improve existing kernels where profiling shows benefit (SIMD byte ops, alignment/orientation, ECC, codec hot loops, isolated hardware routines, platform optimisations with portable fallbacks). C11+; modern C++ only if justified. Explicit ownership, bounds checks, ABI contracts, error propagation. Sanitizers + fuzzing. No C++ for appearance; no two native languages for the same thing without a measured reason.
3.3 Python: existing SDK, research/bench orchestration, test generation, reference implementations, data conversion/reports, compatibility wrappers, dev/admin tooling. Python must NOT be the mandatory per-block path when native can do it. Keep the public Python API; bindings or stable IPC.
3.4 Boundaries: versioned documented interfaces (Rust APIs, C ABIs, Python bindings, Unix-domain socket or other documented IPC, explicit binary formats for persisted records). No raw pointers across process boundaries. Define ownership, buffer lifetime, cancellation, errors, concurrency, version negotiation. Avoid hot-path copies/serialisation. Reproducible builds on supported hosts AND 64-bit ARM Linux.

## 4. Architecture
Data path: Host OS -> Device Controller -> VNX-IO -> Volume + logical mapping -> VNX-Secure -> Cache + VNX-RAM pools -> Load Balancer -> VNX-DNA codec/ECC -> Media Adapter -> persistence backend. Reordering of security checks / separate metadata paths allowed if ordering, integrity, isolation and transactions stay correct. Read path = reverse, integrity verified before returning data. Explicit interfaces, no reaching into internal state.
Diagrams: data flow, control flow, module dependencies, memory ownership, transaction boundaries, failure propagation, security boundaries, host interface, physical-media boundary.

## 5. VNX-RAM
5.1 Electronic working-memory manager (bounded, configurable): codec buffers, in-flight requests, read/write staging, reconstruction temps, metadata/index caches, cache bookkeeping, native buffers, DMA-compatible buffers if a future controller needs them, bounded queues, memory-pressure handling, optional reusable pools. Implement: total budget, per-subsystem quotas, max in-flight bytes/requests, buffer reuse with safe ownership, backpressure, cancellation/cleanup, allocation-failure handling, accounting + leak detection, small-system limits, metrics (allocations, peak, queue depth, pressure). Never reserve all system memory. Zero-copy only with demonstrated lifetime/concurrency correctness.
Write states: RECEIVED -> STAGED -> BACKEND_COMMITTED -> VERIFIED (may fail/recover at any stage; not interchangeable). STAGED = in volatile/staging memory; BACKEND_COMMITTED = backend acknowledged its documented commit; VERIFIED = specified readback/integrity check passed. If the backend can't establish physical durability, say so. Never acknowledge durability because bytes are in RAM. Flush/sync/orderly shutdown so acknowledged writes aren't silently lost within the advertised guarantees.
5.2 Molecular-memory research interface (separate): region discovery, addressing, read/write, verification, capability negotiation, endurance/health metadata, latency/error reporting, persistence semantics, failure/recovery states, simulator, conformance suite, protocol spec for a future experimental implementation. Never call electronic RAM molecular memory; never invent molecular speed/density/endurance/energy. Platform works without it.
5.3 Acceptance tests: memory within configured limits; saturation -> backpressure not unbounded allocation; cancellation releases resources; failed writes leave no inconsistent cache/transaction state; reused buffers don't leak data across security contexts; correct during shutdown/recovery; low-memory config usable on the Pi target.

## 6. VNX-DNA
Keep the verified V13 codec and all archive formats. Optimise only after a reproducible baseline. Investigate the shuffled-pool repeated-search bottleneck and pass-2 costs (detailed spec: notes/V14-BRIEF-2026-10-18.md). Implement/preserve: deterministic encoding, strand ids, data-to-strand mapping, sync markers, indexing + manifests, ECC, checksums, chunked streaming, bounded memory, missing/duplicate strands, RC recognition, reordered reads, substitution + indel correction, corruption detection, explicit reconstruction failures, versioned formats, compatibility/migration tests. Test clean/sub/ins/del/dropout/duplicates/reordered/RC/mixed with fixed seeds + recorded configs. Keep crypto/integrity guarantees. Never weaken validation, discard valid data or fabricate bytes. Separate codec performance from end-to-end storage performance.

## 7. VNX-IO
LBA; logical-to-physical-generation mapping; configurable block size; transfer-size validation; sequential + random R/W; partial-block read-modify-write; request split/coalesce; bounded async queues; cancellation; timeouts; flush/sync; ordering + barriers; concurrent coordination; metrics/tracing; precise errors. Tests: unaligned, overlapping writes, concurrent readers/writers, cancellation, saturation, shutdown during I/O. Defined behaviour for every op when the backend is unavailable.

## 8. VNX Cache
Read-through; optional write-back; explicit write-through; dirty tracking; invalidation after writes; LRU or measured alternative; prefetch when evidence supports it; cache checksums; recovery integration; quotas; metrics; writeback failure handling; persistent-cache recovery only if safe. No stale reads under concurrent writes. Tests: dirty eviction, interrupted writeback, duplicate requests, failed flushes, restart recovery, concurrency. Distinguish volatile ack from durable commit. No risky write-back default unless the durability contract + recovery justify it.

## 9. Load Balancer
Schedules encode, decode, retrieval, indexing, verification, GC, backend I/O, maintenance. Bounded queues, priorities, cancellation, fairness, backpressure. No deadlocks between cache/volume/codec/media. Respect transaction order + backend concurrency limits. Physical synthesis/sequencing is NOT as parallel as CPU work. Benchmark single vs multi-worker: queue latency, utilisation, task latency, throughput, memory, contention. Choose by measurement + correctness.

## 10. Fallback
Bounded retries; retryable vs permanent errors; backend health; crash recovery; journal replay; corruption detection; missing extents; redundant-copy recovery only with real valid redundancy; degraded mode; clean cancel/shutdown; recovery audit events; explicit failures. Never invent a successful read or return unverified data. Explicit error with diagnostics when unrecoverable. Fault injection: crashes, partial writes, unavailable backends, corrupt metadata, invalid manifests, missing extents, interrupted flushes, failed verification.

## 11. VNX-Secure
Extend the existing security code, no competing subsystem. Encryption at the right data/metadata boundaries; key handling; authN; authZ; input validation; manifest/metadata integrity; replay protection where needed; audit-log integrity; native boundary checks; privileged-device access control; config validation; resource-exhaustion protection; secure defaults. Established libraries and constructions only, no proprietary cipher. Document threat model, trust boundaries, key lifecycle, backup/recovery implications, per-backend protection limits.

## 12. VNX Volume
Logical block maps; extent allocation; versioned metadata; transaction ids; journaled metadata; atomic mapping updates; commit procedure; crash recovery; integrity validation; reference tracking; capacity reporting; out-of-space; safe deletion; GC; concurrent access control. DNA generations immutable by default: overwrite = new generation + transactional remap. GC only when no live mapping, recovery record or replica references the old generation. Specify crash before/during/after each commit stage.

## 13. Media Adapter (stable contract)
A: DNA software simulator using the existing codec/ECC, configurable latency + failure models (never presented as physical measurements).
B: Electronic file/block-image backend (persists across restarts; clearly NOT DNA).
C: Fault-injection backend (deterministic failures, delays, partial ops, corruption, unavailable media, recovery).
D: Future physical DNA adapter interface: synthesis job submit/ids/status; strand + archive ids; physical location + preservation metadata; retrieval jobs; sequencing jobs; read-data delivery; quality metrics; reconstruction handoff; retry/cancel; equipment errors; media health/lifecycle. Research real vendor protocols first; never fabricate hardware commands. Without equipment: interface + simulator + conformance tests + docs, no physical-validation claim.

## 14. Device Controller (principal deliverable)
Stage A: Linux userspace block device via ublk (or another documented supported mechanism) exposing a REAL virtual block device, not a file. Create/teardown, capacity, block config, read, write, flush, completion + precise error status, integration with IO/Volume/RAM/Cache, scheduling + cancellation, backend failure reporting, health/diagnostics, safe shutdown. Explicit permissions/privileges. Test only on disposable virtual devices/images; validate with standard tools (partition, mkfs, mount, create/copy files, readback, integrity). NEVER destructive tests on a real disk. Document kernel version, permissions, dependencies, unsupported platforms.
Stage B: portable userspace API for hosts without the kernel interface, same semantics; never called a real block device.
Stage C: external hardware architecture: evaluate USB Mass Storage/UASP vs NVMe; choose initial approach by hardware availability, protocol complexity, controller resources, cost, firmware, use. Specify host protocol, command handling, controller memory, queues, errors/timeouts, power-loss handling, firmware startup, media-adapter comms, device identification, capacity/capability reporting, firmware update/recovery, diagnostics, physical-media readiness. A ublk device is not a USB/NVMe device.

## 15. Continuous use
User flow: connect/start; see capacity + capabilities; partition; format; mount; copy files; read back; modify + delete; flush/sync; restart + verify persistence; detect/recover supported errors. Through the public device interface. The FS/block layers must not bypass VNX mapping, transactions or integrity. Document FS semantics, flush, concurrency, unsupported features; not every OS behaves the same.

## 16. Raspberry Pi / constrained deployment
Portability from the start; optimise after end-to-end correctness. Target a documented 64-bit ARM Linux; pick the actual Pi model by available hardware/memory/interfaces/measurements; Pi models differ. Record model, OS image, kernel, arch, memory, compilers.
Optimisations: bounded pools, configurable queue depth, bounded in-flight bytes, chunked streaming, buffer reuse, fewer copies, ARM64-compatible kernels, runtime CPU-feature detection, scalar fallbacks, adjustable workers, controlled concurrency, memory-pressure handling, efficient metadata indexes, optional background maintenance, graceful degradation, minimal deps, cross + native build docs. No x86 SIMD assumptions; acceleration only where supported + measured.
Pi acceptance (on real hardware): peak RSS, idle/peak CPU, sequential throughput, random latency, codec throughput, end-to-end throughput, startup, recovery time, saturation, thermal throttling, long-run stability, integrity under memory pressure, kill + restart. Budgets in docs/V14_RASPBERRY_PI_PROFILE.md after examining the hardware. Degrade gracefully if DNA medium is slower than the host. NEVER fabricate Pi numbers from desktop runs.
Packaging: reproducible build/deploy docs, config examples, dependency checks, service management, diagnostics. Containers: document privileges/kernel access; a container alone gives no host block device.

## 17. Physical DNA device + controller (design)
Split: host; host-facing controller; electronic working memory; firmware; VNX software; codec engine; synthesis; preservation/archive management; retrieval/sequencing; ECC/reconstruction/verification; power/thermal/fault management; enclosure/serviceability. Research real constraints; classify automatic vs lab equipment vs external services vs open research. An ordinary HDD controller can't drive synthesis/sequencing without a dedicated interface/workflow.
17.1 Evaluate a small electronic metadata/boot region (bootstrap, indexes, recovery records) with DNA as the long-term medium; document security + consistency; never claim DNA supplied boot data if an electronic region did.
17.2 Hardware research: real controllers, boards, interfaces, electrical constraints, firmware, equipment compatibility. BOM only from researched real parts + prices. Classify buildable now / custom engineering / needs lab. Never claim the device exists until assembled + tested.

## 18. Boot architecture (separate milestone)
Research UEFI Block I/O etc. Document firmware-visible reads, boot-region layout, metadata discovery, manifest verification, integrity, recovery, required electronic bootstrap metadata, controller init, physical retrieval constraints, VM test architecture, future physical tests. Validate the software interface first, then firmware path on a disposable virtual device. No physical DNA boot claim without real controller + firmware path + physical retrieval.

## 19. Integrated testing + fault injection
E2E automated: sequential/random R/W, small/large files, partial overwrites, concurrency, cache hit/miss, dirty eviction, flush/sync, device restart, process crashes, journal recovery, missing/corrupt extents, duplicate/reordered reads, out-of-space, backend timeouts, failed verification, unauthorized requests, memory exhaustion, saturation, cancellation, native-extension failures, config corruption, Pi memory constraints. Test the public block-device interface, not only internals. Deterministic fault injection with recorded expected outcomes. Historical format compatibility. Full suite after every major integration stage and before declaring done. Fuzzing, sanitizers, concurrency testing, static analysis.

## 20. Performance sequence
1 verified V13 baseline; 2 complete integrated path; 3 correctness + durability; 4 benchmark integrated system; 5 profile; 6 optimise measured bottlenecks; 7 re-run correctness/compat/fault tests; 8 benchmarks on target hardware; 9 record trade-offs. Measure separately: codec, logical I/O overhead, cache, scheduler overhead, electronic backend, simulator end-to-end, physical DNA (only with real experiments), peak memory, CPU, random latency, queue-depth scaling, recovery time, reconstruction performance. Fixed workloads, repeated trials, raw results, statistics. Never mix simulator/electronic/physical. No post-hoc targets.

## 21. Deliverables
Docs: V14_MASTER_PLAN, V14_ARCHITECTURE, V14_BASELINE, V14_LANGUAGE_AND_RUNTIME_STRATEGY, V14_DEPENDENCY_GRAPH, V14_IMPLEMENTATION_REPORT, V14_BENCHMARK_REPORT, V14_DEVICE_INTERFACE, V14_MEDIA_ADAPTER_SPEC, V14_BOOT_ARCHITECTURE, V14_RASPBERRY_PI_PROFILE, V14_SECURITY_MODEL, V14_FAILURE_AND_RECOVERY_MODEL, V14_BUILD_AND_DEPLOYMENT, V15_FINALIZATION_PLAN.
Also: integrated module implementations; stable interfaces; real Linux software block device where the kernel supports it; simulator + electronic backend; fault injection; E2E tests; historical-format tests; native build/packaging; Pi cross/native build instructions; raw benchmark artifacts + commands; README + CHANGELOG; completed/incomplete/hardware-dependent list; dependency + licence inventory; physical controller + lab integration plan. A software contract is not a physical implementation.

## 22. Completion criteria (founder review)
1 formats compatible; 2 reproducible build; 3 all software modules functional; 4 VNX-RAM bounded; 5 VNX-IO correct incl. sync; 6 cache consistency + dirty recovery tested; 7 scheduling + backpressure measurable; 8 volume/journal recover from tested failures; 9 security + integrity integrated; 10 simulator + electronic backend implement the contract; 11 Linux software block device passes E2E on a supported host; 12 filesystem tests pass on disposable devices; 13 no silent corruption or fabricated success; 14 benchmarks reproducible + separated by backend; 15 ARM64 portability tested, Pi acceptance only on real hardware before claiming; 16 hardware + boot designs documented; 17 remaining physical work listed; 18 V15 plan complete. Hardware-blocked features: document blocker, finish interface + simulator, classify incomplete.

## 23. V15 (research-grade)
Reproducible clean builds; full regression + compatibility evidence; independent architecture review; reproduction of critical benchmarks; correctness + fault-injection evidence; security review; constrained-deployment evidence; comparison with existing DNA-storage approaches; research-backed decisions; limitations + failure cases; physical-media experimental protocol; independent researcher testing where available; claims traceable to raw artifacts. Evidence categories in every report: A software codec; B software storage device; C electronic-backend persistence; D simulated DNA channel; E actual synthesis/preservation/retrieval/sequencing; F complete physical device. Passing one never implies a later one. No physical DNA claims without real experiments.

## 24. Workflow (incremental, reviewed)
P0 audit + baseline + initial docs; P1 contracts + core storage model (interfaces, addresses, transaction states, persistence semantics, error types, deps); P2 Volume + VNX-IO; P3 VNX-RAM + Cache; P4 Fallback + Secure; P5 Load Balancer; P6 VNX-DNA integration + optimisation (V14-BRIEF workstreams A-C, E-F); P7 media adapters; P8 Linux block device; P9 integrated validation; P10 Pi portability + optimisation; P11 hardware + boot engineering; P12 V14 acceptance review (commits, files, tests, benchmarks, limitations, remaining physical work); P13 V15 plan.
After each phase report: implemented, tested, exact results, benchmark changes, known defects, remaining work, acceptance pass/fail. Advance only after validation; fix failures, never weaken tests.

## 25. Final report
Repo path; branch + HEAD; initial + final git status; files created/modified; languages + reasons; architecture + dependency summary; tests + exact results; benchmarks vs verified baseline; storage-device E2E results; memory + Pi results (cross-compile vs device testing distinguished); security + recovery results; completed / incomplete / hardware-dependent features; limitations; V15 plan; exact reproduction commands. No tag/merge/push/release.

Final instruction: build VNX as a complete technical storage platform. Don't reject a novel design because no equivalent product exists: research mechanisms, find constraints, build testable interfaces, implement feasible parts, investigate open questions experimentally. Don't substitute optimism for evidence: name unknowns, research, test a hypothesis, report honestly.
