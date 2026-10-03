# EXP-0006-mixed-channel

**Purpose.** combined substitution + insertion + deletion + dropout at increasing severity, coverage 1 (fixed) and 5 (Poisson)

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** The input is archived and DNA-encoded once. For every channel point and trial, the V4 channel simulator runs with a seed derived from the base seed, point and trial, and `vnx decode` reconstructs the archive from the reads with one worker. A trial is SUCCESS only if the reconstructed container matches the SHA-256 in the superblock and validates structurally; every other outcome is counted in its own column.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0006-mixed-channel/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0006-mixed-channel            # re-run in scratch and compare deterministic fields
```

## Results

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:04:53Z; environment in `environment.json`; every trial in `results.json`.

**results** — 9.8469 nt per input byte, 8247 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| coverage=1, deletion=0.0002, dropout=0.01, insertion=0.0002, level=L1, substitution=0.001 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.3748 |
| coverage=1, deletion=0.0005, dropout=0.02, insertion=0.0005, level=L2, substitution=0.002 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.586 |
| coverage=1, deletion=0.001, dropout=0.05, insertion=0.001, level=L3, substitution=0.005 | 9 | 0 | 1 | 0 | 0 | 0.9 | 0.8088 |
| coverage=5, deletion=0.0002, dropout=0.01, insertion=0.0002, level=L1, substitution=0.001 | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.5508 |
| coverage=5, deletion=0.0005, dropout=0.02, insertion=0.0005, level=L2, substitution=0.002 | 10 | 0 | 0 | 0 | 0 | 1.0 | 2.5744 |
| coverage=5, deletion=0.001, dropout=0.05, insertion=0.001, level=L3, substitution=0.005 | 10 | 0 | 0 | 0 | 0 | 1.0 | 3.6064 |
| coverage=5, deletion=0.002, dropout=0.1, insertion=0.002, level=L4, substitution=0.01 | 2 | 0 | 8 | 0 | 0 | 0.2 | 5.5212 |
| coverage=5, deletion=0.005, dropout=0.15, insertion=0.005, level=L5, substitution=0.02 | 0 | 0 | 10 | 0 | 0 | 0.0 | 9.6649 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
