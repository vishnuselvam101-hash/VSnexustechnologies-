# VNX-DNA V4 baseline audit (V3.0.0)

This document records the state of VNX-DNA 3.0.0 as the frozen baseline for V4: what the code does, where each
stage lives, what is tested and measured, and what V4 can and should not change. It was written by reading the code,
the documentation, the tests and the git history of tag `v3.0.0`, by running the full test suite, and by running the
new baseline benchmark in [`benchmarks/baseline/v3/`](../benchmarks/baseline/v3/README.md).

> **Scope.** VNX-DNA is software. No DNA has been synthesised, stored, amplified or sequenced for this project. Every
> recovery result below is recovery from software-generated strands, optionally passed through VNX-DNA's own seeded
> channel simulator. Nothing here is evidence of physical DNA-storage performance.

Conventions: a statement is either *verified* (with the file and line, document, or command that shows it) or
explicitly marked **(inferred)**. "V3-recorded" numbers come from the repository's own result files and were not
re-measured for this audit; "measured" numbers were produced for this audit on 2026-10-03.

---

## 1. Identification

| item | value | source |
|---|---|---|
| baseline commit | `9b5123ceb86805b9d0eabd7a5f74592c3d97eb58` (`9b5123c`), tag `v3.0.0` | `git rev-parse HEAD`, `git tag` |
| V4 branch | `feature/vnx-dna-v4`, created from `v3.0.0`; tracked files under `src/` and `tests/` identical to the tag at audit time (an untracked `src/vnxdna/v4/` directory appeared during the audit from parallel V4 work; it is not imported by the V3 CLI and is not audited here) | `git diff --name-only HEAD -- src tests` (empty) |
| version string | `3.0.0` (`src/vnxdna/_version.py:2`); `vnx-dna version` → `vnx-dna 3.0.0 (writes archive format 5 / container v2 / frame 5; reads formats 5 and 4 and legacy V0.1)` | file, CLI |
| other tags | `v0.1-baseline` (`cf7d1f5`, 2026-09-26), `v1.0.0` (`0d07bc9`, 2026-09-30), `v2.0.0` (`b6c2c5b`, 2026-09-30) | `git rev-list -n1 <tag>` |
| history | 64 commits on the baseline; V3 itself is one feature commit (`76337d8`) plus 9 fix/test/doc commits and two merges | `git log --oneline` |
| language | Python ≥ 3.12 only (`requires-python = ">=3.12"`); NumPy for all vectorised paths; no compiled extension of its own | `pyproject.toml` |
| build system / packaging | setuptools ≥ 77 via `pyproject.toml` (PEP 621, dynamic version from `vnxdna._version`); `requirements.txt` mirrors the runtime pins; `Dockerfile` (python:3.12-slim, non-root user) | `pyproject.toml`, `Dockerfile` |
| package manager | pip (`pip install -e '.[dev]'`) | `docs/TESTING.md`, CI |
| tests | pytest 8 (+ pytest-cov), Hypothesis property tests (3 files), one `slow` marker (`tests/v2/test_streaming_scale_v2.py:70`) | `pyproject.toml`, `tests/` |
| lint | ruff, pyflakes rules only (`select = ["F"]`), line length 140; archived research code excluded | `pyproject.toml` |
| CI | GitHub Actions `.github/workflows/ci.yml`: one job on `ubuntu-latest`, Python 3.12: install `.[dev]`, `vnx-dna --help`, `ruff check src tests research`, `pytest`, `compileall`. No matrix, no coverage gate, no benchmark job | file |
| entry points | console scripts `vnx-dna` and `vnxdna` → `vnxdna._entry:main` (installs SIGTERM/SIGHUP → exit 130 during import, then calls `vnxdna.cli.main`); `python -m vnxdna` (`__main__.py`) | `pyproject.toml`, `src/vnxdna/_entry.py` |
| licence | MIT (`LICENSE`, `license = "MIT"`) | `pyproject.toml` |

## 2. Module structure

Lines are physical lines (`wc -l`, including docstrings and comments). Total `src/vnxdna`: **13,152** lines; tests:
4,796 lines; `research/` Python outside the archived legacy tree: 2,596 lines.

**Naming caveat.** The format-5 implementation that V3 ships lives in the package `vnxdna.v2`. V3 extended it in place
(`src/vnxdna/v3/__init__.py` docstring) and added only `vnxdna.v3.sweep`, `vnxdna.ecc.engine` and
`vnxdna.ecc.rs_batch`. "V2 code" in this document therefore means the `vnxdna.v2` package, which *is* the V3 codec.

### 2.1 Current codec (format 5) — `vnxdna.v2`, `vnxdna.v3` (7,339 lines)

