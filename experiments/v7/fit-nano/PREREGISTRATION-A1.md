# Fitted nanopore model: pre-registration amendment A1 (M6 noise floor)

Amends `PREREGISTRATION.md` (f8d617a) **before any model is fitted** under it. No FIT, DEV or held-out statistic has
been computed under the 7B design when this file is committed. Founder approval: 2026-10-06 ("yes amend M6").

## Change

| # | metric | old threshold | new threshold |
|---|---|---|---|
| M6 | homopolymer-conditioned indel rate, run length 1…5, 6+ | within 10 % relative per run length with ≥ 10,000 sites | within **max(10 % relative, 3 bootstrap SE of the real rate)** per run length with ≥ 10,000 sites (bootstrap over references, B = 200, the M1 rule) |

Every other metric, threshold, split, seed and the verdict rule are unchanged. Implementation:
`vnxdna.simulation.fit.validate.m6r_homopolymer_runs(..., real_M=...)` (metric key `M6r`), used by
`validate_model(..., prereg="7B")`.

## Reason

At the 10,000-site floor a bin holds about 200 indel events (rates ≈ 2 %), so the sampling noise of the real rate
alone is about 7 % relative. A model identical to the data-generating channel would then fail the old rule on the
sparsest tested bins at a material rate. SIMULATED check (`tests/simulation/test_fit_empirical.py`): two seeds of the
same channel differ by 5.8 % at 19,521 sites and by 35 % at 2,300 sites. The new rule is the one M1 already uses;
a real misfit (indel rate × 1.5) still fails it.

## Effect on strictness

The rule only relaxes bins whose real rate is noisy (3 SE > 10 %). On bins with large counts it is the old rule. Both
the old-rule result (`rel_diff`, `threshold` 0.10) and the A1 tolerance are recorded in every M6r report, so the
old-rule verdict can be read off too.
