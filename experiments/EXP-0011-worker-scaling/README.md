# EXP-0011-worker-scaling

**Purpose.** 1/2/4/8 worker scaling of archive, encode, channel and decode (16 MiB, mixed errors, coverage 3)

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** The same end-to-end run (generate → archive → encode → channel → decode → extract → SHA-256) with 1, 2, 4 and 8 workers, each in a fresh process (peak RSS per process).

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0011-worker-scaling/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0011-worker-scaling            # re-run in scratch and compare deterministic fields
```

## Results

Commit `2cd82b316ff6729b469744f76549b51b98183d7b-dirty`, 2026-10-03T02:38:37Z; environment in `environment.json`; every trial in `results.json`.

| workers | input MB | status | archive s | encode s | channel s | decode s | encode MB/s | decode MB/s | recoverable MB/s | reads/s | peak RSS MB (process / largest worker) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 16.8 | SUCCESS | 0.2094 | 6.0607 | 39.411 | 87.0671 | 2.676 | 0.193 | 0.193 | 17690.0 | 157.0 / 0.0 |
| 2 | 16.8 | SUCCESS | 0.1601 | 3.5197 | 17.7031 | 44.9292 | 4.559 | 0.373 | 0.373 | 34280.9 | 150.3 / 126.9 |
| 4 | 16.8 | SUCCESS | 0.1575 | 2.1954 | 10.2511 | 24.7079 | 7.13 | 0.679 | 0.679 | 62337.0 | 178.3 / 137.3 |
| 8 | 16.8 | SUCCESS | 0.1588 | 1.5963 | 7.4893 | 16.4124 | 9.559 | 1.022 | 1.022 | 93844.7 | 216.6 / 171.0 |

| workers | encode speed-up | decode speed-up | decode scaling efficiency |
|---|---|---|---|
| 1 | 1.00× | 1.00× | 100% |
| 2 | 1.72× | 1.94× | 97% |
| 4 | 2.76× | 3.52× | 88% |
| 8 | 3.80× | 5.30× | 66% |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
