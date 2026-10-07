# V9 pre-registration: adaptive channel and recovery

Committed on `build/v9-adaptive-recovery` before any V9 experiment runs, any V9 model is fitted, any V9 consensus candidate
is evaluated, or any D13 DEV or held-out statistic is computed for V9. The starting point is the frozen V8 state 8935217
(tag `v8.0.0` = main 17e42bb contains it). On that commit the full suite gives 3,206 passed, 0 failed and 6
environment-only skips; that is the regression baseline.

Changes after this commit need a dated amendment (`docs/V9_PREREGISTRATION-A<n>.md`). The amendment states its reason and
is committed **before** the data or results it concerns are produced or looked at. A threshold is never changed because a
model or method failed it. Everything here applies in addition to `docs/V8_PREREGISTRATION.md` and its amendment A1. Where
this document is silent, V8 governs.

## 0. Evidence classes

Every V9 result carries one class: **PUBLIC-DATA-DERIVED** (statistics of D13 reads), **SIMULATED** (anything decoded,
every coverage, oracle, scale and benchmark result) or **REAL EXPERIMENTAL**. For V9, REAL EXPERIMENTAL is **none**. No DNA
is synthesised, stored or sequenced, and no real nanopore read is decoded. The V8 D13-F1 model is INADEQUATE. Every
decoder result under it is a **stress condition, not a realistic-channel claim**. That label stays unless a V9 model
becomes ADEQUATE through §4.

## 1. Hypotheses

- **H1 (native consensus kernel).** A C11 native backend for the consensus/polish hot path (`edit_costs`, `fg`, and the
  alignment primitives they need) is bit-identical to the Python reference and faster on the §7 benchmark.
- **H2 (oracle gap).** The production decoder recovers measurably fewer archives than the perfect-consensus oracle at
  coverage 3 and 5 under the D13-F1 stress channel. ORE (§3) quantifies this, with ≥ 100 seeds per primary cell.
- **H3 (consensus V2).** At least one consensus candidate (§5) has a higher pooled ORE than the V8 consensus on the primary
  cells, with 0 false success.
- **H4 (adaptive coverage).** A computational termination rule uses fewer reads than fixed coverage at equal or better
  archive success, with a bounded false-termination rate.
- **H5 (channel model).** A model family with explicit read-level latent structure (§4) passes the V8 gating metrics that
  F1 failed (M1c, M2, M2b, M3, M10-V8) on FIT, and then on DEV.

Each hypothesis may be rejected. A rejection is reported with the failing numbers (directive §19).

## 2. Data and splits (D13; PUBLIC-DATA-DERIVED)

- **Source, runs, subsample and segmentation:** exactly as in V8 §1 (D13, Lopez et al. 2019, `uwmisl/data-ncomms19-nanopore`
  commit `4bf31ff4`; runs 15, 16, 18, 20; the V7 D3 deterministic subsample; frozen V7 segmentation constants). The file
  SHA-256 values are those pinned in `experiments/v7/datasets/MANIFEST.json`. Only aggregate statistics are committed.
- **Splits:** canonical reference bucket `int(SHA-256(canonical ref), 16) mod 10`: **FIT 0–5, DEV 6–7, HELD-OUT 8–9.**
  Read-level statistics use the same mapping on read-ID buckets. **Run 13 is HELD-OUT as a whole.**
- **Access:** all D13 access goes through the V8 access guard (`experiments/v8/d13/access.py`), extended for V9 with a
  `v9` purpose namespace. Every FIT and DEV request is appended to the access ledger and committed after the run that made
  it. HELD-OUT access is refused until a commit names this document's §4.4 freeze record.
- **Prior exposure (disclosed):**
  - DEV has been seen *descriptively* twice before: V7 D3 aggregate statistics, and the V8.6 A–D DEV comparison
    (`experiments/v8/d13/results/comparison.json`).
  - No V8 parameter was fitted or selected on DEV, and no V9 parameter or decision will be derived from those numbers.
  - The V9 model families in §4 are fixed now, from the V8 FIT-only failure pattern (`VERDICT.json`: M1c, M2, M2b, M3,
    M10 failed in sample on FIT). That pattern is FIT information, not DEV information.
  - HELD-OUT buckets 8–9 and run 13 have never been opened for model evaluation.

## 3. Definitions

- **EXACT:** the decoder reports success and the SHA-256 of every recovered file equals the original. **REFUSED:** the
  decoder raises an explicit, typed failure. **FALSE SUCCESS:** the decoder reports success, or returns bytes without
  raising, while any recovered byte differs from the original. **False frame:** a frame accepted by the inner code whose
  payload differs from the true frame (counted with ground truth, as in V8). Every non-EXACT decode gets a failure-stage
  class from `experiments/v8/matrix/taxonomy.py` (extended only by adding classes). `classified == total` is required.