| module | responsibility | lines |
|---|---|---|
| `v2/archive.py` | streaming `store` (thread pool, checkpoints, resume, AEAD epochs), authenticated open, restore, extract, verify, `AtomicOutput` | 822 |
| `v2/decoder.py` | geometry discovery, parallel read scan (pass 1), disk spill, bucket sort, duplicate resolution, per-chunk outer decoding (pass 2), `ReadsArchive` | 743 |
| `v2/api.py` | public API; detects V1/V2 inputs and dispatches V1 to `vnxdna.api`; pipeline driver | 701 |
| `v2/cluster.py` | address-indexed clustering, minimizer reassignment, orphan clustering, disk buckets | 555 |
| `v2/sequencing.py` | simulated storage + sequencing channel (dropout, coverage models, synthesis/sequencing errors, bursts, truncation, N, duplicates, junk, contamination, out-of-core shuffle) | 527 |
| `v2/scale.py` | test-data generator (`benchmark generate`), process-tree measurement, scale and corruption benchmarks | 476 |
| `v2/strandio.py` | streaming FASTA/FASTQ/plain/VXS readers (bounded line length), atomic strand writers, VXS format | 442 |
| `v2/frame.py` | strand frame format 5, SHAKE-128 scrambler, linear constraint screening, batch parse, vectorised inner-RS correction, table RS parity | 356 |
| `v2/manifest.py` | format-5 manifest schema (pydantic), chunk index (56 B) and plaintext index (36 B) dtypes and validation | 345 |
| `v2/container.py` | `.vxdna` container v2: streaming writer (`.partial`, fsync, `os.replace`), `pread` reader, trailer | 330 |
| `v2/encoder.py` | container → strands (process pool per chunk), Cauchy parity, metadata strands, DNA index `.vxidx` | 300 |
| `v2/consensus.py` | per-cluster consensus: verified read → corrected read → iterative banded-alignment vote with N | 283 |
| `v2/experiment.py` | reproducible experiments, Monte Carlo trials | 224 |
| `v2/sync.py` | single-read indel realignment and burst resynchronisation (RS + CRC as the sync test) | 162 |
| `v2/align.py` | batched banded edit-distance alignment and projection | 140 |
| `v2/crypto.py` | HKDF-SHA256 key separation, chunked AES-256-GCM, HMAC-SHA256 | 132 |
| `v2/constraints.py` | V1 constraint rules + tandem-repeat limit, vectorised checks | 128 |
| `v2/profiles.py` | `StoreOptionsV2`, the four profiles | 114 |
| `v2/stages.py` | in-process stage benchmark (A–H) | 109 |
| `v2/paths.py` | output/temp path safety (same-file, symlink, atomic text write) | 89 |
| `v2/workers.py` | process-pool helper | 59 |
| `v2/crc.py` | vectorised column-wise CRC-32 | 40 |
| `v2/__init__.py` | package docstring | 15 |
| `v3/sweep.py` | `simulate-errors` error-channel sweeps | 238 |
| `v3/__init__.py` | package docstring | 9 |

### 2.2 Shared layers

| module | responsibility | lines |
|---|---|---|
| `ecc/gf256.py` | GF(2⁸)/0x11D tables, vectorised matrix inverse and products | 159 |
| `ecc/cauchy.py` | systematic Cauchy RS erasure code (MDS), batched decode grouped by erasure pattern | 142 |
| `ecc/rs_batch.py` | V3 vectorised bounded-distance RS errors-and-erasures decoder (BM + Chien + Forney) | 193 |
| `ecc/engine.py` | `OuterCode` / `InnerCode` protocols and the name → implementation registry | 101 |
| `ecc/inner_rs.py` | inner RS wrapper (encoder; `reedsolo` reference) | 96 |
| `dna/mapping.py` | `2bit`, `rotation3`, `codebook8` mappings, reverse complement | 219 |
| `dna/constraints.py` | V1 constraint spec and checks (subclassed by `v2/constraints.py`) | 132 |
| `errors.py` | exception hierarchy with stable exit codes (0,1,2,3,4,5,6,7,8,70) | 127 |
| `cli.py` | Typer CLI (thin), mounts `vnx-dna v1`, `legacy`, `experiment`, `benchmark` | 939 |
| `_entry.py`, `__main__.py`, `__init__.py`, `_version.py`, `provenance.py` | entry point, version, run metadata | 77 |

### 2.3 V1 (format 4) and legacy, kept byte-identical for reading old archives

| module | responsibility | lines |
|---|---|---|
| `api.py`, `cli_v1.py`, `bench.py`, `channel.py` | V1 API, CLI (`vnx-dna v1`), benchmark, channel | 1,392 |
| `container/` (`builder`, `reader`, `manifest`, `crypto`, `compression`, `vxdna`) | V1 container; `compression`, `crypto` key parsing, canonical JSON and `_safe_name` are reused by V2 | 1,000 |
| `storage/encoder.py`, `storage/decoder.py` | V1 strand encoder/decoder (frame format 4) | 475 |
| `dna/strand.py`, `dna/reads.py` | V1 strand frame and read handling | 284 |
| `sync/indel.py` | V1 indel handling | 95 |
| `legacy/v0_1.py` | read-only V0.1 compatibility | 382 |

## 3. Architecture and data flow (V3)

Every arrow is a separate CLI command with an atomic output (`docs/V2_ARCHITECTURE.md`); `vnx-dna pipeline` chains them.

