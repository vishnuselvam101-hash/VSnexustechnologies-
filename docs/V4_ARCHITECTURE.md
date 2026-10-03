# VNX-DNA V4 architecture

VNX-DNA V4 is a CPU-only software stack for the digital side of DNA data storage. It takes files and directories,
packs them into a verifiable archive, and turns the archive into constraint-screened DNA strands. It can pass those
strands through a configurable *simulated* storage and sequencing channel, and it reconstructs the exact archive from
the resulting reads. It reports a result only when the reconstruction passes integrity verification.

> **Scope.** Everything here is software and simulation. No strand has been synthesised or sequenced, and the
> channel is a configurable model, not a fit to any platform. Every recovery figure in this repository means
> "recovered from software-generated DNA through a simulated channel under the stated parameters".

Status labels used across the V4 docs: **IMPLEMENTED** (code + tests + measurements), **EXPERIMENTAL** (code +
tests + measurements, but behaviour or guarantees still under study; reached only via `vnx experimental` or
`experimental=True`), **SIMULATED** (applies to simulated data only), **PLANNED** (not implemented), **NOT
VALIDATED** (no physical evidence exists).

## 1. Relationship to V3

V3 (format 5, `vnx-dna`, packages `vnxdna.v2`/`vnxdna.v3`/top-level V1 modules) is unchanged. Its 601 tests still
run and pass. V4 is a separate package, `vnxdna.v4`, with its own formats (VNX4 container, frame version 4,
superblock) and its own CLI, `vnx`. V4 **imports** V3's proven primitives instead of copying or modifying them:

| V3 component reused unchanged | used by V4 for |
|---|---|
| `vnxdna.ecc.cauchy.CauchyErasureCode`, `vnxdna.v2.encoder.cauchy_parity` | default outer code (MDS) |
| `vnxdna.ecc.inner_rs._parity_matrix`, `vnxdna.ecc.rs_batch` | inner RS. `vnxdna.v4.rs_fast` runs the same algorithm with faster kernels; the V3 decoder stays the reference (`VNX_RS_REFERENCE=1`) and the two are tested bit-identical |
| `vnxdna.v2.crc` | vectorised CRC-32 |
| `vnxdna.container.compression.decompress` | bounded zstd decompression |
| `vnxdna.v2.strandio.StrandWriter` | atomic FASTA/FASTQ output |
| `vnxdna.errors` | V4 errors subclass V3 errors, so the exit-code contract is shared |

## 2. Data flow

```
WRITE PATH                                                           module (vnxdna.v4.*)
files / directories
  ↓ deterministic walk (UTF-8 byte order), path safety, symlinks skipped          archive.collect_entries
COMPRESSION     zstd per chunk, kept only if smaller                              archive.build_archive
ENCRYPTION      AES-256-GCM per chunk (HKDF keys; key file or scrypt passphrase)  crypto
CHUNKING        fixed-size chunks, content-addressed IDs, deduplication           archive / container
INTEGRITY       file SHA-256 → chunk ID + stored SHA-256 → Merkle root → manifest → trailer SHA-256
CONTAINER       .vnx: header │ body │ chunk table │ file table │ refs │ manifest │ trailer   container
  ↓
GROUPING        container bytes → groups of K symbols × P bytes                   encoder
OUTER CODE      Cauchy RS (K+M) per group  │  EXPERIMENTAL GF(2) fountain        codecs
ADDRESSING      tag(16) │ group(32) │ symbol(16) │ kind │ version, in-band     frame
FRAMING         variant │ header │ payload │ CRC-32 │ inner RS parity          frame
DNA MAPPING     2 bits/nt + periodic synchronisation markers                      frame
CONSTRAINTS     scrambler variant chosen until the strand satisfies the rules     frame + constraints
SUPERBLOCK      geometry, archive ID, container size + SHA-256 (4× redundant)    encoder.Superblock
  ↓
DNA OUTPUT      FASTA / FASTQ (headers informational only)

CHANNEL (SIMULATED)  dropout, coverage, duplication, substitutions, indels,       channel
                     homopolymer & GC effects, bursts, N, reverse complement

READ PATH
reads (FASTA / FASTQ / plain)                                                       reads
  ↓ validation (ACGTN, bounded lengths), optional quality → erasures
ORIENTATION     marker agreement forward vs reverse complement                    decoder._orientation
FAST PATH       exact-length reads: strip markers, CRC check, inner RS            frame.decode_frames
SYNCHRONIZATION marker-template banded DP; indels → erased segments               sync.TemplateAligner
INNER ECC       RS errors-and-erasures (2e + f ≤ r), then CRC                     codecs.InnerRS / rs_fast
SPILL           verified symbols + pending reads → bucket files (bounded RAM)     decoder.Spill
SUPERBLOCK      decode, choose archive (tag), check layout                        decoder._decode_superblock
DUPLICATES      strict majority of identical verified copies                      decoder.resolve_duplicates
CONSENSUS       soft vote over pending reads per address → erasures → RS → CRC    decoder.consensus_soft/_hard
OUTER ECC       per group: Cauchy RS (or fountain) erasure decoding               codecs
REASSEMBLY      container bytes written at g·K·P                                  decoder._pass2
VERIFICATION    container SHA-256 (superblock) + full structural/Merkle check     container.open_container
  ↓
SUCCESS → .vnx published → extract (each chunk ID + file SHA-256 verified)        archive.extract
PARTIAL → only individually verified files extracted; nothing else published      decoder._partial
FAILURE → structured error, nothing published
```

