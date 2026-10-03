# EXP-0013-channel-models

**Purpose.** effect of homopolymer-dependent indels and GC-dependent coverage bias

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** The input is archived and DNA-encoded once. For every channel point and trial, the V4 channel simulator runs with a seed derived from the base seed, point and trial, and `vnx decode` reconstructs the archive from the reads with one worker. A trial is SUCCESS only if the reconstructed container matches the SHA-256 in the superblock and validates structurally; every other outcome is counted in its own column.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0013-channel-models/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0013-channel-models            # re-run in scratch and compare deterministic fields
```

## Results

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:07:13Z; environment in `environment.json`; every trial in `results.json`.

**results** — 9.8469 nt per input byte, 8247 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| coverage=1, deletion=0.001, homopolymer_indel_multiplier=1, insertion=0.001, model=homopolymer x1 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.8644 |
| coverage=1, deletion=0.001, homopolymer_indel_multiplier=5, insertion=0.001, model=homopolymer x5 | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.1037 |
| coverage=1, deletion=0.001, homopolymer_indel_multiplier=20, insertion=0.001, model=homopolymer x20 | 0 | 0 | 10 | 0 | 0 | 0.0 | 1.7677 |
| coverage=3, gc_bias_strength=0, model=gc bias 0 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.3619 |
| coverage=3, gc_bias_strength=1, model=gc bias 1 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.3449 |
| coverage=3, gc_bias_strength=4, model=gc bias 4 | 6 | 0 | 4 | 0 | 0 | 0.6 | 0.3138 |
| coverage=3, coverage_dispersion=1, model=uneven coverage (NB k=1) | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.3625 |
| coverage=3, coverage_dispersion=0.5, model=uneven coverage (NB k=0.5) | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.3031 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
