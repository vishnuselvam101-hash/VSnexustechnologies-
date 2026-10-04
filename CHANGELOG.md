# Changelog

## Unreleased — V6 Phase 1 (outer-code resilience)

Opt-in; without the new options encoding is byte-identical to 5.0.0. All results are SIMULATED. Details:
docs/V6_PHASE1_REPORT.md, format: docs/V6_OUTER_CODE.md.

- **Superblock version 2** with stripes of row groups plus column-parity groups (a product of two Cauchy RS codes),
  iterative row/column erasure decoding in pass 2, and per-archive strand-loss accounting (`report["outer_v6"]`).
- **Interleaved strand order** (position-major per stripe, superblock strands spread over the file) for burst loss.
- **Adaptive outer plan** (`outer_plan="adaptive"`, `--outer-plan adaptive`, `--redundancy-budget`) choosing row
  length, stripe depth and column parity under a redundancy budget from an analytic dropout bound.
- New `vnx encode` flags `--stripe-depth`, `--column-parity`, `--strand-order`, `--outer-plan`, `--redundancy-budget`
  and matching `dna.*` configuration keys.

## 5.0.0 — 2026-10-04

VNX-DNA V5: adaptive and probabilistic decoding on top of the unchanged V4 format. All results are SIMULATED
(computational/software validation); nothing has been synthesised or sequenced. Details: docs/V5_COMPLETION_REPORT.md.

- **Phase 1 — baseline:** V4 frozen and profiled with provenance (benchmarks/v5/); golden projection hashes for the
  aligner; first bottleneck identified (marker-template alignment, 58 % of single-worker noisy decode).
- **Phase 2 — native alignment kernel:** C kernel via ctypes (`src/vnxdna/v5/native/align.c`, optional setuptools
  extension), bit-exact with the V4 NumPy reference (10/10 golden cases, 0 mismatches in 700,000 fuzzed reads,
  ASan/UBSan clean); backend auto-selection with `VNXDNA_ALIGN_BACKEND`. Aligner 9.6–11.3× faster.
- **Phase 3 — smart indel recovery:** opt-in `DecodeOptions(indel_recovery="smart")` / `--indel-recovery smart`;
  bounded local search and consensus realignment, fail-closed. Median erased nt per true indel 24 → 4.
- **Phase 4 — soft-decision inner decoding:** opt-in `soft_decoding=erasure|chase|auto` / `--soft-decoding`, with
  `--min-quality`; GMD/Chase candidates checked by the unchanged RS + CRC verifier. 0 false acceptances, 0 false SUCCESS.
- **Gate A/B:** Phase 4 results reproduced from the clean commit; deferred per-read recovery
  (`recovery_schedule="deferred"`, default; `eager` available): verified archives/hour 155 → 581 (V5-hard).
- The package version is now 5.0.0, so new archives record encoder version 5.0.0 and `vnx version` reports 5.0.0.
  The VNX4 container format (4.0) and strand frame version (4) are unchanged.

## 4.0.0 — 2026-10-03

VNX-DNA V4: a new format and package (`vnxdna.v4`, CLI `vnx`) on top of the unchanged V3 (format 5, `vnx-dna`).
All results are SIMULATED; nothing has been synthesised or sequenced. Details: docs/V4_COMPLETION_REPORT.md.

- **VNX4 archives:** multi-file/directory archives with deterministic order, content-addressed chunks and
  deduplication, RFC 6962 Merkle tree (`vnx verify --chunk`), AES-256-GCM with key files or scrypt passphrases,
  sealed file tables, random access (`vnx locate`, `vnx extract --file`), verify-before-publish extraction.
- **Strand frame v4** with in-strand synchronisation markers. A marker-template DP turns indels into erasures for
  the inner RS code. A superblock makes strand pools self-describing.
- **Decoder:** CRC-first fast path, orientation detection, sync path, consensus with soft posteriors, address
  snapping, duplicate resolution, outer decoding, a fail-closed publish rule, PARTIAL recovery of individually verified
  files (exit 9), selective decoding of single files from reads.
- **Channel simulator:** substitutions, insertions, deletions, dropout, fixed/Poisson/negative-binomial coverage,
  duplication, homopolymer- and GC-dependent effects, bursts, N calls, reverse complements, qualities. Seeded and
  worker-count independent.
- **Codecs:** comparative outer-code interface. Cauchy RS (V3 code, default) and an EXPERIMENTAL GF(2) fountain
  code. The inner RS gets fast table-gather kernels (bit-identical to V3, 2.5–5.2× faster).
- **Constraint engine** with JSON diagnostics (`vnx validate`), screening by scrambler variants.
- **Benchmarks, error sweeps, fair V3-vs-V4 comparison, 16 reproducible experiment directories**, generated results
  tables (docs/V4_RESULTS.md).
- **Security:** decompression bombs bounded (zstd `max_output_size` is not a bound when a frame declares its size),
  path/symlink safety, bounded parsers, fuzz tests for archives, manifests, frames, read files and configurations.
