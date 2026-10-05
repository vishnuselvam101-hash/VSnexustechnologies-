# VNX-DNA V6 Phase 0: baseline audit

Deliverable of the V6 directive, Phase 0 (directive section 1). All channel results in this document are SIMULATED. No DNA was synthesised, stored or sequenced for any result cited here. V6 results are computational/software validation. No physical DNA synthesis or sequencing validation is claimed.

## 0. Scope, sources and evidence classes

**Subject.** The V6 candidate `081697b` (`build/v6-p1`) against the V5 release `v5.0.0` (tag on merge `0d1c285`; release commit `6aef3f4`; `git diff 6aef3f4 0d1c285` is empty, so both have the same tree).

**Later work on the build branch.** `build/v6-sprint` has since added the job #56 fix (`a76d5e7`), the V6 golden fixtures `tests/fixtures/v6_0` (`6725991`, `c684b6b`, merged as `3e4d681`) and native packaging (`456e9de` and merge `f17f837`, which makes pip build all three native kernels). The suite at `f17f837` is **1864 passed, 6 skipped**, ruff clean, 0 mypy errors in `src/vnxdna/v6` (`/root/vnx-dna-lab/results/task-v6-sprint-20261005-073636.log`). The audit below measures `081697b`, not `f17f837`. Where a finding was addressed later, the text says so. Findings are not re-measured at `f17f837` unless stated.

**Sources (the only ones used).**

| Short name | Path | Nature |
|---|---|---|
| VERIFICATION | `/root/vnx-dna-lab/results/v6-audit/VERIFICATION.md` | executed results, with commands and logs, 2026-10-05 |
| INVENTORY | `/root/vnx-dna-lab/results/v6-audit/INVENTORY.md` | static audit with `file:line` references, no tests or benchmarks run |
| BASELINE-NOTE | `/root/vnx-dna-ai/research/2026-10-05-vnx-baseline-and-readiness.md` | claim verification from committed state; the full suite was not run |
| SPEC | `docs/spec/VNX-DNA-SPEC-V6.md` | V6 specification draft (migration and version axes) |
| ARCH | `docs/V6_ARCHITECTURE.md` | target architecture, migration plan, decisions of 2026-10-05 |
| SPRINT-LOG | `/root/vnx-dna-lab/results/task-v6-sprint-20261005-073636.log` | full-suite log at `f17f837` |

**Evidence classes** (directive). Each result below carries one.

| Class | Meaning here |
|---|---|
| VERIFIED | re-run or re-read for this audit with the command or file:line recorded in a source |
| SIMULATED | produced by a software channel model; no physical data |
| THEORETICAL | analytic bound or specification, not executed end to end |
| NOT RUN | not executed; stated so rather than assumed |

Timings are classed **VERIFIED (measured)** with the host-load caveat of section 5. A result that depends on a simulated channel is also SIMULATED.

## 1. Verified facts

All items are from VERIFICATION unless another source is named. Class: VERIFIED.

### 1.1 Provenance

| Item | Value | Source |
|---|---|---|
| V6 candidate | `081697be5dc8da8adb6f8d98f13767eaa1943618`, branch `work/v6-audit` (= `build/v6-p1`), clean | VERIFICATION s0 |
| V5 release | `6aef3f419f385d12b74d65f75d52fcf6203df539`; tag `v5.0.0` on `0d1c285`; same tree | VERIFICATION s0 |
| Hardware | Intel Xeon Gold 6240 @ 2.60 GHz, 8 vCPU (KVM), 31 GiB RAM, 7 GiB swap, Linux 6.8.0-146 | VERIFICATION s0 |
| Software | Python 3.12.3, pytest 8.4.2, xdist 3.8.0, gcc 13.3.0, clang 18.1.3, valgrind 3.22.0 | VERIFICATION s0 |
| Commits since `v5.0.0` | 35, all by `vishnuselvam101-hash`, 0 AI trailers | INVENTORY s1.1 |
| Diff `v5.0.0..081697b` | 151 files, +82,291/-105; `src/` part is 21 files, +3,588/-104 (`v4.0.0..v5.0.0` changed `src/` in 17 files, +2,604/-28) | INVENTORY s1.1 |
| Package version on the V6 tree | `src/vnxdna/_version.py:2` `__version__ = "5.0.0"` | INVENTORY s1.1 |
| Remote state | `origin/main` = `0d1c285`; `build/v6-p1` has no remote ref, so CI has never run on V6 | INVENTORY s1.1 |
| Code size (tracked) | `src/` 23,859 Python lines (106 files) and 1,384 C lines (3 files); tests 12,023 Python lines (87 files); 808 tracked files | INVENTORY s1.1, s1.2 |
| Audit mode | read-only: `git status --porcelain` empty for both checkouts at the end | VERIFICATION s0 |

### 1.2 Test suites

| Item | Result | Class | Source |
|---|---|---|---|
| V6 full suite (`vnx-task verify v6-audit`, `pytest -n 6`) | **1690 passed, 3 skipped, 0 failed** in 242.95 s | VERIFIED | VERIFICATION s1a |
| The 3 skips (`tests/cli/test_readme.py`) re-run with the venv on PATH | **3 passed**; true total 1693/1693 | VERIFIED | VERIFICATION s1a |
| V6 collection | 1693 tests (v6 604, v5 290, v3 194, unit 163, v2 137, v4 122, compat 76, integration 62, cli 29, adversarial 11, property 5) | VERIFIED | VERIFICATION s1a |
| V5 full suite | **1013 passed, 0 skipped, 0 failed** in 279.83 s; 1013 collected | VERIFIED | VERIFICATION s1b |
| Delta V5 to V6 | +680 tests (v6 +604, compat +76); no directory lost tests | VERIFIED | VERIFICATION s1b |
| ruff, mypy on `src/vnxdna/v6` | ruff passes; 0 mypy errors in v6 | VERIFIED | VERIFICATION s1a |
| Later suite at `f17f837` | 1864 passed, 6 skipped | VERIFIED | SPRINT-LOG, last line |

The V5 release has no `tests/compat`, no `tests/v6`, and its `tests/fixtures` holds only `v0_1` and `v2_0`.

### 1.3 Backward compatibility (V6 code at `081697b`)

- `tests/compat`: **76 passed in 4.91 s**. It covers byte-for-byte fixture reproduction, clean and noisy decode, worker-count determinism, corrupted, truncated and garbage inputs failing closed, and a wrong passphrase (VERIFICATION s2). Class: VERIFIED; the noisy fixtures are SIMULATED reads.
- Independent CLI decode of the stored fixtures with `python -m vnxdna.v4.cli decode`, compared with the manifest `container_sha256`: clean strands 8/8 match, noisy reads 8/8 match (after `gunzip -c`; the CLI refuses `.fastq.gz` with exit 3, documented in the fixture README). The V5 release code decodes the same 8 noisy fixtures: 8/8 match. All 13 lines of `SHA256SUMS` verify for `v4_0` and `v5_0`.
- Regenerating the `v5_0` fixtures with the V5.0.0 code gives a `SHA256SUMS` identical to the committed one.

