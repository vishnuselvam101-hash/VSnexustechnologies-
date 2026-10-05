# VNX-DNA V6 completion report

Deliverable of the V6 directive, Phase 10 part 2 (directive sections 25, 26 and 30). All channel results in this report are SIMULATED. V6 results are computational/software validation unless otherwise explicitly identified. No physical DNA synthesis or sequencing validation is claimed by this release.

## 0. Release record

| Item | Value |
|---|---|
| Source of this report | branch `work/v6-release`, from `build/v6-sprint` @ 9466479 (merge of `work/v6-dropout`) |
| Package version in the tree | `6.0.0.dev0` (`src/vnxdna/_version.py`); the release sets `6.0.0` |
| Release SHA | recorded at merge |
| Tag `v6.0.0` | pending founder approval of the exact SHA (directive section 25 and the lab rules). Not created, not pushed |
| Remote | nothing in this report has been pushed or merged to `main` by this document's author. `main` is `0d1c285` (v5.0.0) |
| Author of all commits | `vishnuselvam101-hash`; no AI trailers |

### Evidence classes used below

| Class | Meaning here |
|---|---|
| VERIFIED | checked by a committed test or script, or by a log named in this report |
| SIMULATED | software strands through a software channel model; no physical data |
| SYNTHETIC | software test data (golden vectors, fuzz inputs, random files); not a channel result |
| EXPERIMENTAL | a measurement of this code on one host, or a pre-registered experiment, with its stated limits; time and memory are MEASURED on a shared development host |
| PHYSICAL | real DNA. Nothing in VNX-DNA is PHYSICAL |
| THEORETICAL | specified or computed, not run end to end |

Where a result depends on a simulated channel it is also SIMULATED. The public-data statistics from the CNR nanopore reads are PUBLIC-DATA-DERIVED (per-read statistics of a public dataset; not a VNX-DNA run on real DNA).

## 1. Overview

V6 is a software release. It adds a formal specification, a layered package structure with a stable SDK, a channel-model framework, opt-in decoder options built and measured under pre-registered rules, native packaging, a security model with fuzzing, a provider and laboratory exchange interface, 226 conformance vectors with an experiment-manifest workflow, and a first benchmark lab stage. The container format (VNX4), frame 4 and superblocks 1 and 2 are unchanged from 5.0.0. Frame 6, superblock 3, primers and `vnx order-export` are specified or planned and not implemented ([V6_DEFERRED.md](V6_DEFERRED.md)).

Per-phase detail with merge SHAs: [V6_IMPLEMENTATION_SUMMARY.md](V6_IMPLEMENTATION_SUMMARY.md). Changes by feature: `CHANGELOG.md`, entry 6.0.0.

## 2. Tests

All counts are `pytest` results from `vnx-task verify` logs under `/root/vnx-dna-lab/results/` (not committed; the log file is named for each row). Skips are explained below the table.

| Point | Passed / skipped / failed | Source |
|---|---|---|
| V5 (`6aef3f4`, v5.0.0), audit re-run | 1013 / 0 / 0 | `/root/vnx-dna-lab/results/v6-audit/VERIFICATION.md` row 1b |
| V6 candidate `081697b` (Phase 0 audit) | 1690 / 3 / 0; 1693 / 0 / 0 when the 3 PATH skips are run by hand | same file, row 1a |
| Native packaging merged `f17f837` | 1864 / 6 / 0 | `task-v6-sprint-20261005-073636.log` |
| Phase 6 security (`work/v6-security`) | 1905 / 3 / 0 | `task-v6-security-20261005-082757.log` |
| Phase 2 refactor merged `08ca9c4` | 2127 / 6 / 0 | `task-v6-sprint-20261005-110400.log` |
| Phase 3 merged `25fd56e` | 2382 / 6 / 0 | `task-v6-sprint-20261005-114639.log` |
| Phase 8 part 1 merged `f0b69df` | 2441 / 6 / 0 | `task-v6-sprint-20261005-115248.log` |
| Phase 8 part 2 branch (`work/v6-repro`) | 2539 / 6 / 0 | `task-v6-repro-20261005-180034.log` |
| Job #79 branch (`work/v6-dropout`) | 2472 / 6 / 0 | `task-v6-dropout-20261005-182609.log` |
| Release branch `c145fcb` (CI hardening + this report merged), full suite | 2555 / 6 / 0 | `task-v6-sprint-20261005-184911.log` (ruff clean, mypy 0 errors) |

