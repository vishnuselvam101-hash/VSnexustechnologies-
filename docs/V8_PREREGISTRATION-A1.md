# V8 pre-registration amendment A1: matched read selection on D13 (V7 protocol A2.2)

Date: 2026-10-07. Written and committed **before** the F1 pre-check is re-run, and before any DEV or held-out access. No
DEV statistic exists for any V8 model.

## Finding (FIT only, in sample)

The first F1 FIT-only pre-check (`experiments/v8/d13/results/precheck-f1.json`, commit before this one) failed M2, M2b and
M3. The cause is a selection mismatch, not a fitted parameter:

- Every real D13 segment was accepted by the frozen segmentation rule `max_err = 0.30`: its edit distance is
  ≤ 0.30 × 150 = 45. In the FIT tables, **0 of 2,673,871** segments have an edit distance above 45.
- The simulated reads that M2, M2b and M3 compare against were **not** selected. Their edit-distance and drift histograms
  include reads above 45: simulated p99 is 55 vs 44 real.
- V7 protocol 5.5 A2.2 (retained by V8 pre-registration §3) requires that "wherever real and simulated reads are compared,
  the dataset's read selection is applied to simulated reads too". The V8 harness did not apply the D13 selection. That
  is an implementation defect against the pre-registered protocol.

## Amendment

1. In every D13 real-vs-simulated comparison (pre-check metrics, M10-V8 round trips, calibration, DEV and held-out
   evaluations), simulated reads whose global edit distance to their reference exceeds `0.30 × reference length` are
   removed before any statistic. That is the D13 segmentation acceptance rule. It is applied through the tally option
   `max_edit_frac = 0.30`: a removed read is counted in `window_out` and contributes to nothing else. Real segments
   already satisfy the rule.
2. Nothing else changes: the model families, fitted F1 parameters, metric definitions, thresholds, look budget and
   seeds all stay as they were. F1 is **not refitted**. Its calibration compared event tallies, which already excluded
   reads above 0.30 × L, so the fitted parameters do not depend on this defect. The pre-check is re-run on the same F1
   model (same SHA-256).
3. Both pre-check results are kept and reported: the first as `precheck-f1.json` (superseded, with the defect described
   here) and the re-run as `precheck-f1-a1.json`.

## Why this is not tuning

The rule restores a comparison procedure that was pre-registered (V7 A2.2) before any V8 data was read. It uses a
selection constant frozen in V7 (PR-4.2 `max_err`). No metric threshold, model parameter or decision rule is changed, and
the amendment is written before its effect on any metric is known.
