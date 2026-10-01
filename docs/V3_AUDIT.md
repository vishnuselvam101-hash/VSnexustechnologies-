# VNX-DNA 3.0.0 release audit

This report covers the audit of VNX-DNA 2.0.0 and the development of VNX-DNA 3.0.0.

> **Scope.** Every result here is a computational simulation result. No physical DNA was synthesised, stored,
> amplified or sequenced. Nothing here claims laboratory validation, physical storage density, long-term
> preservation, commercial archival capability or RAM-class performance.

Status labels used below:

| label | meaning |
|---|---|
| **PASS** | verified and behaving as specified |
| **FAIL** | verified and not behaving as specified |
| **FIXED** | found in this audit, fixed, and covered by a regression test that fails on 2.0.0 |
| **REMAINING ISSUE** | found, not fixed; impact and next action are given |
| **NOT IMPLEMENTED** | outside what this release does |
| **NOT TESTABLE IN CURRENT ENVIRONMENT** | would need resources this environment does not have |

## 1. Method

1. **Discovery.** Worktree `feature/vnx-dna-v3` from `vnx-dna/v2` at `fb468a1`. Tag `v2.0.0` is `b6c2c5b`; the
   branch head only adds the overview PDF. Another session held uncommitted edits in the main checkout, so all V3
   work was done in a separate worktree. Those edits were two flaky-test fixes, which V3 applies byte-identically.
2. **V2 test baseline.** The complete suite on a fresh venv (§2).
3. **Independent audits.** Three parallel audits covered: the container, crypto and store; the ECC, frame and decoder;
   and the channel, clustering, consensus, CLI and documentation claims. Every finding was reproduced with a script
   before being accepted. Suspected findings are marked as such.
4. **Fixes.** Each fix was followed by a regression test, and each test was run against a checkout of `v2.0.0` to
   confirm it fails there.
5. **V3 development**, then an independent review of the V3 diff itself (§4.6), whose findings were also fixed.
6. **Measurement.** V2 and V3 were benchmarked on the same machine, with the same inputs and the same commands
   (`research/v3/run_v3_research.py`), and rendered into the docs by script.

## 2. V2 baseline (2.0.0, before any change)

| check | result |
|---|---|
| test suite (`pytest`, fresh venv) | **406 collected: 404 passed, 2 skipped, 0 failed** in 2 min 50 s (peak RSS 301 MB). The 2 skips are the README tests, which need `vnx-dna` on `PATH`. With it: **406 passed**. |
| running a subset (`pytest tests/v2 tests/unit`) | **FAIL**: collection error (issue T1) |
| scale, noisy decode, stages, indel repair | measured with the 2.0.0 code in `research/results/v3/*-v2.json` (§8) |

The generated baseline figures are in §8 and in [BENCHMARKS.md](BENCHMARKS.md).

## 3. Audit results by area