- **Oracle levels** (V8.10 methodology, `experiments/v8/oracle/run.py`; UPPER-BOUND / ORACLE, not achievable
  performance), decoded from the same reads as production:
  - `production`: the decoder under test;
  - `oracle_strand_identity`: reads grouped by their true source strand;
  - `oracle_consensus_2reads`: the true frame of every strand with ≥ 2 stored reads;
  - `oracle_consensus_1read`: the true frame of every strand with ≥ 1 read (only coverage limits recovery).

  The idealised bound is the analytic P(archive) of the V8 envelope with q = P(reads < 1).
- **Oracle Recovery Efficiency (ORE)**, per cell: ORE = |P ∩ O| / |O|.
  - P = the seeds where `production` is EXACT, and O = the seeds where `oracle_consensus_2reads` is EXACT.
  - ORE is undefined (reported "n/a") when |O| = 0.
  - |P \ O| (production EXACT where the oracle is not) is reported separately. It is expected to be 0, and any non-zero
    value is investigated.
  - Pooled ORE = Σ|P ∩ O| / Σ|O| over the primary cells, with a Wilson 95 % interval.
  - ORE_SI uses `oracle_strand_identity` in place of the consensus oracle; it is reported, not gating.
  - ORE is **not** the EXACT rate and is never reported as one.
- **Primary cells:** D13-F1 stress channel × coverage {3, 5, 10} × profile {v4-balanced, v7-lowcov}, archive 20,000 B,
  negative-binomial coverage with dispersion 4 (V8 matrix conventions). **Secondary cells:** the five shipped channels
  (clean, substitution-, insertion-, deletion-heavy, mixed-harsh) at the same coverages and profiles.

## 4. Channel model (Phase 5; PUBLIC-DATA-DERIVED fitting, SIMULATED validation)

### 4.1 Model families (fixed now)

The model is layered:

1. molecule level: no parameters are fitted, because D13 does not identify dropout or synthesis error, which are inputs;
2. read level;
3. base and context level;
4. error generation;
5. quality generation conditional on the error.

- **G1:** the F1 base/context layer (V8 §2: empirical insertion and deletion run lengths, per-run-length homopolymer
  multipliers, FIT-chosen `min_run`, position profile, 3-mer context, quality model), plus a **discrete latent read
  state**. Each read draws one of K classes. Each class has its own total error rate, substitution/insertion/deletion
  mix and substitution spectrum.
  - K ∈ {1, 2, 3, 4} is chosen on FIT only, by BIC on the per-read edit-count likelihood.
  - Fitting is by EM on per-read sufficient statistics.
  - Qualities are generated conditional on the true error state at each base.
- **G2:** G1 plus a within-read drift process: a per-read piecewise-linear error-rate ramp along the read, and the
  paired-indel event of V8 F2. G2 is fitted **only if** G1 fails M3 or M2b on the FIT pre-check.
- **Run heterogeneity:** a per-run random effect on the read-state weights is estimated and reported. It is not a separate
  family. The pooled model is gated; per-run M1 and M2 are reported prominently.
- **Not inferred from D13** (stated as limitations, never as fitted quantities): molecule dropout, synthesis error,
  chemistry-specific or basecaller-specific behaviour, and coverage. D13 has a single chemistry (R9.4 1D²) and a single
  basecaller generation.

### 4.2 Metrics

The V8 gating set, with V7 thresholds and amendment A1, is unchanged: **M1a, M1, M1c, M2, M2b, M3, M5, M5i, M6r, M8, RL,
M10-V8**. Reported, not gating: M4, M7, M9 (quality calibration), per-run M1/M2, and the latent-state weights with
bootstrap CIs. Every report gives observed, model, absolute error, relative error, uncertainty, sample count, pass/fail and
identifiability.

### 4.3 Selection and look budget

1. **FIT-only pre-check** (the V8 design: full FIT plus two halves, M10-V8 at two seeds). The candidate is the first of
   G1, G2 that passes every gating metric in sample. If neither passes, the verdict is `DEV_LOOK_BLOCKED — FIT PRE-CHECK
   INADEQUATE`, no DEV look is spent, and V9's model is INADEQUATE. That is a valid result.
2. **DEV looks: at most 2**, only for a candidate that passed the pre-check and is frozen (`FREEZE.json`: model SHA-256,
   dataset hashes, fitter version, seed, metric definitions, thresholds). A second look is allowed only after a written,
   committed change note that names the failing DEV metric and the structural change. Parameters are never adjusted to
   DEV values.