The two branch rows at the end are not on a common base: `work/v6-repro` and `work/v6-dropout` were each verified before the other was merged, so their counts are not comparable with each other. A suite run of the merged tree at `9466479` did not finish (the log `task-v6-sprint-20261005-183728.log` has no result). The release-branch row above is the suite count for the merged tree (VERIFIED). The later commit `b38ef2c` changes only a sanitizer shell script.

Before and after: 1013 (V5) to the release-branch count above; 1693 at the V6 audit baseline to the same.

Collection at the base of this branch (`9466479`), run for this report with `pytest --collect-only -q -o addopts= tests`: 2551 tests. This is a count of collected tests, not a pass result.

The 3 skips at the audit are the PATH-dependent `tests/cli/test_readme.py` cases (VERIFICATION row 1a: they pass with the venv on PATH). From `f17f837` on, 6 tests are skipped; the 3 additional skips were not itemised for this report (several tests skip when a native build, clang's libFuzzer or an optional dependency is missing).

A failed run at the Phase 2 branch (`task-v6-refactor-20261005-105615.log`, 1 failed) was fixed and re-run (`task-v6-refactor-20261005-105957.log`: 2123 passed, 6 skipped). No test was deleted or weakened to obtain a pass (ledger rule; not independently re-audited here).

### New tests

The suite grew from 1013 (V5) to 1693 at `081697b` and 2551 collected at `9466479` (+1538 over V5 by collection count; the 1013 and 1693 are also collection counts). New V6 test areas, by directory in the tree: `tests/architecture` (layer rules), `tests/sdk`, `tests/simulation`, `tests/providers`, `tests/physical`, `tests/conformance` (226 vectors and property tests), `tests/reproducibility` (experiment manifests), `tests/fuzz` (smoke replay of every fuzz target and regression inputs), `tests/bench_lab`, `tests/v6` (golden fixtures `tests/fixtures/v6_0`, security regression tests written to fail first, tag collision, probe and dispatch, outer pipeline), `tests/compat`. Per-directory test counts were not computed for this report.

SYNTHETIC: the conformance vectors, golden fixtures and fuzz seeds.

## 3. Conformance

226 vectors (132 positive, 94 negative), `tests/conformance/index.json`, schema `vnx.conformance-index/1`; counts and per-stage table in [CONFORMANCE.md](CONFORMANCE.md). `vnx conformance` ran CONFORMANT on the native and reference backends at Phase 8 part 1 (222 vectors; ledger entry for `f0b69df`); 4 vectors were added with the `high-dropout` profile (job #79). The 226-vector run on both backends is added to CI by the hardening change described in section 13. Class: SYNTHETIC. The vectors were written by the same project as the code; no independent implementation has run them (limitation).

Experiment reproducibility: `vnx.experiment/1` manifests for two kinds (`channel-simulation`, `experiment`); a committed manifest of one EXP-SIM-1 cell reproduces its recorded read-file SHA-256 (SIMULATED; CHANGELOG, Phase 8 part 2).

## 4. Benchmarks

### 4.1 Decode throughput and memory at the Phase 0 baseline

VERIFIED/EXPERIMENTAL, measured at `081697b` on a shared host (load average noted in each row of the source); one host, one run per row unless a range is shown. Source: `/root/vnx-dna-lab/results/v6-audit/VERIFICATION.md`, benchmark table (lines 212-217).

| Case | Encode MB/s | Decode MB/s | Peak RSS (MB) |
|---|---|---|---|
| V6 clean 100 MiB | 17.62 | 11.45 | 319.6 |
| V6 clean 1 GiB | 18.23 | 11.56 | 314.3 |
| V5 illumina-like 10 MiB (3 runs) | 5.70 | 0.59 | 263.8 |
| V6 illumina-like 10 MiB (3 runs) | 7.70 | 1.81 | 269.2 |

Peak RSS at 1 GiB equals that at 100 MiB within 2 %, which is the evidence for bounded-memory streaming on clean input (VERIFICATION line 221). The illumina-like rows are a simulated channel (SIMULATED). This table is the audit baseline; it was not repeated at the release SHA.

### 4.2 Native packaging (Phase 5)

EXPERIMENTAL (MEASURED): installed-path decode of a 4 MiB noisy input, single worker, `pip install` before and after the change: 10.824 s to 5.311 s (2.04x) in round 2 and 10.862 s to 5.322 s (2.04x) in round 3; all 15 repetitions 10.907 s to 5.322 s (2.05x). Round 1 (2.76x) was disturbed by host load and is not used for the ratio. One machine, one workload, one worker count. Source: `benchmarks/v6/native_packaging/README.md`. The data are SIMULATED reads (EXP-0011 channel).

### 4.3 Benchmark lab B0 (Phase 9, first stage)

SIMULATED. VNX-DNA as an external codec in `dt4dds-benchmark`, beside DNA-RS, DNA Fountain and DNA-Aeon. 280 trials, 3 seeds per point (6 per pooled cell), i.i.d. channel, 53/45/2 error composition, not the published protocol and not comparable to it. Source: `benchmarks/competitors/lab/README.md` (tables and caveats 1-8).

| Result | Value |
|---|---|
| false SUCCESS (exit 0, wrong output) | VNX-DNA 0 of 157 trials; DNA-RS 2; DNA Fountain 16; DNA-Aeon 0 |
| Sweep A (19 kB), `vnx-s184`, 1 % errors, 0.992 bit/nt | 5/6 exact (Wilson 0.44-1.00); `dna-rs-medium` 6/6 |
| Sweep C (dropout, 19 kB, 0.5 % errors), `vnx-s184`, 10 % dropout | 0/3 (0.00-0.56); `dna-rs-medium` and `dna-fountain-medium` 3/3 |
| Sweep A, `vnx-s456` (1.552 bit/nt, no inner parity) | 3/6, 0/6, 0/6 at 0.2, 0.5, 1 % errors |
| Decode peak RSS | VNX-DNA 62-76 MiB; DNA-RS 14-16 MiB (776 MiB in the 1 % high-rate cell); DNA-Aeon 1925-1942 MiB where it finished |
| Decode time, 19 kB, 1 % errors, median | VNX-DNA 0.8-1.5 s; `dna-rs-medium` 123.8 s; timings were taken under load 2.4-15 and are indicative to within a factor of about 2 |

DNA-Aeon hit the 900 s limit at the demo conditions and is not a reproduction of a published Aeon result (README, "Published-style run"). VNX-DNA writes 14 bytes of header and CRC into every strand, which costs rate at short strand length (caveat 2).

### 4.4 High-dropout profile (job #79)

SIMULATED, pre-registered (`benchmarks/competitors/lab/results/b0-dropout`; CHANGELOG, job #79): 800 trials in the main grid, 10 seeds per cell. Rule 3 accepted `hd-l256-i4`: 88/100 exact against 35/100 for `s184`, 10/10 in every cell with dropout up to 10 %, 0.9984 bit/nt, 0 false SUCCESS in 720 VNX trials. It loses to DNA-RS-medium (100/100 pooled; 3/10 against 10/10 at 1 % errors and 20 % dropout), its strands are 256 nt against 144, and its decode peak RSS is 63 against 16 MiB. Opt-in (`--redundancy-profile high-dropout`); the default is unchanged.

### 4.5 Alignment band and quality-weighted consensus

SIMULATED, pre-registered. Source: `experiments/v6/align-band/README.md`, `experiments/v6/phase4/README.md`, and [V6_DEFERRED.md](V6_DEFERRED.md) sections 2 and 3.

- `--retry-band` (job #80): 935 trials; 0 false SUCCESS in 1,910 decodes; deletion-heavy coverage 10 went from 0/20 to 6/20; high-indel models +0.0292 [+0.0074, +0.0590]; nanopore-like stays 0/20 at coverage 3, 5 and 10 (C2 REJECT); peak RSS up to 1.258 times the default on deletion-heavy at 1 MiB (C5 REJECT). Kept opt-in.
- `--consensus-weighting quality`: 213/520 against 211/520 exact, difference +0.0038, 95 % CI [-0.0016, +0.0093] (C2 REJECT on efficacy). Kept opt-in.
- Failure taxonomy (P4-EXP-01): 0 false SUCCESS in 1,450 decodes.

## 5. Memory

- Clean-input decode is bounded: 319.6 MB at 100 MiB and 314.3 MB at 1 GiB (section 4.1, EXPERIMENTAL).
- Noisy-channel runs stop at 16 MiB in existing methodology (V4); streaming with bounded memory on noisy channels is not done ([V6_DEFERRED.md](V6_DEFERRED.md) section 4).
- The Phase 4 claim that peak RSS was unchanged by `consensus_weighting=quality` (P4-EXP-04) is withdrawn: the `wait4` method reported the harness's own peak. The committed RSS column is withdrawn and the measurement was fixed to read the child's own `VmHWM` (`experiments/v6/align-band`; the withdrawal is commit 4f2fde3). No replacement figure for that option exists. The time column stands.
- Peak RSS of the retry band and of the `high-dropout` profile are in sections 4.4 and 4.5, measured with the corrected method.

## 6. Fuzzing

Two campaigns. Class: SYNTHETIC (fuzz inputs); results VERIFIED by the logs named.

### 6.1 Phase 6 campaigns (320-630 s per target)

Committed record: [security/V6_FUZZ_REPORT.md](security/V6_FUZZ_REPORT.md), section 2. 3 fuzz-found robustness issues were fixed with regression tests (V6-SEC-04, V6-SEC-22, V6-SEC-23). Harness invalid runs and the `py-align` engine memory growth are triaged in that report.

### 6.2 One-CPU-hour campaign (this release)

Built from `build/v6-sprint` @ b693254, 13 targets, 3600 s each, one CPU, 3 at a time. All 13 exited 0; 0 new crashes in every target; `STATUS` ends with `ALL_DONE`. Source (not committed): `/root/vnx-dna-lab/results/fuzz-1h-20261005-1313/` (`STATUS`, `driver.log`, per-target `<target>.log`, `run_all.sh`). Executions are the `runs=` value of each target's last log line.

| Target | Engine | Seconds | Executions | cov / ft | Corpus | New crashes |
|---|---|---|---|---|---|---|
| reads | libFuzzer + ASan + UBSan | 3600 | 6,796,247 | 199 / 1254 | 273 | 0 |
| align | libFuzzer + ASan + UBSan | 3600 | 1,509,367 | 293 / 1307 | 293 | 0 |
| rs | libFuzzer + ASan + UBSan | 3600 | 460,193 | 358 / 2050 | 276 | 0 |
| py-container | atheris | 3600 | 15,234,510 | 339 / 828 | 63 | 0 |
| py-vxs | atheris | 3600 | 10,061,150 | 130 / 496 | 1176 | 0 |
| other 8 Python targets (`py-align`, `py-bomb`, `py-decode`, `py-frame`, `py-manifest`, `py-reads`, `py-rs`, `py-superblock`) | atheris | 3600 each | see logs | see logs | see logs | 0 |
| Total, 13 targets | | | 312,741,708 (sum of the `runs=` values) | | | 0 |

Limits: the fuzz host was shared and loaded; exec/s differs by target by orders of magnitude. Coverage counts instrumented counters, not lines. A clean campaign shows that these harnesses found nothing in these durations; it is not a proof. No MSan.

## 7. Sanitizers

Class: VERIFIED at the Phase 0 audit (`081697b`); see `/root/vnx-dna-lab/results/v6-audit/VERIFICATION.md` section 3 and the logs `05a`, `05b`, `05c`. Re-run on the release tree with `tools/sanitizers.sh` (CI-sized budgets): aligner, reads parser and RS decoder each passed their native tests and differential stress fuzz under gcc ASan+UBSan, clang ASan+UBSan and clang UBSan trap, with 0 mismatches, and every ASan canary fired (VERIFIED). The table below gives the larger audit-time budgets.

| Kernel | GCC ASan+UBSan | Clang ASan+UBSan | Clang UBSan trap | Differential stress, sanitized |
|---|---|---|---|---|
| aligner | tests/v5 290/290; 100,000 reads, 0 mismatches | 290 passed, 0 reports (run by hand) | 290/290 | 0 mismatches |
| reads parser | 184 passed; 40,000 comparisons, 0 mismatches | 184 passed | 184 passed | 0 mismatches |
| RS decoder | 119 passed; 126,102 comparisons over scalar/avx2/avx512, 0 mismatches | 119 passed | 119 passed | 0 mismatches |

The ASan canary fired in each. The valgrind step in the repository script failed for a host reason (`/dev/null` was a regular file on the host); re-run by hand it passed (135 passed, no invalid read or write; VERIFICATION section 3). The script now omits the ineffective `--suppressions=/dev/null` flag (`b38ef2c`); re-run on the release tree: 135 passed, 0 memory errors. **MSan: NOT RUN** (needs an MSan-instrumented CPython and NumPy). The libFuzzer harnesses in section 6 also run under ASan and UBSan.

## 8. Security

Source: [security/V6_SECURITY_MODEL.md](security/V6_SECURITY_MODEL.md) section 1. Class: VERIFIED for findings with a regression test; the model itself is a review.

- No CRITICAL or HIGH finding recorded.
- Fixed: V6-SEC-01 (container size cap, `--max-container-bytes`), V6-SEC-02 (key checked on full decode), V6-SEC-03 (`--expect-archive-id`, `--expect-sha256`), each with a test that failed first; V6-SEC-04, -22, -23 (fuzz-found); V6-SEC-14 (documentation).
- Open (LOW/INFO): V6-SEC-05, -06, -07, -08, -09, -10, -13 and the pool rule for -11. Example: the default scrypt cost N = 2^15 is below the OWASP minimum cited in the model (V6-SEC-07; founder decision pending).
- Clear (unencrypted) archives carry no authenticity (FC-8, security model section 5.1).
- Dependencies: advisories for `cryptography` and `pytest` were triaged as not reachable; the pinned-dependency decision is open with the founder (V6-SEC-20). Phase 0 security scan: 0 CRITICAL/HIGH (VERIFICATION, row 7).
- `content-v1` archive ID implemented, opt-in.
- Open job #74: `assert` on untrusted-input paths vanishes under `python -O`.

## 9. Compatibility

Source: [COMPATIBILITY.md](COMPATIBILITY.md), [STORAGE_FORMAT.md](STORAGE_FORMAT.md); `/root/vnx-dna-lab/results/v6-audit/VERIFICATION.md` row 4.

- Formats unchanged: VNX4 container, frame 4, superblocks 1 and 2. VERIFIED by golden fixtures `tests/fixtures/v6_0` and byte-identity tests (Phase 2.0).
- Cross-version compatibility checks at the audit: 76/76 passed (ledger entry for Phase 0; VERIFICATION row 4).
- New V6 archives carry `extensions.vnx` (writer provenance), so container bytes differ from 5.0.0 for the same input; `ArchiveOptions(writer_provenance=False)` writes the 5.x layout. A test that pinned the 5.0.0 container SHA-256 was restated before the version bump.
- Every old module path still imports. `--performance bogus` is exit 7 (was 70); `--report` is the `vnx.decode-report/1` envelope with the 5.x fields at top level.
- V5 reads of V6 archives and V6 reads of V5 archives are exercised by the compatibility tests; a full matrix against the released 5.0.0 CLI at the release SHA was not re-run for this report.

## 10. Specification status

[spec/VNX-DNA-SPEC-V6.md](spec/VNX-DNA-SPEC-V6.md): status DRAFT dated 2026-10-05. It covers formats, nine version axes, stage graphs E0-E15 and D0-D14, conformance, correction limits (section 7), failure contract and the provider interface. Frame 6, superblock 3, primers and the wide address class are specified, not implemented (THEORETICAL). The draft status has not been lifted by this release.

## 11. Simulator status

`vnxdna.simulation`, `vnx.channel-model/1`, staged engine, Monte Carlo and sweeps, 14 shipped models, all unfitted (stress models, not platform models). EXP-SIM-1: 210/210 cells (14 models x 3 strand files x 5 seeds) have identical read-file SHA-256 across three runs and match the previous simulator (`experiments/v6/phase3/EXP-SIM-1/README.md`; SIMULATED). The simulated nanopore-like model is not decoded in any tested condition ([V6_DEFERRED.md](V6_DEFERRED.md) section 2).

## 12. Interoperability status

`vnxdna.providers` (`DNAWriter`, `DNAReader`, `DNAProvider`); `ReferenceSimulatorProvider` is the only provider; `vnx.export-package/1` and `vnx.import-package/1`; exit code 10 for provider errors. DDSA Sector Zero/One: mapping table only, never written. No vendor adapter, no primers, no `vnx order-export`. Sources: [INTEROPERABILITY.md](INTEROPERABILITY.md), [LAB_INTERFACE.md](LAB_INTERFACE.md), [DDSA_MAPPING.md](DDSA_MAPPING.md). Class: VERIFIED by tests for the software interface; nothing PHYSICAL.

## 13. CI

Present in `.github/workflows/ci.yml` at the release base: `test`, `install`, `secrets` (gitleaks), `dependencies` (pip-audit), `fuzz-smoke`.

Added by the V6 CI hardening (`369588a`, merged as `f947d69`): `mypy`, `sanitizers` (`tools/sanitizers.sh`: gcc and clang ASan/UBSan for the aligner, reads parser and RS decoder), `conformance` (both backends), `benchmark-smoke`, and a documentation link check (`tools/check_doc_links.py`). Each job's command was run locally on the release tree and passed; the GitHub run on the release PR is the CI evidence. Heavy benchmarks stay outside CI.

## 14. Physical-validation status

None. No DNA was synthesised, stored or sequenced, and there is no wet-lab work in V6. The first small oligo order is placed after the V7 short-strand profile and order export ([V6_DEFERRED.md](V6_DEFERRED.md) section 7). Every channel result in this report is SIMULATED; the CNR statistics are PUBLIC-DATA-DERIVED.

## 15. Acceptance criteria (directive section 26)

MET = built and evidenced here. PARTIAL = built with a stated gap. NOT MET = absent.

| Area | Criterion | Status | Evidence |
|---|---|---|---|
| Architecture | Modular codec | MET | layered packages, rules checked by `tests/architecture`; [ARCHITECTURE.md](ARCHITECTURE.md) |
| | Explicit pipeline | MET | spec section 5 stage graphs E0-E15 / D0-D14; `vnxdna.pipeline`. The absence of hidden transforms is specified, not proved |
| | Physical abstraction | MET | `vnxdna.physical`, `vnxdna.providers`; spec section 9 |
| | Versioned format | MET | nine version axes, frame probe with refusals; spec section 4. Frame 6 and superblock 3 are not implemented |
| | Stable interfaces | MET | `vnxdna.sdk`, `vnx.result/1`, `vnx.error/1`, JSON Schemas |
| Correctness | Round-trip | MET | section 2; golden fixtures |
| | Conformance | MET | 226 vectors (section 3); no independent implementation has run them |
| | Property tests | MET | `tests/property`, conformance property tests (16 at Phase 8 part 1) |
| | Backward compatibility | MET | section 9; the matrix against the released 5.0.0 CLI was not re-run at the release SHA |
| Error recovery | Better indel handling | PARTIAL | `--retry-band` opt-in, high-indel models +0.0292 [+0.0074, +0.0590] (SIMULATED); nanopore-like 0/20; default unchanged |
| | Soft decisions | PARTIAL | quality-weighted consensus built, efficacy C2 REJECT, opt-in only |
| | Configurable channel models | MET | 14 models, `vnx.channel-model/1` (unfitted) |
| | Explicit correction limits | MET | spec section 7 table, including the `high-dropout` profile |
| Performance | Component benchmarks | MET | `benchmarks/v6/native_packaging`, `native_reads`, `native_rs`; audit baseline |
| | Native kept | MET | all three kernels packaged; 2.04x installed-path decode (EXPERIMENTAL) |
| | Memory | PARTIAL | clean input bounded (314 MB at 1 GiB); noisy-channel streaming not done; Phase 4 RSS claim withdrawn |
| | Scaling | PARTIAL | 100 MiB and 1 GiB clean measured; parallel scaling 1-8 workers not measured (deferred) |
| Security | Threat model | MET | [security/V6_SECURITY_MODEL.md](security/V6_SECURITY_MODEL.md) |
| | Parser hardening | MET | V6-SEC-01, -04, -22, -23 fixed with tests; open LOW items listed |
| | Crypto audit | MET | security model section 3 (review; scrypt default below the OWASP figure it cites, decision open) |
| | Fuzzing | MET | section 6: 13 targets x 3600 s, 0 new crashes |
| | Sanitizers | PARTIAL | ASan/UBSan clean on three kernels at the audit and on the release tree; CI job added; MSan not run |
| | Dependency scanning | MET | `dependencies` CI job (pip-audit); two advisories triaged not reachable |
| Scientific integrity | Labels, pre-registration, negative results reported | MET | every channel result SIMULATED; three REJECT outcomes recorded (C2 quality weighting, C2 and C5 retry band, nanopore-like); PHYSICAL absent |
| Interoperability | Writer / reader | MET | `DNAWriter`, `DNAReader` |
| | Reference simulator provider | MET | `ReferenceSimulatorProvider` |
| | Lab exchange format | MET | export and import packages; no vendor adapter |
| Documentation | Spec, architecture, security, channel model, conformance, interoperability, lab interface, completion report | MET | all present; the spec is a DRAFT |

Result: the unmet parts are PARTIAL, not NOT MET: indel and soft-decision efficacy, memory and scaling coverage, and sanitizers (MSan). None is a NOT MET. Whether PARTIAL items block the tag is the founder's decision (directive section 25). This report does not state that the acceptance criteria are met in full.

## 16. Limitations

- Software only; no PHYSICAL validation; all 14 channel models are unfitted.
- The simulated nanopore-like model decodes 0/20 at coverage 3, 5 and 10 (default and retry band). Public CNR reads: 4.34 % have no indel (PUBLIC-DATA-DERIVED; `experiments/v6/phase4/P4-EXP-03-cnr-ids/README.md`).
- B0 is small (3 seeds per point), not the published protocol, with timings taken under load. VNX-DNA was weaker than DNA-RS at 10 % dropout, at 1 % errors near 1 bit/nt, and near 1.5 bit/nt (SIMULATED).
- The default strand is 313 nt; with two 20-nt primers it is 353 nt, over a 350-nt pool limit (THEORETICAL arithmetic).
- Decoder options (`--retry-band`, `--consensus-weighting quality`, `--redundancy-profile high-dropout`) are opt-in; none is a default.
- Open defects: job #62 (`vnx locate` on V6 striped pools), job #66 (`--select` with failed groups outside the index), job #58 (latent mypy errors in `v2`, `v4`, `ecc`).
- Encode events are not implemented; M6 and M7 of the migration plan are not done.
- Fuzz and sanitizer numbers above were taken at earlier SHAs (`b693254` for the one-hour campaign; `081697b` for the larger sanitizer budgets, with a CI-sized re-run on the release tree).

## 17. Deferred work

Full register with reasons and evidence: [V6_DEFERRED.md](V6_DEFERRED.md). Summary of the targets: V7 for frame 6, superblock 3, primers and order export, header-independent clustering for nanopore-like reads, fitted channel models, and benchmark lab B1 (HEDGES and YYC adapters, the ETH protocol; job #78, not required by directive section 26); V8 onward for the wide address class, hierarchical index, streaming on noisy channels; V9-V12 for DDSA writers, vendor adapters and physical work.

## 18. Next milestone

V7: short-strand profile, primers and order export, fitted channel models, the nanopore-like decoding work (job #82), and benchmark lab B1.
