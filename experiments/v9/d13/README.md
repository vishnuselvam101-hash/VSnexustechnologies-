# V9 D13 channel model G1 (Phase 5)

There are two evidence classes here:

- **PUBLIC-DATA-DERIVED:** the parameters, fitted to the D13 FIT split (Lopez et al. 2019). VNX-DNA sequenced nothing.
- **SIMULATED:** reads generated from the models.

The relevant documents are the pre-registration (`docs/V9_PREREGISTRATION.md` §4) and amendment A2. Every data access
is logged in `experiments/v8/datasets/ACCESS_LEDGER.jsonl`. The held-out data was not opened.

## G1 (`models/d13-nanopore-g1.json`, model 6ebd8c04)

G1 is V8 F1 with F1's gamma read multiplier replaced by K latent read classes (A2).

- **Data:** 266,883 FIT segments, from every 10th FIT table row (`results/fit-g1.json`).
- **Choice of K:** BIC for K = 1 / 2 / 3 / 4 is 4,428,451 / 3,612,871 / 3,519,515 / 3,487,649, so **K = 4**.
- **Calibration:** after 4 iterations, the simulated/real rate ratios are within 0.4 %.

The four classes, as multipliers of the mean rate:

| Class | Weight | Substitution | Insertion | Deletion |
|---|---|---|---|---|
| 1 | 0.467 | 0.32 | 0.39 | 0.53 |
| 2 | 0.363 | 1.06 | 1.06 | 1.10 |
| 3 | 0.100 | 2.53 | 3.14 | 1.43 |
| 4 | 0.070 | 3.00 | 1.72 | 3.01 |

## FIT-only pre-check (`results/precheck-g1.json`, `results/VERDICT.json`)

This check is in sample on FIT; it is not validation.

**Verdict: INADEQUATE.** The decision is `DEV_LOOK_BLOCKED — FIT PRE-CHECK INADEQUATE`.

- **Passes:** M1a, M1, M1c, M2, M2b, M3, M5, M5i and RL. F1 failed M1c, M2, M2b and M3.
- **Fails stably:**
  - **M6r**, at homopolymer run length 6+: observed 0.110, model 0.122.
  - **M8**, the consensus curve.
- **Fails, plausibly noise:** M10.

Consequences:

- G2 is not fitted, because its pre-registered trigger is a G1 failure of M3 or M2b, and G1 passes both.
- No DEV look was spent.
- The held-out set stays closed and is carried to V10 unopened.

## Comparison on DEV (`results/comparison.json`)

This is one evaluation. It selects nothing and is not a DEV look for G1. DEV holds 894,901 segments over 42,263
references.

| Model | 7B metrics failed on D13 DEV (M10 excluded) |
|---|---|
| A: V6 shipped nanopore-like | all 11 |
| B: V7 a7b d03-hac-fwd | all 11 |
| C: V8 F1 (D13 FIT) | M1c, M2, M2b, M3, M6r, M8 |
| D: V9 G1 (D13 FIT) | M6r, M8 |

The latent read classes fix the read-level distributions: the edit distance (M2), the per-read rate (M2b) and drift
(M3). They also fix the pooled spectrum (M1c).

Two failures remain:

- **M6r:** the long-homopolymer stratum is still mis-modelled. The model gives too many errors at run length 6+.
- **M8:** the consensus curve does not match.

These two are the open items for V10's channel model.
