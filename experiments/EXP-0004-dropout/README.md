# EXP-0004-dropout

**Purpose.** recovery vs strand dropout (coverage 1, no base errors) for two redundancy budgets

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** The input is archived and DNA-encoded once. For every channel point and trial, the V4 channel simulator runs with a seed derived from the base seed, point and trial, and `vnx decode` reconstructs the archive from the reads with one worker. A trial is SUCCESS only if the reconstructed container matches the SHA-256 in the superblock and validates structurally; every other outcome is counted in its own column.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0004-dropout/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0004-dropout            # re-run in scratch and compare deterministic fields
```

## Results

Commit `2cd82b316ff6729b469744f76549b51b98183d7b-dirty`, 2026-10-03T02:28:53Z; environment in `environment.json`; every trial in `results.json`.

**v4-balanced cauchy-rs 64+16 (20% parity)** — 9.3121 nt per input byte, 8247 strands of 296 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| dropout=0 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.1366 |
| dropout=0.01 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.1866 |
| dropout=0.05 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.1773 |
| dropout=0.1 | 8 | 0 | 2 | 0 | 0 | 0.8 | 0.1741 |
| dropout=0.15 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.1751 |
| dropout=0.2 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.1419 |
| dropout=0.3 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.0829 |
| dropout=0.4 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.0656 |
| dropout=0.5 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.0543 |

**v4-archival cauchy-rs 32+32 (50% parity)** — 17.5017 nt per input byte, 14658 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| dropout=0 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.3024 |
| dropout=0.01 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.2621 |
| dropout=0.05 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.2576 |
| dropout=0.1 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.2908 |
| dropout=0.15 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.2911 |
| dropout=0.2 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.3014 |
| dropout=0.3 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.3157 |
| dropout=0.4 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.2989 |
| dropout=0.5 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.195 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
