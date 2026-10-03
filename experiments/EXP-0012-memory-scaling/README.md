# EXP-0012-memory-scaling

**Purpose.** peak RSS and throughput vs input size, clean DNA path (1 MiB - 1 GiB)

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** Clean end-to-end DNA path at increasing input sizes, each in a fresh process; peak RSS of the process and of its largest worker.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0012-memory-scaling/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0012-memory-scaling            # re-run in scratch and compare deterministic fields
```

## Results

Commit `2cd82b316ff6729b469744f76549b51b98183d7b-dirty`, 2026-10-03T02:43:02Z; environment in `environment.json`; every trial in `results.json`.

| input_size | input MB | status | archive s | encode s | channel s | decode s | encode MB/s | decode MB/s | recoverable MB/s | reads/s | peak RSS MB (process / largest worker) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1048576 | 1.0 | SUCCESS | 0.0188 | 0.6192 | None | 0.7922 | 1.643 | 1.324 | 1.324 | 41446.8 | 100.1 / 100.1 |
| 10485760 | 10.5 | SUCCESS | 0.1007 | 1.4941 | None | 2.554 | 6.575 | 4.106 | 4.106 | 128332.8 | 274.8 / 120.7 |
| 104857600 | 104.9 | SUCCESS | 0.8907 | 9.9336 | None | 22.3168 | 9.687 | 4.699 | 4.699 | 146845.9 | 304.0 / 125.7 |
| 1073741824 | 1073.7 | SUCCESS | 9.2313 | 92.3926 | None | 231.2099 | 10.566 | 4.644 | 4.644 | 145137.9 | 322.1 / 122.9 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