| area | verdict | notes |
|---|---|---|
| binary input, chunking, compression | PASS with FIXED defects | 1,554 extract/restore/verify edge cases (sizes 0, 1, chunk ± 1, multiples; three codecs; plain and encrypted) gave 0 mismatches. Defects C7, C8, C12, S1. |
| encryption interface, authentication | PASS with FIXED defects | primitives and constructions sound (AES-256-GCM chunked AEAD, HKDF, HMAC; no custom crypto); two nonce-reuse paths (C1, C2) and a checkpoint rollback, all FIXED |
| container format, metadata, SHA-256 integrity | PASS with FIXED defects | 3,000 mutated containers gave only clean errors; padded or truncated bodies (C3–C5) FIXED |
| binary ↔ 2-bit ↔ DNA, sequence validation, determinism | PASS | all mappings round-trip all 256 byte values; encoding is deterministic; the variant byte has no CRC-consistent keystream collision for P = 1…240 |
| GF(256) | PASS | x⁸+x⁴+x³+x²+1 (0x11D), generator 2 of order 255; field axioms exhaustive (65,536 pairs, 256³ triples) |
| Cauchy RS (outer) | PASS | MDS by the Cauchy construction for all K + M ≤ 256; exhaustive for all K + M ≤ 12 (8,177 subsets) and all 12,869 square submatrices of the 8+8 metadata code; sampled up to 255+1 and 1+255 |
| inner RS: encoder, locator, correction | PASS | `2e + f ≤ r` exact at the boundary for r = 2…64 |
| inner RS: failure behaviour | FIXED (E1) | `reedsolo` returned out-of-radius miscorrections (7 % of beyond-bound words at r = 2). The CRC rejected all of them (0 wrong frames in 12,000), and V3 is strictly bounded-distance |
| malformed and unsupported input | PASS with FIXED defects | D1, D2, D3, B6 |
| recovery: substitutions, erasures, dropout, duplicates, reordering, reverse complements | PASS | M per group recovers, M + 1 fails cleanly; shuffled and duplicated pools recover |
| recovery: insertions and deletions | PASS | consensus at coverage > 1; single-read repair of 1 indel + ⌊(r−1)/2⌋ errors: 60/60 for each mapping |
| recovery: bursts | NOT IMPLEMENTED in 2.0 → implemented in V3 | the channel had no bursts (V1 had them) |
| scalability | PASS | 1 MB–10 GB at 2.0.0 (V2 docs); 1 MB–5 GB with V3 (§8). Defects B2, B3 (memory growth with coverage or VXS input) FIXED |
| channel determinism | PASS with FIXED defects | the same file and seed give byte-identical output; the output depended on the input file format (B12) FIXED |
| CLI contract | PASS with FIXED defects | Ctrl-C gives 130 everywhere; many paths ended in exit 70 or had no report (B4–B9, C9–C11) FIXED |
| documentation claims | PASS with FIXED corrections | every generated table re-rendered with no diff; the hand-written claims below were corrected |

## 4. Issues discovered and their status

### 4.1 Container, crypto and store

| id | type | issue | status |
|---|---|---|---|
| C1 | security | AES-GCM nonce reuse across two resumes: the new epoch was persisted only at the next periodic checkpoint | FIXED |
| C2 | security | sealed content and plaintext index always used epoch 0, so re-finalising a changed input reused nonces; the checkpoint epoch had only an unkeyed digest (rollback) | FIXED (`final-seal-epoch-v3`, HMAC-bound checkpoints) |
| C3 | bug | body length never compared with `stored_size`, and `stored_sha256` never checked: padded containers passed `verify` | FIXED |
| C4 | doc | V2_FORMAT claimed full reads check the trailer; `restore` did not (per-chunk SHA-256 plus the tiling rule cover the body) | FIXED (doc corrected; rule documented) |
| C5 | bug | `verify` on a truncated body raised instead of reporting FAIL | FIXED |
| C6 | bug | parallel `verify` leaked file descriptors (lazy-open race) | FIXED |
| C7 | bug | `store --compression none` without `--level` always failed | FIXED |
| C8 | bug | non-UTF-8 file names crashed store (exit 70) | FIXED |
| C9 | bug | disk full or file too large gave exit 70, and left unresumable partial files | FIXED (exit 8, cleanup) |
| C10 | bug | `info` always printed JSON (dead human-readable branch) | FIXED |
| C11 | bug/doc | `verify` of an encrypted archive without a key exited 1; `--file` was silently ignored | FIXED (exit 4, reported) |
| C12 | limitation | profile names validated only after the whole store; `options_for(profile=…)` raised TypeError | FIXED |
| S1 | bug | chunk counts above what the 32-bit trailer field can address crashed after processing the whole input | FIXED (checked up front) |
| S2 | bug (low) | an output created between the existence check and the rename was replaced | FIXED (hard-link publish) |

### 4.2 ECC, frame and decoder

