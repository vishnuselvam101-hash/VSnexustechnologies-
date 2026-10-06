# V8 pre-registration: D13 public-data nanopore channel model

Committed before any D13 segment of V8 is produced, any V8 model is fitted, or any V8 DEV or held-out statistic is
computed. Changes after this commit need a dated amendment (`V8_PREREGISTRATION-A<n>.md`), written and committed before
the data it concerns is looked at, with the reason. A threshold is never changed because a model failed it.

## 1. Data and splits

- Source: D13, Lopez et al., Nat Commun 10:2933 (2019), GitHub `uwmisl/data-ncomms19-nanopore` commit `4bf31ff4`. File
  SHA-256 values are pinned in `experiments/v7/datasets/MANIFEST.json` and repeated in `docs/V8_DATA_PROVENANCE.md`.
  Licence: none stated, so internal use only. Only aggregate statistics are committed: no reads, references or per-read
  values.
- Runs: 15 and 18 (apollo), 16 (365-dishes), 20 (vitruvian). Per run, the **first 100,000 read IDs in
  SHA-256("VNX-D3-SUBSAMPLE/" + ID) order**: the V7 D3 deterministic subsample (PR-9), the same reads V7 characterised
  (run 18: all 79,449).
- Segmentation: `experiments/v7/nanodata/nanolib.split_read` with the frozen V7 constants (PR-4.2: k 16, max_occ 8, min_hits
  3, diag_tol 24, pad 30, max_err 0.30, margin 5, overlap 0.5). Ambiguous segments are discarded. Segments are oriented to
  the reference strand.
- Splits: by canonical reference bucket `int(SHA-256(canonical ref), 16) mod 10`: **FIT 0–5, DEV 6–7, HELD-OUT 8–9**.
  **Run 13 (space_shuttle) is HELD-OUT as a whole.** Read-level statistics are split by read-ID bucket with the same
  mapping. Held-out segments are discarded uncounted, apart from a count, until a PREREG commit names this document.
- Prior exposure, disclosed: V7 D3 computed aggregate FIT and DEV statistics of these runs (first 100,000 reads) against the
  V6 nanopore-like simulator (`experiments/v7/nanodata/results/`). No model was fitted or selected on them. V8 therefore
  treats D13 DEV as *descriptively seen*. Its value as a model-selection check is intact, because no V8 parameter or
  decision is derived from those V7 numbers. The V7 run-13 header summary disclosure (V7_PROTOCOL_AMENDMENT_D3 §1) applies.

## 2. Model families (fixed now)

- **F1:** the V7 a7c structure fitted to the pooled D13 FIT segments. That means empirical insertion/deletion run lengths,
  per-run-length indel multipliers, and the FIT-chosen `min_run`, position profiles, 3-mer contexts and per-read
  heterogeneity (V7 `choose_design`), plus the quality model estimated from FASTQ qualities. Tally `max_run` is 32 (V7 used
  16; V7 diagnosis showed the tail mean censored).
- **F2:** F1 plus a paired-indel process (an insertion and a deletion within 2 reference bases, as one event). F2 is
  fitted **only if** the F1 FIT pre-check fails M3 and the FIT reads show an excess of co-located insertion+deletion pairs
  over F1 simulated reads (ratio > 1.5). That is the V7 diagnosis criterion, decided now.
- The candidate is the first of F1, F2 whose FIT-only pre-check passes every gating metric in sample. If neither passes,
  the decision is `DEV_LOOK_BLOCKED — FIT PRE-CHECK INADEQUATE`; no DEV look is spent and the model is INADEQUATE.

## 3. Metrics (V7 7B + A1, with the V8 amendments below)

- Gating: M1a, M1, M1c, M2, M2b, M3, M5, M5i, M6r, M8, RL, M10-V8. Thresholds are those of
  `experiments/v7/fit-nano/PREREGISTRATION.md` and amendment A1, unchanged.
- **M10-V8.** The V7 diagnosis showed the V7 M10 rule statistically inappropriate: its bootstrap SE omits calibration
  noise, sparse bins have SE ≈ 0, and non-identifiable components are tested separately. Each parameter element must
  satisfy |refit − target| ≤ 5·SE + 8 % |target|, where SE² = SE_boot² + SD_rep², and SD_rep is the SD over **3**
  independent simulate+refit replicates.
  - **Sparse elements are reported but not gated:** a run-length pmf bin with target < 1e-3, or a `tail_mean` with fewer
    than 200 tail events in the round-trip simulation.
  - When `estimate.hp_sub_fixed` holds, the substitution matrix and the substitution context are gated through their
    **identifiable product**, the per-3-mer substitution rate of the refit vs the target. The separate components are
    reported but not gated.
  - Scalars must all pass; vectors must have ≥ 99 % of gated elements passing.
- Reported, not gating: M4, M7 (coverage), M9 (quality, now identifiable), and M1 per run. M1 per run is reported
  prominently, and a pooled model failing a run's M1 is stated as a limitation, not hidden.
- Every metric report gives observed, model, absolute error, relative error, uncertainty, sample count, pass/fail and
  identifiability.

## 4. Look budget and decision rules

1. **FIT-only pre-check** (V7 `precheck` design: full FIT + two halves, M10-V8 at two seeds) before any DEV look.
2. **DEV looks: at most 2** for the D13 model, and only for a candidate that passed the pre-check and is frozen
   (`FREEZE.json`: model SHA-256, dataset hashes, fitter version, seed, metric definitions, thresholds).
3. **Held-out: exactly one** evaluation, only for a frozen candidate that is ADEQUATE on DEV. Its result is ADEQUATE or
   INADEQUATE. Nothing is tuned after it, and there is no second attempt.
4. **Comparison (V8.6):** one DEV evaluation of A (V6 shipped `nanopore-like`), B (V7 a7b `d03-hac-fwd`), C (V7 a7c
   `d03-hac-fwd`, per-run-length) and D (the V8 D13 candidate, or F1 if no candidate passed). It runs with the same
   harness after the D verdict is fixed. It selects nothing and does not count against D's look budget.

## 5. Decoder evaluation (V8.8; SIMULATED)

- Coverages 3, 5, 10 (fixed). Channels: `clean`, `substitution-heavy`, `insertion-heavy`, `deletion-heavy`, `mixed-harsh`
  (shipped models), plus the D13-derived model labelled with its adequacy verdict. If that verdict is not ADEQUATE, the D13
  column is a **stress condition, not a realistic-channel claim**.
- Profiles v4-balanced and v7-lowcov, Phase 1 full consensus. Seeds **83000–83009** (10 per cell), reported with Wilson
  95 % intervals.
- Every non-EXACT decode gets a failure-stage class (V8.9), and `classified == total` is required. FALSE SUCCESS is
  counted in every cell.

## 6. What would make V8 a successful scientific release

The criteria in the V8 specification. If the D13 model is INADEQUATE, V8 is still successful when it identifies the
limitation conclusively. A decoder pass is never reported as model adequacy.
