# V14 architecture

Phase 0 document (master directive §4). It defines the target architecture of the V14 storage platform before any
of it is built. Nothing in this file is implemented at 58171b8 unless it is marked EXISTS; every other component is
added by the phase named next to it ([V14_MASTER_PLAN.md](V14_MASTER_PLAN.md)). Language and process decisions
(D1–D5) are in [V14_LANGUAGE_AND_RUNTIME_STRATEGY.md](V14_LANGUAGE_AND_RUNTIME_STRATEGY.md); package and crate
dependencies are in [V14_DEPENDENCY_GRAPH.md](V14_DEPENDENCY_GRAPH.md).

No DNA is synthesised, stored or sequenced by anything in V14. The DNA media adapter is a simulator over the existing
codec and channel model; the electronic image backend is ordinary file storage and is never described as DNA.

## 1. Components

| component | role | language | phase | builds on (EXISTS) |
|---|---|---|---|---|
| contracts | block addresses, generation ids, write states, error classes, persisted record formats (CRC-32), media-adapter trait, `vnx.mgmt/1` and `vnx.codec-ipc/1` schemas | Rust + Python mirror | P1 | `core` errors and canonical JSON |
| device controller | host block interface: NBD server + `/dev/nbdN` attach (Stage A), ublk (Stage A'), userspace API (Stage B) | Rust | P8 | — |
| VNX-IO | LBA requests, split/coalesce, read-modify-write, bounded queues, ordering, barriers, flush, cancellation, timeouts | Rust | P2 | — |
| VNX Volume (block) | logical block map → (extent, generation); transactions, journal, recovery, reference tracking, GC | Rust | P2 | V13 `volume` (byte-stream volume, CRC-framed journal): its extent and journal rules are the reference |
| VNX-Secure boundary | authorisation of management and device operations, sealing of extents (AES-256-GCM via the Python layer), integrity records, audit events | Python (crypto) + Rust (checks) | P4 | `secure` gate, control plane, audit, integrity; `archive` encryption |
| VNX Cache | read-through, write staging, dirty tracking, write-through default, optional write-back, checksums | Rust | P3 | V13 volume hot-extent cache (behaviour reference) |
| VNX-RAM | memory budget, per-subsystem quotas, buffer pools, backpressure, accounting, leak detection | Rust | P3 | `secure/vnxram.py` defines the *security* contract of a future memory subsystem; V14's VNX-RAM is the electronic working-memory manager and implements that contract's authorise/verify/revoke for its pools |
| molecular-memory research interface | region/capability/health protocol + simulator + conformance suite; no hardware | Python | P3 | — |
| Load Balancer | priority queues, worker pools, fairness, per-backend concurrency limits | Rust | P5 | engine `pool.rs` claiming pattern |
| Fallback | retry classes, backend health, degraded mode, crash recovery orchestration, audit of recovery | Rust | P4 | volume journal replay |
| Media adapters | A DNA simulator (codec IPC), B electronic image backend, C fault injection, D physical interface spec | Rust (+ Python codec worker for A) | P7 | `pipeline` encode/decode, `simulation` channel |
| Management | config validation, capacity, stats, health, scrub, lifecycle over `vnx.mgmt/1` | Rust server, Python client/CLI | P1–P8 | `commands`, `sdk` |

## 2. Data flow

Write path (the order of security and cache steps follows §4 of the directive; metadata has its own path):

```
 host write (LBA, len, data)
   │ device controller: validate transfer, copy into a VNX-RAM request buffer           [RECEIVED]
   ▼
 VNX-IO: split by block, read-modify-write partial blocks, order against barriers
   ▼
 Volume: map blocks → extent; allocate a NEW generation for the extent (immutable generations)
   ▼
 Secure boundary: authorise the device session; build the extent payload; seal (AES-256-GCM) if the volume is sealed
   ▼
 Cache + VNX-RAM: stage the sealed extent; account bytes against the staging quota                [STAGED]
   ▼
 Load Balancer: queue a backend write job (priority: flush/barrier > foreground > background)
   ▼
 Media adapter: persist generation (image: file + fsync; sim: codec worker encodes to strands) → ack [BACKEND_COMMITTED]
   ▼
 Volume transaction: journal record "map extent → generation" (CRC-32, fsync); readback check     [VERIFIED]
   ▼
 completion to the host
```

Read path (reverse; nothing is returned before its integrity check):

```
 host read (LBA, len) → VNX-IO → Volume map lookup (extent, generation)
   → Cache hit? verify the cached checksum → copy out
   → miss: Load Balancer → media adapter read (sim: codec worker decodes the strands) → integrity check
     (SHA-256 of the generation; GCM tag when sealed) → cache fill → copy out
   → any failure: typed error to the host (EIO), never zeros or stale bytes
```

## 3. Control flow

```
 vnx CLI / SDK ── vnx.mgmt/1 (Unix socket, JSON lines, hello + version) ──► vnxd management thread
                                                                             │ config validate, create/attach/detach,
                                                                             │ capacity, stats, health, scrub, shutdown
                                                                             ▼
                                                     module handles (never their internal state)
 vnxd ── vnx.codec-ipc/1 (Unix socket) ──► Python codec workers (encode_extent / decode_extent on staged files)
```

## 4. Write states and transaction boundaries

| state | meaning | may the host be told the write is durable? |
|---|---|---|
| RECEIVED | the request is in a VNX-RAM buffer | no |
| STAGED | the extent bytes are in staging memory or the staging directory | no (volatile acknowledgement only, write-back mode) |
| BACKEND_COMMITTED | the backend acknowledged its documented commit (image backend: data file fsynced) | yes, within the backend's stated guarantee |
| VERIFIED | readback/integrity check of the generation passed and the mapping journal record is fsynced | yes |

One transaction = one extent remap: new generation written and committed, then one journal record switches the map.
Crash rules (recorded in `V14_FAILURE_AND_RECOVERY_MODEL.md`, P4):

* crash before the generation is committed → the old mapping stays; the orphan generation is found and removed by GC;
* crash after commit, before the journal record → as above (the record is the commit point of the remap);
* crash during the journal append → replay stops at the first record that fails its CRC (the V13 journal rule);
* crash after the journal record → the new mapping is live; the old generation becomes GC-eligible once nothing
  (mapping, recovery record, replica) references it.

Flush (`NBD_CMD_FLUSH`, FUA) completes only after every earlier write reached BACKEND_COMMITTED and its journal record
is fsynced. The default cache mode is write-through; write-back is opt-in and documented as such.

## 5. Memory ownership

* VNX-RAM owns all request, staging and codec buffers; each has a budget and a quota per subsystem
  (io, cache, staging, codec, metadata). An allocation beyond the quota blocks (backpressure) or fails with a typed
  error; it never grows unbounded.
* A request buffer is owned by its request until the completion is sent; cancellation marks the request and the
  holder releases the buffer when it observes the mark.
* Buffers are zeroed before reuse across security contexts (different devices or sessions).
* No pointer crosses a process boundary: the codec worker receives file paths in the staging directory owned by the
  service; the management client receives copies.

## 6. Failure propagation

```
 media adapter error ─► classified (retryable | permanent | corruption | unavailable)
   ─► Fallback: bounded retries with backoff for retryable; health score per backend; degraded mode
        (reads allowed, writes refused) when a backend is unavailable
   ─► Volume: transaction aborted, mapping unchanged
   ─► VNX-IO: request completed with a precise error; queue drained on shutdown
   ─► device controller: NBD error reply (EIO / ENOSPC / EROFS); the kernel sees a failed request, not wrong data
   ─► audit event (JSON line) + metrics counter
```

A read that cannot be verified is an error. Redundant-copy recovery is used only where a real, verified redundant
copy exists.

## 7. Security boundaries

| boundary | control |
|---|---|
| host kernel ↔ vnxd (block requests) | transfer-size and range validation; device attach requires root/CAP_SYS_ADMIN (documented) |
| CLI/SDK ↔ vnxd (management) | Unix socket with filesystem permissions + peer-credential check; operations authorised through the VNX-Secure policy |
| vnxd ↔ codec worker | private staging directory (0700), message schema validation, SHA-256 of every staged file checked by both sides |
| data at rest (backends) | sealed extents (AES-256-GCM, keys from the existing key handling); unsealed volumes are labelled as such |
| logs | append-only audit log (existing `secure.audit` chain) |

The threat model and key lifecycle extend `docs/VNX_SECURE_THREAT_MODEL.md` in `V14_SECURITY_MODEL.md` (P4).

## 8. Host interface

Stage A: the kernel NBD driver over a Unix-domain socket (`/dev/nbdN`), attached by vnxd through the documented
ioctls (D3). Stage A': ublk (`/dev/ublkbN`) with the same VNX-IO behind it. Stage B: the same operations through the
Python SDK/CLI, never called a block device. Stage C (USB Mass Storage/UASP vs NVMe external controller) is a design
document only (P11). Tests use only devices vnxd itself creates over disposable image files.

## 9. Physical-media boundary

The media-adapter trait is the only place a backend is reached. Adapter D (physical DNA) is an interface
specification with job, sample and quality-metric records, a simulator, and conformance tests; it sends no command to
any equipment. Results are reported by evidence category (A codec, B software device, C electronic backend,
D simulated channel, E real DNA operations, F physical device); V14 can reach A–D only.
