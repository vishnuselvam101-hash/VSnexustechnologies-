# V14 Phase 2 report: block volume and VNX-IO

Date 2026-10-18. Branch `build/v14` on top of 9d48b24 (Phase 1 + crate licence fix, tag `v14-p1`). Scope:
[V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P2 (master directive §7, §12). Specification:
[V14_VOLUME_AND_IO.md](V14_VOLUME_AND_IO.md).

## Implemented

In the Rust crate `vnxd` (std only, no `unsafe`):

* `journal`: append-only `vnx.record/1` journal with `fdatasync` per record, torn-tail truncation limited to a real
  torn append (damage inside the journal is refused, never truncated), poisoning after a failed rollback, atomic
  compaction whose new handle is opened before the rename.
* `volume`: extent map over a media adapter; immutable generations; the five-step write transaction with the map
  record as commit point (RECEIVED → STAGED → BACKEND_COMMITTED → VERIFIED with readback); read verification
  (length + SHA-256); recovery (journal replay with invariant checks, orphan garbage collection, damaged extents,
  foreign objects left alone); backend binding marker checked before any garbage collection; space budget with
  NO_SPACE; compaction; fault-injection hooks at every stage; statistics.
* `io`: `BlockIo` (byte ranges → extent spans, read-modify-write for partial extents, per-extent atomicity, 64 MiB
  request cap) and `IoQueue` (worker threads, bounded queue with backpressure, conflict-ordered scheduling, flush
  barriers, immediate cancellation and deadlines, panic containment, draining or failing shutdown, metrics).

## Tests and exact results

| check | result |
|---|---|
| `cargo test --release --offline --locked`, `RUSTFLAGS=-D warnings` (vnxd) | 65 passed (59 unit, 2 CLI, 4 golden); 29 new in P2 |
| the same, 30 repeated runs of the unit tests | 30/30 passed |
| `cargo test` debug build (overflow checks) | 65 passed |
| full suite (6 workers, systemd scope) | **4,534 passed, 4 skipped, 0 failed**, 399.0 s (unchanged count: the P2 tests are Rust tests, run by `cargo test` and the CI `rust` job) |

What the P2 tests establish (all on real journal files over the in-process `MemoryMedia` adapter):

* 400 random unaligned reads and writes equal a byte model, also after a reopen;
* a crash injected at each of the five transaction stages recovers to the old or the new content, with exactly the
  live generations left on the backend; 60 rounds of a random workload with crashes at random stages (at least 15
  injected) never leave an extent with mixed content or lose a committed write;
* corrupted backend bytes are never returned (caught by readback at write time, or by SHA-256 at read time);
* NO_SPACE leaves no trace; a missing generation is damage, not zeros; damage inside the journal and another
  volume's backend are refused with nothing deleted; foreign objects survive repeated opens;
* queue: 300 overlapping writes apply in submission order and interleaved reads see exactly the earlier writes;
  backpressure, barrier, cancellation and deadline (also behind a stuck write), panic containment, both shutdown
  modes, oversized requests.

## Review

An independent review of the P2 diff found six bugs and eight minor issues; all were fixed with tests:

| finding | fix |
|---|---|
| a bit flip inside the journal truncated every later committed record and recovery then deleted their generations (committed data read as zeros) | truncate only a real torn tail; otherwise refuse with CORRUPTION, change nothing |
| a backend object of an extent outside the device made the volume unopenable after the first open | foreign objects are reported and never touched |
| opening against another volume's backend deleted that backend's data | binding marker checked before any garbage collection |
| a compaction failing after its rename sent later commits to the unlinked old file | the new handle is opened before the rename |
| a failed append with a failed rollback could leave a map record whose generation was then removed | journal poisoning; the volume stops and keeps the generation |
| a panicking request hung the queue | `catch_unwind`; the request fails alone |
| minor: failed put left an orphan uncounted; possible `stored_bytes` underflow; cancel/deadline only checked at start; damaged-extent repair rule undocumented; two replay invariants unchecked; no request size cap; O(n²) scheduler at very deep queues; poisoned-lock shutdown | remove + accounting; saturating arithmetic; sweep with wake-ups; documented; checked; 64 MiB cap; depth ≤ 4096; poison-tolerant stop |

Accepted limits, documented in the specification: a bit flip in the last complete journal record looks like a torn
append (its transaction is undone, the extent keeps its previous verified content); a write spanning several extents
is atomic per extent, not as a whole.

## Benchmark changes

None measured: P2 has no host-facing path yet and runs on the volatile test adapter. Throughput and latency of the
I/O path are measured in P5 (load balancer) and P9 against the electronic image backend (evidence category C), kept
separate from the codec numbers.

## Known defects and remaining work

* Only `MemoryMedia` exists; the image, fault-injection and DNA-simulator adapters are P7.
* Cache, VNX-RAM budgets and write-back staging are P3; security sealing and the fallback retry policy are P4.

## Acceptance (P2 criteria of V14_MASTER_PLAN)

| criterion | result |
|---|---|
| unaligned, overlapping, concurrent and cancelled I/O tests | PASS |
| crash before/during/after each commit stage recovers to a defined state | PASS |
| out-of-space is a typed error | PASS |
| image of a random workload equals a reference byte model | PASS |
| full suite not below the gate, 0 failed | PASS |

Phase 2: **PASS**. Next: P3 VNX-RAM + Cache.
