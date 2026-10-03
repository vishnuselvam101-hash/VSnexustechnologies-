# VNX-DNA 3 architecture

VNX-DNA is a CPU-only Python implementation of the **digital side** of DNA data storage. It turns a file of any
size into constraint-screened DNA strand sequences, can push them through a reproducible *simulated* synthesis and
sequencing channel, and recovers the exact original bytes, verified by SHA-256 at several independent layers.

> **Scope.** Everything here is software and simulation. No strand has been synthesised or sequenced; the channel
> is a configurable stress model, not a model fitted to a platform. "Recovery" always means recovery from
> software-generated DNA through a simulated channel. See [LIMITATIONS.md](LIMITATIONS.md).

V3 is an upgrade of the V2 codebase, not a rewrite: the V2 audit ([V3_AUDIT.md](V3_AUDIT.md)) found the format-5
design sound, and found 48 implementation defects and 13 documentation errors, which V3 fixes. The architectural additions are a vectorised ECC
engine behind an explicit interface, single-read burst resynchronisation, a burst-capable channel simulator with an
error-sweep engine, multi-archive pools, and hardening of every input path. Archives stay format 5, so V2 and V3
read each other's archives (one documented exception, [COMPATIBILITY.md](COMPATIBILITY.md)).

## Pipeline

```
REAL FILE ───────────────────────────────────────────────────────────── vnx-dna store     (vnxdna.v2.archive)
  │ bounded reads, one chunk per worker; resumable (HMAC-bound checkpoints)
  ▼
COMPRESSION        zstd (or zlib) per chunk, kept only if smaller
ENCRYPTION         AES-256-GCM per chunk (nonce = epoch‖domain‖index, AD binds archive, index, count); HKDF keys
CONTAINER          .vxdna v2: header │ stored chunks │ manifest │ chunk index │ plaintext index │ trailer
  │                                                                    vnx-dna encode    (vnxdna.v2.encoder)
  ▼
OUTER ECC          each stored chunk → ECC groups of K data shards; Cauchy RS adds M parity shards (MDS)
STRAND FRAME       address (archive tag, stripe, shard) + payload + CRC-32 + inner RS parity
DNA MAPPING        2bit / rotation3 / codebook8; scrambler variants until GC / homopolymer / repeat / motif rules hold
STRANDS            FASTA or packed 2-bit VXS, plus a DNA index (.vxidx) for random access
  │                                                                    vnx-dna sequence / simulate / simulate-errors
  ▼
SIMULATED CHANNEL  dropout, coverage, synthesis + sequencing substitutions/indels, bursts (V3), truncation, N,
                   duplicates, reverse complements, junk, contamination, reordering; seeded (vnxdna.v2.sequencing)
  │                                                                    vnx-dna cluster / consensus (coverage > 1)
  ▼
READ PROCESSING    address-indexed clustering, banded-alignment consensus with honest Ns
  │                                                                    vnx-dna decode / recover / restore
  ▼
DECODER pass 1     per read: CRC → reverse complement → vectorised inner RS (V3) → single-read indel or burst
                   resynchronisation (V3, opt-in) → spill validated shards to disk
DECODER pass 2     stripe-ordered records → duplicate vote → outer erasure decoding per group → chunk SHA-256
VERIFICATION       manifest digest/HMAC → body length (V3) → chunk SHA-256 → AES-GCM → plaintext SHA-256 →
                   whole-object SHA-256 → atomic, no-clobber publish (V3)
```

## Components

The brief for V3 named fifteen components. They map onto the code as follows; where a component already existed
in V2 and was sound, V3 fixed its defects rather than re-implementing it.

