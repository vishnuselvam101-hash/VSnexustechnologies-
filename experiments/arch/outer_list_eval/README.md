# Held-out evaluation: decoder preset F (outer-assisted short-row recovery) vs E — SIMULATED

Pre-registration: [PREREG.md](PREREG.md) (committed 6869f4b before the run). Code: commit 6869f4b. Run: 2026-10-10
19:57–20:59 IST, `vnx-job ol-eval`, host 4C/8T Xeon Gold 6240, load ~1.4–1.7, decoder workers 4.
Results: `results/eval.jsonl` (one row per seed × preset), `results/summary.json`.

| | E (V9 candidate E) | F (E + outer_list) |
|---|---|---|
| exact SUCCESS (container SHA-256 = original) | **2 / 10** | **7 / 10** |
| false success | 0 | 0 |
| seeds only this preset recovers | 0 | 5 |
| median decode seconds | 142.1 | 148.0 |

Paired McNemar exact test (two-sided): p = 0.0625 (5 discordant seeds, all favouring F) — the direction is consistent,
but with n = 10 it is not significant at 0.05. Acceptance (all met): no false success; no seed where E succeeds and F
does not; identical output where both succeed.

Remaining failures with F: 93022 (row 311), 93027 (rows 57, 109: no symbol could be verified), 93028 (row 220).
Their failed consensus frames carry too few correct bytes (consensus quality, PROJECT_STATE D-3). These ten seeds are
now *used*; any further change to F must be evaluated on new held-out seeds.

Label: SIMULATED — reads from a software channel model (D13-F1, nanopore-like); no DNA was synthesised, stored or
sequenced. Not a claim about real nanopore data.
