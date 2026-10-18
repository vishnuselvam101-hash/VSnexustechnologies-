# V14 Phase 7 report: media adapters

Date 2026-10-18. Branch `work/v14-p7` from `build/v14` b69e70f. Scope: [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P7
(master directive §13). Specification: [V14_MEDIA_ADAPTERS.md](V14_MEDIA_ADAPTERS.md).

Evidence categories: C (electronic image backend), B/D (software device over a SIMULATED channel). Nothing is E or F:
no DNA was synthesised, stored, retrieved or sequenced; no adapter was tested against physical equipment; no number in
this phase is a measurement of a DNA medium.

## Implemented

* **A — DNA software simulator** (`vnxd/src/dnasim.rs`, `codec.rs`; Python `vnxdna.device.dnasim`): `put` frames
  the bytes and encodes them with the existing VNX-DNA codec (`pipeline.stream.encode_extent`: unchanged archive
  container, encoder, ECC) through `vnx.codec-ipc/1` `encode_extent`; `get` runs one simulated sequencing per attempt
  through the existing staged channel simulator (shipped `vnx.channel-model/1`, seed per attempt) and the existing
  decoder (`decode_extent`), accepting only bytes with the recorded SHA-256 and length; seeded synthesis/sequencing
  failure model; modelled latency (accounted, optionally slept); `report()` labelled SIMULATED. Pools can be kept in
  any adapter (in memory, or in an image so a simulation survives restarts).
* **B — electronic image backend** (`image.rs`, `vnx.media-image/1`): one immutable object file per generation;
  temp file + fsync + `link(2)` (no-clobber) + directory fsync; integrity-checked reads (generation in header,
  length, SHA-256); crash leftovers swept at open; advisory single-owner lock; scrub. Labelled "NOT DNA".
* **C — fault injection** (`fault.rs`, extended): wraps any adapter; seeded `FaultPlan` (honest / lying), delays,
  whole-medium outage and recovery, fault trace; forwards durability and sealing of the wrapped adapter.
* **D — future physical-DNA interface** (`lab.rs`, `vnx.dna-lab/1`): synthesis orders and jobs, pools with
  location / preservation / lifecycle, retrieval and sequencing jobs, read deliveries (FASTQ, gzipped FASTQ, BAM,
  POD5, BCL run folder) with SHA-256 and quality metrics, reconstruction handoff to the codec, cancel / retry,
  typed equipment errors, lab health; the job state machine; `SimulatedLab`; `LabMedia` (refuses a non-simulated lab:
  contract v1 has no physical-DNA durability class). Sources of the modelled practice: V14_MEDIA_ADAPTERS.md §5.1.
  No vendor command or API is modelled; no physical validation.
* `worker.rs`: the `vnx.codec-ipc/1` worker pool, extracted from `seal.rs` and shared by sealing and the codec
  (sealing behaviour unchanged).

Contract: media contract **v1 unchanged** (trait, durability classes, conformance suite); `vnx.codec-ipc/1` schemas
unchanged (this phase defines the content of the codec operations' `options`); the P1 golden contract files pass
unchanged (`tests/golden.rs`, `tests/v14/test_device_contracts.py`). Behaviour change of a test adapter:
`FaultMedia::info()` reports the wrapped adapter's durability and `max_in_flight` (identical to before for the
default in-memory inner).

## Conformance

| adapter | `media::conformance` |
|---|---|
| `image` | PASS (also wrapped in `faulty` with a zero-rate plan) |
| `dna-sim` over the test codec, perfect and noisy channel | PASS |
| `dna-sim` over the VNX-DNA codec, perfect channel and `illumina-like` (SIMULATED) | PASS |
| `faulty` (default inner, and over `image`) | PASS |
| `dna-lab-sim` over `SimulatedLab`, test codec and VNX-DNA codec | PASS |

## Tests and exact results

| check | result |
|---|---|
| `cargo test --release --offline --locked`, `RUSTFLAGS=-D warnings` (vnxd) | build clean; 178 passed, 0 failed (150 unit, 2 CLI, 2 concurrency stress, 8 fault matrix, 4 golden, 12 media adapters) + `seal_worker` and `media_worker` via pytest (gate 141) |
| `cargo test` debug build | passed (the pre-existing P2 test `io::tests::backpressure_cancellation_timeout_and_barrier` hung under host load in the debug build, before and after the P7 changes; it passes alone, 3/3, and in the release suite; see below) |
| `tests/media_worker.rs` (real codec, via pytest) | 2 passed |
| `ruff` (`src/vnxdna/device`, new tests) | clean |
| full suite (8 workers, systemd scope, idle host, merged build/v14 41fcc51) | **4,711 passed, 4 skipped, 0 failed** (gate before P7: 4,689 / 4 / 0; P7 adds 22) |

## Review

The independent review of 2fe83ab found no blocking issue (no path returning bytes other than those put; the
`WorkerSealer` refactor keeps its behaviour). Its should-fix findings and what was done:

| finding | severity | fix |
|---|---|---|
| image LOCK was created empty and filled afterwards: a concurrent opener could take it as stale; a failed open kept it; a restart with the same pid (pid 1 in a container) was refused forever | should-fix | LOCK = "<pid> <token>" created by linking a complete fsynced file, ownership re-checked; same-pid locks not held by this process are stale; a failed open releases it; tests |
| Python worker turned its own resource failures (disk full, output/OS errors) into permanent CORRUPTION / INVALID_ARGUMENT | should-fix | BACKEND_UNAVAILABLE (retryable); test |
| `vnxd` passed every worker INVALID_ARGUMENT on as permanent (incl. staging failures) | should-fix | only option refusals (message prefix `options: `) pass; the rest is BACKEND_UNAVAILABLE; test |
| `LabMedia` destroyed the pool after a failed catalogue put even when the entry had been stored | should-fix | destroys only if the entry is absent; test (lost acknowledgement vs unavailable) |
| lab jobs could be left running unowned | should-fix | cancel on status errors; the reconciliation a real lab needs is documented (§6 of the spec) |
| a DNA-sim pool store over an image is limited to 65 MiB objects | should-fix | `ImageMedia::with_max_object`; documented; test |
| the adapters relied on the codec alone to check the decoded SHA-256 | should-fix | `codec::verify_decoded` in both adapters; test with a lying codec |
| ambiguous put retries re-read through a full simulated sequencing (cost; can fail under noise) | should-fix | documented as a known limit: a cheap digest query is a media-contract addition, left to a versioned change |
| `FaultMedia` did not forward `sealed()`; a failed job could be retried twice; offline `cancel`; channel model names matched with a trailing newline and could resolve to a file in the worker's directory; test codec allocation from a damaged length; O(n) trace eviction | nit | all fixed, with tests where behaviour changed |
| determinism of seeded decisions holds for one call order only; `/proc` liveness across pid namespaces | nit | documented (spec §6) |

Found while testing: the toy codec's whole-read majority vote failed under substitutions (per-base vote now); the
`io::tests` flake above (pre-existing, reported, not changed).

## Acceptance (P7)

| criterion | result |
|---|---|
| A: DNA simulator over the existing codec, configurable latency and failure models, SIMULATED labels | PASS |
| B: electronic backend persisting across restarts, crash-safe, integrity-checked, not DNA | PASS |
| C: deterministic seeded faults, delays, partial ops, corruption, unavailable media, recovery; upper layers survive or report | PASS |
| D: interface + simulator + conformance + docs with sources; no physical claim | PASS (interface and simulator only; hardware-dependent work listed in the spec §5 and §6) |
| every adapter passes `media::conformance`; contract unchanged; golden files pass | PASS |
| tests never drop; full suite 0 failed | PASS (4,711 passed / 4 skipped / 0 failed vs 4,689 before; cargo 178 vs 141) |

Phase 7: **PASS**. Next: P8 Linux block device.
