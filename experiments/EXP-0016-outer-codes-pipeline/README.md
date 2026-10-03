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

_Not run yet._

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
