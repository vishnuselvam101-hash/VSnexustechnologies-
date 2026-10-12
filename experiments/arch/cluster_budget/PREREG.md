# Pre-registration: candidate budget lowers the bucket cap (job 86)

SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced.

Written and committed before any run of the seeds below, on code e45c0c9 (branch work/nano-10mb).

## Amendment (2026-10-12 10:55, before any held-out decode)
The development run of 93100 at e13853a (floor 64) still stopped: its slot pairs come from buckets of <= 64 reads.
1935d89 sets the floor default to 2. The held-out seeds are run on 1935d89 (their first attempt at e13853a exited at
start-up, before building an archive: the diag driver needs a recorded W1 row; they now use
tmp driver fresh10m.py = diag_10m.py without the recorded-row check). Seeds and criteria are unchanged.

## Question
At 10 MB (D13-F1 nanopore-like channel, profile and driver of experiments/v10/w3/diag_10m.py, decoder preset E),
does the clustering stage now run (instead of stopping on max_candidate_pairs) without any false success, within
14 GiB of address space?

## Seeds
- Development (already seen): 93100 (failed at v11.0.0 with the candidate-budget stop).
- Held-out, fixed now, each run once, no re-runs, no exclusions: 93110, 93111, 93112.
- 1 MiB no-regression check (development seeds, preset E): 93020, 93021, 93022, at 51550df (v11.0.0) and at the new
  code; same prepared reads for both.

## Measured per run
Outcome (EXACT = output SHA-256 equals the truth; EXPLICIT_FAILURE; FALSE_SUCCESS), clustering status and budget
record (lowered cap, slot pairs requested/used/allowed), clustered/unassigned reads, wall time, peak RSS
(/usr/bin/time -v), host load at start. Runs under `ulimit -v` 14 GiB; a run killed by the limit or the job timeout is
reported as such, never as a decoder outcome.

## Criteria
- Primary (safety): 0 FALSE_SUCCESS over all runs.
- Secondary: clustering status "ran" (not "budget_exceeded") on every 10 MB run that completes.
- Reported, not a pass/fail criterion: number of EXACT outcomes (3 seeds cannot support a rate claim).
- 1 MiB: same outcome and output SHA-256 at both commits on every seed (the budget is not reached at 1 MiB, so the
  change must be inert there).

If time does not allow the held-out seeds, only development results are reported and labelled as such.
