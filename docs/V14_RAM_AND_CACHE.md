# V14 VNX-RAM and VNX Cache

Phase 3 specification (master directive §5.1, §5.3, §8; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P3). Implementation:
`src/vnxdna/native/rust/vnxd/src/{ram,cache}.rs`. The separate molecular-memory research interface is specified in
[V14_MOLECULAR_MEMORY.md](V14_MOLECULAR_MEMORY.md).

## 1. VNX-RAM budget

VNX-RAM is the service's electronic working memory: ordinary host RAM under an explicit budget. It never makes data
durable and is never called molecular memory.

| item | rule |
|---|---|
| budget | a total and a quota per subsystem: `io`, `cache`, `staging`, `codec`, `metadata` (`RamConfig::split` = 25/40/20/10/5 %); a reservation must fit both its quota and the total |
| lease | an RAII reservation; returned when dropped, so error and cancellation paths cannot leak |
| backpressure | a reservation that does not fit waits until it fits; `Wait::No` fails at once with RESOURCE_EXHAUSTED (retryable); `Wait::Until` fails with TIMEOUT at its deadline or CANCELLED when its token is set (checked at least every 10 ms) |
| impossible request | larger than its quota: INVALID_ARGUMENT (it could never fit) |
| buffers | a `Buffer` leases its whole allocation (the power-of-two size class of its length, at least 4 KiB; a class larger than the quota is INVALID_ARGUMENT); on drop the allocation is zeroed and pooled (pool capped at 1/8 of the budget, checked under the accounting lock); a reused buffer is handed out zero-filled, so no byte crosses between requests or security contexts |
| accounting | in use, peak, grants, waits and refusals per subsystem; total in use and peak; `outstanding()` lists leases still held (leak detection at shutdown) |
| poisoning | the accounting lock survives a panic elsewhere (counters stay consistent) |

The service never reserves all system memory: the total is a configured number. A small budget (for the Raspberry Pi
profile, P10) is the same code with a smaller total.

## 2. Memory-pressure behaviour

Under saturation, requests wait in the budget instead of allocating: leased memory stays at or below the total
(tested with 8 threads over all subsystems: peak <= total, every quota respected, no lease leaked). Cancelling a
waiting request reserves nothing. What the budget covers: every buffer the service keeps (cache copies, staging,
codec and request buffers taken through VNX-RAM) and, when `BlockIo` is given the manager (`with_ram`; the P8
service is to use it), every running request's transient working memory (its length plus one extent for a read,
twice its length plus three extents for a write, leased from the `io` quota before the request runs). Not covered:
the reuse pools (at most 1/8 of the total, reported by `pooled_bytes`), the data of requests still waiting in the
`IoQueue` (at most the queue depth x 64 MiB; the P8 service bounds it by leasing at submission), and the process
baseline. P9 measures host RSS against these bounds.

## 3. VNX Cache

An extent cache between VNX-IO and the volume (`ExtentCache`, an `ExtentStore` like the volume itself, so `BlockIo`
runs over either).

| item | rule |
|---|---|
| read | a hit returns the cached copy after its CRC-32 check; a miss reads the verified extent from the volume and keeps a copy |
| bad copy | a clean copy failing its check is dropped and re-read; a dirty copy failing it was the only copy of acknowledged data: it is never written back, the extent becomes **lost** (reads and partial writes are CORRUPTION until a whole-extent write replaces it) and the next flush reports CORRUPTION after flushing the other extents |
| write-through (default) | the volume first; the copy is updated only when the volume accepted the write; a failed write drops the copy, so the next read returns the volume's content. Write state = the volume's (VERIFIED with readback) |
| write-back (opt-in) | a single-extent write stays in the cache as dirty and is acknowledged STAGED — volatile, not durable. `flush` writes dirty extents back in extent order, then flushes the volume; a failed writeback stops the flush and keeps that and every later extent dirty (retry possible) |
| multi-extent writes | in both modes, one volume transaction (`Volume::write_extents`): the new contents are built from the cached view (a dirty copy's data becomes durable with them), committed atomically, then cached as clean copies; on failure nothing changes on the volume, clean copies are dropped and dirty ones kept. Their atomicity therefore never depends on the cache or on a later flush |
| durability | write-back is by definition volatile: an acknowledgement of STAGED promises nothing across a crash, and the data is lost if the process stops before a flush. It is for hosts that flush (filesystems do). A host that needs every acknowledged write to survive a crash uses write-through (the default), whose acknowledgements are VERIFIED. A persistent write-back cache would have to journal the data on local storage first, which is what write-through already does on the volume; V14 does not add a second copy |
| memory | each copy is a `Buffer` from the `cache` quota; an existing copy is overwritten in place (so a dirty copy is never dropped before its replacement is in the cache); making room evicts least recently used extents, walking the LRU order one candidate at a time (O(log n) each, no copy of the order); a dirty victim is written back first, after its checksum check (a victim another request holds is skipped); if no room can be made the request bypasses the cache — a write-back write then goes through to the volume, and if that fails the error is returned and no cached data is dropped |
| consistency | every operation holds the extent's cache lock across the volume call and the cache update (a multi-extent write holds all its extents' locks, taken in ascending order); victims in other lock stripes are taken with `try_lock` only, so no thread waits for one extent while holding another; dropping a cache that still holds dirty extents reports them on stderr (never silent) |
| concurrency | the cache-wide lock guards only the index (which extent has which copy, LRU order, dirty flags) and is held for O(log n) work; copying an extent and computing its CRC-32 happen under that extent's own copy lock, outside it, so hits on different extents run in parallel |
| read-ahead (opt-in) | `with_readahead(n)`: a miss on extent `e` right after a miss on `e - 1` reads the next `n` extents into the cache as clean copies (skipping extents another request holds, cached or lost ones; stopping when no room can be made without evicting the request's own extents); errors end the read-ahead, never the request. Off by default: P9 measures whether the device's read patterns justify it (master directive §8, "prefetch when evidence supports it") |
| statistics | hits, misses, evictions, writebacks, writeback failures, checksum failures, bypasses, lost, prefetched, prefetch hits |

## 4. Tests (P3)

`cargo test` (vnxd): quotas and total, over-committed quotas, backpressure, deadline and cancellation, zeroed buffer
reuse, an 8-thread saturation stress; cache LRU within the quota, write-through failure leaving no stale copy,
checksum failure (clean refetch, dirty refusal), write-back STAGED then durable after flush, interrupted writeback
and retry, dirty eviction and the full-cache fallback, concurrent readers never seeing data older than a completed
write (both modes, constant eviction), and `BlockIo` through the cache against a byte model (both modes, then the
volume after flush). Added when the limits were removed: multi-extent writes through the cache (both modes: durable,
dirty data included, nothing changed on failure), sequential read-ahead with its hit counter, no read-ahead for a
strided pattern, read-ahead within the quota. Python (`tests/v14/test_device_molecular.py`): the molecular-memory interface
([V14_MOLECULAR_MEMORY.md](V14_MOLECULAR_MEMORY.md)).