| # | stage | what happens | implementing code |
|---|---|---|---|
| 1 | **store** | read regular file in chunks (one per worker in flight); per chunk: zstd (kept only if smaller) → optional AES-256-GCM → append to body; checkpoints every 64 chunks | `v2/archive.py:store_file` (373), `seal_chunk` (176); `cli.py:store_cmd` (228) |
| 2 | **container** | `.vxdna` v2: header │ body │ manifest │ chunk index │ plaintext index │ trailer (SHA-256 of file) | `v2/container.py`, `v2/manifest.py` |
| 3 | **encode** | per stored chunk: ECC groups of K×P bytes → Cauchy parity (M shards) → frame (header, payload, CRC-32, inner RS) → scramble → map → constraint screen; metadata strands first; FASTA or VXS + `.vxidx` | `v2/encoder.py:encode_file` (181), `cauchy_parity` (73), `v2/frame.py:build_strands` (113), `_screen_linear_2bit` (177) |
| 4 | **strands** | designed strand pool, 252 nt each (balanced) | `v2/strandio.py` |
| 5 | **channel** (simulation) | dropout, coverage, synthesis + sequencing errors, bursts, truncation, N, RC, duplicates, junk, contamination, shuffle | `v2/sequencing.py:sequence_file` (423), `simulate_batch` (281) |
| 6 | **reads** | FASTQ with simulated qualities (or FASTA/VXS) | `v2/strandio.py`, `v2/api.py` (`reads_filter`) |
| 7 | **cluster** | verified address (CRC, RC, inner RS) → tentative header → minimizer reassignment → orphan clustering | `v2/cluster.py:address_batch` (90) |
| 8 | **consensus** | verified read / corrected read / banded-alignment vote; ambiguity → `N` | `v2/consensus.py`, `v2/align.py` |
| 9 | **decode** | geometry discovery; pass 1: per-read CRC / RC / batch inner RS (N = erasure), optional indel/burst repair → spill; pass 2: stripe-ordered records → duplicate vote → outer erasure decoding per chunk → stored-chunk SHA-256; container rebuilt byte-identically from metadata strands | `v2/decoder.py:discover` (103), `scan_batch` (216), `resolve_copies` (403), `assemble_chunks` (577), `_decode_chunk` (626); `v2/sync.py` |
| 10 | **restore** | stored chunk → AES-GCM open → decompress (bounded by authenticated size) → plaintext SHA-256 → streaming object SHA-256 → atomic rename | `v2/archive.py:open_chunk` (200), `write_verified_plaintext` (283) |

`recover` = decode + restore in one process (used by the baseline benchmark). `extract` = random access (§5.11).

## 4. Formats (summary; the specs are authoritative)

| format | identification | essentials | spec |
|---|---|---|---|
| container file | magic `\x89VXDNA\r\n`, container version 2 | 16-byte header, body of stored chunks, canonical-JSON manifest (≤ 1 MiB, `v2/container.py:51`), chunk index, plaintext index, 64-byte trailer with lengths, `VXDNAEND`, SHA-256 of all preceding bytes. Footer layout lets `store` write in one pass | `docs/V2_FORMAT.md §1`, `docs/STORAGE_FORMAT.md` |
| manifest | `format_version: 5` | canonical JSON (sorted keys, no floats, must equal its re-serialisation); every decoding parameter (chunking, compression, encryption, outer/inner code, frame geometry, constraints); `required_features` (unknown → exit 6); `seal` = SHA-256 (+ HMAC when encrypted). Chunk index 56 B/chunk (offset, size, first stripe, stripes, codec, AEAD epoch, SHA-256); plaintext index 36 B/chunk (size, SHA-256; AES-GCM-sealed when encrypted) | `docs/V2_FORMAT.md §2` |
| strand frame | format nibble 5 | `variant(1) │ ver/kind(1) │ archive tag(4) │ stripe(4) │ shard(1) │ payload(P) │ CRC-32(4) │ inner RS(r)`; bytes 1…14+P scrambled with SHAKE-128 keystream *v*; balanced: P = 40, r = 8 → 63 B → 252 nt. Frame ≤ 255 bytes (one RS codeword, `v2/frame.py:72`) | `docs/V2_FORMAT.md §3` |
| metadata strands | frame kind 1 | `"VNX5" ‖ lengths ‖ manifest ‖ chunk index ‖ plaintext index`, Cauchy 8+8, written first; a strand file alone is a complete archive | `docs/V2_FORMAT.md §3.1` |
| VXS | magic `\x89VXSTRD\n`, version 1 | 32-byte header, 2 bits/nt fixed-length records, 56-byte trailer with count, magic and SHA-256 of records | `docs/V2_FORMAT.md §4.1` |
| DNA index `.vxidx` | `vnx-dna-index-1` | canonical JSON: archive ID, manifest SHA-256, strand-file hash, one row per chunk (strand range, byte range, stripes), self-hash. Accelerator only; data is re-verified | `docs/V2_FORMAT.md §4.2` |
| cluster file | `vnx-clusters-1` | JSON Lines with header and mandatory end record | `docs/V2_FORMAT.md §4.3` |
| store checkpoint | `vnx-store-checkpoint-2` (V3) | HMAC-bound for encrypted stores; epoch persisted before sealing | `docs/STORAGE_FORMAT.md §4` |
| V3 optional feature | `final-seal-epoch-v3` | only in encrypted, resumed stores; VNX-DNA 2.0 refuses those (exit 6) | `docs/STORAGE_FORMAT.md §3` |
| older formats | format 4 (V1), V0.1 | read by the unchanged V1 / legacy code; `migrate` V1 → 5 | `docs/V1_FORMAT.md`, `docs/COMPATIBILITY.md` |

V3 kept archive format 5; for an unencrypted, never-resumed store only `encoder.version` (and therefore the metadata
strands) differs from 2.0.0 output, checked in `tests/v3/test_compat_v3.py` (`docs/STORAGE_FORMAT.md`).

## 5. Algorithms