### 1.4 Native kernels

The kernels are `v5/native/align.c` (400 lines, ABI 2), `v6/native/reads.c` (485 lines, ABI 1) and `v6/native/rs.c` (499 lines, ABI 1). All three are loaded with ctypes, none uses the Python C API, and each has a NumPy or reference fallback (INVENTORY s3).

Kernel-level aligner speed-up, re-measured (`taskset -c 7`, 313 nt, 4,096 reads, band 6, loadavg about 11, median of 5; projections identical to the reference): clean ×9.99, 0.5 % + 0.5 % indel ×12.19, 1 % + 1 % indel ×9.77 (VERIFICATION s3). The documented range is ×9.6 to ×11.3. The ×12.19 case lies above it, which VERIFICATION attributes to contention noise. This is a kernel figure, not end to end.

### 1.5 CLI

- `--help` exits 0 on the root command, all 19 subcommands, `channel simulate`, `experiment run` and `experiment reproduce`.
- The round trip (`generate`, `archive`, `inspect`, `verify`, `encode`, `validate`, `channel simulate`, `decode`, `verify`, `extract`) exits 0 at every step. The recovered container is byte-identical to the original and the payload SHA-256 matches. Decode reported SUCCESS, 523 groups, 0 failed, 419,125 reads, peak RSS 137 MB (VERIFICATION s7). The channel is SIMULATED.

### 1.6 Earlier claims that were confirmed

| Claim | Finding | Source |
|---|---|---|
| "1,690 clean at 081697b" | Confirmed as stated: 1690 passed, 3 skipped (see 3.1) | VERIFICATION s8 |
| V5 1013 tests | Confirmed: 1013 collected, 1013 passed | VERIFICATION s8 |
| Aligner ×9.6 to ×11.3 | Consistent with ×9.77 to ×12.19 measured under load | VERIFICATION s8 |
| P1-EXP-01 dropout thresholds (V5 0.07, V6 internal 0.07, rows255 0.13, plan-seq 0.16, adaptive 0.16) | Reproduced exactly on 200 decodes (20 seeds per cell at each threshold and +0.01); 0 false SUCCESS; SIMULATED | VERIFICATION s8 |
| V4 and V5 release facts (tags, 723 V4 tests, native aligner, fallback, backends, 700k fuzz reads with 0 mismatches, 1013 at release) | Confirmed from committed documents; not re-executed | BASELINE-NOTE A.2, A.3 |

The P1-EXP-01 gains need **opt-in** geometries or `outer_plan=adaptive` at the same 25 % redundancy budget. The default V6 encode keeps the V5 threshold of 0.07 (VERIFICATION s8). Class: SIMULATED.

## 2. Unverified claims

| Claim | Why it is not verified | Class | Source |
|---|---|---|---|
| V4 723 tests; V4 1 GiB round trip (encode 96.8 s, decode 244.4 s, 326 MB peak RSS); EXP-0011 decode 24.98 to 10.33 s (×2.42, 1 worker) | Taken from committed V4/V5 documents; the V4 tree was not run | NOT RUN (documented; the channel parts SIMULATED) | BASELINE-NOTE A.2, A.3, B.9 |
| Clang ASan on the V5 aligner at V5.0.0 ("not run: runtime not installed") | Re-run at `081697b` by hand and clean (VERIFICATION s3); the V5.0.0-time statement stays as written | VERIFIED at `081697b` only | BASELINE-NOTE A.3; VERIFICATION s3 |
| Spill-bucket independence (output independent of `RLIMIT_NOFILE`-derived bucket count) | Claimed by the code comments; no test or run found | NOT RUN | INVENTORY s5.6 |
| Whether job #56 also affects V4/V5 pools with `--select` and smart/soft decoding | Hypothesis from reading `decoder.py:693-711, 1184-1192, 1271` | NOT RUN | INVENTORY s6 |
| Whether `mypy` was run locally before commit `dc0b587` | No mypy config in the repo and no CI step | NOT RUN (unknown) | INVENTORY s8 item 6 |
| Non-x86 builds of `rs.c` (scalar fallback path) | Never built or tested; the baseline states x86-64 only | NOT RUN | INVENTORY s3.3, s3.4 |
| MSan on any kernel | Needs an MSan-instrumented CPython and NumPy, which this host lacks | NOT RUN | VERIFICATION classification summary |
| libFuzzer for the RS decoder and the aligner | `vnx-fuzz` has only a `reads` harness; those two kernels are covered by differential stress fuzzers only | NOT RUN | VERIFICATION s4 |
| Any physical performance (synthesis, storage, sequencing, retrieval of real DNA) | No wet-lab or real-sequencing data exists in the codebase; the physical interface holds schemas and a validator only (`experiments/v6/physical/`, example labelled `synthetic_software_test`) | NOT RUN | BASELINE-NOTE B.11 |
| Channel realism: `illumina-like` and `nanopore-like` models | Documented as not fitted to any measured platform | SIMULATED, unfitted | BASELINE-NOTE B.4 |
| Productivity of the automated workforce | One pilot only; "not proven" in its own validation document | NOT RUN | BASELINE-NOTE C.1 |

## 3. Discrepancies between earlier claims and the evidence

