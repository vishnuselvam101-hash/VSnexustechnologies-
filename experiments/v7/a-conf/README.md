# A-CONF result: Nanopore Phase 1 CONFIRMED on fresh seeds (EXPERIMENTAL, SIMULATED)

Pre-registration: `PREREGISTRATION.md` (commit b8f3338, before any seed ≥ 82100 ran). Thresholds unchanged.

| item | value |
|---|---|
| Software | clean detached checkout at `b8f3338735651d302dad743e26b879f249bb52dc`, all four native kernels built |
| Command | `PYTHONPATH=src python experiments/v7/a-conf/run.py --jobs 4` |
| Run | 2026-10-06T13:52:21Z-14:28:35Z (36 min 09 s), 360 decodes, 4 workers; shared 8-thread host, load average 2.1-4.5 |
| Raw results | `results/confirmatory.jsonl` (one row per seed × coverage × arm; SHA-256 `2870708d7447e5860e277417a7ffbdafbcd1773a2fa9d3e2b2b12367f7188bce`) |
| Summary | `results/summary.json` (`python experiments/v7/a-conf/summarise.py`) |

Channel: the shipped, unfitted `nanopore-like` stress model. D3 showed it is milder than real nanopore reads, so this
confirms the result **on this simulated model only**. No DNA was synthesised, stored or sequenced.

| cov | arm | EXACT | Wilson 95 % | FALSE SUCCESS | false frames | explicit failures | data frames (mean) | consensus-valid strands (mean) | strands written | decode median | peak RSS |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | old | 0/40 | 0.00-0.09 | 0 | 0 | 40 | 123.1 / 679 | 123.1 | 691 | 0.98 s | 94 MiB |
| 3 | phase1 | 0/40 | 0.00-0.09 | 0 | 0 | 40 | 293.4 / 679 | 293.4 | 691 | 16.84 s | 340 MiB |
| 3 | lowcov | 0/40 | 0.00-0.09 | 0 | 0 | 40 | 418.3 / 967 | 418.3 | 979 | 23.9 s | 362 MiB |
| 5 | old | 0/40 | 0.00-0.09 | 0 | 0 | 40 | 278.0 / 679 | 278.0 | 691 | 1.6 s | 120 MiB |
| 5 | phase1 | 0/40 | 0.00-0.09 | 0 | 0 | 40 | 460.8 / 679 | 460.8 | 691 | 24.84 s | 341 MiB |
| 5 | lowcov | 39/40 | 0.87-1.00 | 0 | 0 | 1 | 659.9 / 967 | 659.9 | 979 | 34.94 s | 348 MiB |
| 10 | old | 0/40 | 0.00-0.09 | 0 | 0 | 40 | 511.9 / 679 | 511.9 | 691 | 2.76 s | 193 MiB |
| 10 | phase1 | 40/40 | 0.91-1.00 | 0 | 0 | 0 | 618.0 / 679 | 618.0 | 691 | 42.25 s | 329 MiB |
| 10 | lowcov | 40/40 | 0.91-1.00 | 0 | 0 | 0 | 878.6 / 967 | 878.6 | 979 | 58.58 s | 338 MiB |

`old` = v4-balanced, reference consensus (V6/A1 path); `phase1` = v4-balanced, `consensus_template="full"`;
`lowcov` = v7-lowcov (row code 64 + 48), full consensus. Every non-EXACT decode is an EXPLICIT_FAILURE (typed error,
no bytes published).

## Criteria (from the pre-registration)

| ID | result |
|---|---|
| C1 no false success | PASS: 0 FALSE SUCCESS, 0 false frames in 360 decodes (95 % upper bound 3/360 per decode) |
| C2 Phase 1 > old | PASS at cov 3, 5 and 10: mean frames higher, Phase 1 ≥ old on 40/40 paired seeds, EXACT not worse |
| C3 cov-10 replication | PASS: 40/40 (≥ 36 required) |
| C4 low-coverage replication | PASS: cov 5 39/40 (≥ 36), cov 10 40/40 |
| C5 no harm | PASS: 0 seeds where old is EXACT and Phase 1 is not |

**Decision: Phase 1 CONFIRMED; v7-lowcov coverage-5 result CONFIRMED** — on the simulated nanopore-like stress model.
Cost: Phase 1 decodes 15.3x slower than old at cov 10 (42.3 s vs 2.76 s median) with 1.7-3.6x the peak RSS;
v7-lowcov writes 41.7 % more strands (979 vs 691). Coverage 3 decodes 0/40 in every arm (analysed in `../a-fail/`).

These seeds are confirmatory and are kept separate from the acceptance seeds 82060-82099 (A-CONS, A-PAR); the two
sets are not pooled into one number. Per-seed failure analysis: `experiments/v7/a-fail/`.
