# VNX-DNA V6 implementation summary

What each phase of the V6 directive built, with the merge commits on the integration branch `build/v6-sprint`, the main modules and the documents. Results, test counts and the acceptance table are in [V6_COMPLETION_REPORT.md](V6_COMPLETION_REPORT.md); what V6 does not contain is in [V6_DEFERRED.md](V6_DEFERRED.md). All channel results are SIMULATED; no physical validation is claimed. SHAs are merge commits on `build/v6-sprint` (`git log --merges`); base of the work is `081697b` (`build/v6-p1`, on `v5.0.0`).

| Phase | Subject | Merge SHA(s) |
|---|---|---|
| 0 | V5 forensic audit | 7f65fa9 |
| 1 | Architecture and specification | cddc2a0 |
| 2 | Codec and API refactor | 3e4d681 (golden fixtures), 08ca9c4 |
| 3 | Channel-model framework | 25fd56e |
| 4 | Indel recovery and soft decisions | 29df22a, b693254 (`--retry-band`, job #80) |
| 5 | Performance and native packaging | f17f837 |
| 6 | Security, fuzzing, hardening | b3de24e, a6a38f8 (SEC-01 to -03, `content-v1`) |
| 7 | Interoperability and laboratory interface | 6c378e1 |
| 8 | Conformance and reproducibility | f0b69df (part 1), be13d71 (part 2) |
| 9 | Benchmarking | fff57bc (lab B0), 9466479 (job #79 `high-dropout`) |
| 10 | Documentation and release audit | 89123e5 (research gate), e27bf2e (part 1), this change (part 2) |

## Phase 0: forensic audit of V5

- Built: a read-only audit of `081697b` against `v5.0.0`, with an inventory (static) and a verification (executed: suites, compatibility, sanitizers, fuzz, security scan, benchmarks).
- Documents: [V6_BASELINE_AUDIT.md](V6_BASELINE_AUDIT.md). Sources outside the tree: `/root/vnx-dna-lab/results/v6-audit/{INVENTORY,VERIFICATION}.md`.
- Result: V5 1013 passed; V6 candidate 1690 passed with 3 PATH skips (1693 when run with the venv on PATH). No production code was changed.

## Phase 1: architecture and specification

- Built: the formal specification (formats, nine version axes, encode stages E0-E15 and decode stages D0-D14, conformance, correction limits, provider interface) and the target package structure with the migration plan and decisions.
- Documents: [spec/VNX-DNA-SPEC-V6.md](spec/VNX-DNA-SPEC-V6.md) (DRAFT), [V6_ARCHITECTURE.md](V6_ARCHITECTURE.md), [V6_OUTER_CODE.md](V6_OUTER_CODE.md).
- Not implemented: frame 6, superblock 3, primers, wide address class (specified only).

## Phase 2: codec and API refactor

- Built: layered packages `vnxdna.core`, `native`, `archive`, `codec`, `dnaenc`, `sync`, `recovery`, `pipeline`, `simulation`, `benchmark`, `sdk`, `conformance`, `commands`; layer rules checked by `tests/architecture`. `vnxdna.sdk` as the stable API with `vnx.result/1`; stable error codes (`vnx.error/1`); JSON Schemas; `vnx.event/1` decode events; `vnx.version/1`; writer provenance in `extensions.vnx`; frame-version probe and dispatch; the `vnx` CLI as a thin layer. Golden fixtures `tests/fixtures/v6_0` with section-level byte-identity tests (before the refactor began). Fix for job #56 (`--select` on stripe archives).
- Documents: [ARCHITECTURE.md](ARCHITECTURE.md), [CLI.md](CLI.md), [COMPATIBILITY.md](COMPATIBILITY.md), [STORAGE_FORMAT.md](STORAGE_FORMAT.md).
- Left undone: encode events, `experiments/v6/channel` and `physical` as thin wrappers (M6), V0.1-V3 code moved into `vnxdna.legacy` (M7).

## Phase 3: channel-model framework

- Built: `vnxdna.simulation` with the `vnx.channel-model/1` schema, a staged engine, Monte Carlo and sweeps, 14 shipped models (unfitted), `vnx channel models|show|convert|sweep`. EXP-SIM-1: 210/210 cells byte-identical to the previous simulator.
- Documents: [CHANNEL_MODEL.md](CHANNEL_MODEL.md); results in `experiments/v6/phase3/EXP-SIM-1`.

## Phase 4: indel recovery and soft decisions

- Built: a failure taxonomy of the current decoder, and two opt-in options measured under pre-registered rules: `--consensus-weighting quality` (efficacy C2 REJECT) and `--retry-band R` (nanopore-like REJECT, high-indel models +0.0292; KEEP OPT-IN). Public-data per-read statistics from the CNR nanopore reads (PUBLIC-DATA-DERIVED). Defaults are unchanged.
- Documents and data: `experiments/v6/phase4/README.md`, `experiments/v6/align-band/README.md`; the deferred nanopore-like work is in [V6_DEFERRED.md](V6_DEFERRED.md) section 2.
- Correction: the Phase 4 peak-RSS claim was withdrawn (commit 4f2fde3); the measurement now uses the child process's own `VmHWM`.

## Phase 5: performance and native packaging

- Built: `pip install .` builds the aligner, read parser and inner Reed-Solomon kernels as optional extensions; `python -m vnxdna.native [--require-native]` and `vnx native` report the backend in use; the Docker build checks the kernels. Installed-path decode of a 4 MiB noisy input: 10.8 s to 5.3 s (2.04x, MEASURED, one host and workload).
- Documents: [NATIVE_KERNELS.md](NATIVE_KERNELS.md), `benchmarks/v6/native_packaging/README.md`, `benchmarks/v6/native_reads`, `benchmarks/v6/native_rs`.

## Phase 6: security, fuzzing, hardening

- Built: the security model; libFuzzer harnesses (read parser, Reed-Solomon, aligner) and ten Python fuzz targets with campaigns of 320-630 s each; a one-CPU-hour campaign of 13 targets (0 new crashes; see the completion report). Fixes with tests that failed first: V6-SEC-01 (`--max-container-bytes`), V6-SEC-02 (key checked on full decode), V6-SEC-03 (`--expect-archive-id`, `--expect-sha256`); fuzz-found V6-SEC-04, -22, -23. Opt-in `content-v1` archive ID. CI jobs for secrets, dependencies and fuzz smoke.
- Documents: [security/V6_SECURITY_MODEL.md](security/V6_SECURITY_MODEL.md), [security/V6_FUZZ_REPORT.md](security/V6_FUZZ_REPORT.md). Fuzz code: `fuzz/`, `tests/fuzz/`.
- Open: LOW and INFO findings listed in the model; MSan not run; non-x86 not built.

## Phase 7: interoperability and laboratory interface

- Built: `vnxdna.providers` (`DNAWriter`, `DNAReader`, `DNAProvider`, `ReferenceSimulatorProvider` as the only provider), `vnx.export-package/1` and `vnx.import-package/1`, `vnxdna.physical` with the PUBLIC-DATA-DERIVED evidence class, exit code 10 (PROVIDER_ERROR).
- Documents: [INTEROPERABILITY.md](INTEROPERABILITY.md), [LAB_INTERFACE.md](LAB_INTERFACE.md), [DDSA_MAPPING.md](DDSA_MAPPING.md), [V6_PHYSICAL_VALIDATION_INTERFACE.md](V6_PHYSICAL_VALIDATION_INTERFACE.md).
- Not built: vendor adapters, primers, `vnx order-export`, DDSA Sector Zero/One writers.

## Phase 8: conformance and reproducibility

- Part 1 (f0b69df): 222 conformance vectors, a strict `vnx conformance [--backend native|reference]` runner (CONFORMANT on both backends at that merge), a packaged 22-vector subset, property tests.
- Part 2 (be13d71): `vnx.experiment/1` manifests (`vnxdna.benchmark.manifest`), `vnx channel simulate --manifest`, `vnx experiment reproduce`, and the conformance document. Job #79 later added 4 vectors (226).
- Documents: [CONFORMANCE.md](CONFORMANCE.md). Tests: `tests/conformance`, `tests/reproducibility`.

## Phase 9: benchmarking

- Built: benchmark lab B0, with VNX-DNA as an external codec in `dt4dds-benchmark` beside DNA-RS, DNA Fountain and DNA-Aeon (280 trials, not the published protocol); and the opt-in `high-dropout` redundancy profile (job #79, pre-registered, ACCEPT; the format is unchanged).
- Documents and data: `benchmarks/competitors/lab/README.md`, `benchmarks/competitors/lab/results/b0`, `.../b0-dropout`, [BENCHMARKING.md](BENCHMARKING.md), [VNX_BENCHMARK_PLAN.md](VNX_BENCHMARK_PLAN.md).
- Deferred to V7: lab stage B1 (HEDGES and YYC adapters, the ETH protocol; job #78).

## Phase 10: documentation and release audit

- Part 1 (e27bf2e, with the research gate 89123e5): [V6_DEFERRED.md](V6_DEFERRED.md), [research/V6_COMPETITIVE_RESEARCH.md](research/V6_COMPETITIVE_RESEARCH.md), [research/V6_TECHNICAL_RESEARCH.md](research/V6_TECHNICAL_RESEARCH.md), README, ARCHITECTURE, BENCHMARKING, CLI, COMPATIBILITY, STORAGE_FORMAT and the consolidated 6.0.0 changelog entry.
- Part 2 (this change): [V6_COMPLETION_REPORT.md](V6_COMPLETION_REPORT.md) and this summary; the deferred-work register updated (B1 to V7, nanopore-like drift, one-hour fuzz result).
- CI hardening (mypy, sanitizers, conformance on both backends, benchmark smoke, documentation link check) is merged separately and is not part of this change.
