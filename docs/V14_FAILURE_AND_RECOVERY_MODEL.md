# V14 failure and recovery model

Phase 4 specification (master directive §10; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P4). Implementation:
`src/vnxdna/native/rust/vnxd/src/{fallback,fault}.rs` on the volume ([V14_VOLUME_AND_IO.md](V14_VOLUME_AND_IO.md)).
Security controls are in [V14_SECURITY_MODEL.md](V14_SECURITY_MODEL.md).

## 1. The rule every failure obeys

A request either completes as specified or fails with a typed error ([V14_CONTRACTS.md](V14_CONTRACTS.md) §5). It
never returns bytes other than the last completed write of the range (unwritten ranges read as zeros), and a
failed write leaves the range as it was. Every fault below is tested against that rule (§6).

## 2. Failure classes

| class | examples | what happens |
|---|---|---|
| retryable backend error | BACKEND_UNAVAILABLE, BACKEND_BUSY, TIMEOUT, RESOURCE_EXHAUSTED | retried with backoff (§3); if it persists the device degrades (§4) |
| permanent request error | INVALID_ARGUMENT, OUT_OF_RANGE, NO_SPACE | returned at once, no effect |
| integrity failure | a receipt, readback or read that does not match | CORRUPTION, never the bad bytes (§5) |
| crash | the process stops at any point | recovery at the next open (§5) |
| metadata damage | journal bytes damaged | one copy: repaired; both copies: refused (§5) |
| authorisation | wrong token, wrong key | UNAUTHORIZED, audited |

## 3. Retries

`ResilientMedia` wraps the backend adapter. A retryable failure is repeated up to `max_attempts` (default 4) with
exponential backoff (`base_delay` 10 ms doubling to `max_delay` 500 ms); permanent errors are returned after one
attempt. A `put` is repeated only after checking that the failed attempt left nothing: the generation present with
exactly the given bytes means the earlier attempt reached the backend (success, stored once); present with other
bytes is CORRUPTION. A retried `remove` whose earlier attempt succeeded is a success. Exhausted retries are audited
(`retry_exhausted`).

## 4. Backend health and degraded mode

Every operation's final outcome feeds the backend's health: an exponentially weighted success score and consecutive
failures per operation kind (put, get, remove, list, sync); the worst kind decides the state. Errors that describe
the request, the data or the key (NOT_FOUND, INVALID_ARGUMENT, CORRUPTION, OUT_OF_RANGE, UNAUTHORIZED) do not count
against the backend.

| state | entered when | behaviour |
|---|---|---|
| healthy | no operation kind is failing | normal |
| degraded | `degrade_after` (default 1) consecutive failures of some kind | normal; device health `degraded` |
| unavailable | `unavailable_after` (default 3) consecutive failures of some kind, or a failed probe | **read-only**: `put`, `remove`, `sync` refused at once with READ_ONLY (EPERM; nothing reaches the backend, nothing is half-written); `get` and `list` attempted once each, so reads keep working wherever the backend still answers |

Every `probe_interval` (default 5 s) one write-side operation is let through as a probe. If it succeeds, every
write-side kind (put, remove, sync) starts afresh, so the device leaves read-only mode whichever write-side kind
failed; it is healthy again unless reads are still failing. A failed probe keeps it read-only for another interval;
a probe that panics releases its slot. A successful read clears only the read kind's own streak: when writes caused
the read-only mode, reads never end it (a backend may answer reads and still fail writes), and successful removes
never hide failing puts. Every transition is audited (`backend_state`). The management `health` result reports the state,
consecutive failures, score, retries and refused writes (`Health::to_json`).

## 5. Integrity and crash recovery

* **write path**: a receipt that does not describe the bytes, or a readback that differs (`verify_readback`, on by
  default), fails the write with CORRUPTION; the new generation is removed, the mapping unchanged.
* **read path**: every read is checked against the length and SHA-256 recorded in the journal; a mismatch, a short
  read or a mapped generation missing from the backend is CORRUPTION (never zeros, never other bytes). `Volume::scrub`
  reads and verifies every mapped extent and reports the failures without changing anything; a whole-extent write
  repairs an extent.
* **crash**: the map record is the commit point of every transaction (one extent or a batch). `open` replays the
  journal, cuts a torn last append, removes orphan generations (failed removals are reported and retried at the next
  open), and marks mapped extents whose generation is missing as damaged (CORRUPTION until rewritten).
* **metadata**: the journal is mirrored; one damaged copy of any record is repaired and the mirror restored; damage
  to both copies, or anything that is not a torn last append, is refused with CORRUPTION and nothing is changed or
  collected. An unmirrored (P2/P3) journal is VERSION_MISMATCH.
* **audit log**: verified at open; a broken chain refuses to start the service.

## 6. Fault-injection matrix

`FaultMedia` (`fault.rs`) misbehaves on command; `tests/fault_matrix.rs` runs every row 20 times with different data
on a volume behind `ResilientMedia` (3 attempts, no delay) and checks the recorded outcome every time.

| # | fault injected | recorded expected outcome |
|---|---|---|
| 1 | crash after put / before the map record / inside the map record (torn) | reopen: OLD content, orphan collected; torn tail reported for the torn record |
| 2 | crash after the map record / after the old generation's removal | reopen: NEW content |
| 3 | put fails once or twice (unavailable, busy, timeout) | write succeeds VERIFIED after retry, no orphan |
| 4 | put stored but acknowledgement lost | write succeeds VERIFIED, stored once |
| 5 | put fails persistently | first write BACKEND_UNAVAILABLE; device read-only (READ_ONLY) within the threshold; reads return the old content |
| 6 | torn put with a lying receipt / wrong receipt / silently corrupted write / other bytes stored with the acknowledgement lost (the retry finds them) | write CORRUPTION, old content kept, no orphan |
| 7 | silently corrupted write with readback off | write BACKEND_COMMITTED; read CORRUPTION (never the bad bytes); scrub reports the extent; a rewrite repairs it |
| 8 | bit rot, short read, lost object on read | CORRUPTION; the next (clean) read returns the content |
| 9 | old generation cannot be removed | write succeeds; the next open collects the orphan |
| 10 | sync fails | flush returns BACKEND_BUSY (retryable) |
| 11 | one byte of the journal flipped (any offset) | open repairs it, content intact |
| 12 | both copies of a map record damaged | open refused CORRUPTION, nothing collected |

Covered by other P4 tests: the wrong sealing key (UNAUTHORIZED at open, nothing collected), moved, replayed or
tampered sealed objects (CORRUPTION), a sealing worker that cannot start (BACKEND_UNAVAILABLE); and from P2/P3: random
crash workloads (all-or-nothing requests), damage inside the journal, foreign backend objects, another device's
backend, cache checksum failures and interrupted writebacks.

## 7. What is not handled in V14

A backend cannot serve stale content as current: a generation is immutable once mapped, and every read is checked
against the SHA-256 the journal recorded for the mapped generation (sealed objects are also bound to their
generation). A generation number whose write failed before its map record left no record and no object, so it can be
allocated again after a restart; that is harmless for the same reasons. Simultaneous loss of a generation and of both journal copies of its record is unrecoverable
without a second device (replication is not in V14). The torn-tail rule cannot tell a zero-filled lost page of
committed records from an interrupted append when the zeroed tail is no longer than the largest append the device
can write ([V14_VOLUME_AND_IO.md](V14_VOLUME_AND_IO.md) §2).
