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

### 10 MB, D13-F1, preset E (`ulimit -v` 14 GiB)
| seed | role | code | outcome | clustering | bucket cap | slot pairs requested / used / allowed | clusters | decode s | wall s | peak RSS |
|---|---|---|---|---|---|---|---|---|---|---|
| 93100 | v11.0.0 (2026-10-11 record) | 12ef9a9 | EXPLICIT_FAILURE | stopped | 256 | stop at 584,576,633 / - / 584,574,400 | 0 | - | 934 | 5.29 GB |
| 93100 | dev, floor 64 | e13853a | EXPLICIT_FAILURE | stopped | 256 | stop at 584,576,633 / - / 584,574,400 | 0 | 149 | 911 | 5.30 GB |
| 93100 | dev | 2ec76f0 | **EXACT** | ran | 52 | 2,537,730,294 / 575,203,254 / 584,574,400 | 290,695 | 939 | 1,735 | 5.94 GB |
| 93110 | held-out (PREREG) | 2ec76f0 | **EXACT** | ran | 52 | 2,530,761,156 / 575,349,915 / 583,835,400 | 290,614 | 933 | 1,739 | 5.94 GB |

The wall time includes building the archive and simulating the reads (about 13 min). Peak RSS is that of the
largest process (`/usr/bin/time -v`). The consensus worker processes stayed below 0.3 GB together (sampled every
20 s). At 93100, 1,932,828 of 2,922,872 unplaced reads were clustered; 237,628 cluster frames were verified;
3,699 rows decoded and 333 more were recovered by column parity.

Held-out: only 93110 of the pre-registered 93110-93112 was run (time). One held-out seed supports no rate claim. The
primary criterion (0 FALSE_SUCCESS) and the secondary one (clustering ran) held on every run. A first attempt at
93110 (11:45, unguarded driver) was stopped while it was still building the archive, before any decode.

### 1 MiB, D13-F1, preset E, development seeds (same reads for every tree)
| seed | v11.0.0 51550df | floor 64 (e45c0c9 src) | final 2ec76f0 | budget record (final) |
|---|---|---|---|---|
| 93020 | FAILURE, group 242, 144 s, 1.07 GB | same, 147 s, 1.08 GB | same, 140 s, 1.08 GB | none |
| 93021 | FAILURE, group 144, 193 s*, 1.08 GB | same, 144 s, 1.08 GB | same, 138 s, 1.08 GB | none |
| 93022 | FAILURE, groups 55+311, 143 s, 1.07 GB | same, 144 s, 1.07 GB | same, 141 s, 1.07 GB | none |

*overlapped with a full test-suite run. At 1 MiB the candidate budget is not reached (no record), so the change is
inert there. Outcomes and failed groups are identical. Preset E fails these seeds at every commit, as in earlier
evaluations; preset F was not re-measured.

## Reproduce
`run_1mib.sh` (1 MiB, base vs new tree), `run_10mb.sh` (10 MB; `diag10m.py` for recorded seeds, `fresh10m.py` for
new seeds), then `python collect.py WORKDIR`. The drivers need a `__main__` guard: the consensus step starts worker
processes with `spawn`, and those re-import the main module.
