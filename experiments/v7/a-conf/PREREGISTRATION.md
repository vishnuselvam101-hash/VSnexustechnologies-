# A-CONF pre-registration: confirmatory test of Nanopore Phase 1 (EXPERIMENTAL, SIMULATED)

Committed before any seed ≥ 82100 is run (protocol §7: confirmatory seeds start at 82100). Nothing in the decoder,
the profiles or the thresholds may change after this commit; a change needs a new PREREG with new seeds, and this
result is reported too.

## Hypothesis

H1: the opt-in full-template consensus (`consensus_template="full"`, bff39a9) recovers more data frames and at least
as many exact archives as the reference consensus (`"wildcard"`) on fresh seeds of the nanopore-like channel. Its
coverage-10 result (A-CONS, 40/40) and the v7-lowcov coverage-5 result (A-PAR, 38/40) replicate on seeds never used
for development, tuning or acceptance.

## Scope (stated before the run)

- Channel: the shipped, unfitted `nanopore-like` stress model (`vnx.channel-model/1`), the V6 baseline condition.
  D3 (`work/v7-nanodata`, 5386080) found that this model fails M1-M6 against real D13 and CAS9 reads (real reads have
  1.3-1.7x more edits, 1.7-3.5x more insertions and 27-40 % multi-base deletions vs about 4 %). A pass here confirms the
  result **on this simulated stress model only**. It is not a nanopore-robustness claim and not physical validation.
- Protocol A-EXP-01 criterion P2 (model E fitted on held-out D03) is BLOCKED: no nanopore model is ADEQUATE
  (`docs/V7_CHANNEL_MODELS.md`). This experiment does not replace P2.

## Design

| item | value |
|---|---|
| Baseline | arm `old`: v4-balanced, `consensus_template="wildcard"` (the V6/A1 reference path) |
| Controlled change | arm `phase1`: v4-balanced, `consensus_template="full"` (only this field differs from `old`) |
| Overhead arm | arm `lowcov`: v7-lowcov (row code 64 + 48), `consensus_template="full"` (A-PAR configuration) |
| Input, layout | the slow tier of `tests/nanopore/corpus/cases.json` (20,000 random bytes, 313-nt strands); only the channel seed and, for `lowcov`, the profile change |
| Seeds | 82100-82139 (40), fresh |
| Coverage | 3, 5, 10 (mean; the corpus case's coverage distribution) |
| Decodes | 40 × 3 × 3 = 360, one fresh process each, 4 workers |
| Software | the commit of this file; native kernels built (`python -m vnxdna.native --require-native` all_native true) |

## Metrics (per arm × coverage)

EXACT (container SHA-256) with Wilson 95 % interval; EXPLICIT_FAILURE; FALSE_SUCCESS; false frames; mean data frames
recovered; mean consensus-valid strands; strands written (overhead `lowcov` vs `phase1`); median and max decode
seconds; max peak RSS; load average per decode.

## Criteria (fixed now)

| ID | criterion | rule |
|---|---|---|
| C1 | no false success | FALSE_SUCCESS = 0 and false frames = 0 in all 360 decodes (95 % upper bound 3/360 per decode) |
| C2 | Phase 1 better than old (A-CONS rule), at each coverage | mean data frames `phase1` > `old`; `phase1` ≥ `old` on ≥ 90 % of seeds (paired); EXACT `phase1` ≥ `old` |
| C3 | coverage-10 replication (A-CONS) | `phase1` EXACT ≥ 36/40 (the A-CONS Wilson lower bound 0.91 × 40) |
| C4 | low-coverage replication (A-PAR thresholds unchanged) | `lowcov` cov 5 EXACT ≥ 36/40 and cov 10 EXACT = 40/40 |
| C5 | no harm | 0 seeds at any coverage where `old` is EXACT and `phase1` is not |

Coverage 3 is reported without a claim (predicted to fail: about 207 strands per trial have < 2 reads).

## Decision rule

- C1 fails: REJECT Phase 1 (protocol §6 rule 3), stop, root-cause, re-register with new seeds.
- C1, C2, C3, C5 pass: Phase 1 CONFIRMED on the nanopore-like stress model. C2 is judged per coverage.
- C4 passes: v7-lowcov coverage-5 result CONFIRMED on that model; it fails: reported as not replicated
  (NEEDS MORE DATA), v7-lowcov stays EXPERIMENTAL opt-in.
- Whatever the result: no default changes, no tuning on these seeds, and the verdict is about simulated data only.

## Commands

```
PYTHONPATH=src python experiments/v7/a-conf/run.py --jobs 4        # results/confirmatory.jsonl (resumable)
PYTHONPATH=src python experiments/v7/a-conf/summarise.py           # results/summary.json
```
