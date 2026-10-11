# Pre-registration: outer-assisted short-row recovery (decoder preset F) vs preset E — SIMULATED

Written before any held-out run (branch arch/hybrid-native, commit 1cb416d).

- **Question.** Does preset F (E + `outer_list`) recover nanopore-like 1 MiB archives that E fails, without ever
  reporting a wrong success or losing a case E recovers?
- **Development data (not evidence).** Seed 93000 was used to design F (E: EXPLICIT_FAILURE, 2 rows short; F: SUCCESS).
- **Held-out seeds.** 93020 … 93029 (never used by any VNX-DNA experiment before this run).
- **Case.** 1 MiB random input (nanofunnel.build_archive, profile v4-balanced), channel D13-F1 (experiments/v8/matrix
  case_model), coverage 10, data and channel seed = case seed; decoder workers 4. Both presets decode the same read
  file, each in its own subprocess.
- **Recorded.** Outcome, output SHA-256 vs the original container, failed groups, seconds, host load, F's outer_list
  counters.
- **Acceptance (all required).** 0 false success (SUCCESS with a wrong container) for both presets; no seed where E
  succeeds and F does not; identical output where both succeed. The SUCCESS counts of E and F are reported as
  measured, whatever they are, with a paired McNemar exact p-value.
- **Label.** SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced.
