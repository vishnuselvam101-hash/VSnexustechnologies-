# V6 Phase 4 pre-registration: quality-weighted pass-2 consensus (SIMULATED)

**SIMULATED.** Every channel in this plan is a software model (`experiments/v6/channel`, `vnxdna.v6.loss` +
`vnxdna.v4.channel`). No DNA was synthesised, stored or sequenced. The simulator's quality scores are informative *by
construction* (a configurable share of erroneous bases gets a low Phred score); nothing here says how informative a real
platform's qualities are.

Written and committed **before** P4-EXP-02 was run. The analysis code (`verdict.py`), the cost script (`cost.py`) and the
comparison configuration (`P4-EXP-02-qw-consensus/config.json`) are committed in the same commit. Nothing below is
changed after the comparison runs; any deviation is listed in the results README under "Deviations".

## 1. Question

Does weighting each read's projected base by its own Phred quality in the pass-2 consensus vote recover more archives
than the V4 count vote, without any false SUCCESS, without harm where qualities carry no information, and at acceptable
cost?

## 2. Candidate and baseline (no format change)

| arm | `DecodeOptions` | what changes |
|---|---|---|
| `count` (baseline, current default) | `{}` | V4 vote in `recovery.consensus._consensus_symbols`: one vote per projected read base (erased bases abstain), winner share < 0.6 → erasure |
| `quality` (candidate, opt-in) | `{"consensus_weighting": "quality"}` | `consensus_quality_weighted`: per base log P(obs \| x) under the Phred reading of its quality (ε = 10^(−Q/10), clipped to [1e-6, 0.75]); summed over reads, normalised under a uniform prior; argmax wins; erased if the normalised score of the winner < 0.6, on ties, or with no read base. Address groups whose reads lack qualities fall back to `count` |

Everything else is identical (band 6, threshold 0.6, `max_pending_per_address` 64, segment indel rule, soft decoding off,
deferred schedule irrelevant). Verification is unchanged: every consensus frame must pass the inner RS + CRC-32 and decode
to the group's address; the container SHA-256 decides SUCCESS.

The score is a **Phred-interpreted** posterior, not a calibrated probability. No claim of calibration is made; the
threshold 0.6 is reused unchanged from the count vote and is not tuned.

Implementation: commit `342977e` (+ `9db7a06`, vectorised quality gather). Opt-in, default unchanged; unit and
end-to-end tests in `tests/v6/test_consensus_weighting.py` (count vote unchanged by default, fallback without qualities,
determinism across 1/2 workers, misleading (inverted) qualities never give a false SUCCESS).

## 3. Why this candidate (evidence from the failure analysis, P4-EXP-01)

The failure analysis (P4-EXP-01, seeds 41000-41019; an earlier aborted run of the same grid with the same seeds gave the
first 484 trials used to choose the cells below) showed:

* default-arm failures that are **consensus-limited** (every failed group would decode if consensus recovered the
  addresses that had pending reads): deletion-heavy 0/20 exact, insertion-heavy 0/20, mixed-harsh 19/20,
  substitution-heavy at coverage 3 16/20, mixed-mild at coverage 2 (part);
* on multi-read consensus attempts the count vote's damage is mostly **erasures** (deletion-heavy: mean 12.4 erased
  bytes and 3.3 wrong bytes per attempt, 61 % within 2e + f ≤ r = 16), so turning split votes into correct decisions is
  the lever a quality weight can pull; it cannot help where reads are missing (dropout-20, burst-loss: loss-limited) or
  never align (nanopore-like: every read drifts beyond the band, superblock never decoded).

The cells were chosen from that evidence to sit in the transition region (baseline success neither 0 nor 1) under
informative qualities. The comparison uses **fresh seeds** (base 52000), never the diagnostic seeds.

## 4. Design

