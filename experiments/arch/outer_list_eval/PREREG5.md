# Pre-registration 5: preset F at a419194 (RANSAC codeword scan) vs E — SIMULATED

Written before any run of this evaluation.

- **Change since PREREG4.md.** Commit a419194: the f hypotheses that agree with the winning RANSAC tuple in every
  column determine its codeword; every failed frame (own variant byte plus the 7 most frequent ones) that agrees with
  exactly one missing symbol's predicted payload in >= `list_ransac_tau` columns, and with no verified symbol as well,
  becomes a candidate of that symbol (new setting `list_ransac_scan_variants`, default 8). Candidates are still
  verified at their own address by inner RS + CRC-32. Developed on seed 93081 (the only F failure of evaluation 4;
  row 171, 61/64 verified symbols): with the change the scan finds 4 true frames and 93081 decodes with the original
  SHA-256. 93081 is development data from now on; its result is not evidence. Seeds 93020–93089 (evaluations 1–4) are
  not reused.
- **Held-out seeds.** 93090 … 93109 (20 seeds, never used before).
- **Case, recording, presets.** As PREREG2.md (1 MiB, D13-F1, coverage 10, v4-balanced, workers 4; E and F decode the
  same read file in separate subprocesses; results in `results/eval5.jsonl`, summary `results/eval5-summary.json`).
  The run uses a clean, detached worktree at the commit that adds this file (no edits during the run); the
  1-minute load at each decode start is recorded (other work may share the 4C/8T host).
- **Hypotheses.** H1: F recovers more seeds exactly than E (expected: E about 1–2/20, F at least the 19/20 of
  evaluation 4). H2: F's median decode time stays within 1.15x of E's (load-dependent; reported with the loads).
- **Acceptance (all required).** 0 false success for both presets (SUCCESS with a container SHA-256 different from
  the original); no seed where E succeeds and F does not; identical output where both succeed. Report the
  exact-recovery counts of E and F, the paired McNemar exact p-value (two-sided) and the median decode seconds of
  each preset, whatever they are; list every F failure with its short rows.
- **Label.** SIMULATED: reads from a software channel model (D13-F1, nanopore-like); no DNA was synthesised, stored
  or sequenced. Not a claim about real nanopore data.