- V3 code, tests and formats are unchanged. The package version is now 4.0.0, so new V3-format archives record
  encoder version 4.0.0.

## 3.0.0 — 2026-10-01

### Release review (2026-10-01)
A final pre-release review ran the full suite, an independent end-to-end and corruption harness (42 round trips of
text, binary, random, empty, chunk-boundary and incompressible files; 300 container bit flips; truncations; exact-M
and M+1 strand losses; coverage-1 error channels), a 4 GB encrypted run, and an adversarial review of data-loss,
temporary-file and signal handling. Every finding below was reproduced, fixed, and has a regression test
(`tests/v3/test_release_review_v3.py`, `tests/v3/test_cli_v3.py`) that fails on the previous commit.
- **Data loss:** `pipeline --work-dir` overwrote existing intermediate-named files (e.g. the user's
  `photo.jpg.vxdna`) without `--force`, and `--cleanup` then deleted them. Existing intermediates are now refused
  unless `--force`/`--resume`, and cleanup deletes only files the run wrote.
- **Data loss:** `--report` could replace the key file (`store … -k key.txt --report key.txt --force`), making the new
  archive unrecoverable, or the DNA index. An output may now never be an input, not even with `--force` (this also
  stops `extract a.vxdna -o a.vxdna --force`, `store f -o f --force` and `restore a -o a --force`).
- **Data loss:** an input named `<output>.partial` was deleted by `store` and truncated by `decode`.
- **Signals:** SIGTERM/SIGHUP left partial files, temporary directories and orphaned worker processes (7 per
  command, ~90 MB each). They now clean up like Ctrl-C (exit 130), and workers exit when their parent dies. An
  interrupt no longer waits for the worker pool: a worker killed while writing its result could leave the pool's
  shutdown waiting forever (found while validating the fix: `recover` hung after SIGTERM in about 1 of 8 runs).
  Workers ignore Ctrl-C (each printed a traceback) and an inherited pending SIGTERM (a worker forked at that moment
  broke the pool: exit 70), and an interrupt during start-up ends quietly with exit 130.
  Checked with 300 randomly timed SIGTERM/SIGINT runs: every one exited 130 with no partial files, no temporary
  directories and no live workers.
- **Temporary files:** reports, DNA indexes, cluster files, decoded containers and checkpoints used fixed
  `.<name>.partial` names opened through symlinks; they now use private `mkstemp` files.
- `encode` of an empty unencrypted archive wrote its outputs and then exited 70 (summary formatting of `None`).
- `keygen` wrote through symlinks and truncated an existing key when the write failed; it is now atomic.
- `encode --no-index --force` left a stale DNA index that broke `extract`; it is removed.
- `extract` silently ignored conflicting selections (`--chunk 0 --length 5`, `--length` with `--end`); now exit 3.
- A missing input to `store`, `pipeline`, `simulate-errors` and `experiment run` exited 2 with a usage box instead of
  the documented exit 3.
- Key files must be regular files of at most 4 KiB (`-k /dev/zero` read without limit); loose permissions warn.
- `reads` printed Python reprs (`[{'length': …}]`, `mean None`); `pipeline --help` lost its optional stages to markup.
- Packaging: SPDX `license = "MIT"` (the table form stops building with setuptools releases after 2027-02-18), the
  wheel ships `LICENSE`, a `.dockerignore`, and the Docker example runs as the calling user.

Audit of V2 and an architectural upgrade on the same format. Computational only: no wet-lab validation. Archive
format 5 is kept: V2 archives are read, and V2 reads V3 archives, with one exception (see Compatibility). The full
report is [docs/V3_AUDIT.md](docs/V3_AUDIT.md).

### Security fixes
- **AES-GCM nonce reuse on resumed stores (two paths).**
  - A second interrupted resume could reuse the first resume's nonces, because the new AEAD epoch was persisted only
    at the next periodic checkpoint. The epoch is now written before anything is sealed with it.
  - The sealed content record and plaintext index were always sealed in epoch 0, so re-finalising a changed input
    reused their nonces. They now use the newest chunk epoch, declared by the optional feature `final-seal-epoch-v3`.
  - Checkpoints are now HMAC-authenticated (`vnx-store-checkpoint-2`).
  - A resume's epoch also exceeds every epoch in the index sidecar, so replaying an older, still-authentic
    checkpoint is harmless.
- Containers padded after the last chunk passed `verify`. The body length must now equal `stored_size`, and `verify`
  checks `stored_sha256`.
- DNA metadata headers are not authenticated until the manifest is rebuilt, yet their lengths could drive
  multi-gigabyte allocations from a few kilobytes of reads. They are now bounded by the metadata groups actually
  decodable.
- Whole read lines were loaded into memory before any check. Lines are now cut at 100,002 bytes while reading.
- `--report` overwrote any file, including the command's own input. Reports now require `--force` and may never be
  an input or output.
