# V14 block volume and VNX-IO

Phase 2 specification (master directive §7, §12; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P2). Implementation:
`src/vnxdna/native/rust/vnxd/src/{journal,volume,io}.rs` on the Phase 1 contracts ([V14_CONTRACTS.md](V14_CONTRACTS.md)).
No host block device is attached yet (P8); P2 tests run the volume over the in-process `MemoryMedia` adapter and real
journal files.

## 1. Layout

A volume is a directory holding `journal` and is bound to one media adapter that stores the extents' generations.

```
journal   superblock (geometry, device id, flags), then map_update, map_batch and generation_released records
          (vnx.record/1), every record stored twice (a mirrored pair)
backend   one immutable generation per (extent, generation): the extent's full bytes
          + the binding marker (extent 2^64 - 1, generation 1): "VNXDEV01" + device id
```

The marker binds the backend to the volume. `open` checks it before anything else: a backend without the marker or
with another device's marker is refused (INVALID_ARGUMENT) and nothing on it is touched.

## 2. Journal

Append-only, **mirrored**: every record is written twice, back to back, in one append. `append` writes the pair and
calls `fdatasync` before it returns: a record is durable exactly when `append` returns `Ok`. A failed append cuts the
file back to the last complete pair; if that rollback fails too, the journal is poisoned (every later append fails)
and the volume stops, keeping the new generations, because the record may still reach the disk.

`open` reads the pairs in order:

| what it finds | outcome |
|---|---|
| both copies intact and equal | the record |
| one copy damaged (any byte, header included) | the record from the other copy; counted in `journal_repaired`; after recovery the volume compacts the journal, which rewrites both copies (if that rewrite fails, the volume still opens and reports `mirror_restore_error`) |
| the file ends inside the last pair (copy 2 incomplete; copy 1 incomplete; the append stopped inside the header), or the last append's header was written and the rest of its pair is zeros, or a zero-filled tail no longer than the largest pair this device can write (a map batch of min(extent count, 1,024) entries) — and nothing decodable follows | a torn append: cut there, reported as `torn_tail` |
| anything else: both copies of a complete pair damaged, damage followed by decodable records, a non-zero garbage tail, a zero-filled tail longer than one append, a damaged or unpaired superblock | damage inside the committed journal: CORRUPTION (or VERSION_MISMATCH below), nothing changed (committed records are never discarded and their generations never garbage-collected) |
| the record after a valid first copy is a different valid record, or the superblock has no copy | an unmirrored journal (the P2/P3 layout): VERSION_MISMATCH at the first pair, CORRUPTION elsewhere |

Only the last append can be torn, because every append is fsynced before the next one starts; a crash leaves a
prefix of it or a zero-filled range, which is what the torn rules accept. So a single damaged byte anywhere, in the
last record too, no longer undoes a transaction. What remains ambiguous: a zero-filled tail no longer than the
largest append the device can write is cut as torn even if it overwrote committed records (a lost write of the last
page, for example); with the default geometry that is at most 2 x 65,568 = 131,136 bytes of journal tail.
`vnxd inspect-journal` prints the same reading without
changing the file.

Replay: each record's txid is greater than the previous one; replay refuses a journal that breaks that, names an
extent beyond the device, has a second superblock, has a map update (or batch entry) whose `previous` is not the
current mapping, whose generation is not above every generation the journal used for the extent, or whose length is
not the extent's, or releases the generation currently mapped (CORRUPTION). A `map_batch` is checked whole and then
applied whole. `compact` replaces the journal atomically (temporary file, fsync, rename, fsync of the directory) with
the superblock, one map record per live extent and, for every extent whose highest used generation is above its live
one, a release record of that generation, so generation numbers are never reused, across compactions too. Compaction
holds every extent lock (no commit in flight); the handle on the new file is opened before the rename, so later
appends always reach the live journal.

## 3. Write transaction

Writes replace whole extents with new generations; a partial extent is a read-modify-write inside the transaction.
A transaction covers one extent (`map_update`) or up to 1,024 extents (`map_batch`, `Volume::write_extents`): the
steps below run for every extent of it, and the single map record is the commit point of all of them.

| step | action | state | crash here leaves |
|---|---|---|---|
| 1 | allocate generation g (above every generation used for the extent), reserve backend space | STAGED | old mapping |
| 2 | `media.put`, check the receipt's length and SHA-256 | BACKEND_COMMITTED | old mapping + orphan g |
| 3 | optional readback (`verify_readback`, default on): bytes equal | — | old mapping + orphan g |
| 4 | append + fsync `map_update(txid, extent, g, previous, length, sha256)` — **commit point** | VERIFIED (readback) or BACKEND_COMMITTED | new mapping (+ orphan old generation) |
| 5 | `media.remove(previous)`, append `generation_released` | — | new mapping |

A torn map record (crash inside step 4) is cut at open: old mapping + orphan g. A failed put, a receipt that does not
describe the bytes, a failed readback or a failed map append aborts the write, removes every new generation of the
transaction and releases the space; no mapping changes. The space check covers the whole transaction up front (one
NO_SPACE, no trace). A multi-extent transaction locks its extents' lock stripes in ascending order; single-extent
operations hold one stripe and never wait for a second, so no lock-order cycle exists. A failed remove in step 5 does not fail the write: the old generation stays as an orphan and
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

`BlockIo` maps byte ranges to extent spans (`Geometry::spans`) and writes them as one transaction
(`ExtentStore::write_extents`): after a crash or an error a write request is applied entirely or not at all, across
every extent it spans. That holds for requests spanning up to 1,024 extents, which is every request (64 MiB cap)
when extents are larger than 64 KiB (the default extent is 1 MiB; an unaligned 64 MiB request over extents of exactly
64 KiB touches 1,025); a request spanning more extents is applied as consecutive transactions of 1,024 extents, in
order. A request past the capacity is OUT_OF_RANGE, a zero-length
request INVALID_ARGUMENT, before any effect. `flush` syncs the backend (map records are already fsynced). With a
VNX-RAM manager (`BlockIo::with_ram`), each request first leases its working memory from the `io` quota: its own
length plus one extent (read), or twice its length plus three extents (write: the new extent contents a store
builds, two read-modify-write copies and one readback copy); a request larger than the whole quota is
INVALID_ARGUMENT.

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
panicking adapter failing only its own requests, and oversized requests refused. Added when the limits were removed
(before P4): a one-byte flip at every offset of a journal repaired, damage to both copies refused (in the last
record too), every torn prefix of a pair cut, an unmirrored journal refused; multi-extent transactions (crash at
every stage: all old or all new; a failed put at each position: no trace; NO_SPACE whole; replay checking a batch
whole); generation numbers never reused across compactions; the random crash workload now requires every request to
be all-or-nothing across its extents; request memory leases under 8 threads.