Every stage is a separate module behind a small interface. The outer code is chosen by name from a registry
(`codecs.make_outer`) and recorded in the superblock. The layout (P, r, marker period and length) is a parameter,
also recorded in the superblock. Constraints, the channel and decoding are configuration objects with schema
validation (`config.py`).

## 3. Key interfaces

| interface | methods | implementations |
|---|---|---|
| outer codec (`codecs.OuterCodec`) | `encode`, `decode`, `symbols_for`, `overhead`, `capabilities`, `configuration` (+ `bench.codec_compare`) | `CauchyRSCodec` (IMPLEMENTED), `LTFountainCodec` (EXPERIMENTAL; dense and robust-soliton) |
| inner code (`codecs.InnerRS`) | `parity`, `decode(codewords, erasures)` | V3 RS with fast kernels |
| channel (`channel.ChannelConfig`, `simulate_batch`, `simulate_file`) | pure function of (strands, config, seed) | one configurable model |
| synchronisation (`sync.TemplateAligner.project`) | reads → frame-coordinate bases + erasure mask | marker-template DP |
| soft information (`decoder.consensus_soft`) | reads → per-position base probabilities (L × 4) | add-½ vote posterior; thresholded into hard bases plus erasures today (§6) |
| constraints (`constraints.ConstraintConfig`, `violations_batch`, `diagnose`, `validate_file`) | sequences → per-rule violations | GC, windowed GC, homopolymer, tandem repeat, motifs, length, invalid base |
| read input (`reads.iter_reads`) | file → bounded batches | FASTA, FASTQ, plain (BAM: PLANNED, not supported) |
| physical adapters | — | PLANNED (§7) |

## 4. Architecture decisions

| # | decision | reason | evidence |
|---|---|---|---|
| AD4-1 | V4 is a new package and format; V3 untouched | V3 is the known-good baseline. A new format avoids silently changing V3 behaviour | 601/601 V3 tests at every V4 commit |
| AD4-2 | Multi-file container with a content-addressed chunk table and Merkle root | Directories, deduplication, per-chunk verification (`verify --chunk`) and random access without reading unrelated content | `tests/v4/test_container_v4.py` |
| AD4-3 | Body first, tables + manifest + trailer last | One streaming pass; memory independent of content size (the tables hold 84 B per unique chunk) | EXP-0012 |
| AD4-4 | One RS codeword per frame (address included) plus CRC-32, instead of a separately protected address | A separate address code adds 6–8 bytes per strand. With markers, address bytes inside an indel-hit segment become erasures, and the frame RS recovers them along with the payload. Unreadable addresses are counted (orphans) | engineering log 2026-10-03 |
| AD4-5 | In-strand synchronisation markers, with indels turned into **erasures** by a marker-template DP | RS cannot correct indels. A marker-anchored alignment bounds the damage of an indel to one segment, which costs the inner code 1 unit per byte instead of 2 | `docs/INDEL_ENGINE.md`, EXP-0008/0009 |
| AD4-6 | Superblock group (4× redundant) instead of metadata strands | A strand pool describes itself (geometry, archive ID, container SHA-256). The rest of the archive metadata lives in the container's own index section, itself protected by the outer code like any data | `encoder.Superblock` |
| AD4-7 | Group g ↔ container bytes `[g·K·P, (g+1)·K·P)` | Random access needs no DNA index file: `locate` computes groups and strand records directly | `vnx locate --dna-profile`, selective decode test |
| AD4-8 | CRC-first acceptance, then RS | Clean reads skip RS decoding (V3 did the same). The assurance is still CRC-32 plus the final container SHA-256 | `docs/PERFORMANCE.md` |
| AD4-9 | Channel seeded per fixed batch of 1024 strands | Output is independent of the worker count (determinism with parallelism) | `test_channel_deterministic_and_worker_independent` |
| AD4-10 | Fountain code kept EXPERIMENTAL | Not MDS, and its guarantees are probabilistic. Robust-soliton LT failed in the systematic setting, so only measured results are claimed | EXP-0007 |
| AD4-11 | Publish only after SHA-256 + structural verification; PARTIAL extracts only verified files | "Decode succeeded" must mean "verified", never "bytes were produced" | `test_partial_recovery_extracts_only_verified_files` |
| AD4-12 | Errors are classes with `stage`, `retryable` and V3 exit codes; exit 9 = PARTIAL | Tells the user what failed, where, why, and whether retrying with more reads can help | `errors.py`, `tests/v4/test_cli_v4.py` |

