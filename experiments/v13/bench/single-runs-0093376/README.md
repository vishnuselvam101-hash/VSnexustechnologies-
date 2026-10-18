# Single-run decode timings at build/v13-hybrid 0093376 vs build/v12 6bf7749 — SIMULATED

One decode per row (`decode_reads` wall time in one process, imports excluded, output checked byte for byte against the
archive), on a quiet host (load average 0.8–1.3 at start of each row, recorded in `load1_before`), 2026-10-17 22:04.
Fixtures as the A/B harness builds them (`experiments/arch/compare.py _prepare` semantics: archive from the V12 tree,
strands + reads from the V13 tree): 4 MiB random file, indel channel (1 % substitution, 0.2 % insertion, 0.2 % deletion,
coverage 5; seeds 95500 for w1, 95300 for w4), clean (coverage 3, seed 95100) and substitution (2 %, coverage 5, seed
95200). These rows decided the address-scheduler ("single verified copy per address") question; the paired A/B
benchmark with its noise band is `../ab-final` and `../baseline-aa`.

| fixture | workers | V12 6bf7749 | V13 engine (stream) | V13 path (`engine=v13`) | engine, scheduler off (`engine_dedup=False`) |
|---|---|---|---|---|---|
| indel 95500 | 1 | 9.393 s | 1.748 s | 4.592 s | 4.123 s |
| indel 95300 | 4 | 2.820 s | 1.110 s | 1.800 s | 1.812 s |
| clean 95100 | 1 | 3.015 s | 0.952 s | — | — |
| clean 95100 | 4 | 1.441 s | 0.697 s | — | — |
| substitution 95200 | 1 | 5.482 s | 1.517 s | — | — |
| substitution 95200 | 4 | 2.059 s | 0.877 s | — | — |

Every row: status SUCCESS, `matches_input` true. The engine rows carry `report["engine"]["pass1"]` (per-stage
seconds and read counts); e.g. indel w1: scan 0.10 s, prep 0.32 s, attempts 0.98 s (cheap 0.51, full 0.16, grouping
0.22), spill 0.03 s; 149,340 of 655,755 reads attempted, 506,415 skipped as copies of verified addresses.
