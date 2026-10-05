# P4-EXP-02: quality-weighted consensus against the count vote, pre-registered (SIMULATED)

**SIMULATED.** Software channel models; the simulator's qualities are informative by construction where
`quality_informative` > 0, and constant (Q30) where it is 0. No DNA was synthesised, stored or sequenced. The weighted
vote's score is Phred-interpreted, **not calibrated**.

Design, criteria and analysis code were committed before the run: `../PREREG.md`, `../verdict.py`, `config.json`
(commit `38f1a5b`). No deviation from the pre-registration.

## Provenance

| item | value |
|---|---|
| code | commit `38f1a5b` (`dirty_tracked: false`); option implemented in `342977e` + `9db7a06` |
| command | `PYTHONPATH=src nice -n 10 python experiments/v6/phase4/phase4.py run --config experiments/v6/phase4/P4-EXP-02-qw-consensus/config.json --jobs 3` |
| verdict | `python experiments/v6/phase4/verdict.py --trials …/trials.jsonl --cost ../P4-EXP-04-cost/results.json --out …/verdict.json` |
| config SHA-256 | `121b1c1126640005835aaa138182c02a8b66a3b63ef6dc6c188487a5636c686e` |
| seeds | 52000-52039 (primary), 52000-52019 (control, regression), 52000-52004 (determinism); fresh, disjoint from the diagnostic seeds 41000-41019 |
| backends | align, reads, RS native; decode workers 1 (determinism arm 4) |
| files | `trials.jsonl` (975 paired trials, 1,965 decodes, all kept), `summary.json`, `verdict.json`, `log.txt`, `models/` |

## Verdicts (pre-registered criteria)

| id | criterion | result | verdict |
|---|---|---|---|
| C1 | 0 false SUCCESS | 0 of 1,965 decodes | **ACCEPT** |
| C2 | pooled primary gain, 95 % lower bound > 0 | 213/520 vs 211/520 exact; difference +0.0038, 95 % CI [−0.0016, +0.0093] (2 pairs only the weighted vote decoded, 0 the reverse) | **REJECT** |
| C3 | cells with a gain (interval excludes 0) | none of 38 cells | (no gain claimed) |
| C4 | no harm | no cell with upper bound < 0; pooled control +0.0062 [−0.0076, +0.0204] (105 vs 104 of 160); pooled regression +0.0036 [−0.0044, +0.0116] (182 vs 181 of 280) | **ACCEPT** |
| C5 | mechanism: more multi-read attempts within 2e + f ≤ r | +5.38 attempts per primary trial, 95 % CI [4.59, 6.17] | **ACCEPT** |
| C6 | cost: median decode-time ratio ≤ 1.30, fresh-process peak-RSS ratio ≤ 1.15 | 1.288 (in-process, shared CPU); peak RSS ratio 1.000 in all 6 pairs (P4-EXP-04) | **ACCEPT** (narrowly; see below) |
| C7 | determinism, workers 1 vs 4 | 15/15 identical | **ACCEPT** |

**Default-change rule** (C1, C2, C4, C6, C7 all ACCEPT): **not satisfied** (C2 REJECT). The option stays opt-in.

## Per-cell results (exact / n, count → quality; paired difference with Newcombe 95 % CI; symbols recovered by consensus per trial, mean difference with 95 % CI)

