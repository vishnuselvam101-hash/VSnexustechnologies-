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

Commit `2cd82b316ff6729b469744f76549b51b98183d7b-dirty`, 2026-10-03T02:29:09Z; environment in `environment.json`; every trial in `results.json`.

**results** — 9.5526 nt per input byte, 2115 strands of 296 nt, 5 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| coverage=1 | 0 | 0 | 5 | 0 | 0 | 0.0 | 0.485 |
| coverage=2 | 0 | 0 | 5 | 0 | 0 | 0.0 | 0.8806 |
| coverage=5 | 1 | 0 | 4 | 0 | 0 | 0.2 | 2.4402 |
| coverage=10 | 5 | 0 | 0 | 0 | 0 | 1.0 | 4.642 |
| coverage=20 | 5 | 0 | 0 | 0 | 0 | 1.0 | 9.1078 |
| coverage=30 | 5 | 0 | 0 | 0 | 0 | 1.0 | 14.5678 |
| coverage=50 | 5 | 0 | 0 | 0 | 0 | 1.0 | 24.5225 |
| coverage=100 | 5 | 0 | 0 | 0 | 0 | 1.0 | 47.9639 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