| # | component | module(s) | V3 change |
|---|---|---|---|
| 1 | Data engine | `vnxdna.v2.archive` (streaming reads, ordered worker pool), `vnxdna.v2.scale` (test data) | non-UTF-8 names, disk-full handling, chunk-count limit checked up front |
| 2 | Compression engine | `vnxdna.v2.archive.seal_chunk` / `open_chunk` (zstd, zlib, per-chunk decision) | `--compression none` works without `--level` |
| 3 | Encryption / authentication | `vnxdna.v2.crypto` (HKDF, AES-256-GCM, HMAC), resume logic in `vnxdna.v2.archive` | two nonce-reuse paths closed; checkpoints HMAC-bound; `final-seal-epoch-v3` |
| 4 | Chunk engine | `vnxdna.v2.archive` (fixed plaintext chunks), `vnxdna.v2.manifest` (binary chunk index) | body length must equal the authenticated `stored_size` |
| 5 | DNA encoding engine | `vnxdna.v2.frame`, `vnxdna.dna.mapping`, `vnxdna.v2.constraints`, `vnxdna.v2.encoder` | unchanged output (byte-identical data strands) |
| 6 | Addressing engine | frame header (archive tag, 32-bit stripe, shard), `vnxdna.v2.encoder.write_dna_index` | pools with several archives: automatic choice or `--archive-tag` |
| 7 | Metadata engine | `vnxdna.v2.manifest` (canonical JSON, strict schema, features), metadata strands (Cauchy 8+8) | forged metadata lengths bounded by the strands present; metadata repairs reported |
| 8 | ECC engine | **`vnxdna.ecc.engine`** (interface + registry), `vnxdna.ecc.cauchy`, **`vnxdna.ecc.rs_batch`**, `vnxdna.ecc.inner_rs` | new: vectorised bounded-distance RS decoder; codes chosen by the names the manifest declares |
| 9 | Archive / container engine | `vnxdna.v2.container` (writer, reader, trailer) | descriptor race fixed; body SHA-256 verified; no-clobber publish |
| 10 | Index engine | chunk index + plaintext index (`vnxdna.v2.manifest`), DNA index (`vnxdna.v2.encoder`) | trailer-addressable chunk limit enforced |
| 11 | Random-access engine | `vnxdna.v2.archive.extract_range`, `vnxdna.v2.api._extract_reads` | explicit missing `--dna-index` is an error |
| 12 | Error / channel simulator | `vnxdna.v2.sequencing`, **`vnxdna.v3.sweep`** | new: burst errors; `simulate-errors`; format-independent shuffling; coverage-bounded batches |
| 13 | Verification engine | `vnxdna.v2.archive.verify_container`, `vnxdna.v2.api.verify` | stored-body SHA-256 check; truncated bodies reported, not raised; exit 4 without a key |
| 14 | Benchmark engine | `vnxdna.v2.scale`, `vnxdna.v2.stages`, `vnxdna.v2.experiment`, `research/v3/` | V2-vs-V3 harness, noisy-read and indel benchmarks, error sweeps |
| 15 | CLI / API | `vnxdna.cli` (Typer, thin), `vnxdna.v2.api` | `simulate-errors`, `--burst-repair`, `--archive-tag`; reports never clobber; clean exits on every bad path |

The package name `vnxdna.v2` is kept for the format-5 implementation so the VNX-DNA 2 Python API keeps working.
New V3-only modules live in `vnxdna.ecc.engine`, `vnxdna.ecc.rs_batch` and `vnxdna.v3`. The V1 modules
(`vnxdna.api`, `vnxdna.container`, `vnxdna.storage`, …) are unchanged and read format-4 archives
([V1_ARCHITECTURE.md](V1_ARCHITECTURE.md)).

## Architecture decisions added in V3

