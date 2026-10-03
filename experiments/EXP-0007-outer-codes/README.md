# EXP-0007-outer-codes

**Purpose.** Cauchy RS vs GF(2) fountain at the same 25% redundancy budget under i.i.d. symbol loss

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** Erasure-only comparison of outer codes on identical data with i.i.d. symbol loss, the same seeds and the same parity/data budget for every scheme. A trial succeeds when every data symbol is recovered and equals the original.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0007-outer-codes/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0007-outer-codes            # re-run in scratch and compare deterministic fields
```

## Results

Commit `44d3ae932bd4896879fe517d4b7ab52157256da6`, 2026-10-03T02:20:41Z; environment in `environment.json`; every trial in `results.json`.

Same data (6400 symbols × 40 B), same i.i.d. loss, same seeds, 25% parity/data for every scheme, 200 trials per point.

| scheme | loss 0.0 | loss 0.05 | loss 0.08 | loss 0.1 | loss 0.12 | loss 0.14 | loss 0.16 | loss 0.18 | loss 0.2 | encode MB/s |
|---|---|---|---|---|---|---|---|---|---|---|
| cauchy-rs(200+50) | 1 | 1 | 1 | 1 | 1 | 0.905 | 0.25 | 0 | 0 | 6.0 |
| cauchy-rs(64+16) | 1 | 1 | 0.99 | 0.805 | 0.25 | 0 | 0 | 0 | 0 | 6.91 |
| fountain-dense(256+64) | 1 | 1 | 1 | 1 | 0.995 | 0.91 | 0.305 | 0 | 0 | 10.06 |
| fountain-soliton(256+64) | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8.43 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
