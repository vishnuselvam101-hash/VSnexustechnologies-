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

## Evaluation 2: F with frame-level RANSAC (pre-registration [PREREG2.md](PREREG2.md), f832ea3)

Code at f832ea3 (RANSAC in 802add0). Run 2026-10-10 21:5x–00:0x IST, `vnx-job ol-eval2`, same host and settings.
Results: `results/eval2.jsonl`, `results/eval2-summary.json`. Seeds 93030–93049 (held out).

| | E | F (+ RANSAC) |
|---|---|---|
| exact SUCCESS | **1 / 20** | **15 / 20** |
| false success | 0 | 0 |
| seeds only this preset recovers | 0 | 14 |
| median decode seconds | 139.6 | 150.6 |

Paired McNemar exact test: p = 0.0001. Acceptance (all met): no false success; F never loses a case E recovers;
identical output where both succeed. F still fails 5 seeds, each by one row: 93032 (row 209), 93034 (20), 93037 (143),
93044 (184), 93046 (164). The development seeds of the RANSAC step (93022, 93027, 93028) are not part of this result.

Label: SIMULATED (D13-F1 nanopore-like software channel); no DNA was synthesised, stored or sequenced.

## Evaluation 3: F at 87b532f (RANSAC + round fix) (pre-registration [PREREG3.md](PREREG3.md), eb6a5a0)

Run 2026-10-11 00:2x–02:3x IST, `vnx-job ol-eval3`. Results: `results/eval3.jsonl`, `results/eval3-summary.json`.
Seeds 93050–93069 (held out).

| | E | F |
|---|---|---|
| exact SUCCESS | **2 / 20** | **15 / 20** |
| false success | 0 | 0 |
| seeds only this preset recovers | 0 | 13 |
| median decode seconds | 139.9 | 151.6 |

McNemar exact p = 0.0002. Acceptance (all met). F still fails 93054 (row 401), 93057 (231, 282), 93059 (306),
93062 (91), 93069 (350). Development seeds 93022, 93027, 93028 (the three failures of evaluation 1) now decode SUCCESS
end to end with the original container SHA-256 (development data, not evidence).

Over the three held-out evaluations (50 seeds, three code versions): E 5/50, F 37/50 exact, 0 false success.
