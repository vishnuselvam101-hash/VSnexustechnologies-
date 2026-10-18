# V14 block volume and VNX-IO

Phase 2 specification (master directive §7, §12; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P2). Implementation:
`src/vnxdna/native/rust/vnxd/src/{journal,volume,io}.rs` on the Phase 1 contracts ([V14_CONTRACTS.md](V14_CONTRACTS.md)).
No host block device is attached yet (P8); P2 tests run the volume over the in-process `MemoryMedia` adapter and real
journal files.

## 1. Layout

A volume is a directory holding `journal` and is bound to one media adapter that stores the extents' generations.

```
journal   superblock (geometry, device id, flags), then map_update and generation_released records (vnx.record/1)
backend   one immutable generation per (extent, generation): the extent's full bytes
          + the binding marker (extent 2^64 - 1, generation 1): "VNXDEV01" + device id
```

The marker binds the backend to the volume. `open` checks it before anything else: a backend without the marker or
with another device's marker is refused (INVALID_ARGUMENT) and nothing on it is touched.

## 2. Journal

Append-only. `append` writes one record and calls `fdatasync` before it returns: a record is durable exactly when
`append` returns `Ok`. A failed append cuts the file back to the last complete record; if that rollback fails too,
the journal is poisoned (every later append fails) and the volume stops, keeping the new generation, because the
record may still reach the disk. `open` keeps every record up to the first that does not decode. It truncates there
only when the rest is a torn last append: shorter than one record and with no decodable record after it. Any other
undecodable bytes are damage inside the committed journal: `open` refuses with CORRUPTION and changes nothing, so
committed records after the damage are never discarded and their generations never garbage-collected.

Limit: a bit flip in the last, complete record cannot be told apart from a torn append. That record is dropped; its
transaction is undone (the extent keeps its previous, verified content, never zeros). Each
record's txid is greater than the previous one; replay refuses a journal that breaks that, names an extent beyond
the device, has a second superblock, has a map update whose `previous` is not the current mapping, or has a length
other than the extent's (CORRUPTION). `compact` replaces the journal atomically (temporary file, fsync, rename, fsync
of the directory) with the superblock and one map record per live extent; the handle on the new file is opened
before the rename, so later appends always reach the live journal. Replay also refuses a release of the generation
currently mapped and a map update whose generation is not above every generation the journal used for the extent.
After a compaction the released numbers are forgotten and may be used again; they no longer exist on the backend.

## 3. Write transaction (one extent)

Writes replace a whole extent with a new generation; a partial write is a read-modify-write under the extent's lock.

| step | action | state | crash here leaves |
|---|---|---|---|
| 1 | allocate generation g (above every generation used for the extent), reserve backend space | STAGED | old mapping |
| 2 | `media.put`, check the receipt's length and SHA-256 | BACKEND_COMMITTED | old mapping + orphan g |
| 3 | optional readback (`verify_readback`, default on): bytes equal | — | old mapping + orphan g |
| 4 | append + fsync `map_update(txid, extent, g, previous, length, sha256)` — **commit point** | VERIFIED (readback) or BACKEND_COMMITTED | new mapping (+ orphan old generation) |
| 5 | `media.remove(previous)`, append `generation_released` | — | new mapping |

A torn map record (crash inside step 4) is cut at open: old mapping + orphan g. A failed put, a receipt that does not
describe the bytes, a failed readback or a failed map append aborts the write, removes g and releases the space; the
mapping is unchanged. A failed remove in step 5 does not fail the write: the old generation stays as an orphan and
the next open collects it.

## 4. Recovery (`open`)

0. Check the backend's binding marker (§1).
1. Replay the journal (§2) into the map; remember the highest generation ever seen per extent (never reused).
2. List the backend. Objects of extents outside the device are reported as `foreign` and left untouched. Every other
   generation the map does not reference is an orphan: it is removed and its release is journaled (removals that
   fail are reported in `orphans_left` and still count against the space budget).
3. A mapped generation missing from the backend marks its extent **damaged**: reading it is CORRUPTION (never zeros
   or other bytes) until a whole-extent write replaces it (a partial write reads the extent first, so it fails too).

The recovery report lists records replayed, the torn-tail reason, orphans removed or left, and damaged extents.

## 5. Reads

An unwritten extent reads as zeros (it holds no data). Otherwise the bytes come from the backend and are returned
only when their length and SHA-256 equal the map's; a mismatch is CORRUPTION and is counted. Backend errors keep
their class (BACKEND_UNAVAILABLE / BACKEND_BUSY are retryable).

## 6. VNX-IO

`BlockIo` maps byte ranges to extent spans (`Geometry::spans`): whole-extent spans are written directly, partial spans
are read-modify-write. A request past the capacity is OUT_OF_RANGE, a zero-length request INVALID_ARGUMENT, before any
effect. A multi-extent write is atomic per extent, not as a whole (as a disk is atomic per sector); its result is the
weakest write state of its extents. `flush` syncs the backend (map records are already fsynced).

`IoQueue` (workers + bounded queue):

| property | rule |
|---|---|
| backpressure | `submit` blocks while `depth` requests are queued; `try_submit` returns RESOURCE_EXHAUSTED (retryable) |
| ordering | a request starts only when no earlier-submitted pending request conflicts with it (overlapping extent ranges, at least one write); overlapping writes apply in submission order, a read sees exactly the writes submitted before it |
| barrier | `Flush` conflicts with everything: it waits for all earlier requests and later requests wait for it |
| cancellation | a request cancelled before it starts completes with CANCELLED at once (also while it waits behind a conflicting request) and has no effect; a started request completes |
| timeout | a request whose deadline passes before it starts completes with TIMEOUT (retryable) at that moment and has no effect |
| size | a request above 64 MiB is INVALID_ARGUMENT; queue depth is at most 4096 |
| panics | a request whose execution panics completes with INTERNAL and frees its place; the queue keeps running (a panic inside the volume poisons that extent's lock stripe, so later requests on it fail with INTERNAL) |
| shutdown | new submissions get SHUTTING_DOWN; queued requests are drained, or failed with SHUTTING_DOWN when not draining; workers are joined |
| progress | the earliest pending request never has an earlier conflict, so the queue cannot deadlock |
| metrics | submitted, completed, failed, cancelled, timed out, rejected (queue full), max queue depth, total queue wait |

## 7. Space

`max_stored_bytes` bounds the bytes of all generations held by the backend, live and in flight. An overwrite needs
room for the new generation before the old one is released; a write that does not fit is NO_SPACE with no trace (no
generation, no record, space unchanged). A generation that the backend could not remove after a failed write stays
counted until the next open collects it.

## 8. Tests (P2)

`cargo test` in `src/vnxdna/native/rust/vnxd`: journal create/append/reopen, torn tail, atomic rewrite; volume
write/read/reopen, readback state, NO_SPACE, corrupted media never returned (with and without readback), a crash at
every stage of the transaction recovering to old or new content with exactly the live generations left, a missing
generation as damage, an inconsistent journal refused, compaction; VNX-IO against a byte model over 400 random
unaligned requests and a reopen, a random workload with crashes injected at random stages (every extent is always
either its last committed content or the crashed write's content, never a mix), concurrent writers and readers,
queue ordering over 300 overlapping writes with interleaved reads, backpressure, barrier, cancellation, timeout and
both shutdown modes; and, from the Phase 2 review: damage inside the journal refused without truncation or garbage
collection, another volume's or an unbound backend refused and untouched, foreign objects left alone across opens,
replay refusing a released live generation or a reused one, cancel and deadline resolving behind a stuck write, a
panicking adapter failing only its own requests, and oversized requests refused.
