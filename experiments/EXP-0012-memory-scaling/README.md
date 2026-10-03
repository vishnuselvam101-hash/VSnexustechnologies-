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

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:17:48Z; environment in `environment.json`; every trial in `results.json`.

| input_size | input MB | status | archive s | encode s | channel s | decode s | encode MB/s | decode MB/s | recoverable MB/s | reads/s | peak RSS MB (process / largest worker) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1048576 | 1.0 | SUCCESS | 0.018 | 0.6417 | None | 0.7903 | 1.589 | 1.327 | 1.327 | 41547.1 | 102.0 / 102.0 |
| 10485760 | 10.5 | SUCCESS | 0.1005 | 1.5325 | None | 2.6196 | 6.421 | 4.003 | 4.003 | 125117.7 | 273.0 / 126.3 |
| 104857600 | 104.9 | SUCCESS | 0.9229 | 10.5275 | None | 23.3109 | 9.158 | 4.498 | 4.498 | 140583.5 | 302.2 / 126.5 |
| 1073741824 | 1073.7 | SUCCESS | 8.8582 | 96.8205 | None | 244.4004 | 10.16 | 4.393 | 4.393 | 137304.7 | 326.1 / 123.7 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
