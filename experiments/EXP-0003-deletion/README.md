# EXP-0003-deletion

**Purpose.** recovery vs deletion rate at coverage 1 and 5

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** The input is archived and DNA-encoded once. For every channel point and trial, the V4 channel simulator runs with a seed derived from the base seed, point and trial, and `vnx decode` reconstructs the archive from the reads with one worker. A trial is SUCCESS only if the reconstructed container matches the SHA-256 in the superblock and validates structurally; every other outcome is counted in its own column.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0003-deletion/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0003-deletion            # re-run in scratch and compare deterministic fields
```

## Results

Commit `790d4f4ce14ef4d288a8acaf182a1fd9887a1bc4-dirty`, 2026-10-03T02:27:53Z; environment in `environment.json`; every trial in `results.json`.

**results** — 9.3121 nt per input byte, 8247 strands of 296 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| coverage=1, deletion=0 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.1465 |
| coverage=1, deletion=0.0001 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.3206 |
| coverage=1, deletion=0.0005 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.4158 |
| coverage=1, deletion=0.001 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.6272 |
| coverage=1, deletion=0.005 | 0 | 0 | 10 | 0 | 0 | 0.0 | 1.6261 |
| coverage=1, deletion=0.01 | 0 | 0 | 10 | 0 | 0 | 0.0 | 2.2548 |
| coverage=5, deletion=0 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.5648 |
| coverage=5, deletion=0.0001 | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.1421 |
| coverage=5, deletion=0.0005 | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.7996 |
| coverage=5, deletion=0.001 | 10 | 0 | 0 | 0 | 0 | 1.0 | 2.7426 |
| coverage=5, deletion=0.005 | 10 | 0 | 0 | 0 | 0 | 1.0 | 6.8886 |
| coverage=5, deletion=0.01 | 10 | 0 | 0 | 0 | 0 | 1.0 | 9.603 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
