# VNX-DNA V8 completion report

Branch `build/v8-public-channel` (from the closed V7 state 0fd7c54). Not merged, not tagged; the release is the founder's
decision. Every number below comes from a committed results file under `experiments/v8/`, named in each section.

**Physical validation.** VNX-DNA has not physically synthesised, stored or sequenced DNA. No such experiment has taken place
or is documented. Nothing in V8 is REAL EXPERIMENTAL.

## 1. Executive result

1. **Public-data channel model: INADEQUATE (a conclusive negative result).** A nanopore channel model was fitted to public
   D13 data (Lopez et al. 2019, ONT R9.4 1D², 2.67 M FIT segments) with full provenance.
   - It reproduces the per-base rates, the multi-base indel run lengths and the homopolymer classes in sample.
   - It fails the pre-registered FIT-only pre-check on read-level structure: the shape of the edit-distance distribution,
     the per-read error rate, read drift, and the substitution spectrum.
   - No DEV look was spent and no held-out data was opened. The model is not ADEQUATE and is used only as a stress
     condition.
2. **Decoder under controlled SIMULATED channels:** 360 decodes, **0 false success, 0 false frames**. All 88 failures are
   classified (classified == total).
3. **Limiting stage under the D13-derived stress channel:** the indel-aware consensus at 2–5 reads (oracle bounds). Strand
   identity and clustering contribute little.
4. **Archive integrity:** encrypted multi-file archives with deduplication decode byte-identically through noisy channels.
   Merkle/AEAD verification, single-file extraction and random-access location all work. Wrong keys, tampering and
   insufficient reads are all refused.
5. **Scale (clean channel, this host):** 4 KiB to 1 GiB, all exact. 1 GiB encodes in 105 s and decodes in 187 s, at
   9.78 nt per byte and peak RSS ≤ 314 MiB.

## 2. Scientific status

| class | V8 results |
|---|---|
| PUBLIC-DATA-DERIVED | D13 error extraction; F1 parameters; the FIT pre-check (real side); the DEV comparison (real side) |
| SIMULATED | everything decoded: decoder matrix, coverage envelope, oracle bounds, archive check, scale benchmark, fuzzing inputs |
| REAL EXPERIMENTAL | none |

## 3. Channel-model result (`experiments/v8/d13/README.md`)

- **Pre-registration** (`docs/V8_PREREGISTRATION.md`, committed before any V8 data was processed):
  - Splits: D13 reference buckets FIT 0–5 / DEV 6–7 / HELD-OUT 8–9; run 13 held out whole.
  - Model families: F1, and F2 under a pre-stated rule.
  - Metrics: 7B + A1 with an M10-V8 amendment, justified by the V7 diagnosis.
  - Look budget: ≤ 2 DEV looks, 1 held-out evaluation.
- **Amendment A1** was written before the re-run. It restores the protocol's matched read selection: simulated reads are
  removed above edit distance 0.30 × L, the D13 segmentation rule. The first pre-check had compared selected real
  segments (0 of 2.67 M above 45 edits) with unselected simulated reads.
- **Extraction (FIT):**
  - Rates: substitution 2.58 %, insertion 2.93 %, deletion 4.00 % per base (CI widths ≤ 0.02 pp). Runs differ: insertion
    2.45–3.77 %.
  - Indel rate by homopolymer run length 1–6+: 4.34 / 4.13 / 2.68 / 8.43 / 13.97 / 11.0 %.
  - 35 % of insertion runs and 40 % of deletion runs are 2 bases or longer.
  - Quality is calibrated up to about Q30 and over-confident above Q45.
- **Not identifiable from these data:** dropout as molecule loss (upper bound only), synthesis vs sequencing errors, and
  basecaller or chemistry dependence.
- **F1 pre-check, in sample, under A1:**
  - Passes: M1a, M1, M5, M5i and RL. M6r and M8 pass unstably across halves.
  - Fails stably: M1c, M2, M2b and M3. M10 fails, plausibly from noise.
  - F2 rule: not triggered (pair ratio 1.44 ≤ 1.5).
  - **Verdict: INADEQUATE, `DEV_LOOK_BLOCKED — FIT PRE-CHECK INADEQUATE`** (`results/VERDICT.json`).
