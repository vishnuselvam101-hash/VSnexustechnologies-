# Changelog

## 2.0.0 — 2026-09-30

Streaming, scalable V2. Computational only: no wet-lab validation. V1 archives stay readable.

### Added
- **Archive format 5 / container file v2**: body first, then a canonical manifest, a binary chunk index (56 B per
  chunk) and a plaintext index (36 B per chunk, sealed when encrypted), and a trailer with a SHA-256 of the file.
  Written to `<output>.partial`, fsynced, renamed atomically.
- **Streaming store/restore/verify/extract** with bounded memory (≤ 2 × workers chunks in flight) and parallel
  compression/encryption/hashing. Per-chunk compression decision (zstd kept only if smaller).
- **Chunked AES-256-GCM** with the chunk count in the associated data (detects modified, reordered, duplicated,
  missing, truncated and spliced chunks). HKDF labels and AD carry `VNX-DNA/5` (downgrade protection).
- **Resumable store** (`--resume`): validated checkpoints; SIGKILL-tested; byte-identical to an uninterrupted run.
- **Frame format 5**: 32-bit stripe index and archive tag (V1 capped at 2²⁴ stripes). Vectorised CRC-32; linear
  scrambler screening (parity and 2bit mapping are XOR-linear); table-driven inner RS and outer Cauchy encoders.
  All byte-identical to the reference computations.
- **Chunk-parallel encoder** (process pool) writing FASTA or the new **packed VXS** strand file (2 bits per nt),
  plus a **DNA index** (`.vxidx`) for random access from DNA.
- **Two-pass decoder** with a disk spill and external bucket sort: memory independent of the pool size, any read
  order, any multiplicity. Geometry discovery falls back to inner-RS correction when no read is error-free.
  Quality-based erasures (`--quality-erasure-below`).
- **Sequencing simulator** (`sequence`, `simulate`): coverage models including log-normal abundance, synthesis and
  sequencing errors, duplication, truncation, N calls, junk and contamination reads, reverse complements, out-of-core
  shuffle, informative quality scores, FASTQ/FASTA/VXS output, event counts that actually happened.
- **Read processing**: `reads` (validation/filtering), `cluster` (address indexing + minimizer index), `consensus`
  (batched banded alignment, quality-weighted vote, `N` for ambiguity), single-read RS-assisted realignment for V2 frames.
- **Profiles**: compact, balanced, resilient, archival. **Tandem-repeat** constraint.
- **Random access** by byte range on containers and on DNA (via the DNA index).
- **`migrate`**: V1 → V2 with full verification before and after (optional key rotation).
- **Experiment engine** (`experiment run`, Monte Carlo, Wilson intervals, configuration/results JSON, CSV, summary)
  and research scripts that generate every table in the docs.
- **Benchmarks**: `benchmark generate` (reproducible 1 MB–10 GB test data), `benchmark scale` (the real CLI measured
  per stage: time, CPU, peak RAM of the process tree, swap, disk), `benchmark corruption`, `benchmark stages`.
- 1–10 GB acceptance runs, 1 GB corruption acceptance, and the V2 test suite (container, crypto, resume, DNA
  guarantees, channel, clustering, consensus, alignment, CLI, properties, fuzzing, bounded memory).
- Documentation: V2_ARCHITECTURE, V2_FORMAT, LARGE_FILES, STREAMING, SYNCHRONIZATION, CONSENSUS, EXPERIMENTS; ECC,
  CHANNEL_MODEL, RANDOM_ACCESS, BENCHMARKS, SECURITY, COMPATIBILITY, CLI and TESTING rewritten for V2.

### Changed
- The main CLI is V2 (writes format 5). The V1 CLI is unchanged and available as `vnx-dna v1 …`. Every reading
  command accepts V1 inputs and dispatches them to the unchanged V1 modules.
- `benchmark` is now a command group (`generate`, `scale`, `corruption`, `stages`, `v1`).

### Compatibility
- Reads archive formats 5 and 4 and the legacy V0.1 formats. Writes format 5 (format 4 via `vnx-dna v1`).
- The V1 test suite still runs; the V1 CLI tests now invoke `vnx-dna v1`.

## 1.0.0 — 2026-09-29

First stable research-grade release. Computational only: no wet-lab validation.

### Fixed (V0.1 defects; see docs/V0.1_BASELINE.md)
- **Erasure code was not MDS.** V0.1 claimed any 4 of 12 shards recoverable (8+4), but 10 of 495 four-loss patterns,
  including {4,5,7,11}, could not be recovered. It is replaced by a systematic Cauchy Reed–Solomon code with a proof
  and exhaustive verification.
- `vnx-dna simulate` recursed infinitely. The simulator is rewritten and reports the events that actually happened.
- A corrupt first duplicate hid a valid copy. Every copy is now validated, and conflicts resolve by majority or become
  an erasure.
- `verify` printed the *stored* hash as the "recovered" hash. It now recomputes the SHA-256 of the recovered bytes.
- Malformed manifests raised `KeyError`/`TypeError`. They now raise structured errors with stable exit codes; the
  CLI used to exit 2 for every failure.
- With encryption, the manifest leaked the file name, size and plaintext SHA-256, and it was unauthenticated. These
  fields are now sealed, and the manifest is HMAC-authenticated.
- Strand addresses lived only in FASTA headers. They are now inside the DNA.
- Version labels disagreed (1.0.0 / 0.1.0). There is now one source, `src/vnxdna/_version.py`.

### Added
- The `.vxdna` self-describing container (archive format 4), a strict canonical manifest, and `required_features`.
- AES-256-GCM per chunk with HKDF-SHA256 subkeys, HMAC-SHA256 manifest authentication, and wrong-key detection.
- Two-level coding: per-strand CRC-32 + inner RS, and an outer Cauchy RS K+M with shortening. Metadata strands make a
  FASTA pool a complete archive.
- 2bit / rotation3 / codebook8 mappings, a constraint layer (GC, windowed GC, homopolymers, motifs) and scrambler
  screening that fails loudly when constraints are unsatisfiable.
- A seeded channel simulator (dropout, coverage, substitutions, indels, bursts, reverse complement, shuffle) with an
  event log.
- Experimental RS-assisted indel realignment (`--experimental-indel-repair`).
- Random access by chunk or byte range (`extract`).
- CLI: `store/pack, encode, simulate, decode, restore, recover, verify, info, extract, pipeline, keygen, benchmark,
  version, legacy`.
- Explicit read-only V0.1 compatibility (`vnx-dna legacy`).
- Test suite: unit, integration, property (Hypothesis), fuzz/adversarial, CLI, the clean-room acceptance test, and a
  README-executes test. Also a benchmark suite, an experiment runner and an extended fuzz campaign.

### Removed
- The V0.1 HTTP API and its web and plotting dependencies (fastapi, uvicorn, pandas, matplotlib). The V0.1 code,
  tests, docs and results are preserved under `research/legacy/`.

## 0.1 (baseline) — tag `v0.1-baseline` (cf7d1f5)

The R&D prototype. See docs/V0.1_BASELINE.md for its forensic record.
