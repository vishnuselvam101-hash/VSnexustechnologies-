# A1-SMOKE: the read-clustering reference runs end to end (EXPERIMENTAL, SIMULATED)

**EXPERIMENTAL / SIMULATED. A smoke check, not an efficacy result.** Software strands, the unfitted V6 channel models
(the A-DIAG coverage variants in `experiments/v7/a-diag/models/`) and the V7 item A Python reference
(`DecodeOptions(read_clustering="fallback")`, `src/vnxdna/recovery/cluster/`). No DNA was synthesised, stored or
sequenced; no public data, no fitted model, no parameter tuning. Five exploration seeds per cell (82020-82024, protocol
§7 range); no pre-registration, so nothing here supports a claim. Exploration (A0) and the confirmatory A-EXP-01 are
separate steps.

## Provenance

| item | value |
|---|---|
| code | commit `0b7f0e9` (work/v7-cluster); header of `trials.jsonl`: `dirty_tracked: false` (only this run's own outputs untracked) |
| run | `PYTHONPATH=src nice -n 10 python experiments/v7/a1-smoke/smoke.py run --config experiments/v7/a1-smoke/config.json --jobs 4` (2 min 11 s wall, `log.txt`) |
| summary | `PYTHONPATH=src python experiments/v7/a1-smoke/smoke.py summarise --config experiments/v7/a1-smoke/config.json` |
| files | `config.json` (SHA-256 `1fca5304…`), `trials.jsonl` (header, 10 trial records, end record), `summary.json`, `log.txt` |
| input | the A-DIAG archive: 20,000 random bytes (data seed 6201), uncompressed, v4-balanced, 691 strands of 313 nt (679 data in 9 rows, 12 superblock strands, 3 needed) |
| arms | `off`: `DecodeOptions(stage_counters=True)` (6.0 defaults); `fallback`: the same plus `read_clustering="fallback"` with `ClusterConfig()` defaults; 1 worker each |

Outcomes follow protocol §6 (`vnxdna.benchmark.outcome`). The funnel comes from the decoder's own report
(`report["clustering"]`, `report["stage_counters"]`); no ground truth is used.

## Results (5 seeds per cell)

| cell | arm | EXACT | FALSE SUCCESS | EXPLICIT FAILURE | CRASH | terminal stage | median decode s |
|---|---|---|---|---|---|---|---|
| nanopore-like/cov10 | off | 0/5 | 0 | 5 | 0 | superblock 5 | 0.37 |
| nanopore-like/cov10 | fallback | 0/5 | 0 | 5 | 0 | outer_ecc 5 | 53.3 |
| deletion-heavy/cov10 | off | 0/5 | 0 | 5 | 0 | outer_ecc 5 | 0.37 |
| deletion-heavy/cov10 | fallback | 5/5 | 0 | 0 | 0 | — | 36.7 |

Stage funnel of the `fallback` arm (mean per trial; the stage ran in every trial):

| step | nanopore-like/cov10 | deletion-heavy/cov10 |
|---|---|---|
| trigger | superblock (NO_SUPERBLOCK on 6.0 symbols) | rows (a row without enough 6.0 symbols) |
| reads | 6914.8 | 6870.2 |
| unplaced reads stored | 6914.6 | 6205.4 |
| clustered / unassigned reads | 6879.0 / 35.6 | 6204.4 / 1.0 |
| clusters (= consensus attempts) | 671.8 | 690.0 |
| consensus: erasures exceed parity / decode failed (all trials of the ladder) | 98.6 / 151.4 | 22.0 / 7.6 |
| verified cluster frames (superblock + data) | 520.4 (9.0 + 511.4) | 682.4 (11.8 + 670.6) |
| filled superblock symbols | 9.0 | 0 |
| filled data symbols / confirmations of 6.0 symbols / conflicts | 511.2 / 0.2 / 0 | 64.2 / 532.8 / 0 |
| rows short of 6.0 symbols / rows filled | 9.0 / 9.0 | 3.2 / 3.2 |

## Reading (descriptive only)

1. The reference runs end to end on both cells, with 0 FALSE SUCCESS and 0 CRASH in 20 decodes (rule of three: a
   95 % upper bound of 0.15 per arm and cell at n = 5 — far too few decodes for any statement beyond "it runs").
2. nanopore-like/cov10: the cluster frames supply the superblock in every trial, so the decode now stops at the outer
   code instead of the superblock; 511 of 679 data addresses get a verified frame on average, which leaves rows below
   k. deletion-heavy/cov10: the fill completes the rows 6.0 leaves short in 5 of 5 seeds.
3. Every filled symbol passed inner RS + CRC-32 and the container SHA-256 decided each SUCCESS (FC-1, FC-9); no cluster
   frame disagreed with a 6.0 symbol at the same address (0 conflicts).
4. Cost: the Python reference adds about 35-55 s per decode of about 6,900 reads on this host (MEASURED, shared
   development host, 4 trials in parallel); native kernels are step A2.

## Limitations

Synthetic, i.i.d. channel models, unfitted to any platform. One archive, five seeds per cell, default parameters only;
no tuning sweep was run (A0 does that on DEV models). The decode times are wall-clock on a shared host and include the
6.0 path. These results are not evidence for or against any acceptance criterion of `docs/V7_PROTOCOL.md` §8.3.