3. **Comparison:** one DEV evaluation of A (V6 `nanopore-like`), B (V7 a7b `d03-hac-fwd`), C (V8 F1) and D (the V9
   candidate, or G1 if none passed). It runs with the same harness after D's verdict is fixed, selects nothing and does
   not count against D's budget.

### 4.4 Held-out

**Exactly one** evaluation (buckets 8–9 plus run 13), only for a frozen candidate that is ADEQUATE on DEV, and only after
a commit naming its `FREEZE.json`. The result is ADEQUATE or INADEQUATE. Nothing is tuned after it, and there is no second
attempt. If no candidate is ADEQUATE on DEV, the held-out set stays closed and is carried to V10 unopened.

## 5. Consensus V2 (Phase 3; SIMULATED)

- **Candidates:**
  - **A:** improved pairwise alignment (adaptive band, affine indel costs);
  - **B:** partial-order alignment (POA graph with heaviest-path consensus);
  - **C:** quality-weighted consensus (Phred-weighted votes and costs);
  - **D:** homopolymer-aware consensus (run-length-collapsed alignment with run-length voting);
  - **E:** hybrid indel-aware consensus (the V8 full-template polish with candidate-specific move scoring).

  The baseline is the V8 production consensus (Phase 1 full consensus).
- **Development** uses DEV seeds only (§8) and is unrestricted.
- **Evaluation:**
  - Each candidate is frozen (commit SHA recorded), then evaluated **once** on the primary cells with the EVAL seeds.
  - Each candidate decodes the same read pools as the baseline (a paired design).
  - Measured per candidate: EXACT, base-level accuracy, indel accuracy, frames passing inner RS, ORE, median and p95
    runtime, peak RSS, determinism (identical bytes at 1 and 4 workers and on repeat), false success, false frames and
    the failure taxonomy.
- **Selection rule:**
  - Eligibility: 0 false success, 0 false frames, deterministic, and median runtime ≤ 2× the baseline's on the same host
    with the native backend where applicable.
  - Among eligible candidates, the winner has the highest pooled ORE. It must also beat the baseline on paired archive
    outcomes: exact McNemar, two-sided, Holm-corrected over the candidates tested, adjusted p < 0.05.
  - A tie on ORE is broken by lower median runtime.
  - If no candidate qualifies, the V8 consensus stays the default and H3 is rejected.
- Rejected candidates and their results stay committed.

## 6. Adaptive computational coverage (Phase 4; SIMULATED)

- This is a **computational** termination rule over an existing read pool. It does **not** control sequencing hardware.
- **Setup:** per seed, a pool is drawn at mean coverage 15 (NB dispersion 4). Reads are consumed in a seeded random order in
  batches of 0.5× mean coverage.
- **Rule:** after each batch, the decoder computes recoverability: per outer row, the count of strands whose consensus
  confidence passes a threshold, against the row's parity budget.
  - It stops when every row has a margin ≥ m strands, or when the pool is exhausted.
  - The rule's form is fixed now. The two parameters (confidence threshold and m) are tuned on DEV seeds only, then frozen
    before evaluation.
- **False termination:** the rule stops, the decode at the stopping point is not EXACT, and the decode of the full pool of
  the same seed is EXACT.
- **Evaluation:** EVAL seeds and primary cells, against fixed coverage at 3, 5, 7, 10 and 15. Measured: reads consumed,
  coverage used, EXACT, reads avoided relative to the smallest fixed coverage with equal or higher EXACT, false-termination
  rate, recoveries after continuing, and runtime.
- **Acceptance:** 0 false success, and a false-termination Wilson 95 % upper bound ≤ 0.05.

## 7. Native kernel and performance (Phases 1 and 8; SIMULATED inputs)

- **Backends:** `reference` (Python; the correctness oracle, never removed), `native` and `auto` (native if built, else
  reference).
- **Correctness before any timing:**
  - golden vectors (committed hashes of reference outputs);
  - randomised differential testing: ≥ 10⁵ random cases, plus hypothesis property tests;
  - malformed-input tests;
  - archive-byte identity of full decodes;
  - ASan/UBSan under gcc and clang;
  - libFuzzer.

  **Any byte difference stops the work until it is fixed.**
- **Benchmark:**
  - `edit_costs`/`fg` on recorded cluster inputs, plus full noisy decodes of the primary cells (20,000 B; 10 EVAL seeds per
    cell, median and IQR).
  - Native/reference speed ratio with a bootstrap 95 % interval.
  - Host load is recorded with every timing; one heavy job runs at a time.
  - The native backend is accepted only if it is faster with the CI excluding 1.0. **No speed-up target is set in
    advance.**
  - A SIMD path is accepted only under the same rule, with the scalar native path as the reference timing.