| # | Earlier statement | What the evidence shows | Source |
|---|---|---|---|
| 1 | "1,690 clean" (V6 suite) | 1690 passed plus 3 skipped. The skips are PATH-dependent README tests ("needs the installed vnx-dna on PATH and bash"). With the venv on PATH, 1693 of 1693 pass. The two figures are the same suite counted with and without PATH | VERIFICATION s1a, s8 |
| 2 | V5 "839 tests" | Not a whole-suite figure at `6aef3f4`. The whole suite is **1013**. 839 equals 723 V4 + 116 Phase 2 tests, the V5 count after Phase 2 (`docs/V5_COMPLETION_REPORT.md:92`); at that time 836 ran in one run and 3 README tests were PATH-dependent | VERIFICATION s8; BASELINE-NOTE A.3 |
| 3 | "30 commits since v5.0.0" | `git log v5.0.0..HEAD` shows **35**. The earlier table folded 5 commits into its last row | INVENTORY s8 item 2 |
| 4 | V5 "reduced memory" | True for the aligner only (peak growth about one third of V4: 19.2 to 6.0 MB at 313 nt). End-to-end decode peak RSS was unchanged (167 MB vs 167 MB at 1 worker; 194 to 178 MB at 8 workers). The claim must be scoped to the aligner | BASELINE-NOTE A.3 |
| 5 | V4 "1 GiB" large-scale result | The 1 GiB round trip is a clean-channel run. The largest noisy run is 16 MiB (EXP-0011). V6 repeated a clean 1 GiB run in this audit (section 5); no noisy run at that size exists | BASELINE-NOTE A.2; VERIFICATION s6 |
| 6 | V6 native benchmarks as release-grade evidence | Both committed native benchmarks (`benchmarks/v6/native_reads/results/bench_reads.json`, `benchmarks/v6/native_rs/results/bench.json`) were produced at `8e517ef` with `git_dirty: true`, before the kernels were committed. Their provenance is weaker than the repository's own standard. This audit re-measured the aligner and ran the whole-decode comparison from a clean tree (section 5) | BASELINE-NOTE A.4 |
| 7 | `docs/V6_PHASE1_REPORT.md` cited by the CHANGELOG entry "Unreleased: V6 Phase 1" | The file does not exist at `081697b`. Not resolved by this document | BASELINE-NOTE A.4; INVENTORY s8 item 7 |
| 8 | A V6 build reports `5.0.0` | `_version.py:2` is `5.0.0` and `vnx version` prints `"vnx": "5.0.0"` on the V6 candidate; containers record `encoder.version` = `5.0.0`. A V6 output cannot be told apart from a V5 one by software version. SPEC section 4.2 specifies `6.0.0.dev0` for Phase 2 | INVENTORY s4; VERIFICATION s7; SPEC s4.2 |
| 9 | `vnx-task verify` builds the native kernels in the audited tree | It runs the build step without `PYTHONPATH`, so the editable install resolves to `/root/vnx-dna-lab/v6/src` and the kernels were built into that tree. The audit avoided it by exporting `PYTHONPATH`. This is a defect in the task tool, since reported fixed; the fix is **not verified here**. The SPRINT-LOG still lists the three `.so` paths under `/root/vnx-dna-lab/v6/src` | VERIFICATION s1a; SPRINT-LOG header |
| 10 | The mypy "legacy errors" count | 49 errors in 8 files without `PYTHONPATH`, 0 with `PYTHONPATH=src` (mypy then silences followed modules). The 49 errors are real. The v6 gate (0 errors in v6) holds both ways. The sprint log reports 51 legacy errors at `f17f837` | VERIFICATION s1a; SPRINT-LOG |
| 11 | Valgrind PASS in `benchmarks/v6/native_reads/sanitizers.txt` | The valgrind step in `sanitizers.sh` fails on this host (exit 1, `FATAL: in suppressions file "/dev/null" near line 1: expected '{'`) because `/dev/null` is a regular file here (48 bytes of shell-snapshot output, rewritten repeatedly, owner changing). This is a host defect, not a code defect: the same step with an empty suppressions file passes (135 passed, 36 deselected, exit 0). **A person needs to repair `/dev/null` on this host** (VERIFICATION proposes `rm /dev/null && mknod -m 666 /dev/null c 1 3` as root, after finding what recreates it). Anything that reads or discards into `/dev/null` is affected | VERIFICATION s3 |
| 12 | `vnx-fuzz` and `vnx-security-scan` default to the right tree and output | `vnx-fuzz` defaults to `WT=/root/vnx-dna-lab/v6` (the work-in-progress tree); the audit overrode it with `WT=`. `vnx-lab sanitize` defaults to the same tree and runs only the V5 aligner script. The `vnx-security-scan` default `--out` would have overwritten the comparison baseline `/root/VNX-Vault/Builds/security-2026-10-05.md`; the audit redirected it | VERIFICATION s3, s4, s5 |
| 13 | Bench path and CLI agree on noisy decode time | They do not, and this is not root-caused. For V5, the bench-path noisy 10 MiB decode (17.8 s, channel seed 12345) is much slower than the CLI decode of a different reads file (10.4 s, seed 777). For V6 the bench path is faster (5.8 s vs 8.5 s). The reads files and seeds differ, and the bench calls `decode_reads(DecodeOptions(profile, workers))` directly rather than through the CLI configuration defaults. Neither path should serve as the regression baseline until this is investigated | VERIFICATION s6 |
| 14 | Docs describe the current product | `docs/CLI.md` documents only the V3 `vnx-dna` CLI; the package docstring names the V1 `vnxdna.api`; `docs/ARCHITECTURE.md` and `docs/RANDOM_ACCESS.md` describe V3. The current CLI `vnx` (V4+) has no reference page | INVENTORY s5.4, s8 item 5 |
| 15 | `docs/SECURITY.md:62` says CI honours `.gitleaksignore` | `.github/workflows/ci.yml` has no gitleaks step | INVENTORY s8 item 3 |
| 16 | `native_rs.py:25-27` implies a pip-built RS extension may exist | At `081697b`, `setup.py:10-11` built only the V5 aligner. Native packaging on `build/v6-sprint` (`f17f837`) now builds all three kernels | INVENTORY s1.3, s8 item 4; section 0 |
| 17 | "No readiness documents exist" (earlier note) | Both documents named in the note exist in `/root/vnx-dna-ai/` at audit time; they were probably written after the note | INVENTORY s8 item 1 |
| 18 | The workforce status test count of 1781 | A work-in-progress tree count (includes 88 untracked perf-gate tests). The committed state at `081697b` is 1693 | BASELINE-NOTE A.5, discrepancy 1 |

## 4. Architecture as it is at `081697b`

Class: VERIFIED by static analysis (INVENTORY s2; no tests were run for that part). Line references are in INVENTORY.

```
             +-------------- vnx CLI (v4/cli.py): also orchestrates archive-before-encode, verify-after-encode,
             |               decode -> extract, report/events files, redundancy-profile resolution, budgets
             v
 ARCHIVE  v4/archive.py -- chunking + dedup -- zstd (container/compression, bounded reader)
             |                 -- AES-256-GCM (v4/crypto) -- Merkle (v4/merkle)
             |                 +------------ VNX4 container (v4/container.py) ------------+
             v
 ENCODER  v4/encoder.py --(opt.v6)--> v6/encoder.py + v6/outer.py (stripes, column parity, interleave, superblock v2)
             |   outer: v4/codecs.CauchyRSCodec -> ecc/cauchy (+ v2/encoder.cauchy_parity) | LT fountain (experimental)
             v
 FRAME    v4/frame.py: header/CRC (v2/crc) | scrambler SHAKE-128 | inner RS parity (ecc/inner_rs matrix)
             |         | 2-bit map | markers.   constraints: v4/constraints.py (screening by scrambler variant,
             |         not constrained coding)
             v
 STRANDS  v2/strandio.StrandWriter (FASTA/FASTQ)      SIMULATOR v4/channel.py (+ v6/loss.py, experiments/v6/channel/*)
             v reads
 DECODER  v6/native_reads | v4/reads -> v4/decoder._process -- orientation -- v4/sync TemplateAligner -- v5/native align.c
             |                  +- v5/indel (smart) - v5/soft (GMD/Chase) - v4/frame.decode_frames
             |                  - v6/native_rs | v4/rs_fast | ecc/rs_batch
             v spill buckets
          pass 2: superblock -> duplicates -> consensus -> outer rows -> v6/decode StripeRecovery (columns)
                  -> SHA-256 check -> open_container -> atomic publish
          cross-cutting: v6/recovery (planner, budgets), v6/observe (events), v4/errors + errors (exit codes)
```

