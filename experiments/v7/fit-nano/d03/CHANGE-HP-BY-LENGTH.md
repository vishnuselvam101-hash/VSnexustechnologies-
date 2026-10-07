# Controlled change for DEV look 2: per-run-length homopolymer indel multiplier

Evidence class of the motivating numbers: SIMULATED vs PUBLIC-DATA-DERIVED (D03 DEV look 1, `README.md` in this folder).
Written before any implementation (V7 reset directive: every change answers 8 questions).

## 1. Hypothesis
The real per-site indel rate depends on the length of the homopolymer run the site lies in, and the dependence is not a single
step. DEV look 1, M6r, real indel rate by run length 1 / 2 / 3 / 4 / 5 / 6+:
fast-fwd 4.47 / 4.37 / 5.99 / 7.06 / 7.51 / 8.13 %, fast-bwd 4.94 / 4.28 / 5.21 / 6.75 / 7.02 / 7.17 %
(70k to 12M sites per bin, bootstrap SE ≤ 0.17 pp). The /2 model has one threshold (`min_run`) and one multiplier, so the simulated
rate is flat on both sides of the step (fast-fwd simulated 4.5 / 5.0 / 4.9 / 5.0 / 4.7 / 4.3 %). Replacing the step by one
multiplier per run length (1, 2, 3, 4, 5, 6+) lets the simulator reproduce the observed ramp; M6r then passes.

## 2. Failure addressed
M6r, failed by all four D03 models at DEV look 1. This change does not address M3 (fraction of zero-drift reads), M1 (insertion
rate on the bwd models) or M10 (round trip on small pmf bins and the 64-value substitution context); those are diagnosed
separately on FIT and SIMULATED data (step 6 below), not by this change.

## 3. Experiment
- Schema: optional `/2` field `sequencing.homopolymer.indel_by_length` = 6 multipliers (run length 1..5, 6+), each in [0, 100].
  When present it replaces `indel_multiplier`, which must then be 1. Substitutions keep `min_run` and
  `substitution_multiplier`. Absent field: canonical form, SHA-256 and the draws are unchanged. `/1` cannot carry it, and
  `to_v1` refuses.
- Simulator: per-site indel rate × multiplier of the site's run length. Rates only change, so no new random draws.
- Fitter: design flag `hp_by_length`. The joint Poisson ML (IPF) of the insertion and deletion rates and per-class multipliers
  uses the per-length site/event tables. Those come from differencing the min_run 2..6 context blocks, with the class-1
  multiplier fixed at 1. Calibration adds a damped per-class update and a `hp_len` convergence ratio.
- Unit tests (SIMULATED): validation and refusal, canonical form, /1 refusal, engine rate by run length, fitter recovery of a
  known ramp, and M6r accept/reject.
- FIT-only pre-check (no DEV, no held-out): on the four D03 FIT sets, compare simulated tallies against FIT tallies with the
  same metric code (M1, M3, M6r) and run M10 (SIMULATED round trip). These are in-sample numbers and are recorded as such.
- DEV look 2, the last: refit the four D03 models (round `a7c`, version 3.1.0, outputs in `fit-nano/d03-look2/`). Validate
  with prereg 7B + A1 and commit the split ledger after every run.

## 4. Acceptance
- Change accepted (software): full suite passes, the test count does not drop, and ruff and mypy are clean. The recovery test
  gets every class multiplier within 10 % of truth, and fails with the per-class update disabled.
- Change effective: M6r passes at DEV look 2 for at least one model.
- A model is frozen only if the prereg 7B verdict is ADEQUATE (every gating metric). Otherwise the verdict stays
  INADEQUATE / NEEDS MORE DATA, and no held-out evaluation runs.
- DEV look 2 is spent only if the FIT pre-check shows M6r passing in-sample for at least one model. If M1, M3 or M10 also
  fail in-sample for every model, DEV look 2 waits for those diagnoses instead of being used on a model known to fail.

## 5. Held-out
Not touched. Held-out data is opened once, and only for a model frozen in a prereg commit after an ADEQUATE DEV verdict.

## 6. False-success risk
- Overfitting: 6 parameters estimated from ≥ 75k FIT sites per class; M6r is judged on DEV, not FIT.
- Silent compatibility drift: golden SHA-256 and determinism tests for models without the field.
- The pre-check is in-sample and is never reported as validation.
- Decoder FALSE SUCCESS is not affected: no codec code changes.

## 7. Compatibility
Opt-in field. Existing /1 and /2 models, SHA-256 values and draws are unchanged. The fitter only uses it under `hp_by_length`;
the `a2` and `a7b` rounds reproduce as committed.

## 8. Benchmark
Simulation cost: one integer lookup per site. Fit cost: one extra IPF over 6×64 cells. Recorded with the fit timings
(≤ 4 workers, load average logged).
