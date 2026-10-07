# A-PAR: outer parity for low coverage (EXPERIMENTAL, SIMULATED RESULT)

SIMULATED RESULT only: unfitted nanopore-like channel, no real nanopore data; no DNA was synthesised or sequenced.
Pre-registration: `PREREGISTRATION.md` (e57d593). Results: `results/heldout.jsonl` (seeds 82060-82099), `results/dev.jsonl`.

Why: with the Phase 1 consensus, cov 5 loses ~32 % of strands (2-read clusters fail the inner RS: ~32 erased bytes vs
16; strands with <= 2 reads ~26 %), above the v4-balanced outer budget (16 of 80 per row). An RS/erasure policy cannot
recover them (A-RS diag: partial strands add 0 rows), and read qualities cannot resolve deletions. So only the outer
redundancy was varied.

| cov | profile | EXACT (SHA-256) | false success | false frames | data frames | decode s | peak RSS MiB |
|---|---|---|---|---|---|---|---|
| 3 | v4-balanced (A-CONS) | 0/40 | 0 | 0 | 294.7/679 | 16.9 | 339 |
| 3 | v7-lowcov | 0/40 | 0 | 0 | 421.7/967 | 24.1 | 351 |
| 5 | v4-balanced (A-CONS) | 0/40 | 0 | 0 | 463.0/679 | 24.8 | 333 |
| 5 | v7-lowcov | 38/40 (Wilson 0.84–0.99) | 0 | 0 | 664.6/967 | 34.9 | 355 |
| 10 | v4-balanced (A-CONS) | 40/40 | 0 | 0 | 618.1/679 | 42.0 | 327 |
| 10 | v7-lowcov | 40/40 | 0 | 0 | 880.2/967 | 58.5 | 339 |

Pre-registered criterion met (cov5 >= 36/40, cov10 40/40, 0 false). Binomial prediction for cov5 was 0.953 (38.1/40).
Cost: +42 % strands and nucleotides (967 vs 679 data strands for 20,000 B). cov 3 stays unrecoverable (57 % strand loss).
