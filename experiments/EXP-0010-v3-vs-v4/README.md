# EXP-0010-v3-vs-v4

**Purpose.** V3 (unchanged) vs V4 under the identical channel and seeds

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** V3 (unchanged CLI, default balanced profile) and V4 (v4-balanced) receive the same input, the same channel implementation, parameters and per-trial seeds, and the same success criterion (SHA-256-identical output). V3 at coverage 1 runs both with defaults and with its single-read indel + burst repair; at coverage > 1 it runs cluster → consensus → decode.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0010-v3-vs-v4/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0010-v3-vs-v4            # re-run in scratch and compare deterministic fields
```

## Results

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:07:28Z; environment in `environment.json`; every trial in `results.json`.

Input {'pattern': 'random', 'seed': 42, 'size': 131072}. V3 balanced: 8.0846 nt/byte; V4 v4-balanced: 9.9198 nt/byte. identical input, channel implementation, channel parameters and per-trial seeds; success = SHA-256-identical output; redundancy budgets differ (see nt_per_input_byte).

| channel | trials | V3 default | V3 best (coverage 1: indel + burst repair) | V4 | V3 median s | V4 median s |
|---|---|---|---|---|---|---|
| coverage=1, substitution=0.002 | 6 | 1.0 | 1.0 | 1.0 | 1.399 | 0.096 |
| coverage=1, deletion=0.0005 | 6 | 1.0 | 1.0 | 1.0 | 1.443 | 0.227 |
| coverage=1, deletion=0.001 | 6 | 0.0 | 1.0 | 1.0 | 0.648 | 0.266 |
| coverage=1, deletion=0.002 | 6 | 0.0 | 1.0 | 1.0 | 0.542 | 0.403 |
| coverage=1, deletion=0.001, insertion=0.001 | 6 | 0.0 | 1.0 | 1.0 | 0.588 | 0.417 |
| coverage=1, deletion=0.002, insertion=0.002 | 6 | 0.0 | 0.1667 | 1.0 | 0.585 | 0.719 |
| coverage=1, dropout=0.1 | 6 | 1.0 | 1.0 | 1.0 | 1.073 | 0.076 |
| coverage=5, deletion=0.001, dropout=0.02, insertion=0.001, substitution=0.005 | 6 | 1.0 | n/a (coverage > 1 uses cluster + consensus) | 1.0 | 9.659 | 1.932 |
| coverage=5, deletion=0.004, dropout=0.02, insertion=0.004, substitution=0.01 | 6 | 0.0 | n/a (coverage > 1 uses cluster + consensus) | 1.0 | 25.58 | 4.3 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
