# VNX-DNA V2 architecture

VNX-DNA V2 is a **streaming, bounded-memory** software implementation of the digital side of DNA data storage. It
takes a real file of any size (tested up to 10 GB), turns it into a structured DNA strand representation, can pass
that representation through a reproducible *simulated* storage and sequencing channel, and recovers the exact
original bytes, verified by SHA-256 at several independent layers.

> **Scope.** Everything is software. No strand has been synthesised or sequenced. The channel is a simulation, not a
> model fitted to a platform. "Recovery" below always means recovery from software-generated DNA through a simulated
> channel ([CHANNEL_MODEL.md](CHANNEL_MODEL.md)).

## The invariant, stage by stage

```
REAL FILE ──────────────────────────────────────────────────────── vnx-dna store            (vnxdna.v2.archive)
  │ bounded reads (1 chunk per worker)
  ▼
COMPRESSION        zstd per chunk, kept only if it makes the chunk smaller (per-chunk codec flag)
AUTHENTICATED ENC. AES-256-GCM per chunk; nonce = domain‖index; AD binds archive, index and chunk count
CONTAINER          .vxdna v2: header │ stored chunks … │ manifest │ chunk index │ plaintext index │ trailer
CHUNKING           fixed plaintext chunk size (64 KiB … 64 MiB), independent chunks → random access, damage isolation
  │                                                          vnx-dna encode            (vnxdna.v2.encoder)
  ▼
REDUNDANCY         each stored chunk → ECC groups (stripes) of K data shards of P bytes
ERROR CORRECTION   outer Cauchy RS (K+M) per group ─ inner RS (r bytes) + CRC-32 per strand
DNA ENCODING       frame format 5 → 2-bit (or rotation3 / codebook8) mapping
SEQUENCE CONSTR.   GC, homopolymer, tandem repeats, motifs; scrambler variants until satisfied, else fail
DNA STRANDS        FASTA or packed VXS, written incrementally; DNA index (.vxidx) for random access
  │                                                          vnx-dna simulate / sequence (vnxdna.v2.sequencing)
  ▼
SIMULATED CHANNEL  dropout, abundance, synthesis + sequencing errors, duplicates, truncation, N, junk, contamination
RAW READS          FASTQ with quality scores (or FASTA / VXS)
  │                                                          vnx-dna reads / cluster / consensus
  ▼
READ FILTERING     validation, length/quality filters                              (vnxdna.v2.api.reads_filter)
CLUSTERING         address indexing (verified CRC, tentative header), minimizer index for orphans (vnxdna.v2.cluster)
CONSENSUS          banded alignment to a draft + quality-weighted vote; ambiguity → N (vnxdna.v2.consensus, .align)
SYNCHRONIZATION    multi-read alignment (consensus) and single-read RS-assisted realignment   (vnxdna.v2.sync)
  │                                                          vnx-dna decode / recover  (vnxdna.v2.decoder)
  ▼
ECC RECOVERY       pass 1: per-read CRC / RC / inner RS (N = erasure) → spill file
                   pass 2: stripe-ordered records → duplicate vote → outer erasure decoding per group
CONTAINER RECONST. byte-identical .vxdna (manifest and indexes rebuilt from metadata strands)
AUTHENTICATION     manifest HMAC (encrypted) / digest; chunk SHA-256; AES-GCM tag
DECOMPRESSION      bounded by the authenticated plaintext size
EXACT ORIGINAL     whole-object SHA-256 recomputed while streaming; output renamed into place only if it matches
```

