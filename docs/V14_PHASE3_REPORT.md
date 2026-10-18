# V14 Phase 3 report: VNX-RAM and VNX Cache

Date 2026-10-18. Branch `build/v14` on top of 952459d (tag `v14-p2`). Scope: [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md)
P3 (master directive §5, §8). Specifications: [V14_RAM_AND_CACHE.md](V14_RAM_AND_CACHE.md),
[V14_MOLECULAR_MEMORY.md](V14_MOLECULAR_MEMORY.md).

## Implemented

* `vnxd/src/ram.rs` — VNX-RAM, the electronic working-memory manager: total budget and per-subsystem quotas (io,
  cache, staging, codec, metadata), RAII leases, backpressure (wait, RESOURCE_EXHAUSTED, TIMEOUT, CANCELLED),
  buffers that lease their whole allocation and are zeroed into a capped reuse pool, accounting (in use, peak,
  grants, waits, refusals) and leak detection (`outstanding`).
* `vnxd/src/cache.rs` — VNX Cache: verified read-through extent cache within the cache quota; write-through by
  default; opt-in write-back acknowledged as STAGED (volatile) with dirty tracking, flush in extent order, dirty
  writeback before eviction, checksum check before every writeback, lost-extent handling, bypass when no room.
* `vnxd/src/io.rs` — `ExtentStore` trait: `BlockIo` runs over the volume or over the cache (`BlockIo::over`
  checks that both have the same geometry).
* `vnxdna.device.molecular` — the separate molecular-memory research interface `vnx.molecular-memory/1`: protocol,
  seeded simulator (all error, endurance and latency figures are caller-chosen model inputs), conformance suite.

Not implemented, on purpose: prefetch (no read-pattern evidence yet; P9 measures), a persistent write-back cache
(needs its own journal; not claimed).

## Tests and exact results

| check | result |
|---|---|
| `cargo test --release --offline --locked`, `RUSTFLAGS=-D warnings` (vnxd) | 83 passed (77 unit, 2 CLI, 4 golden); 18 new in P3 |
| the unit tests, 30 repeated runs | 30/30 passed |
| `cargo test` debug build | 83 passed |
| `pytest tests/v14` | 87 passed (7 new: molecular interface) |
| full suite (6 workers, systemd scope) | **4,541 passed, 4 skipped, 0 failed**, 360.2 s (4,534 + the 7 molecular-interface tests) |
| `ruff`, `mypy` (202 files), doc links | clean |

What the P3 tests establish: quotas and the total bind (also when quotas over-commit the total); a saturated
8-thread workload keeps leased memory at or below the budget with no lease leaked; deadlines and cancellation
reserve nothing; reused buffers expose no earlier bytes; the cache stays within its quota, serves verified copies,
never shows data older than a completed write under concurrent readers and writers with constant eviction (both
modes), refuses or re-reads bad copies, never writes a corrupt dirty copy back, keeps acknowledged write-back data
through a backend outage under memory pressure, and `BlockIo` through the cache matches a byte model in both modes;
the molecular simulator passes its conformance suite, detects every simulated read error, models wear-out, and is
deterministic for a seed.

## Review

An independent review of the P3 diff found two blocking cache bugs, both reproduced, and six minor issues; all fixed:

| finding | fix |
|---|---|
| write-back could lose a write already acknowledged STAGED (old dirty copy dropped before its replacement was allocated; under memory pressure + backend outage the fallback write failed and a later flush reported success) | existing copies are overwritten in place; a failed fallback keeps any dirty copy |
| a dirty copy failing its checksum was still written back by flush/eviction/invalidate, after which the corrupt bytes were served as valid | checksum check before every writeback; the extent becomes lost (CORRUPTION until a whole-extent write) and flush reports it |
| leases covered the requested length, not the allocation (real cache memory up to 2x its quota) | the whole size class is leased; the spec states what the budget covers and bounds the rest |
| pool cap could be overshot by concurrent drops | checked under the accounting lock |
| a cache dropped with dirty data was silent | reported on stderr |
| extents in the caller's lock stripe were never evicted | evictable (the stripe is already held) |
| `BlockIo::over` did not check geometries | checked |
| molecular simulator: doc said a stored bit flips (it is a transient read error); DEGRADED stuck after one read error; endurance 0 accepted; non-bytes data raised TypeError; `set_state(DEGRADED)` ignored | doc corrected; DEGRADED = worn-out blocks; validated; INVALID_ARGUMENT; refused |

Accepted limits (documented): write-back data is volatile until flushed; each cache hit copies the extent and
computes its CRC under one cache-wide lock (a throughput limit to be measured in P9, not a correctness issue).

## Benchmark changes

None measured in P3 (no host-facing path yet); cache hit rates and memory use are measured on the integrated device
in P9 against the electronic image backend (evidence category C).

## Remaining work

Security sealing and the fallback retry policy (P4); load balancer (P5); codec integration (P6); media adapters
(P7); the block device (P8).

## Acceptance (P3 criteria of V14_MASTER_PLAN)

| criterion | result |
|---|---|
| memory stays within limits under saturation (measured peak) | PASS (leased memory; pools and transient copies bounded and documented) |
| cancellation releases buffers | PASS |
| no stale read under concurrent writes | PASS |
| dirty eviction and interrupted writeback tests | PASS |
| buffers do not leak across security contexts | PASS |
| full suite not below the gate, 0 failed | PASS |

Phase 3: **PASS**. Next: P4 Fallback + Secure.
