# Pre-registration 2: preset F with frame-level RANSAC (commit cf. git log) vs E — SIMULATED

Written before any run of this evaluation.

- **Change since PREREG.md.** F now also assigns failed frames to missing symbols by frame-level RANSAC. It was
  developed on seeds 93000, 93022, 93027 and 93028 (all now development data; their results are not evidence).
- **Held-out seeds.** 93030 … 93049 (20 seeds, never used before).
- **Case, recording, presets.** As PREREG.md (1 MiB, D13-F1, coverage 10, v4-balanced, workers 4; E and F decode the
  same read file in separate subprocesses; results in `results/eval2.jsonl`).
- **Acceptance (all required).** 0 false success for both presets; no seed where E succeeds and F does not; identical
  output where both succeed. Report the exact-recovery counts of E and F, the paired McNemar exact p-value and the
  median decode seconds of each preset, whatever they are.
- **Label.** SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced.
