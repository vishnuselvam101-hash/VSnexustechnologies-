# EXP-0009-marker-design

**Purpose.** marker period/length design study: success vs nucleotide cost at fixed indel rate (0.2% ins + 0.2% del, coverage 1)

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** The input is archived and DNA-encoded once. For every channel point and trial, the V4 channel simulator runs with a seed derived from the base seed, point and trial, and `vnx decode` reconstructs the archive from the reads with one worker. A trial is SUCCESS only if the reconstructed container matches the SHA-256 in the superblock and validates structurally; every other outcome is counted in its own column.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0009-marker-design/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0009-marker-design            # re-run in scratch and compare deterministic fields
```

## Results

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:06:33Z; environment in `environment.json`; every trial in `results.json`.

**P=40 r=16 period=0 len=0** — 8.8087 nt per input byte, 8247 strands of 280 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 0 | 0 | 10 | 0 | 0 | 0.0 | 1.2433 |

**P=40 r=16 period=48 len=2** — 9.1233 nt per input byte, 8247 strands of 290 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 0 | 0 | 10 | 0 | 0 | 0.0 | 1.2103 |

**P=40 r=16 period=32 len=2** — 9.3121 nt per input byte, 8247 strands of 296 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 2 | 0 | 8 | 0 | 0 | 0.2 | 1.2193 |

**P=40 r=16 period=24 len=2** — 9.5009 nt per input byte, 8247 strands of 302 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 9 | 0 | 1 | 0 | 0 | 0.9 | 1.2113 |

**P=40 r=16 period=16 len=2** — 9.8784 nt per input byte, 8247 strands of 314 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.1813 |

**P=40 r=16 period=32 len=3** — 9.5638 nt per input byte, 8247 strands of 304 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 5 | 0 | 5 | 0 | 0 | 0.5 | 1.2549 |

**P=40 r=16 period=24 len=3** — 9.8469 nt per input byte, 8247 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.2685 |

**P=40 r=16 period=32 len=4** — 9.8155 nt per input byte, 8247 strands of 312 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 6 | 0 | 4 | 0 | 0 | 0.6 | 1.3131 |

**P=36 r=20 period=24 len=3** — 10.949 nt per input byte, 9170 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.3718 |

**P=36 r=20 period=32 len=3** — 10.6342 nt per input byte, 9170 strands of 304 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.3994 |

**P=32 r=24 period=24 len=3** — 12.3089 nt per input byte, 10309 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.4939 |

**P=36 r=20 period=16 len=3** — 11.5786 nt per input byte, 9170 strands of 331 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
|  | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.3735 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