| cell | count | quality | difference [95 % CI] | consensus symbols +/trial [95 % CI] | time ratio |
|---|---|---|---|---|---|
| P substitution-heavy cov 2.5 | 8/40 | 9/40 | +0.025 [−0.040, +0.096] | +1.40 [0.99, 1.81] | 1.34 |
| P substitution-heavy cov 3 | 37/40 | 38/40 | +0.025 [−0.056, +0.122] | +1.10 [0.75, 1.45] | 1.33 |
| P substitution-heavy cov 3.5 | 40/40 | 40/40 | 0 | +1.13 [0.82, 1.43] | 1.28 |
| P mixed-harsh cov 4 / 6 / 8 | 0/40 each | 0/40 each | 0 | +18.0 / +24.8 / +19.1 (all CIs > 0) | 1.76 / 1.67 / 1.57 |
| P quality-degradation cov 2 / 2.5 / 3 | 0, 6, 38 of 40 | 0, 6, 38 of 40 | 0 | +0.60 / +0.65 / +0.60 (all CIs > 0) | 1.42 / 1.36 / 1.34 |
| P mixed-mild cov 2 / 2.5 | 21/40, 40/40 | 21/40, 40/40 | 0 | 0 | 1.14 / 1.16 |
| P illumina-like cov 1.5 / 2 | 0/40, 21/40 | 0/40, 21/40 | 0 | 0 (no multi-read attempts) | 1.02 / 1.07 |
| C deletion-heavy / insertion-heavy (cov 10) | 0/20 each | 0/20 each | 0 | +0.85 / +1.00 | 1.58 / 1.57 |
| C deletion-heavy / insertion-heavy cov 15 | 20/20, 18/20 | 20/20, 19/20 | 0, +0.05 [−0.096, +0.225] | +1.20 / +1.80 | 1.46 / 1.43 |
| C uneven-coverage; B0-like `s184` cov 5 / 10; B0-like v4-balanced cov 5 | 20, 6, 20, 20 of 20 | identical | 0 | 0 | 1.13-1.43 |
| R mixed-harsh | 19/20 | 20/20 | +0.05 [−0.116, +0.236] | +7.35 [6.44, 8.26] | 1.36 |
| R the other 13 named models | identical in every cell | | 0 | 0 to +1.0 | 1.04-1.56 |

## What the numbers say

* **The weighted vote does what it is built to do at the consensus level** (C5): on the same pending reads it turns
  erasures into correct bases (mixed-harsh cov 6: erased bytes in multi-read attempts 148,191 → 123,977, wrong bytes
  46,240 → 47,291, attempts within 2e + f ≤ r 4,092 → 5,021 of 9,137) and recovers more symbols (up to +25 per trial).
* **It does not move archive-level success** in any pre-registered cell (C2 REJECT). In the cells where it recovers most
  symbols (mixed-harsh cov 4-8, 0/40 in both arms) the remaining deficit per failed group is larger than the gain; in
  the transition cells (substitution-heavy cov 2.5-3) the gain is +1 archive each, inside the interval.
* **It converts erasures into errors where qualities say nothing.** With constant qualities the score is an equal-weight
  likelihood vote that resolves 2-1-1 splits the count vote erases: in quality-degradation and substitution-heavy at
  coverage 10 (wrong-address groups, none fitting either way) wrong bytes rose 20 → 794 and 16 → 687 while erasures fell.
  No outcome changed, but this is the uncalibrated-score risk in plain view.
* **Cost**: the median per-trial time ratio 1.288 passes the 1.30 bound narrowly; in fresh processes at 1 MiB the
  wall-time ratio was 1.32-1.42 (P4-EXP-04, not a pre-registered criterion), from the path alignment and the quality
  gather on every read in pass 1. Peak RSS did not change (the extra 285 bytes per pending record go to the spill files).
* **Safety**: 0 false SUCCESS; misleading qualities are covered by a unit test (inverted qualities never give a false
  SUCCESS).

## Proposed default change

None. The pre-registered rule is not satisfied. `consensus_weighting="quality"` remains opt-in (`--consensus-weighting
quality`, config key `decode.consensus_weighting`).

## Limitations

SIMULATED qualities (informative by construction); default decode mode only (not combined with smart indel recovery or
soft decoding, which already use per-read quality evidence in their own soft consensus); v4-balanced and one B0-like
`s184` layout; 20 kB archives (1 MiB only in P4-EXP-04); per-cell intervals uncorrected for multiplicity; decode-time
ratios measured under shared CPU load with a fixed arm order (count first).