| # | decision | reason |
|---|---|---|
| AD3-1 | **Keep archive format 5** and frame format 5 | The audit found the format sound. A new format would break V2 interoperability for no gain; every V3 capability fits inside format 5. |
| AD3-2 | **One vectorised RS decoder** (NumPy Berlekamp–Massey / Chien / Forney over a batch) for every damaged-read path (decoder, clustering, consensus, indel and burst repair) | The per-read `reedsolo` call was the dominant decode cost on noisy reads. The batch decoder is also strictly bounded-distance: it never returns a codeword outside `2e + f ≤ r` (the old path sometimes did; the CRC caught it). |
| AD3-3 | **ECC interface with a registry keyed by manifest names** | A decoder must apply exactly the code an archive declares. New codes need a name, a registration and a required feature, so older readers refuse them cleanly. Fountain codes are left as a research item because nothing untested is shipped. |
| AD3-4 | **Burst resynchronisation as F hypotheses** (one per start byte, the burst's bytes as erasures) | A contiguous run of L lost or extra bases would cost O(F^L) independent-indel hypotheses; as a burst it costs F, for any L the inner code can absorb (`⌈(L + 3)/4⌉ ≤ r` with the 2bit mapping). |
| AD3-5 | **Optional feature `final-seal-epoch-v3`** instead of a new format | Only resumed encrypted stores need the fix for the finalisation nonce. Declaring it as a required feature keeps every other archive byte-compatible and makes V2 refuse the affected ones cleanly instead of failing authentication. |
| AD3-6 | **Checkpoints authenticated with the archive MAC key** | The AEAD epoch in the checkpoint decides nonce freshness, so it must not be forgeable by anyone who can write to the output directory. |
| AD3-7 | **Error sweeps reuse the experiment trial function** | One code path for Monte Carlo trials: the sweep adds a grid, not a second simulator. |

## Where each guarantee comes from

| property | mechanism | tested in |
|---|---|---|
| any M strands of an ECC group may be lost | Cauchy MDS outer code | `tests/v2/test_dna_v2.py`, `tests/v3/test_v3_features.py` |
| substitutions/erasures inside a strand | inner RS, `2e + f ≤ r`, strict bounded-distance decoding | `tests/v3/test_ecc_decoder_v3.py` |
| no corrupt strand accepted | CRC-32 after any correction (≈ 2⁻³² per corrupt read) | fuzz tests, `test_correct_frames_accepts_only_crc_verified_corrections` |
| one indel per read + ⌊(r−1)/2⌋ byte errors (coverage 1) | single-read realignment | `tests/v3/test_ecc_decoder_v3.py` |
| one burst of L lost/extra bases per read, `⌈(L + b − 1)/b⌉ + 2e ≤ r` (coverage 1) | burst resynchronisation (`--burst-repair`) | `tests/v3/test_v3_features.py` |
| indels at coverage > 1 | clustering + consensus alignment | `tests/v2/test_channel_cluster_consensus.py`, [ERROR_MODEL.md](ERROR_MODEL.md) |
| no wrong output ever published | chunk SHA-256 → AES-GCM → plaintext SHA-256 → object SHA-256 → atomic publish | `tests/v2/test_container_v2.py`, `tests/v3/test_container_security_v3.py`, sweeps (0 undetected) |
| bounded memory | one chunk per worker, disk spill, bounded line reads (V3) | `tests/v2/test_streaming_scale_v2.py`, `tests/v3/test_ecc_decoder_v3.py`, [LARGE_FILES.md](LARGE_FILES.md) |
| no (key, nonce) reuse | per-archive salt, per-resume epoch persisted before sealing, HMAC-bound checkpoint | `tests/v3/test_container_security_v3.py` |

Details: [STORAGE_FORMAT.md](STORAGE_FORMAT.md), [ENCODING.md](ENCODING.md), [ECC.md](ECC.md),
[RANDOM_ACCESS.md](RANDOM_ACCESS.md), [ERROR_MODEL.md](ERROR_MODEL.md), [SECURITY.md](SECURITY.md),
[BENCHMARKS.md](BENCHMARKS.md), [REPRODUCIBILITY.md](REPRODUCIBILITY.md).

## Engineering environment (ops layer)

Development tooling — the LAYA decision layer, the agent harness, the resource governor, model routing, queues,
benchmarks, experiments and release gates — lives in `ops/vnxops` with its policy in `config/laya/`. It is described
in [ENVIRONMENT.md](ENVIRONMENT.md). The dependency is one-way: `ops/vnxops` drives the library through its CLI and
API, and `src/vnxdna` never imports `ops/vnxops`, so the storage library and its formats do not depend on the
environment.
