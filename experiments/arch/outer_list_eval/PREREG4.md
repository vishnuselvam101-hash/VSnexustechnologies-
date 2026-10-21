# Pre-registration 4: preset F at a12afb4 (hypothesis selection and inner-trial fixes) vs E — SIMULATED

Written before any run of this evaluation.

- **Change since PREREG3.md.** Commit a12afb4: RANSAC hypotheses ranked by group ambiguity, header-assigned
  candidates as RANSAC anchors with their assigned variant, best-first tuple order (C++ kernel and reference),
  tied placement of frames with damaged symbol bytes, payload consistency over the common variant bytes, inner RS
  trials for every candidate (with its variant, and with the disagreeing proposal columns erased on their own).
  Developed on the F failures of evaluations 2 and 3 (seeds 93032, 93034, 93037, 93044, 93046, 93054, 93057, 93059,
  93062, 93069) and checked on 93022, 93027, 93028: all development data, their results are not evidence. Offline,
  on the 39 short rows of those 10 seeds, ede0b80 recovered 29 rows exactly and a12afb4 34 (0 wrong). Seeds
  93020–93069 (evaluations 1–3) are not reused.
- **Held-out seeds.** 93070 … 93089 (20 seeds, never used before).
- **Case, recording, presets.** As PREREG2.md (1 MiB, D13-F1, coverage 10, v4-balanced, workers 4; E and F decode the
  same read file in separate subprocesses; results in `results/eval4.jsonl`, summary `results/eval4-summary.json`).
- **Hypotheses.** H1: F recovers more seeds exactly than E (expected: E about 1–2/20 as in evaluations 2–3, F above
  the 15/20 of evaluations 2–3, since the development rows that remain short are 4–5 symbols short, which was 4 of
  the 10 development failures). H2: F's median decode time stays within 1.15x of E's (evaluation 3: 151.6 s vs
  139.9 s).
- **Acceptance (all required).** 0 false success for both presets (SUCCESS with a container SHA-256 different from
  the original); no seed where E succeeds and F does not; identical output where both succeed. Report the
  exact-recovery counts of E and F, the paired McNemar exact p-value (two-sided) and the median decode seconds of
  each preset, whatever they are; list every F failure with its short rows.
- **Label.** SIMULATED: reads from a software channel model (D13-F1, nanopore-like); no DNA was synthesised, stored
  or sequenced. Not a claim about real nanopore data.
