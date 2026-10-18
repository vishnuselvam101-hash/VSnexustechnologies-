# Single-run decode timings at build/v13-final f7586b9 (the final engine) — SIMULATED

One decode per row (`decode_reads` wall time in one process, imports excluded, output checked byte for byte against
the archive), 2026-10-18 08:5x host clock, right after the A/B run `../ab-final2` (load1 at start in `load1_before`).
Fixtures as the A/B harness builds them (`experiments/arch/compare.py _prepare`: archive from the V12 tree 6bf7749,
strands + reads from this tree): 4 MiB random file, indel channel (1 % substitution, 0.2 % insertion, 0.2 % deletion,
coverage 5; seed 95500 at 1 worker, 95300 at 4). `engine` = the rung; the `stream` rows carry `report["engine"]`
with the pass-1 counters and stage seconds. Companion of `../single-runs-0093376` (the pre-fix engine).

| fixture | workers | engine `stream` | V13 path (`engine=v13`) |
|---|---|---|---|
| indel 95500 | 1 | 3.047 s (pass 1 2.689: scan 0.10, prep 0.31, attempts 2.20 = cheap 1.06 + full DP 0.75 + grouping 0.21, spill 0.06; pass 2 0.23; verify 0.01) | 4.705 s |
| indel 95300 | 4 | 1.681 s (pass 1 1.30: attempts 1.00 = cheap 0.32 + full 0.29 + grouping 0.20; pass 2 0.25) | 1.927 s |

Every row SUCCESS, `matches_input` true. At 1 worker the fixed scheduler attempted 323,312 of 655,755 reads
(361,152 cheap attempts, 66,458 full DP attempts, 166,626 claim splits) and skipped 332,443 as corroborated copies;
the pre-fix engine (`../single-runs-0093376`) attempted 149,340 (171,182 cheap, 11,722 full) and skipped 506,415,
in 1.748 s. The difference is the copies whose raw header does not name the verified address (a header base error
or an indel before the header): they are now attempted, cheap paths first.
