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

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:12:51Z; environment in `environment.json`; every trial in `results.json`.

| workers | input MB | status | archive s | encode s | channel s | decode s | encode MB/s | decode MB/s | recoverable MB/s | reads/s | peak RSS MB (process / largest worker) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 16.8 | SUCCESS | 0.2058 | 6.3168 | 40.1078 | 100.199 | 2.572 | 0.167 | 0.167 | 15371.6 | 162.9 / 0.0 |
| 2 | 16.8 | SUCCESS | 0.1551 | 3.6533 | 20.6637 | 51.5901 | 4.405 | 0.325 | 0.325 | 29854.8 | 156.9 / 130.3 |
| 4 | 16.8 | SUCCESS | 0.1584 | 2.2068 | 10.7969 | 28.6061 | 7.093 | 0.586 | 0.586 | 53842.2 | 172.4 / 140.5 |
| 8 | 16.8 | SUCCESS | 0.1617 | 1.6478 | 7.9929 | 18.4316 | 9.272 | 0.91 | 0.91 | 83563.6 | 243.2 / 195.1 |

| workers | encode speed-up | decode speed-up | decode scaling efficiency |
|---|---|---|---|
| 1 | 1.00× | 1.00× | 100% |
| 2 | 1.73× | 1.94× | 97% |
| 4 | 2.86× | 3.50× | 88% |
| 8 | 3.83× | 5.44× | 68% |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