| id | type | issue | status |
|---|---|---|---|
| E1 | robustness | inner decoder (`reedsolo`) returned codewords outside `2e + f ≤ r` (caught by the CRC) | FIXED (strict vectorised decoder) |
| D1 | bug | geometry discovery built impossible geometries for long reads: a few junk reads over 1,020 nt aborted decoding (exit 7) | FIXED |
| D2 | security | unauthenticated DNA metadata lengths drove allocation (4 KB of reads → 413 MiB, extrapolated to ~3 GB) | FIXED (bounded by the decodable groups; see R2) |
| D3 | security | whole lines read before any length check (190 MiB line → 430 MiB RSS) | FIXED (47 MiB) |
| D4 | bug | metadata repaired by the outer code reported SUCCESS instead of RECOVERED | FIXED |
| D5 | limitation | reads with IUPAC or other non-ACGTN symbols dropped entirely | FIXED (decoded with erasures) |
| D6 | limitation | pools containing two archives' metadata always refused | FIXED (automatic choice, `--archive-tag`) |
| D7 | limitation | single-read indel repair skipped reads containing `N` | FIXED |
| D8 | doc | indel repair cost (up to 4 × hypotheses decodes, not 2·F) and discovery sample size misstated | FIXED |
| D-S1 | suspected | the external bucket sort assumes stripes are spread evenly; a pool heavily skewed to one stripe could exceed the 64 MiB bucket target | REMAINING ISSUE: not reproduced. Impact: memory only, for adversarial or extreme-coverage pools. Next action: cap bucket size by splitting hot buckets. |
| D-S2 | performance | `detect()` runs geometry discovery and the decoder runs it again | REMAINING ISSUE. Impact: seconds on very noisy pools. Next action: pass the discovered geometry through. |
| D-S3 | minor | FASTA files starting with a UTF-8 BOM are rejected (a clean error) | REMAINING ISSUE. Impact: low. Next action: strip a leading BOM in `detect_format`. |

### 4.3 Channel simulator, reads, CLI

| id | type | issue | status |
|---|---|---|---|
| B1 | bug | truncation of a read emptied by indels copied bases from the next read, or crashed (exit 70) | FIXED |
| B2 | bug | shuffle bucket count estimated from file bytes (a VXS input at 10× peaked at 1.6 GB) | FIXED |
| B3 | bug/doc | fixed 8,192-strand batches: memory grew linearly with coverage (30× → 1 GB) | FIXED (bounded by bases; ordinary channels unchanged) |
| B4 | bug | `--report` overwrote any file without `--force`, including the command's own input | FIXED |
| B5 | bug | `.partial` files leaked on errors and Ctrl-C (sequence, cluster) | FIXED |
| B6 | bug | exit 70 for missing, directory or malformed cluster files, bad output paths, missing temp dirs; broken pipes | FIXED |
| B7 | bug | `benchmark generate` onto an existing file exited 3 instead of 8 | FIXED |
| B8 | bug | consensus accepted nonsense parameters (negative fractions, NaN) | FIXED |
| B9 | bug | pipeline leaked its temp dir; `--cleanup all` kept the largest files; late checks | FIXED |
| B10 | bug | experiment ECC statistics used successful trials only (all-failed points showed 0) | FIXED (and the V2 renderer shows n/a) |
| B11 | minor | observed error rates diluted by duplicates | FIXED |
| B12 | limitation | shuffled output depended on the input file format | FIXED |
| B13 | limitation | above 200,000 orphans, clustering depended on read order | FIXED |
| B14 | minor | an explicit missing `--dna-index` was silently ignored | FIXED |
| B15 | minor | compressed or alignment output names (`.gz`, `.bam`) silently written as FASTA | FIXED (refused unless `--format`) |
| B-S1 | suspected | one huge cluster (extreme coverage of one strand) makes a consensus block's alignment matrix large | REMAINING ISSUE: not reproduced. Impact: memory at extreme coverage. Next action: split clusters above a read cap. |

### 4.4 Tests and environment

