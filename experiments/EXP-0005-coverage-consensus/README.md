# EXP-0005-coverage-consensus

**Purpose.** recovery and decode cost vs coverage (1-100x) under heavy mixed errors (1% sub, 0.4% ins, 0.4% del)

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** The input is archived and DNA-encoded once. For every channel point and trial, the V4 channel simulator runs with a seed derived from the base seed, point and trial, and `vnx decode` reconstructs the archive from the reads with one worker. A trial is SUCCESS only if the reconstructed container matches the SHA-256 in the superblock and validates structurally; every other outcome is counted in its own column.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0005-coverage-consensus/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0005-coverage-consensus            # re-run in scratch and compare deterministic fields
```

## Results

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:03:14Z; environment in `environment.json`; every trial in `results.json`.

**results** — 10.1012 nt per input byte, 2115 strands of 313 nt, 5 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| coverage=1 | 0 | 0 | 5 | 0 | 0 | 0.0 | 0.5799 |
| coverage=2 | 0 | 0 | 5 | 0 | 0 | 0.0 | 1.011 |
| coverage=5 | 5 | 0 | 0 | 0 | 0 | 1.0 | 2.3942 |
| coverage=10 | 5 | 0 | 0 | 0 | 0 | 1.0 | 4.449 |
| coverage=20 | 5 | 0 | 0 | 0 | 0 | 1.0 | 8.7657 |
| coverage=30 | 5 | 0 | 0 | 0 | 0 | 1.0 | 13.22 |
| coverage=50 | 5 | 0 | 0 | 0 | 0 | 1.0 | 22.6814 |
| coverage=100 | 5 | 0 | 0 | 0 | 0 | 1.0 | 43.481 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
