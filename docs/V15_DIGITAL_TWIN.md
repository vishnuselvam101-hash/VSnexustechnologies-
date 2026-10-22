# V15 digital twin

**Evidence: SIMULATED.** Everything this document describes is a software model. No DNA was synthesised, stored,
preserved or sequenced, and no channel, synthesis or sequencing measurement is claimed here. The twin is a fault
laboratory for the *software* device: it exercises the code paths of `vnxd` under injected faults and checks one
property of them. The property is narrow and it is the one that matters for a decoder: **the device never answers a
read with bytes other than the bytes of the last completed write of that extent.** A fault may cost a read (a typed
error) or a write (the old data survives the reopen), but it never changes what the device hands out.

| what | where |
|---|---|
| the twin | `src/vnxdna/native/rust/vnxd/src/twin.rs` |
| the declared campaign | `src/vnxdna/native/rust/vnxd/tests/digital_twin.rs` |
| the device under test | `src/vnxdna/native/rust/vnxd/src/volume.rs` |
| the media contract | `src/vnxdna/native/rust/vnxd/src/media.rs` |
| the fault model | `src/vnxdna/native/rust/vnxd/src/fault.rs` |
| the simulated medium | `src/vnxdna/native/rust/vnxd/src/dnasim.rs` |

The twin is a new module. It changes no production behaviour: it is built on the existing `MediaAdapter`,
`DnaSimMedia`, `FaultMedia`, `Volume` and `Sealer` interfaces, and it adds no dependency.

## 1. The six pools

A digital device is not one store but a set of stores with different failure behaviour. The twin models the six
roles the device uses (`PoolRole` in `src/twin.rs`):

| role | what it holds | how it is mirrored |
|---|---|---|
| `storage` | the extent generations of the volume | the pool's replicas |
| `ram` | the cached copies of the extents the volume has read | the pool's replicas |
| `io-journal` | the write-ahead copy of every generation | the pool's replicas |
| `metadata` | the map of the volume (its journal) | the pool's replicas |
| `security` | the sealed objects of a sealed volume | the pool's replicas |
| `bootstrap` | the binding marker of the volume | the pool's replicas |

Each pool is a `PoolSpec`: a role, a seed, the configuration of the simulated medium and a number of replicas
between one and three. The replica `r` of a pool runs the simulator with the seed `seed + r`, so every replica of every
pool has its own deterministic stream and the twin is reproducible from the specs alone. `Twin::new` takes exactly one
spec per role and refuses anything else with `INVALID_ARGUMENT`; a spec that asks for a replica count outside `1..=3`
is refused the same way.

The twin builds every pool as an N-way mirror over simulated media (`MirrorMedia`). A `put` reaches every replica. A
`get` returns the bytes of the first replica whose SHA-256 matches the receipt of the `put`; a replica that answers
with other bytes is recorded as damaged and the next replica is tried. When no replica answers with the expected
bytes the mirror answers with a typed error — `CORRUPTION` when a replica answered with other bytes, otherwise the
error of the last replica — and never with those bytes. A damaged replica is therefore visible in the report without
ever reaching the caller.

## 2. The three paths in front of the pools

Three of the roles are not plain pools but a path in front of a pool, because that is how the device uses them.

**The RAM pool is a verifying read-through cache** (`CacheMedia`). It holds a copy of the bytes the storage pool
served, and a copy whose SHA-256 does not match the digest recorded when it was cached is not served: the read falls
back to the storage pool. The cache counts the reads it answered, the reads that reached the storage pool and the
cached copies it rejected. A damaged RAM pool therefore changes no byte the volume reads; it only changes where the
read was answered from.

**The IO-journal pool is a write-ahead path** (`JournalMedia`). Every generation is written to the journal pool
first and read back, and only then written to the storage pool. A torn write-ahead copy is caught by the read-back
and aborts the write before the storage pool holds the bytes, so the volume's map record is never written and the old
data survives the reopen.

**The metadata pool carries a copy of the map.** The volume's journal is a file, so the twin publishes a copy of it
into the metadata pool under a reserved extent outside every device extent and outside the binding marker. Each
publication gets its own generation, so a copy published before a crash is never overwritten. Opening a volume
requires the metadata plane: the map must be reachable in the metadata pool and must name the same device as the
journal. A volume whose map cannot be confirmed is not opened and no byte of it is served.

