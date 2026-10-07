# V7 7.4: a7c candidates, FIT-only pre-check and diagnosis. Decision: DEV look 2 not spent

Evidence class: PUBLIC-DATA-DERIVED (D03 FIT reads) vs SIMULATED (reads from the candidates). Every number here is
**in sample** (the candidates were fitted on the same FIT data). Nothing here is validation. No DEV data was read after
DEV look 1, and no held-out data was read at all (split access ledger: `experiments/v7/datasets/SPLIT_ACCESS_LEDGER.jsonl`).

## Decision

**`DEV_LOOK_2_BLOCKED — FIT PRE-CHECK INADEQUATE`**, for all four candidates. Every candidate fails at least one 7B gating
metric in sample, and the failure is stable, both across two disjoint halves of the FIT references and across seeds. DEV
look 2, the last permitted DEV look, is **not spent**. No model is frozen. The held-out data stays unopened. Under the V7
rules the current model-selection cycle stops here: no further tuning in V7.

| candidate (a7c, v3.1.0) | model SHA-256 | gating failures in sample (class) |
|---|---|---|
| d03-hac-fwd | `d6f40cda…29802` | M3 (FAIL_STABLE), M10 (FAIL_STABLE) |
| d03-hac-bwd | `d69d126a…2252` | M3 (FAIL_STABLE), M10 (FAIL_STABLE in the pre-check; 5 of 6 diagnosis replicates pass) |
| d03-fast-fwd | `03a5b3f3…b4ec` | M10 (FAIL_STABLE) |
| d03-fast-bwd | `2538bafc…1f90` | M3 (FAIL_STABLE), M10 (FAIL_STABLE) |

Full hashes, seeds, dataset digests and code state: `precheck/*.precheck.json`, `diagnosis/*.json`, `DIAGNOSIS.json`.

## What the per-run-length change fixed

M6r, the structural failure of DEV look 1, now passes in sample for all four candidates, on the full FIT data and on both
halves. Calibration matches each run-length class within 4 %, and the fitted multipliers rise with run length, for example
hac-fwd 1.00 / 1.05 / 1.31 / 1.68 / 2.16 / 3.11 for run lengths 1 to 6+. M1a, M1c, M2, M2b, M5, M5i, M8 and RL pass for all
four candidates.

## Diagnosis of the remaining failures (step 3)

| metric | mechanism | class |
|---|---|---|
| M3 (zero-drift reads) | Real reads carry **co-located insertion+deletion pairs**. 16.6–22.8 % of real insertions have a deletion within 2 reference bases, against 4.1–8.2 % in the simulation. The model draws insertions and deletions independently, so it under-produces balanced reads at low edit distance (HAC: 28.9–29.1 % zero-drift real vs 25.7–26.5 % simulated). The per-read I, D and S means and dispersions agree within a few %. | genuine model misspecification |
| M10 (round trip) | (a) The deletion `tail_mean` refits low (bias up to 15 %, z up to 11.5). Runs are tallied up to 16 and longer runs are folded into the last bin, so a tail mean of 12–15 is **censored**. (b) The refit's seed-to-seed SD is **1.4–7× the bootstrap SE** used by the rule, because the bootstrap over references omits the calibration's Monte-Carlo noise. (c) Empirical pmf bins with target ~1e-4 have a bootstrap SE of ~0 (**sparse bins**). (d) The fast models (`min_run` 2 + substitution context) do not re-identify their substitution matrix and context (bias z up to 76): an **identifiability** limit. | estimator bias + metric statistics + sparse bins + identifiability |
| M1 (insertion, DEV look 1 bwd) | **Stratified**. Interior insertion bases match (simulated/FIT 0.99–1.01 in every position decile), but insertions after the last reference base are almost absent from the simulation (simulated/FIT 0.01–0.30, while 1–31 % of real inserted bases sit at the read end). Calibration matches interior events only. | genuine misspecification (read-end insertions), hidden by the aggregate metric |

The M10 rule text in `validate.compare_params` said `+ 0.02` (an absolute floor), but the formula never used it. The text
now states the formula that produced every V7 M10 result (5 SE + 8 % relative). **No threshold or metric was changed.**
Remedies (paired-indel process, read-end insertions, uncensored tail tally, calibration-aware SE, sparse-bin rule,
substitution identifiability) are candidates for the **V8 pre-registration**. None is applied in V7.

## Reproduce

```
PYTHONPATH=src python experiments/v7/fit/run.py fit <job> --round a7c --workers 4        # FIT only
PYTHONPATH=src python experiments/v7/fit/run.py precheck <job> --round a7c --workers 4   # FIT only
PYTHONPATH=src python experiments/v7/fit-nano/d03-a7c/diagnose.py <job> --workers 4 --replicates 6
PYTHONPATH=src python experiments/v7/fit-nano/d03-a7c/diagnose_m1.py <job> --workers 4
```
`run.py validate <job> --round a7c` (DEV look 2) is refused by `dev_look_2_gate`. There is no justifying pre-check and no
`FREEZE.json`.
