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

## Evaluation 4: F at a12afb4 (hypothesis selection + inner trials) (pre-registration [PREREG4.md](PREREG4.md), e1c630b)

Code at 0f5f8a9 (a12afb4 + a -Wshadow rename, no behaviour change) for seeds 93070–93079 and a444721 (0f5f8a9 + the
opt-in v10-oligo300 profile; v4-balanced decoding unchanged) for seeds 93080–93089 — see Deviation below. Same case
and settings as evaluations 2–3. Results: `results/eval4.jsonl`, `results/eval4-summary.json`. Seeds 93070–93089
(held out).

| | E | F |
|---|---|---|
| exact SUCCESS | **2 / 20** | **19 / 20** |
| false success | 0 | 0 |
| seeds only this preset recovers | 0 | 17 |
| median decode seconds | 141.26 | 147.18 |

McNemar exact p = 1.53e-5 (17 discordant seeds, all favouring F). Acceptance (all met): no false success (True); no seed
where E succeeds and F does not (none); identical output where both succeed
(True). H1 (F recovers more seeds exactly than E): met
(F 19/20 vs E 2/20); the pre-registered expectation of F above the 15/20 of evaluations 2–3:
met. H2 (F median within 1.15x of E): met (1.042x) — timing caveat below.
F failures with their short rows: 93081 (row 171).

Median decode seconds by batch: seeds 93070–93079 (serial, one seed at a time) E 141.3 / F 148.73;
seeds 93080–93089 (two seeds concurrently) E 141.26 / F 146.6.

**Deviation (dirty tree during the run).** The run started 2026-10-11 09:56 IST as `vnx-job ol-eval4` at 0f5f8a9.
Between 11:06 and 11:13 IST the working tree's `src/` was edited (unfinished, unregistered read-recall code) while
the job was running with `PYTHONPATH=src`; every decode started after 11:06 (seeds 93080 onwards, 14 rows) imported
that code. Those rows were removed from `results/eval4.jsonl` and kept, uncounted, in
`results/eval4-excluded-dirty-tree.jsonl`; the edits were moved to branch `work/nanopore-f2-wip` (d53fc2b, not
evaluated). The tree was restored and seeds 93080–93089 were re-run from 11:50 IST at a444721 as two parallel jobs
(`ol-eval4a`: 93080–93084, `ol-eval4b`: 93085–93089; 11:51–12:20 IST), so two seeds decoded concurrently
(each seed's E and F still ran back to back under the same load, workers 4). The counts are unaffected; H2's
timing for 93080–93089 is under heavier load than evaluations 1–3 (1-minute load at decode start, E rows:
1.8, 6.9, 3.5, 7.1, 3.6, 6.0, 3.7, 6.3, 3.7, 7.3; F rows: 6.5, 3.7, 6.9, 3.8, 6.8, 1.8, 6.9, 3.7, 6.9, 3.6; seeds 93070–93079: E 3.3, 4.0, 2.8, 6.2, 9.5, 8.9, 8.1, 9.1, 7.5, 8.3; F 5.6, 3.0, 4.6, 2.9, 9.5, 6.7, 9.2, 7.1, 9.1, 6.7).
A competitor benchmark running on the host was paused (SIGSTOP) during the re-run.

Over the four held-out evaluations (70 seeds, four code versions): E 7/70, F 56/70 exact, 0 false success.

Label: SIMULATED — reads from a software channel model (D13-F1, nanopore-like); no DNA was synthesised, stored or
sequenced. Not a claim about real nanopore data.

