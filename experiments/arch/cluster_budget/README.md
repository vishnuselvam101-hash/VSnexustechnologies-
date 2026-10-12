# Candidate budget: lower the bucket cap instead of dropping the clustering stage (job 86)

SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced.

## Problem
v11.0.0, 10 MB D13-F1 nanopore-like pool, seed 93100, preset E: the candidate step needed 584,576,633 slot pairs
against a budget of 584,574,400 (200 per unplaced read), so the read-clustering stage stopped with no pairs. All
2,922,872 unplaced reads stayed unassigned, 4,031/4,032 rows stayed below K, and the decode refused
(EXPLICIT_FAILURE, wall 934 s, peak RSS 5.29 GB; tmp evidence diag-93100-E.json of 2026-10-11).

## Change
- Over the budget, the candidate step lowers the bucket cap to the largest c in [max(2, bucket_cap_floor), bucket_cap)
  whose slot pairs fit. c is computed from the histogram of bucket sizes, so it depends only on the input. The larger
  buckets (the least specific k-mers) are dropped and counted, and the stage continues. The budget record says
  `"stopped": false` and carries requested/used/allowed slot pairs, the cap and the dropped buckets. If no cap fits,
  the step stops exactly as before. `ClusterConfig.bucket_cap_floor` defaults to 2 (see below); a floor >= bucket_cap
  restores the all-or-nothing behaviour. Native kernel ABI 6 (`vnx_cl_cand_run_capped`), byte-equal to the reference.
- Floor: a first version used 64. The development run of 93100 still stopped, because at this coverage (~10 reads
  per strand) the slot pairs come from buckets of <= 64 reads. The budget, not the cap, bounds the work, and every
  cluster frame is still verified (inner RS + CRC-32, then the container SHA-256). So any cap can only add verified
  frames compared with stopping, and the default is 2.
- Once the stage ran at 10 MB, `cluster_frames` turned out to scan all labels once per cluster
  (O(clusters x reads)). `graph.members` groups them with one stable sort (same arrays).
- No format change. Decoder output is unchanged whenever the budget is not exceeded.

## Verification
- Tests: tests/v11/test_candidate_cap_lowering.py (reference semantics, maximal cap, stop equals the floor-less stop,
  native = reference over budgets/floors/chunk sizes, stage report, `members`), tests/v7/test_read_clustering.py
  (stage: budget 10,000 -> cap lowered, EXACT; budget 2,000 -> EXACT or EXPLICIT_FAILURE, never FALSE_SUCCESS).
- Sanitizers (results/sanitizers.log, code e45c0c9; C unchanged since): clang ASan+UBSan 142/142, clang UBSan-trap
  142/142, gcc ASan+UBSan 141/142. The one failure was a hard-coded ABI-5 assertion, fixed in e13853a; that test
  then passed under gcc ASan. The stress fuzz found 0 mismatches in every build, and both ASan canaries fired.
- libFuzzer (results/libfuzzer.txt): capped_fuzz.c 102,164 runs / 300 s, candidates_fuzz.c 51,286 runs / 180 s,
  no crash or oracle failure.

## Measurements
See results/summary.json (collect.py) and the per-run JSON files. Every run was preset E under `ulimit -v` 14 GiB,
started when the 1-minute load was below 3.

RESULTS_PLACEHOLDER

## Reproduce
`run_1mib.sh` (1 MiB, base vs new tree), `run_10mb.sh` (10 MB; `diag10m.py` for recorded seeds, `fresh10m.py` for
new seeds), then `python collect.py WORKDIR`. The drivers need a `__main__` guard: the consensus step starts worker
processes with `spawn`, and those re-import the main module.