- An output created while a command ran could be silently replaced. Outputs are now published by hard link.

### Added
- **Vectorised Reed–Solomon decoder** (`vnxdna.ecc.rs_batch`): batched Berlekamp–Massey/Chien/Forney over NumPy,
  strictly bounded-distance, used by the decoder, clustering, consensus and indel repair.
- **ECC engine interface** (`vnxdna.ecc.engine`): outer and inner code protocols and a registry keyed by the code
  names the manifest declares.
- **Single-read burst resynchronisation** (`--burst-repair N`): one contiguous run of up to N lost or extra bases per
  read, recovered at coverage 1 with F hypotheses.
- **Burst errors in the channel simulator** (`--burst-rate`, `--burst-length`, `--burst-kind`).
- **`vnx-dna simulate-errors`**: seeded error-channel sweeps with recovery statistics (JSON, CSV, Markdown). It exits
  70 on any undetected corruption.
- **Multi-archive pools**: the only decodable archive is used, or `--archive-tag` picks one.
- `verify --force` (for `--report`), exit code 141 for a closed standard output, `ruff` lint in CI, V2 compatibility
  fixtures (`tests/fixtures/v2_0/`), the V3 research harness (`research/v3/`), and 195 new tests (194 in `tests/v3/`, 1 README test; 29 of them from the release review).
- Docs: ARCHITECTURE (V3), STORAGE_FORMAT, ENCODING, ERROR_MODEL, LIMITATIONS, REPRODUCIBILITY and V3_AUDIT. The V1
  documents were renamed V1_ARCHITECTURE and V1_FORMAT.

### Changed
- Single-read indel repair decodes its hypotheses in vectorised blocks: same results as V2, faster. It also repairs
  reads that contain `N`.
- Reads with IUPAC or other non-ACGTN symbols are decoded with those positions as erasures instead of being dropped.
- The simulator's shuffle buckets now depend on the pool, not the input file's byte size, so FASTA and VXS inputs
  give identical reads. This can change shuffled output relative to V2 for large pools and VXS inputs. Batches also
  shrink at very high coverage, which bounds memory. Ordinary channels (e.g. 276-nt strands up to coverage 14.8) keep
  V2's batches.
- `verify` on an encrypted archive without a key exits 4 when everything checkable without the key passed (was 1).
  `--file` without a key is now reported instead of being ignored silently.
- Disk full, file too large, quota and read-only file-system errors exit 8 (were 70). An interrupted store without a
  checkpoint removes its partial files.

### Fixed
- **Decoder**
  - Long junk reads aborted decoding of pools that had no error-free read (exit 7).
  - Metadata repaired by the outer code was reported as SUCCESS instead of RECOVERED.
  - A descriptor leak in parallel `verify`, and a truncated body raising instead of producing a FAIL report.
- **Store**
  - `store --compression none` always failed.
  - Non-UTF-8 file names crashed store (exit 70).
  - Profile names were validated only after the whole file was stored.
  - Chunk counts that the trailer cannot address crashed store after processing the whole input.
  - `info` printed raw JSON.
- **Simulator and read processing**
  - Truncation could copy bases from the next read.
  - Leaked `.partial` files.
  - Exit 70 for many bad paths and malformed cluster files.
  - Consensus accepted out-of-range parameters.
  - The pipeline leaked its temporary directory and deleted too little with `--cleanup all`.
  - Experiment ECC statistics ignored failed trials.
  - Observed error rates were diluted by duplicates.
  - The orphan cap made clustering depend on read order.
  - An explicit missing `--dna-index` was ignored.
  - Compressed output names were silently written as FASTA.
- **Tests**
  - `from conftest import …` made `pytest tests/v2 tests/unit` fail at collection.
- **Docs.** Claims corrected where the V2 docs were contradicted by code or results:
  - 10 GB at 10× (overstated about 1.7×);
  - consensus throughput (unmeasured);
  - trailer checks on restore;
  - indel repair cost;
  - geometry discovery sample size;
  - the fixed-coverage integer requirement;
  - Wilson intervals rendered as [1.000, 1.000].

### Compatibility
- Reads archive formats 5 and 4 and legacy V0.1. Writes format 5.
- V2 cannot read encrypted archives whose store was resumed by V3: they declare `final-seal-epoch-v3`, and V2 exits
  6. V3 does not resume V2 checkpoints.

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

### Fixed during the release candidates (rc1 → rc4)
- A resumed store could re-seal chunks with a nonce it had already used; chunks now carry an AEAD epoch in the
  authenticated chunk index, so no (key, nonce) pair repeats.
- Clustering memory grew at high error rates; weak-read and orphan files are now streamed.
- Geometry discovery failed on pools where only a few percent of reads are correctable; `pipeline --resume` now
  validates a container before reusing it.
- Experiments counted a correct refusal (no read passed a frame CRC) as an internal error; it is now a detected
  failure.

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
