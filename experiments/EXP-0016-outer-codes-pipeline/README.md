# EXP-0016-outer-codes-pipeline

**Purpose.** full DNA pipeline under strand dropout: three outer codes at the same 25% redundancy (parity/data), coverage 1, no base errors

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** The input is archived and DNA-encoded once. For every channel point and trial, the V4 channel simulator runs with a seed derived from the base seed, point and trial, and `vnx decode` reconstructs the archive from the reads with one worker. A trial is SUCCESS only if the reconstructed container matches the SHA-256 in the superblock and validates structurally; every other outcome is counted in its own column.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0016-outer-codes-pipeline/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0016-outer-codes-pipeline            # re-run in scratch and compare deterministic fields
```

## Results

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:05:44Z; environment in `environment.json`; every trial in `results.json`.

**cauchy-rs 64+16 (default)** — 9.8469 nt per input byte, 8247 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| dropout=0.05 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.152 |
| dropout=0.1 | 9 | 0 | 1 | 0 | 0 | 0.9 | 0.1766 |
| dropout=0.12 | 2 | 0 | 8 | 0 | 0 | 0.2 | 0.1461 |
| dropout=0.14 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.147 |
| dropout=0.16 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.1488 |
| dropout=0.18 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.1417 |
| dropout=0.2 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.1291 |

**cauchy-rs 200+50** — 9.8493 nt per input byte, 8249 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| dropout=0.05 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.1668 |
| dropout=0.1 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.1919 |
| dropout=0.12 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.1757 |
| dropout=0.14 | 9 | 0 | 1 | 0 | 0 | 0.9 | 0.1827 |
| dropout=0.16 | 1 | 0 | 9 | 0 | 0 | 0.1 | 0.1892 |
| dropout=0.18 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.1855 |
| dropout=0.2 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.1503 |

**lt-fountain dense 256+64 (EXPERIMENTAL)** — 9.8457 nt per input byte, 8246 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| dropout=0.05 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.2986 |
| dropout=0.1 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.2777 |
| dropout=0.12 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.2565 |
| dropout=0.14 | 9 | 0 | 1 | 0 | 0 | 0.9 | 0.2542 |
| dropout=0.16 | 3 | 0 | 7 | 0 | 0 | 0.3 | 0.2535 |
| dropout=0.18 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.2292 |
| dropout=0.2 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.1747 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