1. **Chunking.** Fixed plaintext chunk size, 1 B … 64 MiB (`v2/manifest.py:39`); profiles use 256 KiB – 4 MiB
   (balanced 1 MiB). `chunk_count = max(1, ⌈size/chunk_size⌉)`; trailer limit ≤ 76,695,844 chunks
   (`v2/archive.py:391`). Chunks are independent units of compression, encryption, ECC, damage and random access.
2. **Compression.** zstd (default level 3; archival 9) or zlib or none, per chunk, kept only if smaller; the codec is
   recorded per chunk in the index (`v2/archive.py:seal_chunk`). Measured: the random input stays raw (0 of 1 chunks
   compressed), the 10 MiB mixed input compressed 8 of 10 chunks.
3. **Encryption** (optional). HKDF-SHA256(master key, random salt) → AEAD key, MAC key, 8-byte key check;
   AES-256-GCM per chunk, nonce `t(4) ‖ i(8)` with `t = epoch·256 + domain`, associated data binds archive ID, index
   and chunk count; manifest HMAC-SHA256; content record and plaintext index sealed (`v2/crypto.py`,
   `docs/SECURITY.md`). ECC is applied to ciphertext, so DNA decoding needs no key.
4. **Outer code.** Systematic Cauchy RS over GF(2⁸)/0x11D, `C[i][j] = 1/(x_i + y_j)`; any M of K+M shards may be
   lost per ECC group; the last group per chunk is shortened (padding-only data shards not emitted). Encoder: one
   (M×256) table gather per data column (`v2/encoder.py:73`). Decoder: per distinct erasure pattern one Gauss–Jordan
   inverse shared by all groups with that pattern (`ecc/cauchy.py:80-129`). MDS proof in `docs/ECC.md`.
5. **Inner code + CRC.** RS(n, n−r) over the same field on the whole frame, parity by per-column table gather
   (bit-identical to `reedsolo`). CRC-32 (zlib/IEEE) over bytes 1…10+P before scrambling, vectorised column-wise
   (`v2/crc.py`). V3 batch decoder (`ecc/rs_batch.py`): syndromes, erasure locator, Berlekamp–Massey, Chien, Forney
   over a NumPy batch; accepts only if root count = locator degree, zero syndromes and the CRC verifies; strictly
   bounded-distance (`2e + f ≤ r`). Decision rule: with flagged erasures first, errors-only retry.
6. **DNA mappings** (`dna/mapping.py`): `2bit` (4 nt/byte, 2 bits/nt, no constraint by construction), `rotation3`
   (6 nt/byte, no equal adjacent bases), `codebook8` (8 nt/byte, 256 words with 50 % GC, runs ≤ 3).
7. **Scrambler and constraint screening.** 256 SHAKE-128 keystream variants; the encoder picks the smallest variant
   whose mapped strand satisfies GC bounds (optionally windowed), homopolymer, tandem-repeat and motif rules (also
   reverse complement); none → `ConstraintError`. For `2bit`, RS parity and mapping are XOR-linear, so each variant
   is `codes(v=0) XOR mask_v` (`v2/frame.py:177`). This is *screening*, not constrained coding.
8. **Clustering** (`v2/cluster.py`): O(1) address lookup for reads that verify; tentative headers; minimizer index
   (k = 12, w = 8) for weak reads; leader clustering for orphans; disk buckets; caps at 2,000,000 clusters and
   200,000 orphans (`docs/CONSENSUS.md`). Output depends only on the multiset of reads (tested).
9. **Consensus** (`v2/consensus.py`): verified read → up to 3 inner-RS-corrected reads → iterative (2 rounds) banded
   alignment (band 12) with quality-weighted voting over {A,C,G,T,deleted} and insertion slots; < 60 % winner share
   → `N` (an erasure for the inner code).
10. **Synchronization / burst repair** (`v2/sync.py`, opt-in): single-read realignment for 1–3 indels
    (O(F^|d|) hypotheses, budget 4,096 per orientation), burst resynchronisation in F hypotheses for one contiguous
    run of L ≤ N bases (balanced: up to 29 nt with no other error). Both use RS + CRC as the acceptance test. No
    in-strand markers.
11. **Random access.** Container: authenticated chunk index → `pread` of overlapping chunks only. DNA: `.vxidx`
    gives each chunk's strand range → only those strands plus metadata strands are decoded. Without an index (real
    reads), every read is scanned but only needed chunks are assembled (`docs/RANDOM_ACCESS.md`). No molecular
    (primer) access.
12. **Duplicate resolution** (`v2/decoder.py:403`): identical copies merge; strict majority wins; a tie becomes an
    erasure.
13. **Channel simulator** (`v2/sequencing.py` docstring): dropout; fixed / Poisson / log-normal coverage;
    per-molecule synthesis sub/ins/del; sequencing sub/del/ins; bursts (V3); truncation; N calls; reverse
    complement; duplicates; junk and contamination reads; out-of-core shuffle; informative or flat qualities.
    Not fitted to any platform (`docs/LIMITATIONS.md`).
14. **Hashing / identity.** SHA-256 per stored chunk, per plaintext chunk, whole object, body (`stored_sha256`),
    container trailer, both index tables, manifest seal; HMAC-SHA256 when encrypted; content-derived archive ID
    when unencrypted (`SHA-256("VNX-DNA/5 archive-id\0" ‖ options ‖ SHA-256(data))[:16]`); 32-bit archive tag in
    every strand. The integrity structure is a flat list of per-chunk hashes authenticated through the manifest, not
    a Merkle tree (no `merkle` anywhere in `src/`).

