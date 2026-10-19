# V14 Phase 4 report: fallback and security

Date 2026-10-18. Branch `build/v14` on top of c200ea8 (the limit fixes after tag `v14-p3`). Scope:
[V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P4 (master directive §10, §11). Specifications:
[V14_FAILURE_AND_RECOVERY_MODEL.md](V14_FAILURE_AND_RECOVERY_MODEL.md), [V14_SECURITY_MODEL.md](V14_SECURITY_MODEL.md).

## Before P4: the documented limits were removed (commit c200ea8)

| limit (P2/P3 reports) | now |
|---|---|
| a bit flip in the last journal record undid its transaction | mirrored journal: one damaged copy of any record is repaired; damage to both copies is refused, never guessed |
| a multi-extent write was atomic per extent only | one `map_batch` transaction per request (up to 1,024 extents) |
| generation numbers could be reused after compaction | compaction records each extent's highest used generation |
| cache hits copied and checked under one cache-wide lock | per-extent copy cells; the index lock covers O(log n) work only |
| O(n) LRU candidate copy on eviction | lazy LRU walk |
| transient per-request copies not leased | `BlockIo::with_ram` leases them from the `io` quota |
| no prefetch | opt-in sequential read-ahead (off by default until P9 measures read patterns) |

The independent review of that work found two blocking journal bugs, both reproduced and fixed with tests before the
commit: an unmirrored (P2/P3) journal was truncated instead of refused, and the torn-tail rule accepted up to 131 KB
of damaged tail as a torn append. Write-back caching stays volatile by definition (STAGED promises nothing across a
crash; write-through is the durable default); Pi acceptance and the ARM toolchain remain P10.

## Implemented in P4

* `fallback.rs`: retry by error class with exponential backoff; put retries that never store twice; backend health
  (per-operation-kind failure streaks, score); read-only degraded mode with timed probes; audit events.
* `fault.rs` + `tests/fault_matrix.rs`: the fault-injection adapter and the 12-row matrix, 20 runs per row.
* `audit.rs`: hash-chained, fsynced JSON-lines audit log with verification.
* `authz.rs`: token roles for `vnx.mgmt` (new op `authenticate`), constant-time comparison, lock-out, audited
  denials; transport-free `MgmtSession` (wired to the socket in P8).
* `seal.rs` + `vnxdna.device.seal` + `vnxdna.device.worker`: sealing at the extent boundary with the existing
  AES-256-GCM/HKDF layer in Python workers (`vnx.codec-ipc` ops `key_check`, `seal_extent`, `open_extent`); sealed
  volumes refuse plain adapters and vice versa; the wrong key is refused at open.
* `Volume::scrub`; a mapped generation missing from the backend reads as CORRUPTION.

## Tests and exact results

| check | result |
|---|---|
| `cargo test --release --offline --locked`, `RUSTFLAGS=-D warnings` (vnxd) | 127 passed (113 unit, 2 CLI, 8 fault matrix, 4 golden) + 1 run by pytest (`seal_worker`, ignored under plain cargo) |
| the vnxd tests, 30 repeated runs | 30/30 passed |
| `cargo test` debug build | passed |
| `pytest tests/v14` | 116 passed (94 before P4: sealing format and worker, new protocol messages) |
| full suite (6 workers, systemd scope) | **4,570 passed, 4 skipped, 0 failed**, 405.2 s (4,548 after the limit fixes) |
| `ruff`, `mypy` (`vnxdna.device`, 7 files) | clean |

What the P4 tests establish: every row of the fault matrix gives its recorded outcome 20 times out of 20; retries
absorb transient faults without double-storing; a backend that cannot write turns the device read-only while reads
keep working, and it leaves read-only mode once writes succeed again, whichever kind failed; sealed volumes put no
plaintext on the backend and refuse moved, replayed, tampered or oversized objects and the wrong key; management
operations are refused and audited by role; the audit chain detects edits, removals and reordering.

## Review

Own tests found three defects while P4 was written, each fixed with a test: a successful read ended read-only mode;
a successful clean-up remove kept a backend whose puts all failed looking healthy (now consecutive failures are
counted per operation kind); a write refused in read-only mode was "cleaned up" and its space stayed counted.

Two independent reviews of the P4 diff (code; security):

| finding | severity | fix |
|---|---|---|
| a write-side probe that succeeded did not clear a failing `sync`/`remove` streak: the device could stay read-only forever after the backend recovered (reproduced) | blocking | a successful probe resets every write-side kind; test for each kind |
| a probe that panicked kept the probe slot taken: every later write refused (reproduced) | blocking | drop guard releases the slot; test |
| the sealing worker inherited the service's working directory, and `python -m` imports from it first: a planted module would get the key (reproduced) | medium | workers start in the private staging directory |
| the staging directory's privacy was assumed, not enforced; inputs and outputs followed symlinks (reproduced) | medium | both sides refuse a symlinked, foreign-owned or group/other-accessible directory; no symlinks followed; only regular files |
| plaintext could stay in staging after a crash; restart reused file names | medium | per-process names; files of dead processes swept at start (live sealers sharing the directory are left alone) |
| worker-side failures (missing staging file, existing output) surfaced as CORRUPTION or "not stored" | minor | only CORRUPTION, UNAUTHORIZED, VERSION_MISMATCH pass through; the rest is BACKEND_UNAVAILABLE |
| a flipped bit in a sealed header (version, key check) stopped `scrub` and read as "wrong key" | minor/low | per-object mismatches are CORRUPTION; scrub records every non-transport error; UNAUTHORIZED never counts against backend health |
| a failed audit append left a fragment that broke the chain at the next open | low-medium | cut back to the last complete line, or poison until reopened; log created 0600, symlinks refused |
| a raw 32-byte passphrase was accepted as a key (offline guessing via the key check) | low-medium | the worker accepts only generated keys (64 hex / 44 base64) from a private, owned, non-symlink file |
| key and policy files: check-then-open races, symlinks | low | opened once (no symlinks), checks on the opened file |
| one session could flood the audit log with denials; a failed authentication kept an earlier grant | low/minor | session cut off after 16 refusals; a failed authentication drops the role, a failed hello resets the session |
| unbounded worker output line; oversized backend objects staged before refusal | low | 1 MiB line bound; objects above extent + 56 bytes refused first |
| the worker died on non-protocol exceptions | low | INTERNAL error response, partial output removed |
| the salt test hook could reuse a nonce | low | private known-answer helper only |
| the put-retry "other bytes stored" branch was untested (a mutation survived) | test gap | new fault `CorruptAckLost`, matrix row 6 |
| docs: "a successful read never ends read-only mode", "generations never reused", audit tamper evidence | doc | corrected |

Open item for the founder (not changed in P4): the `cryptography` pin `>=44,<47` predates advisories fixed in 48.0.1
(one concerns the bundled OpenSSL); raising it is a dependency decision recorded with the V11 crypto-pin question.

## Benchmark changes

None measured in P4. The cost of sealing (two staging transfers and AES-GCM per extent) and of retries is measured
on the integrated device in P9 (evidence category C).

## Acceptance (P4 criteria of V14_MASTER_PLAN)

| criterion | result |
|---|---|
| fault-injection matrix (crash, partial write, unavailable backend, corrupt metadata, failed verification) gives the recorded expected outcome every time | PASS (12 rows x 20 runs each, and the vnxd tests repeated 30 times) |
| unauthorised requests refused and audited | PASS (every op per role, failed authentications, lock-out, flood cut-off) |
| no unverified data returned | PASS (every read verified against the journal digest; sealed objects authenticated; every fault row checks it) |
| sealing at the extent boundary with the existing security layer | PASS (end to end through the AES-256-GCM worker; no plaintext on the backend; wrong key refused at open) |
| full suite not below the gate, 0 failed | PASS (4,570 passed, 0 failed) |

Phase 4: **PASS**. Next: P5 load balancer.