Each arrow is an executable CLI command, so the whole chain can be run step by step
([README](../README.md#complete-dna-workflow)) or with `vnx-dna pipeline`.

## Package layout (V2)

| module | responsibility |
|---|---|
| `vnxdna.cli` | V2 Typer CLI (thin; no business logic). `vnx-dna v1 …` mounts the unchanged V1 CLI (`vnxdna.cli_v1`). |
| `vnxdna.v2.api` | Public API; detects V1/V2 inputs and dispatches V1 to the unchanged `vnxdna.api`. |
| `vnxdna.v2.profiles` | `StoreOptionsV2`, the storage profiles (compact / balanced / resilient / archival). |
| `vnxdna.v2.manifest` | Format-5 manifest schema, binary chunk index and plaintext index, validation. |
| `vnxdna.v2.crypto` | HKDF-SHA256 key separation, chunked AES-256-GCM, HMAC-SHA256 (VNX-DNA/5 labels). |
| `vnxdna.v2.container` | `.vxdna` container file version 2: streaming writer (partial file, fsync, atomic rename), reader with `pread`. |
| `vnxdna.v2.archive` | Streaming store (thread pool, resumable checkpoints), authenticated open, restore, extract, verify. |
| `vnxdna.v2.frame` | Strand frame format 5, vectorised CRC-32 (`vnxdna.v2.crc`), linear screening, inner RS table encoder. |
| `vnxdna.v2.constraints` | V1 constraint rules plus the tandem-repeat limit; vectorised checks. |
| `vnxdna.v2.encoder` | Chunk-parallel container → strands (process pool), metadata strands, DNA index. |
| `vnxdna.v2.strandio` | Streaming FASTA / FASTQ / plain / VXS readers and atomic writers. |
| `vnxdna.v2.decoder` | Geometry discovery, parallel read scan, disk spill, external bucket sort, per-chunk outer decoding. |
| `vnxdna.v2.sequencing` | Simulated storage and sequencing channel (streaming, out-of-core shuffle). |
| `vnxdna.v2.cluster`, `.consensus`, `.align`, `.sync` | Read processing: clustering, consensus, batched banded alignment, single-read realignment. |
| `vnxdna.v2.experiment` | Reproducible experiments and Monte Carlo trials. |
| `vnxdna.v2.scale`, `.stages` | Test-data generator, process-tree resource measurement, scalability, corruption and stage benchmarks. |
| `vnxdna.*` (top level) | V1 (format 4) modules, unchanged; used to read V1 archives and by `vnx-dna v1`. |

## Architecture decisions (V2)

| # | decision | reason |
|---|---|---|
| AD2-1 | **Body first, index and manifest in a footer** (container v2) | The manifest depends on every chunk (sizes, hashes). Writing it last means one pass, no temporary copy of the body, and memory independent of the file size. |
| AD2-2 | **Binary chunk index** (56 B/chunk) + **plaintext index** (36 B/chunk), SHA-256 of both inside the sealed manifest | A JSON list of 160,000 chunks would make the manifest multi-megabyte and slow to validate. Fixed-width tables are validated vectorised and still authenticated through the manifest HMAC. |
| AD2-3 | **Chunked AEAD with the chunk count in the associated data** | Established streaming construction: every chunk is independently authenticated, bound to its position and to the total count, so reordering, duplication, truncation and splicing all fail. |
| AD2-4 | **Per-chunk compression decision** | Compression can expand incompressible data. Each chunk records its codec; raw chunks cost nothing extra. |
| AD2-5 | **Frame format 5: 32-bit stripe index and archive tag** | V1's 24-bit stripe index capped archives at about 42 GB of stored data at the default geometry. Two extra bytes per strand lift that to about 11 TB. |
| AD2-6 | **Vectorised CRC and linear screening** | Per-strand Python work does not scale to 10⁸–10⁹ strands. CRC runs column-wise over batches. RS parity and the 2bit mapping are linear, so each scrambler variant is one XOR with a constant mask. The output is byte-identical to the direct method (tests). |
| AD2-7 | **Two-pass decoder with a disk spill and external bucket sort** | Reads can arrive in any order and any multiplicity. Holding them all in memory does not scale; spilling validated shards (10 + P bytes each) and sorting by ECC group does. Ordered input skips the sort. |
| AD2-8 | **Packed VXS strand file** | FASTA spends 8 bits per nucleotide. For a 10 GB input that is ~45 GB of text; VXS stores 2 bits per nucleotide. |
| AD2-9 | **Address-indexed clustering** | Reads carry their address inside the DNA, so most reads cluster in O(1) without any pairwise comparison. Only unaddressable reads use a minimizer index. |
| AD2-10 | **Consensus exposes ambiguity as N; decoders treat N as an erasure** | An erasure costs the inner RS code half as much as an unknown error, and a guess would cost more. |
| AD2-11 | **Every stage is a separate, atomic, verifiable CLI command** | Crash safety, restartability, and the ability to inspect and verify every intermediate file. |
| AD2-12 | **V1 code untouched; V2 dispatches format-4 inputs to it** | V1 archives stay readable with the exact code that wrote them; `migrate` converts with verification on both sides. |

## Where each guarantee comes from

| property | mechanism | tested in |
|---|---|---|
| any M strands of an ECC group may be lost | Cauchy MDS outer code (shortened last group per chunk) | `tests/v2/test_dna_v2.py` (exactly M per group; M + 1 fails), V1 exhaustive MDS tests |
| substitutions inside a strand | inner RS, `2e + f ≤ r`; N and low-quality bases as erasures | `test_frame_crc_constraints.py`, `test_dna_v2.py` |
| indels | consensus alignment (coverage > 1); single-read realignment (opt-in) | `test_channel_cluster_consensus.py`, experiments |
| no corrupt strand accepted | CRC-32 re-check after any correction (≈ 2⁻³² per corrupt read) | fuzz tests |
| no wrong output ever published | stored SHA-256 → AES-GCM → plaintext SHA-256 → streaming object SHA-256 → atomic rename | `test_container_v2.py`, `test_properties_adversarial_v2.py` |
| bounded memory | one chunk per worker, bounded in-flight window, disk spill | `test_streaming_scale_v2.py`, [LARGE_FILES.md](LARGE_FILES.md) |
| crash safety | `.partial` files, fsync, `os.replace`; validated checkpoints | `test_container_v2.py` (SIGKILL + resume) |
| determinism | content-derived archive ID (unencrypted), seeded channel, order-independent voting | property and CLI tests |

See [V2_FORMAT.md](V2_FORMAT.md) for byte layouts, [STREAMING.md](STREAMING.md) and [LARGE_FILES.md](LARGE_FILES.md)
for the memory model and measurements, [ECC.md](ECC.md) for the coding layers, [SYNCHRONIZATION.md](SYNCHRONIZATION.md)
and [CONSENSUS.md](CONSENSUS.md) for read processing.
