# EXP-0008-indel-profiles

**Purpose.** indel recovery at coverage 1: markers vs no markers (equal outer code)

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** The input is archived and DNA-encoded once. For every channel point and trial, the V4 channel simulator runs with a seed derived from the base seed, point and trial, and `vnx decode` reconstructs the archive from the reads with one worker. A trial is SUCCESS only if the reconstructed container matches the SHA-256 in the superblock and validates structurally; every other outcome is counted in its own column.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0008-indel-profiles/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0008-indel-profiles            # re-run in scratch and compare deterministic fields
```

## Results

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:05:58Z; environment in `environment.json`; every trial in `results.json`.

**v4-dense (no markers, P=44, r=12)** — 8.0151 nt per input byte, 7504 strands of 280 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| deletion=0, insertion=0 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.0968 |
| deletion=0.0005, insertion=0.0005 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.4921 |
| deletion=0.001, insertion=0.001 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.7007 |
| deletion=0.002, insertion=0.002 | 0 | 0 | 10 | 0 | 0 | 0.0 | 1.0284 |
| deletion=0.003, insertion=0.003 | 0 | 0 | 10 | 0 | 0 | 0.0 | 1.2272 |
| deletion=0.005, insertion=0.005 | 0 | 0 | 10 | 0 | 0 | 0.0 | 0.9851 |

**v4-balanced (3-nt markers / 24 nt, P=40, r=16)** — 9.8469 nt per input byte, 8247 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| deletion=0, insertion=0 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.1339 |
| deletion=0.0005, insertion=0.0005 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.5985 |
| deletion=0.001, insertion=0.001 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.7867 |
| deletion=0.002, insertion=0.002 | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.2181 |
| deletion=0.003, insertion=0.003 | 0 | 0 | 10 | 0 | 0 | 0.0 | 1.5571 |
| deletion=0.005, insertion=0.005 | 0 | 0 | 10 | 0 | 0 | 0.0 | 2.0438 |

**v4-indel (3-nt markers / 24 nt, P=36, r=20)** — 10.949 nt per input byte, 9170 strands of 313 nt, 10 trials per point

| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |
|---|---|---|---|---|---|---|---|
| deletion=0, insertion=0 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.1475 |
| deletion=0.0005, insertion=0.0005 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.6444 |
| deletion=0.001, insertion=0.001 | 10 | 0 | 0 | 0 | 0 | 1.0 | 0.8693 |
| deletion=0.002, insertion=0.002 | 10 | 0 | 0 | 0 | 0 | 1.0 | 1.2886 |
| deletion=0.003, insertion=0.003 | 6 | 0 | 4 | 0 | 0 | 0.6 | 1.6135 |
| deletion=0.005, insertion=0.005 | 0 | 0 | 10 | 0 | 0 | 0.0 | 2.0996 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