Paired: per (cell, seed) the reads are simulated once and decoded by every arm (decode workers = 1, arms in fixed order
count → quality). 20 kB random payload, v4-balanced unless stated, data seed 6201.

| panel | cells | seeds | purpose |
|---|---|---|---|
| primary | substitution-heavy coverage 2.5 / 3 / 3.5; mixed-harsh 4 / 6 / 8; quality-degradation 2 / 2.5 / 3; mixed-mild 2 / 2.5; illumina-like 1.5 / 2 (13 cells; other parameters of the named model unchanged) | 40 | efficacy where qualities are informative and consensus limits |
| control | deletion-heavy, insertion-heavy (coverage 10 and 15), uneven-coverage, B0-like (1 %, 53/45/2 sub/del/ins, fixed coverage, constant qualities) on `s184` (no markers) coverage 5 and 10 and on v4-balanced coverage 5 (8 cells) | 20 | harm where qualities are uninformative (all Q30) |
| regression | the 14 named models at their own parameters | 20 | no regression anywhere |
| determinism | substitution-heavy cov 3, mixed-harsh cov 6, quality-degradation cov 2.5, plus arm `quality-w4` (workers = 4) | 5 | identical output for 1 and 4 workers |

Total 975 (cell, seed) pairs. Cost: P4-EXP-04 (`cost.py`), 1 MiB payload, mixed-mild and mixed-harsh, 3 seeds, each
decode in a fresh child process (peak RSS by `wait4`), arm order alternating by seed.

## 5. Statistics

* Success = SUCCESS with the original container SHA-256 (`exact`). Rates with Wilson 95 % intervals.
* Paired difference p(quality) − p(count): Newcombe's method 10 interval (hybrid score, no continuity correction) on
  the paired outcomes; pooled per panel over all (cell, seed) pairs.
* Mechanism: per primary trial, (multi-read consensus attempts with 2e + f ≤ r under the weighted vote) − (the same
  under the count vote), both computed by the harness observer from the **same pending reads** of the quality arm against
  ground truth; mean with a t interval over trials.
* Per-cell intervals are not corrected for multiplicity (13 primary cells); the pooled primary interval is the
  efficacy criterion.

## 6. Criteria (each reported ACCEPT or REJECT)

| id | criterion | ACCEPT iff |
|---|---|---|
| C1 | safety | 0 false SUCCESS over every decode of every arm |
| C2 | efficacy (primary) | pooled primary paired difference: 95 % lower bound > 0 |
| C3 | per-cell gains (reported) | a gain is claimed for a cell only where its 95 % interval excludes 0 (lower bound > 0) |
| C4 | no harm | no cell in any panel has a 95 % upper bound < 0, and the pooled control and pooled regression upper bounds are ≥ 0 |
| C5 | mechanism | mean per-trial difference in fitting attempts (weighted − count): 95 % lower bound > 0 |
| C6 | cost | median per-trial decode-time ratio quality / count ≤ 1.30 (P4-EXP-02, same process, shared CPU) and every fresh-process peak-RSS ratio ≤ 1.15 (P4-EXP-04) |
| C7 | determinism | 15/15 pairs identical between workers 1 and 4 (status, container SHA-256, read statistics) |

**Default-change rule.** A change of the default (`consensus_weighting = "quality"`) is *proposed* to the lead only if
C1, C2, C4, C6 and C7 all ACCEPT. Even then it is a proposal (the founder delegated the decision to the lead), the
evidence is SIMULATED with informative-by-construction qualities, and FASTA input keeps the count vote by the fallback.
Otherwise the option stays opt-in.

## 7. What is not tested

Real platform qualities (the CNR dataset has no qualities), smart indel recovery or soft decoding combined with the
weighted vote, V6 stripe profiles, archives above 1 MiB, other thresholds. No comparison with BMA or Trellis BMA is made
in this experiment; none can be, because VNX-DNA decodes only its own frames (see `docs/ALGORITHM_COMPARISON.md` §5).