- **Comparison on DEV** (one evaluation after the verdict; selects nothing): A (V6 nanopore-like), B (V7 a7b) and C (V7 a7c)
  fail all 11 DEV-evaluated 7B metrics. D (V8 F1) passes M1a, M1, M5, M5i and RL, and fails M1c, M2, M2b, M3, M6r and M8.
  The D13 fit is far closer to D13 than the earlier models, and still inadequate.

## 4. Decoder result (`experiments/v8/matrix/results/summary.json`; SIMULATED; seeds 83000–83009)

EXACT out of 10 per cell. Coverage is negative binomial, mean as given, dispersion 4. Decoding uses Phase 1 full
consensus.

| channel | cov3 v4 | cov3 lowcov | cov5 v4 | cov5 lowcov | cov10 v4 | cov10 lowcov |
|---|---|---|---|---|---|---|
| clean | 10 | 10 | 10 | 10 | 10 | 10 |
| substitution-heavy | 4 | 10 | 10 | 10 | 10 | 10 |
| insertion-heavy | 0 | 10 | 9 | 10 | 10 | 10 |
| deletion-heavy | 0 | 10 | 9 | 10 | 10 | 10 |
| mixed-harsh (5 % strand loss) | 0 | 5 | 0 | 10 | 10 | 10 |
| D13-F1 (stress; model INADEQUATE) | 0 | 0 | 0 | 5 | 10 | 10 |

FALSE SUCCESS 0/360; false frames 0. Wilson 95 % intervals per cell are in the summary; 10/10 means at least 0.72.

## 5. Coverage envelope (`experiments/v8/coverage/results/envelope.json`)

The analytic model (negative-binomial reads per strand, binomial outer rows) is combined with the matrix. One
assumption was corrected post hoc and the correction is disclosed: cells decoded in pass 1 from single reads use
P(reads < 1). The pre-specified class is kept beside it.

Under the D13-derived stress channel:

| | v4-balanced | v7-lowcov |
|---|---|---|
| cov10 | MARGINALLY SUPPORTED | SUPPORTED |
| cov5 | NOT SUPPORTED | MARGINALLY SUPPORTED |
| cov3 | NOT SUPPORTED | NOT SUPPORTED |

At coverage 3 with dispersion 4, 29 % of strands have fewer than 2 reads. No coverage level is called supported because a
single seed succeeded.

## 6. Failure analysis (V8.9, `experiments/v8/matrix/taxonomy.py`)

- All 88 failures are classified, against 88 total.
- Primary cause: the RS parity budget (87), and one archive reconstruction (superblock) failure.
- The leading contributing loss is insufficient coverage. Exceptions: read loss for mixed-harsh, and substitution
  correction for D13-F1 v4-balanced at coverage 5.
- Oracle bounds (`experiments/v8/oracle/results/summary.json`; UPPER-BOUND / ORACLE; D13-F1; 5 seeds per cell):

| cell | production | true strand identity | perfect consensus (≥ 2 reads) | perfect consensus (≥ 1 read) |
|---|---|---|---|---|
| cov3 v7-lowcov | 0/5 | 0/5 | 5/5 | 5/5 |
| cov5 v7-lowcov | 2/5 | 3/5 | 5/5 | 5/5 |
| cov5 v4-balanced | 0/5 | 0/5 | 3/5 | 5/5 |
| cov10 (both) | 5/5 | 5/5 | 5/5 | 5/5 |

Oracle channel knowledge is not implemented, because the decoder takes no channel model. Oracle results are not
achievable production performance.

## 7. Performance

**Scale** (`experiments/v8/scale/results/scale.json`; clean channel; host Xeon Gold 6240, 8 vCPU, 31 GiB; 4 workers):