## 3. Fault injection

Faults are injected per pool and per replica (`Twin::inject`): a `Rule` from `src/fault.rs` says which media operation
(`get`, `put`, `remove`, `list`, `sync`, `info`) is hit and which fault is used (`BitRot`, `TornPut`, `Unavailable`,
`Timeout`, `AckLost`, `WrongReceipt`, `ShortRead`, `Lost`, and the rest). A rule hits one replica only; the other
replicas of the same pool are untouched.

A whole replica can also be taken offline (`Twin::lose_pool`): every operation of that replica then fails with
`BACKEND_UNAVAILABLE` and changes nothing, and the replica can be brought back (`Twin::restore_pool`). This is how
the campaign models a lost device rather than a damaged one.

The twin's report (`Twin::report`) lists every pool, every replica, the counters of the simulated medium and the
faults that were injected. Every field of the report is labelled `SIMULATED`. The report is deterministic: the pools
are ordered, the seeds are fixed and no wall-clock time is read anywhere in it.

## 4. The declared campaign

The campaign is `tests/digital_twin.rs`. It is a **declared** campaign: the cases, their seeds and their pass
conditions are fixed in advance in the test file, and the file is the authority — the numbers in the test output
come from the test run, not from this document.

Every case runs with a fixed seed against a fixed instant of the deterministic clock, and every case states its
pass condition in terms of the invariant of §1: either the read is right or it is a typed error, never other bytes.

| # | case | fault | pass condition |
|---|---|---|---|
| 1 | bit rot in the storage pool | `BitRot` on a `get` of one replica | the read either returns the bytes of the last write or fails with `CORRUPTION`/`BACKEND_UNAVAILABLE`; a permanent rot is reported by the scrub as a failed extent |
| 2 | one metadata replica lost | the replica is offline | every extent still reads and the map is still readable from the replicas that are left |
| 3 | every metadata replica lost | all replicas are offline | the open fails with `NOT_FOUND`/`BACKEND_UNAVAILABLE` and the map is not readable; no byte of the volume is served |
| 4 | damage in the RAM pool | `BitRot` on a `get` of the RAM pool | the bytes the volume reads are unchanged; the read falls back to the storage pool and the damaged replica is reported |
| 5 | a torn write-ahead copy | `TornPut` on a `put` of the journal pool | the reopen gives the old data or the new data, and never a mix of the two |
| 6 | the security pool is gone | every security replica is offline | a sealed volume refuses to open with `UNAUTHORIZED`/`BACKEND_UNAVAILABLE`; a different key is refused with `UNAUTHORIZED`; the volume never opens unsealed |
| 7 | bit rot in one bootstrap replica | `BitRot` on a `get` of one replica | the volume opens and reads from the other replicas and the damaged replica is reported |
| 8 | a crash at every stage of the commit | the volume's fault hook fails at each stage of the commit in turn | the reopen gives the old data or the new data at every stage, and never other bytes |
| 9 | determinism of the campaign | the same seeds, the same call sequence | two runs give the same report |

The stages of case 8 are the stages the volume itself defines (`Stage` in `src/volume.rs`): after the put, before the
map record, at the torn map record, after the map record and after the remove.

## 5. Running the campaign

```
cargo test --offline --locked -j 4 --manifest-path src/vnxdna/native/rust/vnxd/Cargo.toml --test digital_twin
```

The campaign is deterministic: the same specs give the same run, the same faults and the same report on every host.
The test file leaves nothing behind: each case works in a directory of its own that is removed when the case ends.

## 6. What the twin is not

- It is not a model of a physical medium. The medium is the existing simulated medium of `src/dnasim.rs`; the twin
  adds the *pool structure* and the *fault placement* on top of it.
- It is not a proof. It is evidence at the `SIMULATED` rung for one property, under the faults the fault model can
  express. A fault outside `src/fault.rs` is outside the campaign.
- It does not exercise the physical DNA path and makes no claim about synthesis, preservation, retrieval or
  sequencing.
- It does not measure performance. The campaign checks correctness under faults, and its timings say nothing about
  throughput.
