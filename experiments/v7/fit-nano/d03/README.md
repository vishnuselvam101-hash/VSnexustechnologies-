# a7b: empirical indel run lengths on D03 (V7 step 7.4) — DEV look 1 of 2

Labels: fitted parameters and real-read statistics PUBLIC-DATA-DERIVED (D03, Welter et al.); every compared read from
a model SIMULATED. Rules: `experiments/v7/fit-nano/PREREGISTRATION.md` (7B) + `PREREGISTRATION-A1.md` (M6). Driver
`experiments/v7/fit/run.py --round a7b` (4172815, provenance fix 32bf0c9); fits 33e8f61..157167f on FIT (clean tree,
seed 20261005, bootstrap 200, calibration 4,000 refs × cov 10 × 5 iterations, 4 workers, load 0.9-2.4); validation
on DEV (seed 20261006). Held-out data not opened.

| step | content |
|---|---|
| Hypothesis | D3: the shipped model fails mainly on multi-base indels (M5). An empirical 1..8+ run-length pmf, calibrated on FIT, removes that misfit. |
| Baseline | round a2 (`experiments/v7/fit-d03/a2`): geometric runs, gating M2/M3/M8; HAC failed M3 |
| Controlled change | run-length pmf (insertion and deletion) instead of geometric; layout minruns 2..6 + read_rate for the 7B metrics. Nothing else. |
| Data / split | D03 guppy HAC and fast, pass, forward and backward; FIT files 0/2 buckets 0-5, DEV buckets 6-7 |

## Result (DEV look 1; verdicts under 7B gating)

| model | verdict | failed gating | M5 | M5i |
|---|---|---|---|---|
| hac-fwd | INADEQUATE | M3, M6r, M10 | pass | pass |
| hac-bwd | INADEQUATE | M1, M3, M6r | pass | pass |
| fast-fwd | INADEQUATE | M6r, M10 | pass | pass |
| fast-bwd | INADEQUATE | M1, M3, M6r, M10 | pass | pass |

M1a, M1c, M2, M2b, M8 and RL pass for all four. Calibration residuals: rates within 1.1 %, pmf TV ≤ 0.002.

## Failure analysis

1. **M6r (all four), structural.** Real indel rate rises with homopolymer length (hac-fwd 1.87 % at 1, 2.30 % at 3,
   2.83 % at 4, 3.57 % at 5, 4.61 % at 6+; 70k-10.7M sites per bin). The schema has one `min_run` step and one
   multiplier, so simulated rates are flat (2.0-2.1 %). Not sampling noise: failures exceed the A1 tolerance many times.
2. **M3 (three of four).** P(|drift| = 0) real 0.293 vs simulated 0.265 (hac-fwd), −2.8 pp; already failing in a2.
   Cause not identified (candidates: length-dependent homopolymer slips, insertion-deletion compensation).
3. **M10 (three of four), round-trip precision.** Worst ratios 1.03 (hac-fwd, a 0.02 % pmf bin) to 2.6 (fast-bwd tail
   mean); the fast models also miss on the 64-element substitution context and the matrix. Every vector element must pass.
4. **M1 insertion (bwd models).** Simulated inserted-base rate 5-6 % below real.

## Decision

**NEEDS MORE DATA / model change.** The run-length change did what it was meant to do: M5 and M5i pass. No model is
ADEQUATE, so none is frozen and no held-out evaluation runs. The next single controlled change (DEV look 2, the last
one) must address M6r: a homopolymer indel multiplier per run length (1…6+) in place of the single step. It needs a
schema extension, simulator support, fitter estimation and tests first, and an 8-question note before implementation.