## 6. Guarantees and limitations

### 6.1 What is proven or tested (verified in docs and test names)

| property | basis |
|---|---|
| any M strands of an ECC group may be lost | Cauchy MDS theorem (`docs/ECC.md`); exhaustive pattern tests for small (K, M), sampled for large; exactly-M-per-group and M+1 tests on the full pipeline (`tests/v2/test_dna_v2.py`) and at 1 GB (V3-recorded, `docs/LARGE_FILES.md`) |
| inner code corrects `2e + f ≤ r` and never miscorrects beyond it | boundary tests r ∈ {2…64}, 1,500 random words vs `reedsolo` (`tests/v3/test_ecc_decoder_v3.py`) |
| no corrupt strand accepted | CRC-32 after correction (≈ 2⁻³² per corrupt read, a probability, not a proof) |
| no wrong output published | stored SHA-256 → AES-GCM → plaintext SHA-256 → object SHA-256 → atomic rename; fuzz/mutation tests |
| bounded memory for store/restore/encode/decode | chunk × workers design; V2/V3-recorded measurements up to 10 GB |
| crash safety of `store` | `.partial` + fsync + `os.replace`; SIGKILL + resume test |
| determinism | content-derived archive ID, seeded channel, order-independent voting (property tests) |

### 6.2 Limitations (facts from `docs/LIMITATIONS.md` and `docs/ROADMAP.md`, plus code findings)

* Software and simulation only; channel not fitted to a platform; no molecular random access (no primers); constraint
  rules are screening rules (no secondary structure, Tm or cost model); density figures are nucleotide counts.
* Guarantees are per ECC group and per strand; archive recovery under random damage is a measured probability.
* Indels at coverage 1: one indel (or up to 3 same-direction within budget) or one burst per read, opt-in; +1/−1
  pairs and multiple bursts per read are not repaired. Indels shared by every molecule of a strand become erasures.
* **No cross-group code**: more than M lost strands in one group loses that chunk even when other groups have spare
  parity. Fountain codes and cross-chunk interleaving are research items (`docs/ROADMAP.md`).
* CPU-bound Python/NumPy; clustering and consensus are the slowest stages; the sequencing chain is measured only up
  to 10 MB (stage benchmark).
* Encrypted archives leak approximate size, per-chunk compressibility and ECC parameters (no padding); no password
  KDF; resume after a full snapshot rollback could reuse nonces (`docs/SECURITY.md`).
* Not implemented (verbatim list): molecular primers and PCR selection, in-strand synchronisation markers or
  watermark/VT codes, fountain outer codes, cross-chunk interleaving, secondary-structure screening, password KDF,
  size padding, GPU acceleration, resume for `encode`/`decode`.
* Code findings for this audit: `store` accepts exactly one regular file (`v2/archive.py:383-384`): no directories,
  no multi-file archives, no metadata (permissions, timestamps) preserved (`docs/LIMITATIONS.md`, Operations).
  Frame length is capped at one RS codeword over GF(2⁸): ≤ 255 bytes, i.e. ≤ 1,020 nt with `2bit`
  (`v2/frame.py:72`). The per-strand stripe field is 32-bit and the shard field 8-bit (K+M ≤ 256).

## 7. Tests

**Full suite run for this audit** (started by the operator before any benchmark, `python -m pytest -q -p no:cacheprovider`
under `/usr/bin/time -v`, venv on `PATH`, 8 logical CPUs):

| item | value |
|---|---|
| result | **601 passed, 0 failed, 0 skipped, 0 errors**; exit status 0 |
| how counted | `addopts = "-q"` plus `-q` suppresses pytest's summary line; the count is the 601 progress characters, all `.` (no `s`, `F`, `E`, `x`), equal to `pytest --collect-only` (601 collected) |
| wall time | **4 min 19.4 s** (259.4 s) |
| CPU | user 455.1 s, system 34.7 s (188 % of one CPU) |
| peak RSS | 801,784 KiB (≈ 783 MiB), largest single process |
| documented expectation | "≈ 4 min on 8 cores" (`docs/TESTING.md`); 601 passed recorded at `b205f45` (commit `477e3e2`) |

Collected tests per directory (`pytest --collect-only -q`):

| directory | files | tests | content (from `docs/TESTING.md`) |
|---|---|---|---|
| `tests/unit` | 9 | 163 | V1 units: Cauchy (exhaustive patterns), inner RS, indel, mapping, manifest/crypto, container, channel, strand, duplicates |
| `tests/integration` | 3 | 62 | V1 full DNA storage, legacy V0.1 compatibility, matrix and random access |
| `tests/cli` | 3 | 29 | V1 CLI, clean-room V1, README commands |
| `tests/property` | 1 | 5 | Hypothesis properties (V1) |
| `tests/adversarial` | 1 | 11 | fuzzing (Hypothesis) |
| `tests/v2` | 8 | 137 | frame/CRC/constraints, container, DNA, channel/cluster/consensus, CLI, compat, properties/adversarial, streaming scale (`slow`) |
| `tests/v3` | 9 | 194 | container security, batch RS decoder, burst/sweep features, V2 compat fixtures, round trips, channel, CLI, release review, review regressions |
| **total** | 34 | **601** | |

Coverage percentage is not measured in CI (pytest-cov is installed but not used by `ci.yml`).

## 8. Performance and memory

### 8.1 V3 baseline benchmark (measured for this audit)