| input | strands | nt/byte | encode s (CPU×) | decode s (CPU×) | peak RSS enc / dec MiB | exact |
|---|---|---|---|---|---|---|
| 4 KiB | 197 | 15.05 | 0.06 (1.1) | 0.06 (1.7) | 44 / 45 | yes |
| 1 MiB | 32,838 | 9.80 | 0.20 (2.6) | 0.44 (1.7) | 55 / 87 | yes |
| 10 MiB | 327,763 | 9.78 | 1.18 (3.7) | 2.09 (2.3) | 61 / 262 | yes |
| 100 MiB | 3,277,129 | 9.78 | 11.3 (4.0) | 17.0 (2.3) | 64 / 288 | yes |
| 1 GiB | 33,557,316 | 9.78 | 105 (4.0) | 187 (2.5) | 143 / 314 | yes |

- The outer parity overhead of v4-balanced is 25 % (M 16 / K 64).
- Decode worker scaling at 10 MiB is 3.4 / 2.4 / 2.2 s for 1 / 2 / 4 workers.
- Noisy decoding is far slower. Under D13-F1 at coverage 10, a 20 KB archive's median decode takes 63 s (v4-balanced) and
  89 s (v7-lowcov).

**Profile** (`experiments/v8/perf/results/profile.json`): consensus takes 82 % of the slowest realistic decode, and the
Phase 1 polish `edit_costs` alone about 50 %.
- One optimisation candidate was measured: vectorising over the 4 bases (commit 12cc2ff). It was bit-identical but
  0.83–0.86× the speed, so it was rejected and reverted (a52f348).
- No optimisation was shipped. A native port of the polish is the evident next step.

## 8. Security / integrity

- FALSE SUCCESS is 0 across the matrix (360), the oracle runs (120 decodes), the archive check and 7 adversarial read sets.
  Those sets are garbage, truncated, 40× duplicated, extreme homopolymer, pathological indel and empty records; all
  decoded deterministically.
- Archive check (`experiments/v8/archive/results/check.json`): VERIFIED on both channels; a wrong key gives `WRONG_KEY`, a
  tampered byte gives `INTEGRITY_ERROR`, and too few reads gives `FAILURE` with no output.
- Fuzzing (`experiments/v8/fuzz/campaign-2026-10-07.json`): 13 targets × 300 s (native ASan + UBSan, and atheris),
  28.8 M executions, 0 new crashes.

## 9. Reproducibility

`experiments/v8/reproduce.sh all` then `verify` (`docs/V8_REPRODUCTION.md`). The run-15 segmentation counts reproduce
the independent V7 D3 run exactly. Pinned inputs are in `experiments/v8/V8_MANIFEST.json`, and every FIT and DEV request
is in `experiments/v8/datasets/ACCESS_LEDGER.jsonl`.

## 10. Audit (V8.17, `experiments/v8/results/audit.json`)

- No held-out request was granted, and run 13 was never opened.
- No model, fit, extraction or F2-rule script requested DEV. DEV evaluations came only after the verdict.
- **Disclosure:** the segmentation pass wrote the DEV cache and recorded DEV segment counts and read-level histograms
  before the verdict.
- The pre-registration preceded all data access, and A1 preceded its pre-check.
- Seeds are complete in every cell; classified == total; 0 false success; parameter hashes match; no stale results;
  author, attribution and secret checks are clean.

Tests: 3206 passed / 0 failed / 6 skipped (environment-only: CLI on PATH, opt-in install tests); ruff and mypy clean.

## 11. Limitations (what V8 does NOT establish)

- That any channel model reproduces real nanopore reads: D13 F1 is INADEQUATE, so every "realistic channel" decoder
  number is a stress condition.
- Decoder performance on real reads: none was decoded.
- Generality beyond D13:
  - D13 is one condition (R9.4 1D²) with an unknown basecaller.
  - Its reads are concatemers, so read-end and whole-read effects are not modelled.
  - Pooling four runs hides per-run heterogeneity: insertion rates differ by up to 1.5×.
- Coverage statements: they rest on 10 seeds per cell and a 20 KB archive. Larger archives have more rows and lower
  per-archive success at the same per-row risk.
- Performance: measured on one shared host. The 1 GiB result is measured, not extrapolated, and only for a clean channel.
