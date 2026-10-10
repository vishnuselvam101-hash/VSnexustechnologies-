# V10 W3 pre-registration: cross-row redundancy for large noisy archives (SIMULATED)

Written and committed before any evaluation decode. Only the 20 KB smoke case (seed 95000, `results/smoke.jsonl`) was
decoded before this file, to check that the pipeline runs.

## Finding that changes the plan item

The plan (W3) proposed a new cross-row or archive-level erasure layer as an opt-in format version. That layer already
exists: the V6 outer code (`docs/V6_OUTER_CODE.md`, superblock version 2) adds column-parity rows per stripe of row
codewords and recovers failed rows in pass 2 (`vnxdna.recovery.stripes`). The V9 noisy runs used the default
v4-balanced archive without it. W3 is therefore an evaluation of the existing opt-in format, not a format change.
Old archives are unaffected because no format code changes.

## Hypothesis

Under D13-F1 at coverage 10 with decoder E, V9 measured per-row success of about 0.995 at 1 MiB but whole-archive
EXACT 2/30, because every one of the 411 rows must decode. Four column-parity rows per stripe of 128 rows
(+3.9 % strands at 1 MiB) turn the rare row failures into recoverable erasures.

Analytic model (`run.py analytic`, conservative: a stripe decodes only if at most Mc of its D + Mc rows fail row-wise,
i.i.d. row failure q = 0.005 from V9): whole-archive success 0.128 -> 0.998 at 1 MiB, 3e-9 -> 0.98 at 10 MB.

## Design

- Archive: v4-balanced (Layout 40/16, markers 24/3; row code 64 + 16) with `stripe_depth=128`, `column_parity=4`.
  Random data, uncompressed container, data seed = case seed.
- Channel: D13-F1 stress model (`experiments/v8/matrix/run.py case_model`), coverage 10, simulation seed = case seed.
- Decoder: E (V9 production candidate), 4 workers, native kernels required (the runner refuses otherwise).
- Cases: 1 MiB, seeds 93000-93009 (V9 baseline on these seeds: 0/10 EXACT). 10 MB, seeds 93100-93101.
- Comparison: unpaired against the committed V9 rows for the same data seeds (the strand set differs because column
  parity adds strands, so reads cannot be paired).

## Acceptance (fixed now)

1. 0 false success in every case (hard gate).
2. 1 MiB: at least 9/10 EXACT. Report the Wilson 95 % interval.
3. 10 MB: report each outcome. No acceptance claim on two cases; they show feasibility and decode time.
4. If (2) fails, record it as a failed hypothesis. Do not change the geometry and rerun the same seeds.

## Reported

Outcome, rows recovered by columns, per-row success, decode seconds with host load, strand count / overhead.
Every number comes from `results/w3.jsonl` and `results/summary.json`.