## 5. Determinism

The following are deterministic: entry order (UTF-8 byte order), chunk boundaries and IDs, unencrypted archive IDs,
container bytes (identical for every worker count), DNA encoding (strand files identical for every worker count and
task size), channel output (identical for every worker count), decode results (order-independent voting), data
generators and experiment seeds.

Intentionally nondeterministic: the salt and archive ID of **encrypted** archives (random by design), timestamps
in reports and environment records, and timings. `--preserve-metadata` stores mtimes and therefore makes archives
depend on file-system state.

## 6. Soft-information extension point

Today the inner decoder takes hard bytes plus an erasure mask. Soft information already enters at three points:
- FASTQ qualities below `--min-quality` become erasures;
- the consensus posterior (`decoder.consensus_soft`, an L × 4 probability matrix per strand) is thresholded into
  bases and erasures (`consensus_threshold`);
- marker-detected indel segments become erasures.

A soft-decision inner decoder (for example GMD decoding over the posterior, or an LDPC inner code) would consume the
same L × 4 posterior matrix. That is the only interface change needed. **PLANNED, not implemented.**

## 7. Future physical interface (PLANNED)

```
VNX archive → vnx encode (FASTA) → synthesis provider adapter → physical DNA → sequencing →
FASTQ/BAM source adapter → vnx decode → verified archive
```

The strand files are vendor-neutral FASTA/FASTQ. No provider API is implemented, because none is needed until a
physical experiment is planned. A provider adapter would map FASTA records to the provider's order format and attach
the primer/adapter sequences the provider requires. Primers are not part of the frame; adding them is a layout
change, recorded in the superblock. The decoder ignores headers and accepts any read order, multiplicity or
orientation, so a sequencing adapter only has to emit FASTQ.

## 8. Package layout

| module | lines | responsibility |
|---|---|---|
| `archive.py`, `container.py`, `merkle.py`, `crypto.py` | 542 + 455 + 105 + 156 | VNX4 container, archive engine, integrity, encryption |
| `codecs.py`, `rs_fast.py` | 361 + 183 | outer/inner codes, comparative interface, fast RS kernels |
| `frame.py`, `constraints.py`, `encoder.py` | 293 + 256 + 293 | frame v4, markers, constraint engine, superblock, strand writer |
| `channel.py` | 356 | simulated channel |
| `reads.py`, `sync.py`, `decoder.py` | 181 + 250 + 732 | read input, synchronisation, reconstruction pipeline |
| `config.py`, `errors.py`, `util.py`, `version.py`, `__init__.py` | 127 + 101 + 167 + 4 + 9 | configuration, error taxonomy, helpers |
| `bench.py`, `sweep.py`, `experiment.py`, `compare.py`, `datagen.py` | 353 + 175 + 163 + 212 + 82 | benchmarks, sweeps, experiments, fair comparisons, data generation |
| `cli.py` | 475 | `vnx` command |

6,031 lines of V4 source in total; 1,075 lines of V4 tests (122 tests).