| id | type | issue | status |
|---|---|---|---|
| T1 | test infra | `from conftest import …` was ambiguous, so `pytest tests/v2 tests/unit` failed at collection | FIXED (`tests/v1_support.py`) |
| P1 | flaky tests | a property test sampled coverage 3, which fails ~8 % of the time (detectably); a fixture scan included `__pycache__` | FIXED (byte-identical to the other session's pending fix) |
| N1 | environment | the README tests skip unless `vnx-dna` is on `PATH` | documented (TESTING.md; CI installs the CLI) |

### 4.5 Documentation claims corrected

| claim (V2 docs) | correction |
|---|---|
| 10 GB at 10× = ~2.3 × 10⁹ reads, ~6 × 10¹¹ bases, ~1.2 TB (LARGE_FILES); ~10¹² bases (README) | ~1.39 × 10⁹ reads, ~3.5 × 10¹¹ bases, ~0.7 TB (from the 10 GB run's strand count) |
| ~2.3 × 10⁸ strands for 10 GB (ECC.md) | 1.39 × 10⁸ (measured) |
| consensus ~4,000 reads/s (BENCHMARKS, LARGE_FILES) | unmeasured; the stage benchmark shows ~18,000 reads/s |
| full reads check the trailer (V2_FORMAT) | only `verify` does; restore relies on per-chunk SHA-256 and the tiling rule |
| indel repair "up to 2·F decodes" (SYNCHRONIZATION) | 4 × hypotheses |
| discovery "tries up to 400 reads" (V2_FORMAT) | scores the first 100 erasure-free frames per orientation |
| fixed coverage "any value in (0, 1000]" (CHANNEL_MODEL) | must be an integer |
| "one indel per read" (README) vs `--max-indel` up to 3 | stated precisely |
| 2²⁸-chunk limit (SECURITY) | 76,695,844 (the trailer's addressable limit) |
| "outputs never overwritten without --force" (CLI.md) | now true, including reports |
| Wilson intervals rendered as [1.000, 1.000] | 4 decimals ([0.9996, 1.0000]) |
| `experiment run` printed "all detected" even with undetected or internal failures | prints each category |

### 4.6 Found while reviewing the V3 changes

| id | type | issue | status |
|---|---|---|---|
| R1 | security | replaying an older, still-authentic checkpoint let a resume reuse an epoch | FIXED (epoch above every epoch in the sidecar, including entries past the checkpoint). **Residual:** a full rollback of the partial file, sidecar and checkpoint together (a restored snapshot), followed by a same-size, same-mtime input change, could still reuse nonces. REMAINING ISSUE, documented in SECURITY.md. Next action: a random per-run nonce component (format feature; ROADMAP). |
| R2 | security | one forged metadata strand at a high stripe raised the new allocation bound | FIXED (every needed group must be decodable before allocation; 22.8 s / 733 MB → 0.4 s / 54 MB) |
| R3 | bug | `verify --report` could not overwrite its own report (no `--force`) | FIXED |
| R4 | bug | insertion bursts were refused though one erased byte suffices | FIXED |
| R5 | bug | rotation3 bursts could leave the byte after the run wrong | FIXED |
| R6 | bug | the recovered manifest's archive tag was not checked against the strands' tag | FIXED |
| R7 | robustness | the hard-link publish fallback missed some errnos | FIXED |
| R8 | bug | `simulate-errors` validated decoder options only inside each trial | FIXED |

### 4.7 Found in the release review (2026-10-01)

A final review before release ran the full suite, an independent harness that drives only the installed CLI (42
round trips of text, binary, random, empty, chunk-boundary and incompressible files through FASTA and VXS, plain and
encrypted, clean and through an 8× noisy channel with consensus; 300 single-bit container flips, 52 truncations,
exact-M and M+1 losses in data and metadata groups, forged FASTA headers, malformed read files, coverage-1 channels),
a 4 GB encrypted store → encode → recover run, and an adversarial review of data-loss, temporary-file, signal and
packaging behaviour. Undetected corruption: 0. Every confirmed issue was reproduced, then fixed with a regression
test that fails on commit `e3aa2d8` (`tests/v3/test_release_review_v3.py`; F1 in `tests/v3/test_cli_v3.py`).

| id | type | issue | status |
|---|---|---|---|
| H1 | data loss | `pipeline --work-dir` overwrote files with intermediate names (e.g. `photo.jpg.vxdna`) without `--force`, and `--cleanup` deleted them | FIXED (refused up front; cleanup deletes only what the run wrote) |
| H2 | data loss | `--report` could replace the key file or the DNA index (`--report key.txt --force`), making a new encrypted archive unrecoverable | FIXED (key files and DNA indexes are inputs/outputs) |
| M1 | robustness | SIGTERM/SIGHUP left partial files, `vnxdna-decode-*` directories and orphaned workers (7 per command); SIGKILL orphaned workers | FIXED (handled like Ctrl-C; workers watch their parent) |
| M1b | robustness | found while validating M1: an interrupt that killed a worker mid-write left `ProcessPoolExecutor.shutdown` waiting forever (`recover` hung after SIGTERM in ~1 of 8 randomly timed runs; stack dump: main thread joining the pool's management thread, which was blocked reading a partial result); Ctrl-C printed one traceback per worker; a worker forked while SIGTERM was pending ran the parent's handler in its initializer and broke the pool (exit 70, 1 of 300 runs); an interrupt during start-up printed a traceback | FIXED (pools are abandoned on interrupt and the command ends with `os._exit(130)` after its cleanup; workers ignore SIGINT and inherited signals; a light entry module). 300 randomly timed SIGTERM/SIGINT runs: all exit 130, no leftovers, no live workers, no tracebacks |
| M2 | security | reports, DNA indexes, cluster files, decoded containers and checkpoints used fixed temporary names opened through symlinks | FIXED (`mkstemp`; a resumable store's fixed names are re-created with `O_EXCL`) |
| M3 | data loss | an input named `<output>.partial` was deleted by `store` and truncated by `decode` | FIXED |
| F1 | bug | `encode` of an empty unencrypted archive published its outputs, then exited 70 | FIXED |
| L1 | robustness | `keygen` wrote through symlinks and truncated the old key when the write failed | FIXED (private temporary file + publish) |
| L2 | data loss | an output could be the input with `--force` (`extract a -o a`, `store f -o f`, `restore a -o a`) | FIXED (refused) |
| L4 | bug | `encode --no-index --force` left a stale DNA index that made `extract` fail | FIXED |
| L5 | bug | `extract` ignored conflicting selections | FIXED (exit 3) |
| L6 | CLI contract | a missing input to `store`, `pipeline`, `simulate-errors`, `experiment run` exited 2, not 3 | FIXED |
| L7 | robustness | key files were read without a size limit (`-k /dev/zero`); loose permissions went unnoticed | FIXED (regular file ≤ 4 KiB; warning) |
| L8 | packaging | `license = {text = …}` stops building with setuptools releases after 2027-02-18; `LICENSE` not in the wheel; no `.dockerignore`; the Docker example failed on a mounted directory owned by another user | FIXED |
| C1 | cosmetic | `reads` printed Python reprs; `pipeline --help` lost its optional stages to Rich markup | FIXED |
| L3 | robustness | two `store` commands writing the same output at the same time share the fixed resumable work files; the slower one can fail with exit 70 (the published archive stayed consistent in the reproduction) | REMAINING (documented in LIMITATIONS.md); next action: an exclusive lock on the checkpoint |
| L7b | behaviour | a malformed `VNXDNA_KEY` is an error (exit 7) even for an unencrypted `restore` | KEPT deliberately (a misconfigured key should never go unnoticed); documented in CLI.md |

**Totals.** 48 code and test defects fixed in the audit: C1–C12, S1–S2, E1, D1–D7, B1–B15, T1, P1 (two tests) and
R1–R8, and 15 more in the release review (§4.7). There were also 13 documentation corrections (C4, D8, §4.5).
Remaining: D-S1, D-S2, D-S3, B-S1, the R1 residual and L3. All are low impact, and none can produce wrong output.

## 5. V3 changes

### Architecture

The components and modules are listed in [ARCHITECTURE.md](ARCHITECTURE.md). The key decisions:

* **Format 5 kept** (AD3-1). V2 archives stay readable, and V2 reads V3 archives with one exception (§9).
* **One vectorised, strictly bounded-distance RS decoder** for every damaged-read path (AD3-2).
* **ECC engine interface** with a registry keyed by the code names the manifest declares (AD3-3). Fountain codes are
  NOT IMPLEMENTED; nothing untested is registered.
* **Burst resynchronisation** as F hypotheses per read (AD3-4).
* **`final-seal-epoch-v3`** as an optional required-feature instead of a new format (AD3-5), and HMAC-bound
  checkpoints (AD3-6).
* **Error sweeps** reuse the experiment trial function (AD3-7).

### New capabilities

| capability | status | evidence |
|---|---|---|
| vectorised RS decoder | PASS | exact inside the bound for 8 (r, n) pairs; strict beyond it; ~33,000 fuzz rows against `reedsolo` in review |
| burst resynchronisation (coverage 1) | PASS | L = 1…29 nt, deletions and insertions, both orientations; a pool that fails without it recovers with it |
| burst channel | PASS | contiguity, counts, determinism, byte identity with v2.0.0 at rate 0 |
| `simulate-errors` | PASS | reproducible across worker counts; 0 undetected corruption in every sweep (§8) |
| multi-archive pools | PASS | two-archive pool decoded per archive by tag |
| batched indel repair | PASS | same results as V2, faster (§8) |

## 6. Test results

| suite | result |
|---|---|
| full suite, V3 (`PATH=.venv/bin:$PATH pytest`) | see the generated block below |
| new V3 tests | 195 (194 in `tests/v3/`, 9 files, 29 of them from the release review, and the README's V3 block in `tests/cli/test_readme.py`), all pass |
| regression tests run against `v2.0.0` | container/security 15 of 16 fail on 2.0.0 (the 16th is a guard that must pass on both); decoder 7 of 7 fail; channel/CLI 19 targeted tests fail; review regressions R1–R8 target V3-only code |
| lint (`ruff check src tests research`, pyflakes rules) | PASS (V1 modules exempt from unused-import fixes: they are byte-identical to 1.0.0) |
| `compileall` | PASS |
| CLI smoke test (`research/v3/cli_smoke.sh`) | PASS: all 27 commands, all recovered files identical; the documented exit codes for a missing file (3), a usage error (2), a missing or wrong key (4), a truncated container (3), an existing output (8), bad parameters (7), unwritable paths (8), and redundancy exceeded (5, no output written); no traceback |
| permission-denied directory (`chmod 555`) | NOT TESTABLE IN CURRENT ENVIRONMENT: the tests run as root, which ignores mode bits. Unwritable paths (`/sys`, a path below a file) were tested instead and exit 8 |
| type checker | NOT IMPLEMENTED (none configured in the project) |
| formatter | NOT IMPLEMENTED (none configured; the existing style was kept) |

<!-- BEGIN GENERATED: v3-audit-results -->
*(generated by `research/v3/render_v3_tables.py` from `research/results/v3/*.json`)*

Measured on this project's machine (8 logical CPUs, Linux-6.8.0-139-generic-x86_64-with-glibc2.39); software simulation only. Versions: vnx-dna 3.0.0 (writes archive format 5 / container v2 / frame 5; reads formats 5 and 4 and legacy V0.1) vs vnx-dna 2.0.0 (writes archive format 5 / container v2 / frame 5; reads formats 5 and 4 and legacy V0.1).

| measurement | result | source (`research/results/v3/`) |
|---|---|---|
| 1 GB: store → restore → encode (VXS) → random access → recover from DNA (V3) | PASS; all stages 0.8 min, peak RAM 559 MiB; SHA-256, `cmp` and random access equal: True (V2: 0.8 min, 630 MiB) | `scale-v3.json, scale-v2.json` |
| 5 GB: store → restore → encode (VXS) → random access → recover from DNA (V3) | PASS; all stages 3.7 min, peak RAM 609 MiB; SHA-256, `cmp` and random access equal: True | `scale-v3.json, scale-v2.json` |
| restore 10MB from coverage-1 reads with 0.006 substitutions per base | V3 2.2 s vs V2 16.0 s (7.37× faster), both exact | `noisy-decode.json` |
| coverage 1, lost/extra-base bursts (all burst-deletion and burst-insertion points) | exact 20/100 with V2 decoding options → 100/100 with V3 burst + indel repair | `sweep-cov1-*.json` |
| error sweeps, every error type (coverage 1 and 5) | 1,310 trials: undetected corruption 0, internal errors 0 | `sweep-*.json` |
| single-read repair of 2 indels | 40/40 correct in 145.69 ms per read (V2: 40/40 in 422.25 ms) | `indel-*.json` |
| VNX-DNA 2.0.0 reading V3 output | plain.vxdna: exit 0 (identical), encrypted.vxdna: exit 0 (identical), resumed.vxdna: exit 6, plain.fasta: exit 0 (identical) | `v2-reads-v3.json` |

Full test suite at commit `cbb9c6a47907` (dirty=False): **601 tests, 601 passed, 0 failed, 0 errors, 0 skipped** in 267 s (`pytest (full suite, vnx-dna on PATH)`). VNX-DNA 2.0.0 collected 406 tests.
<!-- END GENERATED: v3-audit-results -->

## 7. Security summary

What is encrypted and what stays visible, key handling, and the V3 fixes are in [SECURITY.md](SECURITY.md).
Primitives and labels are unchanged from V2, so cryptographic semantics are unchanged. The nonce-uniqueness argument
is now actually enforced on every resume path, except the documented full-rollback residual (R1).

## 8. Benchmarks, memory, performance, error recovery, random access

All generated from `research/results/v3/`:

* storage pipeline 1 MB – 1 GB, V2 vs V3; density; stages: [BENCHMARKS.md](BENCHMARKS.md);
* memory, CPU and disk per stage (and V3 at 5 GB): [LARGE_FILES.md](LARGE_FILES.md#v3-memory-cpu-and-disk);
* noisy-read decoding, V2 vs V3: [BENCHMARKS.md](BENCHMARKS.md#v3-noisy-read-decoding);
* single-read indel repair, V2 vs V3: [SYNCHRONIZATION.md](SYNCHRONIZATION.md#v3-measurements);
* error recovery per error type and rate, coverage 1 (repairs off and on) and coverage 5 with consensus:
  [ERROR_MODEL.md](ERROR_MODEL.md);
* random access (container and DNA, at each size): the random-access columns of the scale table in BENCHMARKS.md.

The summary block in §6 lists the headline figures.

## 9. Compatibility

| direction | status |
|---|---|
| V3 reads V2 containers (plain, encrypted, resumed) and strands | PASS (fixtures from the 2.0.0 release) |
| V3 writes V2-identical archives for fresh stores (apart from `encoder.version`) | PASS |
| V2 reads V3 archives and strands | PASS, except resumed encrypted stores, which V2 refuses with exit 6 (`research/results/v3/v2-reads-v3.json`) |
| V2 checkpoints resumed by V3 | refused with a clear message (by design) |
| V1 and V0.1 archives | PASS (unchanged V1 code and tests) |

## 10. Known limitations and unimplemented features

See [LIMITATIONS.md](LIMITATIONS.md). NOT IMPLEMENTED: molecular primers and PCR selection, in-strand
synchronisation markers or VT codes, fountain codes, cross-chunk interleaving, secondary-structure screening,
password KDF, size padding, GPU acceleration, resume for `encode`/`decode`.

NOT TESTABLE IN CURRENT ENVIRONMENT: physical synthesis, storage and sequencing (no laboratory). A V3 rerun of the
10 GB scale matrix was not performed; V3 was measured up to 5 GB. The 2.0.0 10 GB results remain the reference above
5 GB, since V3 did not change the streaming architecture.

## 11. Reproducibility

See [REPRODUCIBILITY.md](REPRODUCIBILITY.md). In short: `pip install -e '.[dev]'`, then `pytest`,
`research/v3/run_v3_research.py --baseline-python <v2.0.0 python>`, and `research/v3/render_v3_tables.py`. Every
result file records the commit, the dirty flag, the machine and the library versions.