Script, data and method: [`benchmarks/baseline/v3/`](../benchmarks/baseline/v3/README.md) (`run_v3_baseline.py`,
`results.json`, `environment.json`). Machine: Intel Xeon Gold 6240 @ 2.60 GHz, 8 logical CPUs, 31.3 GiB RAM,
Ubuntu 24.04.5 LTS, Python 3.12.3, NumPy 2.5.3. Profile `balanced`, no encryption, default workers, FASTA strands,
`store` → `encode` → `recover` (from the FASTA alone), each under `/usr/bin/time -v`; median of 3 repeats; peak RSS
is the largest single process (not the process-tree sum used in `docs/BENCHMARKS.md`). All 8 inputs × 3 repeats
recovered with identical SHA-256. Total runtime 66 s; load average 0.45 at start.

| input | bytes | stored | strands | nucleotides | nt / input byte | parity / strands | store + encode s | recover s | peak RSS MiB (store / encode / recover) | result |
|---|---|---|---|---|---|---|---|---|---|---|
| tiny (text) | 100 | 87 | 115 | 28,980 | 289.8 | 13.9 % | 0.40 + 0.46 | 0.49 | 57 / 57 / 57 | PASS |
| small (text) | 10,240 | 4,540 | 242 | 60,984 | 5.955 | 13.2 % | 0.43 + 0.48 | 0.48 | 57 / 57 / 57 | PASS |
| medium (mixed) | 1,048,576 | 469,646 | 14,782 | 3,725,064 | 3.553 | 19.9 % | 0.44 + 0.63 | 0.63 | 57 / 65 / 76 | PASS |
| large (mixed) | 10,485,760 | 4,650,068 | 145,537 | 36,675,324 | 3.498 | 20.0 % | 0.49 + 0.96 | 1.17 | 68 / 113 / 137 | PASS |
| repetitive | 1,048,576 | 128 | 116 | 29,232 | 0.028 | 13.8 % | 0.42 + 0.48 | 0.52 | 57 / 57 / 58 | PASS |
| random | 1,048,576 | 1,048,576 | 32,871 | 8,283,492 | 7.900 | 20.0 % | 0.49 + 0.83 | 0.77 | 58 / 82 / 102 | PASS |
| text | 1,048,576 | 352,021 | 11,105 | 2,798,460 | 2.669 | 19.9 % | 0.43 + 0.64 | 0.60 | 57 / 63 / 72 | PASS |
| binary (records) | 1,048,576 | 594,043 | 18,676 | 4,706,352 | 4.488 | 20.0 % | 0.43 + 0.65 | 0.68 | 57 / 67 / 82 | PASS |

Observations (from these numbers and the reports in `results.json`):

* **Process start-up dominates small inputs.** `vnx-dna version` alone takes 0.37 s (median of 5); importing
  `vnxdna.cli` takes ≈ 0.27 s (`python -X importtime`), partly because `cli.py:31` imports the whole V1 CLI and API
  eagerly. The commands' own `elapsed_s` for the 1 MiB inputs is 0.02–0.03 s (store), 0.05–0.44 s (encode),
  0.08–0.32 s (recover); for 10 MiB 0.08 / 0.55 / 0.73 s.
* **Metadata floor.** Every archive carries ≥ 96 metadata strands (Cauchy 8+8 copies of the ≈ 1.8 KB manifest
  plus indexes): 96 of 115 strands for the 100-byte input, 144 for 10 chunks. Below ~100 KB the metadata, not the
  data, determines the DNA size.
* **ECC overhead** approaches M/(K+M) = 20 % of strands; smaller inputs show less because metadata strands dilute
  the ratio and shortened last groups are counted with their full M parity strands.
* **Incompressible data costs 7.9 nt/byte**: the balanced frame carries 40 payload bytes in 63 frame bytes and the
  outer code 64 data in 80 shards, so ≈ 1.016 information bits per nucleotide out of the 2 bits/nt of the `2bit`
  mapping (computed: 8 / (252 · 80 / (64 · 40)) = 8 / 7.875).

**Noisy pipeline** (medium input; coverage 10 Poisson, substitution 0.001, insertion 0.0001, deletion 0.0001, dropout
0.02, seed 42, cluster + consensus): **PASS**, SHA-256 identical. Wall 20.2 s, CPU 24.1 s, peak RSS (largest process)
560 MiB. Stage times inside the process: store 0.03 s, encode 0.19 s, sequence 2.58 s, **cluster 9.01 s, consensus
7.62 s**, decode 0.34 s, restore 0.01 s, verify 0.02 s. 14,782 strands → 145,350 reads; 283 strands had zero reads;
14,499 clusters, 14,498 CRC-valid consensus sequences; 283 shards erased, 128 of 184 ECC groups used the outer code,
worst group lost 7 strands (guarantee 16). Clustering + consensus are 82 % of the wall time.

### 8.2 V3-recorded numbers (from `docs/BENCHMARKS.md` and `docs/LARGE_FILES.md`; not re-measured)

Same 8-CPU machine class (kernel 6.8.0-139 at the time), `mixed` input, balanced, process-tree RSS:

| input | store | restore | encode → VXS | recover from DNA | nt / input byte |
|---|---|---|---|---|---|
| 1 MB | 0.47 s / 57 MiB | 0.48 s / 57 MiB | 0.64 s / 325 MiB | 0.61 s / 306 MiB | 3.599 |
| 100 MB | 1.04 s / 80 MiB | 0.82 s / 84 MiB | 2.25 s / 446 MiB | 2.59 s / 562 MiB | 3.493 |
| 1 GB | 6.25 s / 80 MiB | 4.16 s / 85 MiB | 16.41 s / 509 MiB | 20.61 s / 559 MiB | 3.492 |
| 5 GB | 29.33 s / 81 MiB | 18.93 s / 87 MiB | 78.35 s / 548 MiB | 94.82 s / 609 MiB | 3.492 |

* 10 GB acceptance (V2.0.0rc4-recorded, stated as still the reference above 1 GB): store 61 s, encode 161 s,
  recover 200 s, peak RAM 599 MiB, 138,563,183 strands, peak disk 33.16 GiB, SHA-256 identical.
* Stage benchmark, 1 MB V3: clustering 8.94 s, consensus 7.50 s, end to end 19.57 s; consensus ≈ 18,000 reads/s
  (V2 10 MB measurement).
* Noisy decode, 10 MB, coverage 1: V3 is 5.1–7.4× faster than V2 at substitution 0.002–0.006 (vectorised RS).
* Single-read indel repair (V3): ≈ 9 ms/read for 1 indel, 146 ms for 2, 1.7 s for 3.
* Profiles under a harsh channel (V2-recorded, 100 trials): compact 0/100, balanced 59/100, resilient 100/100,
  archival 99/100 exact recoveries.

## 9. Dependencies

| package | constraint (`pyproject.toml`) | installed | licence (package metadata) | role |
|---|---|---|---|---|
| numpy | `>=2.0,<3` | 2.5.3 | BSD-3-Clause (and bundled 0BSD, MIT, Zlib, CC0-1.0) | all vectorised code |
| cryptography | `>=44,<47` | 46.0.7 | Apache-2.0 OR BSD-3-Clause | AES-GCM, HKDF, HMAC |
| zstandard | `>=0.23,<1` | 0.25.0 | BSD-3-Clause | compression |
| reedsolo | `>=1.7,<2` | 1.7.0 | Public Domain (Unlicense / MIT-0 classifiers) | reference RS (tests, V1 path) |
| pydantic | `>=2.10,<3` | 2.13.5 | MIT | manifest schema validation |
| typer | `>=0.15,<1` | 0.27.2 | MIT | CLI |
| dev: pytest | `>=8,<9` | 8.4.2 | MIT | tests |
| dev: pytest-cov | `>=6,<8` | 7.1.0 | MIT | (unused in CI) |
| dev: hypothesis | `>=6.100,<7` | 6.168.3 | MPL-2.0 | property tests |
| dev: ruff | `>=0.6,<1` | 0.16.10 | MIT | lint |
| build: setuptools | `>=77` | (build-time only) | MIT | packaging |

No lock file; CI installs the newest versions inside the ranges, so CI runs are not bit-for-bit reproducible
**(inferred from the absence of a lock file and `pip install -e '.[dev]'` in `ci.yml`)**.

## 10. Technical debt (each item checked in the code)

| # | item | evidence |
|---|---|---|
| D1 | **Package name does not match the version.** The V3 codec lives in `vnxdna.v2`; `vnxdna.v3` holds only the sweep. A V4 author must know that `vnxdna.v2.*` is the current format-5 implementation | `src/vnxdna/v3/__init__.py:1-9` |
| D2 | **Large modules mixing concerns.** `cli.py` 939 lines; `v2/archive.py` 822 (store, resume, restore, extract, verify, atomic output); `v2/decoder.py` 743 (discovery, scan, spill, sort, outer decode, random access); `v2/api.py` 701 | `wc -l` |
| D3 | **Two full codec stacks in one package.** V1 (format 4: `api.py`, `cli_v1.py`, `channel.py`, `bench.py`, `container/*`, `storage/*`, `dna/strand.py`, `dna/reads.py`, `sync/indel.py`, ≈ 3,200 lines) sits beside the V2/V3 stack with parallel modules for crypto, manifest, encoder, decoder, channel, CLI and sync. V1 is frozen by policy (ruff per-file ignores in `pyproject.toml`) | module list §2.3 |
| D4 | **The new stack depends on private helpers of the frozen V1 stack**: `_safe_name` and `_format_validation_error`, plus canonical JSON and key parsing; `decoder.discover` imports the V1 decoder for format-4 geometry | `v2/archive.py:48-49`, `v2/manifest.py:32`, `v2/crypto.py:51`, `v2/decoder.py:145` |
| D5 | **Eager import of V1 at CLI start-up** (≈ 0.27 s import, 0.36 s per command measured) | `cli.py:31`; §8.1 |
| D6 | **Python-bound per-read / per-position loops** in the slowest stages: clustering builds per-read slices and per-read byte strings (`v2/cluster.py:97-98`, `139-149`, `167-170`); consensus loops per cluster and per draft position (`v2/consensus.py:160-180`); the decoder handles length-mismatched reads one by one (`v2/decoder.py:283-303`); outer decoding loops over distinct erasure patterns (`ecc/cauchy.py:111-129`) | code; noisy case 82 % in cluster + consensus |
| D7 | **Decoder pass 2 is sequential** (one chunk after another in the parent process; reports `pass2_mode: sequential` or `bucket-sort`) | `v2/decoder.py:614-622`; ROADMAP "parallel pass 2" |
| D8 | **Single-file archives only**; no directory or multi-file manifest, no file metadata | `v2/archive.py:383-384` |
| D9 | **Flat integrity structure, no Merkle tree**: per-chunk SHA-256 in a table whose SHA-256 is in the manifest. Verifying one chunk needs the whole chunk index (56 B × chunks), and there is no compact proof for a sub-range | `v2/manifest.py`, `docs/V2_FORMAT.md §2.1`; no `merkle` in `src/` |
| D10 | **No in-strand synchronisation markers**; coverage-1 indel handling relies on exhaustive RS+CRC hypothesis search whose cost grows as O(F^d) | `docs/SYNCHRONIZATION.md`; `v2/sync.py:129` |
| D11 | **No fountain / cross-group code**; the ECC engine registry has exactly one outer and one inner code | `ecc/engine.py:30`, `86-101` |
| D12 | **High fixed metadata cost**: ≥ 96 metadata strands per archive (Cauchy 8+8 = 100 % redundancy on the manifest, which is canonical JSON) | §8.1 tiny/repetitive cases |
| D13 | **Frame length ceiling** from GF(2⁸) RS: frame ≤ 255 bytes; longer strands would need a different inner code or interleaving | `v2/frame.py:72` |
| D14 | **Constraint satisfaction by screening**: up to 256 scrambler variants, then hard failure (`ConstraintError`); no constrained code that guarantees success | `v2/frame.py:137-208`, `docs/V2_FORMAT.md §3` |
| D15 | **No resume for `encode`/`decode`** | `docs/LIMITATIONS.md` |
| D16 | **CI is minimal**: single Python version, no coverage gate, no benchmark or performance-regression check, no lock file | `.github/workflows/ci.yml` |
| D17 | **Cosmetic**: `--no-name` (or `--timestamp`) relabels the recorded profile `balanced-custom` although no coding parameter changes, because any override counts as customisation | `v2/profiles.py:104-111`; observed in a trial run of the benchmark |