Layer assessment against the target layering (INVENTORY s2.7):

- **Present, fused into `v4`:** archive, encryption, channel coding, synchronization, DNA encoding, simulator, observability.
- **Partial:** API (no V4+ Python API; callers import `v4.archive`, `v4.encoder`, `v4.decoder` directly), streaming (decode streams, there is no streaming API, inputs must be paths), physical abstraction (schemas under `experiments/`, outside the package).
- **Absent:** providers.
- The codec core is `v4/decoder.py` (1,524 lines) plus `v4/encoder.py`, with V5 and V6 behaviour attached through lazy imports. The `vnx` CLI also performs pipeline work.

Code distribution by package (Python lines; V6 also 984 C, V5 400 C): v2 7,092; v4 7,077; package root 2,535; v6 1,982; v5 1,648; container 1,000; ecc 691; dna 635; storage 475; legacy 382; v3 247; sync 95 (INVENTORY s1.2). About 12k lines (V1 to V3) serve only the legacy `vnx-dna` CLI.

## 5. Dependency map

### 5.1 Package import graph (python `ast`, module-level and function-level; INVENTORY s2.2)

Class: VERIFIED (static).

```
v4 -> v5: 12    v4 -> v6: 12    v4 -> v2: 5    v4 -> ecc: 8    v4 -> container: 1
v5 -> v4: 8
v6 -> v4: 21    v6 -> v2: 2     v6 -> ecc: 1
v3 -> v2: 8     v2 -> api (V1): 1
cli (V3) -> v2: 10, v3: 1, cli_v1: 1
```

Module edges that make the cycles:

- `v4.decoder` imports `v5.indel.{recovery,path,consensus}`, `v5.soft.{decoder,frames,symbols}` and `v6.{decode,native_reads,observe,recovery}`.
- `v4.encoder` imports `v6.{encoder,outer}`; `v4.codecs` imports `v6.native_rs`; `v4.sync` imports `v5.native_alignment`; `v4.cli` imports `v6.{errors,observe,profiles,recovery}`.
- `v5.indel.*` and `v5.soft.decoder` import `v4.{frame,sync,errors}`.
- `v6.encoder` imports `v4.{encoder,codecs,container,frame,constraints,util}`; `v6.native_reads` imports `v4.reads`; `v6.native_rs` imports `v4.rs_fast`.

**Cycles v4 <-> v5 and v4 <-> v6** exist. They are broken only by function-level imports, for example `v4/decoder.py:40` imports `v6.native_reads` at top level while `v6.native_reads` imports `v4.reads`, and `v4/encoder.py:129,292` imports v6 lazily. The `v4` package is therefore not a frozen baseline: V5 and V6 behaviour is patched into it in place.

Modules not imported by any `src` module: `vnxdna.__main__`, `v4.cli` (the entry point), `v6.loss` (used by experiments and tests only), and the empty `dna`, `storage`, `sync` and `v3` `__init__` files. `v4/config.py:28` imports `ChannelConfig`, so the decode configuration path loads the simulator module (a harmless coupling).

### 5.2 Runtime dependencies (`pyproject.toml`; INVENTORY s1.6)

| Dependency | Pin | Used for | Note |
|---|---|---|---|
| numpy | >=2.0,<3 | vectorised codec, GF tables, spill files, reference kernels | everywhere |
| cryptography | >=44,<47 | AES-256-GCM, HKDF, scrypt (`v4/crypto.py`); V1 to V3 crypto and legacy Fernet | the `<47` pin keeps 4 known advisories, including bundled-OpenSSL GHSA-537c-gmf6-5ccf (fixed in 48.0.1); triaged "not reachable" in `docs/SECURITY.md:68-80` |
| zstandard | >=0.23,<1 | chunk compression (`v4/archive.py`, `container/compression.py`) | |
| reedsolo | >=1.7,<2 | V1 inner RS, legacy v0.1 | still on the `vnx` import path: `v4/codecs.py:36` imports `ecc.inner_rs._parity_matrix` and `ecc/inner_rs.py:24` imports reedsolo at module top |
| pydantic | >=2.10,<3 | V1/V2 manifests only | not used by `vnx` |
| typer | >=0.15,<1 | all three CLIs | |
| dev: pytest | >=8,<9 | tests | `<9` keeps PYSEC-2026-1845, triaged in `docs/SECURITY.md` |
| dev: pytest-cov, hypothesis, ruff | ranges | test and lint | |
| dev: jsonschema | >=4.18,<5 | optional cross-check (`tests/v6/physical/test_physical_interface.py:60-61`, `importorskip`) | `experiments/v6/physical/validate.py` has its own validator |

Build and tooling: setuptools>=77, Python >=3.12 (`pyproject.toml`). The three native kernels need a C compiler (`$CC`, cc, gcc or clang; `-O3 -std=c11 -fPIC -shared -Wall -Wextra -Werror` for in-place builds). At `081697b` only `align.c` was a pip extension (`setup.py:10-11`, optional); `reads.c` and `rs.c` were built in place only. A pip or Docker install at that commit therefore ran the NumPy parser (×4.81 slower per the committed dirty-tree benchmark) and the NumPy RS (×1.55 slower end to end), with only a log warning. Native packaging at `f17f837` addresses this and reports a 2.04x installed-path decode (MEASURED, SIMULATED, per the merge message; not re-verified here).

Not part of the package: security tooling (gitleaks, pip-audit, bandit, cppcheck) runs from `/root/vnx-dna-ai/tools/vnx-security-scan`. The repository holds only `.gitleaksignore` (5 reviewed fingerprints).

### 5.3 Duplicate implementations (INVENTORY s5.3)

Five channel simulators (`channel.py`, `v2/sequencing.py`, `v4/channel.py`, `v6/loss.py`, `experiments/v6/channel/channel.py`); four inner-RS implementations (`ecc/inner_rs.py`, `ecc/rs_batch.py`, `v4/rs_fast.py`, `v6/native/rs.c`); three CLIs (`cli_v1.py`, `cli.py`, `v4/cli.py`); three crypto, constraint and container implementations (V1, V2, V4). Backend choice is spread over `VNXDNA_RS_BACKEND` and the separate `VNX_RS_REFERENCE=1`.

## 6. Performance baseline

Class: VERIFIED (measured). Channel rows: SIMULATED. Every figure is copied from VERIFICATION s6 (`bench/summary-table.md`, logs in `/root/vnx-dna-lab/results/v6-audit/bench/`).

