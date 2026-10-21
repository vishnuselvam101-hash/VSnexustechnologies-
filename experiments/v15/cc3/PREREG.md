# V15 CC-3 pre-registration: nanopore-like decode failures before/after the ported fixes (SIMULATED)

SIMULATED: reads from software channel models; no DNA was synthesised, stored or sequenced. Not a claim about real
nanopore data.

Written and committed before any run listed here. Only the harness smoke cases (`smoke-*`, seed 95000, 20 KB) and one
local development decode of seed 93090 (already used by evaluation 5) ran before this file.

## Trees
- **base** = `cfd6ee0` (build/v15 head = v14.0.0 + docs), the V15 behaviour before CC-3.
- **new** = the commit that adds this file on `work/v15-cc3`. Code changes vs base (ported, not redone):
  - from `work/nano-10mb` (job 86): over the candidate-pair budget the clustering stage lowers the bucket cap instead
    of stopping (`bucket_cap_floor` 2, native cluster ABI 6), and cluster members are grouped with one sort;
  - from `work/nanopore-f2`: preset F hypothesis selection + inner trials (a12afb4) and the RANSAC codeword scan of
    every failed frame (a419194), with their tests and evaluation 4/5 records;
  - new: the opt-in decoder preset `nanopore` (= F) on the CLI/config (`--cluster-preset`).
  The default decode path (no read clustering) executes none of the changed code.
- Not ported: the `v10-oligo300` strand profile (not needed for these failures; it is a format/profile decision for
  Q1 frame 6 / CC-5 / FZ-3).

## Harness
`experiments/v15/cc3/run.py` (cases, configurations and recorded fields are defined there), run on Kaggle CPU
sessions through `kaggle.sh` (4 vCPU, decoder workers 4). Every configuration of a (case, seed, tree) decodes the same
prepared read file in its own process. Configurations: `default` (no read clustering), `E`, `F`
(`experiments/v9/consensus/candidates.py`). Outcome per row: EXACT (output container SHA-256 = original),
EXPLICIT_FAILURE (non-SUCCESS), FALSE_SUCCESS (SUCCESS with another SHA-256), or CRASH/ERROR (reported, never
dropped). A job killed by Kaggle's time limit is reported as such, never as a decoder outcome. Timings are Kaggle
timings, reported only within this experiment and never compared with VPS timings.

## Runs and seeds (fixed now; each run once, no re-runs, no exclusions)
| id | case | seeds | trees x configs | role |
|---|---|---|---|---|
| R | d13f1-1m | 93090-93099 | base x {default, E, F} | reproduction of the V15 behaviour (seeds used by evaluation 5: development) |
| P | d13f1-1m | 93090-93109 | new x {F} | port fidelity: evaluation 5 had F 19/20 (fails 93097, row 92) at the source commit |
| H | d13f1-1m | **93120-93139** (never used) | base x {E, F}, new x {E, F} | held-out 1 MiB |
| M | d13f1-10m | 93100 (dev), 93110 (held-out of job 86, run there on old code), **93111** (never run) | base x {E}, new x {E, F} | 10 MB |
| G | clean-1m, illumina-like-1m, indel-1m, nanopore-like-1m | **93140-93142** | base and new x {default, E, F} | regression + the versioned nanopore-like model |

The 1 MiB d13f1 case equals `experiments/arch/outer_list_eval` (v4-balanced, D13-F1 at coverage 10, negative
binomial dispersion 4); the 10 MB case equals `experiments/v10/w3` / job 86 (column parity 128/4). `indel-1m` is the
p6e INDEL channel (sub 0.01, ins 0.002, del 0.002, coverage 5).

## Criteria
- **Primary (hard gate):** 0 FALSE_SUCCESS over every row of every run.
- **H (1 MiB held-out):** new F exact count is reported with its Wilson 95 % interval; acceptance: no seed where base F
  or new E is EXACT and new F is not; new F >= base F. McNemar exact p (new F vs new E) is reported.
- **M (10 MB):** acceptance: on new, the clustering stage runs (status not stopped by `max_candidate_pairs`) on every
  completed run; outcomes and peak RSS are reported per seed. Expected on base: EXPLICIT_FAILURE with the candidate
  budget stop (reproduction). Three seeds support no rate claim.
- **G (regression):** `default` returns the same status and output SHA-256 on base and new for every row; on every
  row new E/F is EXACT wherever base E/F is EXACT.
- **P:** reported; a difference from evaluation 5 is investigated before any claim (development data, not evidence).

## Amendment 1 (2026-10-19, before any run of the seeds below)
Seen so far: runs R, H (15 of 20 seeds) and the port check in progress; no result of this amendment's case. Finding:
on the default v4-balanced archive the remaining F failures on D13-F1 at 1 MiB are each one or two rows short, and
V10 W3 (`experiments/v10/w3`, seeds 93000-93009) recovered 10/10 with decoder E once the existing opt-in V6 column
parity (stripe depth 128, 4 column-parity rows, +3.9 % strands) was used. Added run:

| id | case | seeds | trees x configs | role |
|---|---|---|---|---|
| C | d13f1-cp-1m (v4-balanced + column parity 128/4) | **93150-93169** (never used) | new x {default, E, F} | held-out: encoder-side redundancy + decoder preset |

Criteria: 0 FALSE_SUCCESS (hard gate); exact counts of E and F with Wilson 95 % intervals; no seed where E is EXACT
and F is not. If F (or E) reaches >= 19/20 here, the recommendation for nanopore-like reads is column parity at encode
time plus the `nanopore` decoder preset; otherwise the gap is reported as the remaining limit.