- **V8 vs V9 benchmark:** 4 KiB, 1 MiB, 10 MiB, 100 MiB and 1 GiB (encode, clean decode, CPU utilisation, worker scaling
  1/2/4, peak RSS). Noisy decode, consensus time and alignment time are measured at every size up to the size limit of §9.

## 8. Seeds

All seeds are disjoint from V8's (20261010–20261014, 83000–83009, 83100 and the V8 scale and fuzz seeds in
`experiments/v8/V8_MANIFEST.json`).

| purpose | seeds |
|---|---|
| channel fit / pre-check / round trip / comparison | 20261020 / 20261021 / 20261022, 20261023 / 20261024 |
| decoder DEV (development, tuning; unrestricted) | 90000–90099 |
| decoder EVAL (frozen methods only; one evaluation per method) | 91000–91199 (primary cells: 91000–91099) |
| coverage envelope | 92000–92099 |
| archive-size scaling | 93000–93029 |
| benchmarks | 94000–94009 |
| fuzz / adversarial corpora | 95000–95009 |

Seeds are recorded in `experiments/v9/V9_MANIFEST.json`. A seed is never dropped after its result is seen.

## 9. Coverage envelope and archive size (Phases 6 and 7; SIMULATED)

- **Coverages:** 1, 2, 3, 4, 5, 7, 10 and 15 (NB, dispersion 4: the V8 framework). Reported per cell:
  - mean coverage and dispersion;
  - the fractions of strands with 0 reads, 1 read and < 2 reads;
  - row-level failure probability (analytic and empirical);
  - archive-level success with a Wilson 95 % interval.
- **Classes:** the V8 rules, fixed: SUPPORTED requires analytic P(archive) ≥ 0.95 and an empirical Wilson lower bound
  ≥ 0.70. NOT SUPPORTED means analytic < 0.50 or a Wilson upper bound < 0.50. MARGINALLY SUPPORTED otherwise. One
  successful seed never makes a coverage level supported. V8's post-hoc q_struct correction is now pre-specified: q =
  P(reads < 1) for cells where the cluster stage runs in under half the decodes, otherwise P(reads < 2).
- **Archive sizes:** 20 KB, 1 MB, 10 MB, 100 MB and 1 GiB.
  - Clean-channel decodes cover every size.
  - Noisy decodes run at every size whose projected single-decode time on this host (4 workers) is ≤ 2 h, measured from
    the 20 KB and 1 MB runs.
  - Above that, results are the analytic row model with the empirically measured per-strand loss q. They are labelled
    **ANALYTIC EXTRAPOLATION (SIMULATED)** and never reported as decoded.
  - Per-strand, per-row and whole-archive success are always reported separately.

## 10. Statistics, stopping and acceptance

- **Statistics:**
  - proportions: Wilson 95 % intervals;
  - paired archive outcomes: exact McNemar (Holm-corrected across candidates);
  - timings: medians with bootstrap 95 % intervals.
- **Seeds per primary cell:** ≥ 100 (EVAL 91000–91099). The secondary and envelope cells use ≥ 30. If the projected
  compute for a phase exceeds 48 h on this host, the seed count may drop to no fewer than 50 per primary cell. That needs an
  amendment committed before the run, stating the projection.
- **Stopping rules:**
  - Stop and fix before continuing on: any native/reference byte difference; any false success or false frame; any V8
    regression test failure.
  - Stop and report to the founder if the V8 baseline cannot be reproduced, required data are unavailable, or an
    experiment would need the held-out set before §4.4 permits it.
- **Acceptance (V9 complete):**
  - every hypothesis H1–H5 has a committed verdict (accepted or rejected, with numbers);
  - regression suite: 0 failures, with every skip reasoned;
  - false success 0 and false frames 0 across all V9 decodes;
  - sanitizers and fuzzing clean;
  - `experiments/v9/reproduce.sh` regenerates the committed result hashes;
  - every table in `docs/V9_COMPLETION_REPORT.md` names its committed results file.

## 11. Deviations and reproducibility

- **Deviations:** a deviation discovered after data are seen is disclosed in the completion report as post hoc. The
  pre-specified result is kept beside the corrected one (the V8 envelope precedent). Results are never deleted or
  overwritten, and superseded result files stay in history.
- **Reproducibility:**
  - Every result file records the commit, the parameter hash, the model hash and the input hashes, the seeds, the
    environment (Python, NumPy, compiler and flags, CPU) and the host load.
  - Caching is keyed by commit, corpus, seed and parameters, so no V9 result silently reuses a V8 artifact.
  - `docs/V9_REPRODUCTION.md` and `experiments/v9/reproduce.sh` regenerate everything except the 1 GiB runs. Those are
    documented, with their hashes.