## 11. V4 opportunities

Ordered by expected value for V4 **(assessment, not measured)**:

1. **Faster read processing** (D6): batched/vectorised clustering and consensus, or a compiled kernel behind the
   same interface; target the 82 % of noisy-pipeline time spent there. Measurable against §8.1 and the stage benchmark.
2. **Parallel pass 2 and vectorised outer decoding across patterns** (D7, D6): already on the ROADMAP.
3. **Lazy CLI imports** (D5): cuts ≈ 0.3 s per command without touching formats.
4. **Multi-file / directory archives with a Merkle-tree manifest** (D8, D9): enables per-file random access and
   compact sub-range proofs. Needs a new required feature or format version.
5. **Optional sync-marker frame** (D10) as a *new frame format*, benchmarked against V3's burst repair on the
   coverage-1 sweeps, and **fountain / cross-chunk outer code** (D11) via the existing ECC registry, gated by
   `required_features` so V3 readers refuse rather than misdecode.
6. **Lower metadata overhead** (D12): compact binary manifest encoding inside the metadata strands, or an adaptive
   metadata code for small archives.
7. **Reproducibility and regression guard**: commit the baseline harness (`benchmarks/baseline/v3/`) and compare V4
   against it in a CI job with tolerances; add a lock file for benchmark runs.

## 12. V4 risks

| risk | why it matters | mitigation |
|---|---|---|
| breaking V3 (and V2/V1) compatibility | three formats and the V0.1 legacy layer are readable today, with fixtures from 2.0.0 (`tests/fixtures/`, `tests/v3/test_compat_v3.py`) | keep the 601 V3 tests passing unchanged; add V3-written fixtures; new behaviour only behind new `required_features` / format versions |
| format proliferation | V1 + V2/V3 code already coexist (D3); each new frame/manifest variant multiplies the test matrix and reader code | one new format version at most; keep optional features orthogonal; document a deprecation policy for writers (readers stay) |
| performance regressions | V3 numbers are recorded per stage; added layers (markers, Merkle tree, fountain code) cost time and density | run `benchmarks/baseline/v3/run_v3_baseline.py` and the V3 stage benchmark on V4 on the same machine; publish deltas |
| sync-marker density cost | markers spend nucleotides on every strand at every coverage; at coverage > 1, consensus already resynchronises (`docs/SYNCHRONIZATION.md`) | make markers optional, measure nt/byte and recovery against V3 burst repair before making any default change |
| fountain-code complexity | rateless codes add decoding failure probability and memory; the MDS guarantee per group is simple and proven | keep Cauchy as default; report fountain overhead and failure rates with confidence intervals |
| overclaiming | the project's documents are careful to say "simulation"; new features (e.g. "molecular random access", "error-free") invite stronger wording than the evidence supports | keep the LIMITATIONS scope statement; every number generated by script with machine metadata; no wet-lab claims |
| crypto regressions | V3 fixed two AES-GCM nonce-reuse paths (`docs/V3_AUDIT.md`); a new container or resume path can reintroduce them | reuse `v2/crypto.py` constructions; carry over the V3 security tests; review any nonce derivation change |
| scope creep in a 13 kLOC Python codebase | D2/D3 already make changes touch large modules | add new code in new modules; avoid editing the frozen V1 modules |

## 13. Reproduction

```bash
. .venv/bin/activate
python -m pytest -q                                   # 601 tests, ≈ 4.3 min on 8 logical CPUs (this audit)
python benchmarks/baseline/v3/run_v3_baseline.py      # ≈ 66 s; writes results.json, environment.json, README.md
```