**Host load caveat.** Other sessions ran heavy jobs throughout the audit (another audit's `pytest -n 6`, a competitor benchmark, a native-packaging benchmark). Load average was 3.3 to 15.6 against 8 vCPU. All timings are **contended**. Ratios are indicative, not exact. The load average of every run is in the last column.

**Method.** `vnxdna.v4.bench.isolated("end_to_end", ...)`, the function behind `vnx benchmark`: a fresh spawned process per case, driven by `bench_driver.py`. Profile `balanced`, 4 workers, layout `v4-balanced`, outer code Cauchy RS 64+16, V6 opt-in features off (the default at both versions). Data: pattern `mixed`, seed 42. **The pattern is compressible**: zstd stores 1 MiB as a 289 KB container, 10 MiB as 5.38 MB, 100 MiB as 50.5 MB. All MB/s are input MB/s (decimal). Medians over 3 runs for 10 MiB and below; 1 run for larger sizes; spread is [min-max]. Noisy channel (SIMULATED): `experiments/v6/channel/models/illumina-like.json` v1.0.0, seed 12345; substitution 0.003, insertion and deletion 5e-5, coverage 10 Poisson, duplication 0.02, GC bias 0.3, N rate 5e-4, reverse-complement rate 0.5, dropout 0. This model is not fitted to any measured platform. V6 native backends: aligner native, reads parser native, RS `avx2`. V5: aligner native only.

| version | case | n | encode s | encode MB/s | decode s | decode MB/s | channel sim s | peak RSS MB | status | loadavg |
|---|---|---|---|---|---|---|---|---|---|---|
| V5 | clean 1 MiB | 3 | 0.57 [0.55-0.58] | 1.85 | 0.56 [0.53-0.58] | 1.89 | n/a | 77.6 | SUCCESS x3 | 3.6-3.7 |
| V6 | clean 1 MiB | 3 | 0.55 [0.52-0.55] | 1.91 | 0.55 [0.55-0.57] | 1.89 | n/a | 78.7 | SUCCESS x3 | 3.5-3.7 |
| V5 | clean 10 MiB | 3 | 1.21 [1.15-1.23] | 8.68 | 1.75 [1.72-1.76] | 6.00 | n/a | 177.9 | SUCCESS x3 | 3.7-5.2 |
| V6 | clean 10 MiB | 3 | 1.16 [1.13-1.18] | 9.02 | 1.45 [1.43-1.45] | 7.26 | n/a | 176.6 | SUCCESS x3 | 5.1-5.5 |
| V5 | clean 100 MiB | 1 | 5.91 | 17.75 | 11.70 | 8.96 | n/a | 323.8 | SUCCESS | 6.0-8.1 |
| V6 | clean 100 MiB | 1 | 5.95 | 17.62 | 9.16 | 11.45 | n/a | 319.6 | SUCCESS | 6.0-6.8 |
| V6 | clean 1 GiB | 1 | 58.91 | 18.23 | 92.88 | 11.56 | n/a | 314.3 | SUCCESS (182 s wall) | 4.9-7.4 |
| V5 | illumina-like 1 MiB (SIMULATED) | 3 | 0.55 [0.55-0.56] | 1.90 | 1.30 [1.30-1.34] | 0.80 | 1.57 | 253.4 | SUCCESS x3 | 5.2-5.5 |
| V6 | illumina-like 1 MiB (SIMULATED) | 3 | 0.56 [0.51-0.56] | 1.89 | 0.91 [0.89-0.95] | 1.15 | 1.60 | 253.3 | SUCCESS x3 | 5.5-6.6 |
| V5 | illumina-like 10 MiB (SIMULATED) | 3 | 1.84 [1.78-1.96] | 5.70 | 17.83 [17.69-18.19] | 0.59 | 24.51 | 263.8 | SUCCESS x3 | 5.2-15.1 |
| V6 | illumina-like 10 MiB (SIMULATED) | 3 | 1.36 [1.14-1.56] | 7.70 | 5.79 [5.56-6.84] | 1.81 | 16.71 | 269.2 | SUCCESS x3 | 10.5-13.9 |

How VERIFICATION reads these numbers:

- V6 vs V5 clean decode: ×1.21 at 10 MiB, ×1.28 at 100 MiB. Noisy decode (SIMULATED): ×1.43 at 1 MiB and ×3.1 at 10 MiB (×2.1 in an earlier contended pair, loadavg 6.5 to 15.6, V5 17.91 s vs V6 8.66 s, kept as `bench/*-illumina-10MiB.contended.*`). Clean encode is unchanged; the noisy encode medians differ only through load.
- The 1 GiB clean run is a single run. There is no noisy run at that size.
- The 1 MiB end-to-end rows of the repository's own suite (`bench/V5|V6-vnx-benchmark*`) are single runs under load.
- Discrepancy 13 (section 3) applies: the bench path and the CLI path disagree and the cause is open. Use these figures as an indicative baseline, not as a regression gate.

**Stage breakdown** (VERIFIED, from `bench/V5|V6-vnx-benchmark*`, `python -m vnxdna.v4.cli benchmark --profile balanced --size 1MB`, single core): `inner_rs_decode_noisy_frames_s` is 55,093 (V5) vs 548,153 (V6), ×9.9 from the native RS; `frame_check_clean_frames_s` is 572k vs 919k; the other stages are within noise.

**CLI decode of one 10 MiB illumina-like reads file** (1.07 GB FASTQ, channel seed 777, `-w 4 --report`, 3 runs each; SIMULATED; `profile/01-decode-times.txt`, `profile/02-stage-seconds.txt`):

| version | wall s | pass1_reads s | pass2_decode s | verify s | RSS MB |
|---|---|---|---|---|---|
| V5 | 10.47 / 10.65 / 10.82 | 8.7 to 9.0 | 0.86 to 0.89 | 0.02 | about 166 |
| V6 | 7.84 / 9.04 / 9.48 | 6.1 to 7.7 (pass-1 worker CPU 14.9 to 15.5 s) | 0.94 to 1.15 | 0.02 | about 141 |

Both outputs are byte-identical to the original container.

**Profile of a V6 10 MiB noisy decode** (`profile/04-pyspy.txt`, py-spy `--subprocesses`, 3,948 samples; SIMULATED input): `v4/decoder.py:343 _process` 9.8 %; `v2/crc.py crc32_rows` 5.7 %; `v6/native_reads iter_reads_native` 5.1 %; `v4/decoder.py _try` (lines 200 to 218 together) about 14 %; `v4/sync.strip_markers_exact` 3.5 %; `v6/native_rs._run` 3.4 %; `v5/native_alignment.align_usable` 3.1 %; pickling/IPC `_send`/`_recv` about 7 %. cProfile sees only the main process, which mostly waits on the worker pool (`select.poll` 4.3 s).

**Fuzz and differential speed (kernel level):** differential native-vs-reference runs of the shipped -O3 builds took 48.4 s (aligner, 100,000 reads), 31.7 s (parser, 20,000 cases) and 19.2 s (RS, 600 rounds, 126,102 comparisons); these are correctness runs, not speed benchmarks.

## 7. Memory baseline

Class: VERIFIED (measured). Peak RSS in the performance table is the maximum of the parent and the largest single worker, from `getrusage` (VERIFICATION s6). The decode report's `peak_rss_bytes` is the parent process only; worker RSS is not captured (INVENTORY s7.2).

| Case | V5 peak RSS MB | V6 peak RSS MB | Source |
|---|---|---|---|
| clean 1 MiB | 77.6 | 78.7 | VERIFICATION s6 |
| clean 10 MiB | 177.9 | 176.6 | VERIFICATION s6 |
| clean 100 MiB | 323.8 | 319.6 | VERIFICATION s6 |
| clean 1 GiB | not run | 314.3 | VERIFICATION s6 |
| illumina-like 1 MiB (SIMULATED) | 253.4 | 253.3 | VERIFICATION s6 |
| illumina-like 10 MiB (SIMULATED) | 263.8 | 269.2 | VERIFICATION s6 |
| CLI decode, 10 MiB reads file (SIMULATED) | about 166 | about 141 | VERIFICATION s6 |
| CLI round trip decode, 2 MB mixed file (SIMULATED) | n/a | 137 (report) | VERIFICATION s7 |

- At 1 GiB clean, peak RSS is 314.3 MB against 319.6 MB at 100 MiB. Memory is flat across that range, consistent with the bounded-memory streaming added in `8e517ef`. The V6 test of that claim is a clean-channel run only.
- At 10 MiB noisy through the bench path, V6 peak RSS (269.2 MB) is 5.4 MB above V5 (263.8 MB). Through the CLI decode path V6 is lower (about 141 vs about 166 MB). Different paths, different reads files; see discrepancy 13.
- V6 libFuzzer run peak RSS: 433 MB (parser harness). V6 full suite `maxrss` 3.67 GB (`vnx-task verify`, `pytest -n 6`).
- Earlier documents give the V4 1 GiB clean round trip at 326 MB (BASELINE-NOTE B.9; documented, not re-run) and the V5 aligner peak growth at 6.0 MB vs 19.2 MB for V4 (aligner only; end-to-end decode RSS unchanged, discrepancy 4).
- Not measured: noisy decode above 16 MiB (V4 documents) or above 10 MiB (this audit); memory under a read pool larger than one 10 MiB reads file.

## 8. Security baseline

### 8.1 Scan results (`vnx-security-scan` at `081697b`)

Class: VERIFIED. Output was redirected so the vault baseline was not overwritten (VERIFICATION s5).

| Scan | CRITICAL | HIGH | MEDIUM | LOW | INFO | Time |
|---|---|---|---|---|---|---|
| Fast (`--fast`) | 0 | 0 | 1 | 39 | 0 | 8.2 s |
| Full (adds gitleaks history and pip-audit) | 0 | 0 | 10 | 39 | 5 | 13.6 s |
| Vault report at `5c39a50` (fast; comparison baseline) | 0 | 0 | 1 | 37 | 0 | n/a |

- **New vs the vault report:** 2 LOW, both `B101 assert` at `src/vnxdna/v6/decode.py:39` and `:48`. None removed.
- **Triaged MEDIUMs.** The fast-scan MEDIUM is the cppcheck `uninitvar Lv` at `align.c:322`; reading the code, it is a false positive (the `for l < LANES` loop assigns every lane before use; `docs/SECURITY.md:80`). The 9 extra MEDIUMs in the full scan are dependency advisories: cryptography 46.0.7 (PYSEC-2026-3552, -3553, -3554 and GHSA-537c-gmf6-5ccf) and pytest 8.4.2 (PYSEC-2026-1845). They are triaged in `docs/SECURITY.md` (commit `c41f83e`, "none reachable") and kept by the pins `cryptography<47`, `pytest<9`. The 5 INFO findings are the reviewed test-only keys in `.gitleaksignore`.
- These are scanner results plus a documented triage. They are not a penetration test or an independent review.

### 8.2 Native safety matrix

Class: VERIFIED, all on synthetic software inputs (INVENTORY and VERIFICATION s3).

| Kernel | GCC ASan+UBSan | Clang ASan+UBSan | Clang UBSan trap (integer, bounds, nullability) | valgrind | ASan canary | Differential fuzz (sanitized) |
|---|---|---|---|---|---|---|
| aligner `v5/native/align.c` | tests/v5 290/290; stress 100,000 reads (400 rounds x 250), 0 mismatches | run by hand (not in the repo script): 290 passed, 100,000 reads, 0 mismatches, 0 reports | 290/290; 0 mismatches | not in script | fired | 0 mismatches |
| reads parser `v6/native/reads.c` | 184 passed; stress 20,000 cases / 40,000 comparisons, 0 mismatches | 184 passed, 0 mismatches | 184 passed, 0 mismatches | script failed (host defect, discrepancy 11); by hand: 135 passed, 36 deselected, exit 0, no invalid read or write | gcc and clang fired | 0 mismatches |
| RS decoder `v6/native/rs.c` | 119 passed; stress 600 rounds, 42,034 words, 126,102 comparisons over scalar/avx2/avx512, 0 mismatches | 119 passed, 0 mismatches | 119 passed, 0 mismatches | not in script | gcc and clang fired | 0 mismatches |

RS compile warnings: gcc and clang `-O3 -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wpedantic` give 0 warnings each, and `gcc -fanalyzer` gives 0. Differential runs with the shipped -O3 builds: 0 mismatches for the aligner (100,000 reads), the parser (20,000 cases) and the RS decoder (126,102 comparisons, 3 SIMD levels).

**MSan: NOT RUN** (needs an MSan-instrumented CPython and NumPy; not available on this host). **Non-x86 builds: NOT RUN.** **libFuzzer for the RS decoder and the aligner: NOT RUN.**

### 8.3 Fuzzing

Class: VERIFIED.

- Native libFuzzer (`WT=/root/vnx-dna-lab/v6-audit vnx-fuzz native 300`; clang `-fsanitize=fuzzer,address,undefined`; target `reads`, i.e. `v6/native/reads.c`): **1,873,071 executions in 301 s (6,222 exec/s)**, cov 194, ft 1211, 36 new units, corpus 2468 to 2504 files, **0 crashes**, 0 timeouts, peak RSS 433 MB, commit `081697b`.
- Python (`vnx-fuzz python 5`): 5 rounds, 0 failed rounds, 34.6 s. Each round runs 16 tests (`tests/adversarial` and `tests/property`) with a fresh `--hypothesis-seed`. This is a thin campaign, and the seeds of passing rounds are not logged.
- Only one native target has a libFuzzer harness.

### 8.4 Known security-relevant weaknesses (static findings, INVENTORY s3.4, s5.5, s5.4)

- The three native library paths are read from environment variables (`VNXDNA_NATIVE_LIB`, `VNXDNA_READS_LIB`, `VNXDNA_RS_LIB`) and load arbitrary shared objects. INVENTORY rates this LOW (local-user trust boundary).
- Stale in-place `.so` files are found by fixed name and checked only by an ABI integer, with no source hash.
- Buffer lengths are implicit contracts and not covered by an ABI number: align output buffers (`native_alignment.py:223-238`) and the reads `info[4]` and `ctx[40]` arguments (`native_reads.py:245-246`, `reads.c:28`).
- The exported RS test hook `vnx_rs_restrict_levels` mutates process-global dispatch (`rs.c:452-453`); it is not thread-safe.
- Runtime builds use `-Werror`: a new compiler warning turns into a silent fallback under `auto`.
- Decoding an encrypted archive from DNA reads returns SUCCESS without a key (it publishes the ciphertext container). Decryption happens only at `extract`, which fails closed.
- The unencrypted `archive_id` is derived from options, paths and sizes only, so two datasets with the same names and sizes share an archive ID and DNA tag. The SHA-256 check still fails closed.
- CI has no gitleaks, bandit, pip-audit, cppcheck, mypy, sanitizer or non-x86 job, and CI has never run on V6.
- `vnx decode x --performance bogus` ends with `INTERNAL_ERROR`, exit 70 (`KeyError`), instead of a configuration error.

## 9. Known limitations

Each item is stated as found; none is mitigated by this document.

**Physical validation.**
- **There is no physical validation.** No DNA was synthesised, stored or sequenced. All results are SIMULATED or SYNTHETIC SOFTWARE TEST. The physical interface is schemas and a validator only (BASELINE-NOTE B.11).
- Channel models, including `illumina-like` and `nanopore-like`, are not fitted to measured platforms. Position-dependent errors, synthesis truncation, chimeras, strand breakage and PCR dynamics are not modelled (BASELINE-NOTE B.4).

**Codec and format.**
- Constraints are enforced by screening up to 256 scrambler variants, not by constrained coding. Secondary structure and melting temperature are not checked. No primers or PCR random access exist. There are no LDPC or polar codes and no indel-correcting code (VT, HEDGES-like); indels become erasures through markers. Reads are grouped by decoded address, with no clustering of unaddressed reads (BASELINE-NOTE B.1, B.3, B.5).
- BAM input is not supported. Third-party codec datasets cannot be decoded; the decoder reads VNX4 frames only.
- The V6 gains in P1-EXP-01 to -03 require opt-in geometries; the default V6 encode keeps V5 behaviour and its 0.07 dropout threshold (SIMULATED).
- Version information is partial: the package says 5.0.0 on V6 output; DNA records only the superblock version (1 or 2); reports, events and `vnx encode` output carry no schema or software version; `frame_version` in `vnx version` is hard-coded (INVENTORY s4, s7.2). The frame-version mismatch is reported as a retryable decode failure ("no superblock could be decoded", exit 5), which suggests adding coverage when the real cause is an unsupported frame.
- `locate --dna-profile` assumes the V4 geometry and sequential order and is wrong for V6 interleaved or column-parity output and for custom `-K/-M` (INVENTORY s5.5).
- The unknown-key behaviours: `vnx channel simulate -c` expects a flat channel dict while `encode` and `decode -c` take a sectioned configuration (exit 7 on mismatch). The `--config` docstring example is stale (`marker_period 32, marker_len 2` against the default 24/3). `vnx encode` on a non-container ignores archive options (INVENTORY s5.4).
- "Profile" means four different things; `balanced` exists in two sets (INVENTORY s5.6).
- Random access: pass 1 parses, aligns and RS-decodes every read; there is no byte-range, chunk-level or strand-range selector in `vnx`; molecular (primer) random access is absent (INVENTORY s6).
- No key management (rotation, KMS/HSM, multi-recipient, escrow); no snapshot or incremental append; no REST service, object store or FUSE; no streaming API.

**Job #56.** At `081697b`, `vnx decode --select` could fail with "no superblock could be decoded" on V6 stripe archives (depth 4, column parity 2) at coverage 2 to 3 where the full decode succeeds. INVENTORY s6 records the suspected cause (round S skipped in select mode; UNVERIFIED hypothesis). Commit `a76d5e7` on `build/v6-sprint` ("random access runs deferred round S before decoding the superblock (job #56)") addresses it. The effect of that commit is not measured in this document; the SPRINT-LOG shows only the full-suite result.

**Native code.**
- `align.c` has no runtime CPU dispatch (GCC vector extensions, baseline ISA). `rs.c` auto-selects AVX2; AVX-512 runs only when forced (0.94x AVX2 in the committed benchmark). Non-x86 is untested.
- Two committed native benchmarks come from a dirty tree (discrepancy 6).

**Process and tooling.**
- CI covers one job (Ubuntu, Python 3.12, `vnx-dna --help`, ruff rule set `F` only, pytest, compileall). `vnx` itself is not smoke-tested in CI.
- At `081697b`, `pyproject.toml` has no mypy configuration. The mypy gate lives in the task script.
- Docs: no `vnx` CLI reference; `docs/V6_PHASE1_REPORT.md` is missing; seven environment variables are undocumented or partly documented (INVENTORY s5.4). `docs/COMPATIBILITY.md`, `docs/STORAGE_FORMAT.md` and `docs/CLI.md` consistency belongs to the Phase 2 documentation steps, not to this audit.
- Technical debt: no TODO, FIXME or XXX markers (INVENTORY s5.1). Dead-code candidates are `v4/decoder.py:503 consensus_soft`, `v4/util.py:70 sha256_hex` and `v5/soft/frames.py:69 read_posterior` (zero references). About 12k lines serve only the V3 CLI.
- Hosts: `/usr/local/bin/vnx` on this machine is the control-plane script, not the codec CLI, and the lab venv's editable `vnxdna` resolves to the `/root/vnx-dna-lab/v6` work-in-progress tree. Every audit command used an explicit `PYTHONPATH`. `/dev/null` is a regular file on this host (discrepancy 11).
- Only one machine was used (shared 8-vCPU VPS); no results exist for other hardware.

## 10. Recommended migration strategy

Source: ARCH s2, s3, s7, s8 and the decisions table of 2026-10-05; SPEC s1 and s4. Class: THEORETICAL for the target (the golden fixtures `v6_0` exist at `f17f837`; the layered packages do not yet).

**Problem the audit shows.** The decoder and encoder are fused into `v4`, V5 and V6 are patched into `v4` through lazy imports, three CLIs exist, and there is no V4+ API (sections 4 and 5).

**Decided approach: a layered refactor with facades and aliases, gated by golden fixtures.**

1. **Evidence before change (M0).** Generate `tests/fixtures/v6_0` from the unrefactored tree (done at `f17f837`, cases `stripes-seq`, `adaptive-interleaved`, `max-recovery`, `encrypted-stripes`, SYNTHETIC SOFTWARE TEST data, never regenerated) and add `tests/architecture/test_public_paths.py`, listing every `vnxdna.*` path used outside `src/` (58 files import `vnxdna.v4`).
2. **Target layers** (ARCH s2, s3), downward imports only:
   `L0 core`, `L1 native`, `L2 archive` and `codec`, `L3 dnaenc` and `sync`, `L4 recovery`, `L5 pipeline`, `simulation` and `physical`, `L6 providers` and `benchmark`, `L7 sdk` and `conformance`, `L8 commands`, plus a frozen `legacy` package. Rules R1 to R6: downward only, codec purity (no import of simulation, providers or physical), no cycles, legacy isolation, ctypes only in `native`, a thin CLI. A new `tests/architecture/test_layers.py` walks `src/vnxdna` with `ast` (module and function level) and ships with an allow-list that may only shrink.
3. **Steps, one commit set each, full suite after every step:** M1 core and native; M2 archive and codec; M3 DNA encoding and sync (`v4/frame.py` split, old module becomes a facade); M4 recovery and pipeline (`v4/decoder.py` split, facade); M5 SDK and commands (`vnx` entry point to `vnxdna.commands:main`, `vnxdna.v4.cli:main` stays); M6 simulation and physical (from `experiments/`); M7 legacy (V0.1 to V3 into `vnxdna.legacy`, none deleted, V3 tests keep running).
4. **Aliases and facades (ARCH s7.2).** Whole-module moves leave a three-line `sys.modules` alias, so old and new names are the same module object and existing `monkeypatch.setattr` calls keep working. Split modules become facades that re-export every name, private ones included; the tests that patch a facade name (`setattr(de, ...)` once, `setattr(StripeRecovery, ...)` once) are updated to patch the new home in the same commit, with unchanged assertions and count. Package aliases cover `vnxdna.v2` to `vnxdna.legacy.v2` and similar. Deprecation warnings are opt-in in 6.x (`VNXDNA_WARN_LEGACY_IMPORTS=1`), default in 7.x; removal needs the founder's approval and is not before 8.0. `VNX_RS_REFERENCE` becomes a deprecated alias of `VNXDNA_RS_BACKEND=reference`.
5. **Gates for every step.** Full suite on 6 workers in the foreground, ruff, the layer test, goldens `v4_0`, `v5_0`, `v6_0` bit-exact, the strand byte-identity test and native RS golden vectors; the test count never drops; a bench-compare against the phase base within +/-5 % on a clean tree (median of 5 runs of 1 MiB and 64 MiB clean round trips and the 4 MiB noisy decode; SIMULATED). Experiment EXP-REF-1 checks that the refactor preserves behaviour (goldens plus a P1-EXP-01 subset). Because of discrepancy 13, a bench-compare baseline needs the bench-path versus CLI-path question settled first, and runs need an explicit `PYTHONPATH` (the venv's editable install points at another worktree).
6. **Formats are untouched by the refactor.** Where the version axes change (Phase 2.4), the container byte-identity test is restated before the bump, as decided on 2026-10-05: (a) re-encoding the stored V5 containers must reproduce the 5.0.0 strand FASTA SHA-256s, and (b) every container section except the manifest and trailer stays byte-identical, with the manifest equal after removing `extensions.vnx` and normalising `encoder.version`.
7. **Version axes (SPEC s1, s4).** Nine axes are versioned independently: software, specification, container, strand frame, superblock, codec identifier (derived, `f<frame>-sb<superblock>-<outer>`), channel model, provider adapter, and report/event/result/manifest schemas (`"schema": "vnx.<kind>/<major>"`). Phase 2 sets `_version.py` to `6.0.0.dev0`, writes `extensions.vnx` by default (decided), and makes `vnx version` emit `vnx.version/1` with every axis and the active native backends. This closes discrepancy 8 and the audit's gap on missing backend and version information in reports.
8. **Phase order after M0 (ARCH s8).** 2.1 public-path and layer tests; 2.2 moves M1 to M5; 2.3 SDK and thin CLI (profile merge in one place; `--performance` error becomes `CONFIGURATION_ERROR`); 2.4 version axes; 2.5 probe, dispatch and error codes; 2.6 schemas; 2.7 bugs (job #56, job #13 `_FlatReads[-1]`, `locate --dna-profile` refusing superblock-2 archives, tag-collision detection); 2.8 spill-bucket independence test (closes the unverified claim in section 2). Then Phase 3 channel-model framework, with the 14 existing models reproducing byte-identical reads for identical seeds. Deferred by decision: Sector Zero/One layouts (V9), short-strand profile freeze (V7), CRC-16 for the shortest profile, pydantic to an optional legacy extra (7.0).

**Items from this audit the migration does not address by itself:** repairing `/dev/null` on the host; adding CI for V6 (needs a push, which needs the founder's go); adding gitleaks to CI; resolving the bench-path versus CLI-path decode difference; writing `docs/V6_PHASE1_REPORT.md` or removing the CHANGELOG reference; adding libFuzzer harnesses for the RS decoder and aligner; MSan and non-x86 runs; any physical validation.

## 11. Result classification summary

| Result | Class |
|---|---|
| V6 suite 1690 passed, 3 skipped (1693/1693 with PATH); V5 suite 1013 passed; later 1864 passed, 6 skipped at `f17f837` | VERIFIED |
| Compatibility: 76 compat tests, 8/8 clean, 8/8 noisy, 8/8 V5 code, regenerated `v5_0` hashes identical | VERIFIED (noisy reads are SIMULATED) |
| Sanitizers: GCC and Clang ASan+UBSan, UBSan trap, valgrind (by hand), differential fuzz, 0 mismatches | VERIFIED (synthetic software inputs) |
| Native libFuzzer 1,873,071 executions, 0 crashes; Python fuzz 5 rounds, 0 failures | VERIFIED |
| Security scan 0 CRITICAL, 0 HIGH; MEDIUM and LOW counts as in section 8.1 | VERIFIED (scanner output plus triage) |
| Performance and memory tables | VERIFIED (measured, contended host); noisy rows SIMULATED |
| Aligner ×9.77 to ×12.19 | VERIFIED (kernel level, contended) |
| P1-EXP-01 dropout thresholds (spot-check of 200 decodes) | SIMULATED, reproduced |
| Analytic dropout and burst bounds (P1-EXP-04) | THEORETICAL |
| Target architecture, aliasing scheme, version axes, phases | THEORETICAL (specified, not implemented at `081697b`) |
| MSan; RS and aligner libFuzzer; non-x86; spill-bucket independence; physical results of any kind | NOT RUN |

V6 results are computational/software validation unless otherwise explicitly identified. No physical DNA synthesis or sequencing validation is claimed by this release.
