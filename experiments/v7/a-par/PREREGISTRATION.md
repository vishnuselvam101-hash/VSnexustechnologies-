# A-PAR pre-registration: outer parity for low coverage (EXPERIMENTAL, SIMULATED)

Registered before any decode on seeds 82060-82099 with profile v7-lowcov.

- Variable: outer row code only. `v7-lowcov` = v4-balanced strand layout (313 nt, 16 B inner parity, 3-nt markers
  every 24 nt), row code K=64 + M=48 (v4-balanced: 64 + 16). No format change (existing profile mechanism).
- Decoder: `read_clustering="fallback"`, `consensus_template="full"`, all other defaults. Nothing tuned.
- M chosen before the held-out run from the measured A-CONS held-out per-strand loss (cov5 p=0.318) with a binomial
  row model: predicted archive success M=48 cov5 0.953, cov10 1.000, cov3 0.000. Dev check (seeds 82043-82045, cov5): 3/3 EXACT.
- Comparison arm: v4-balanced with the same decoder = A-CONS held-out "full" results (same seeds, same channel).
- Cost: 20,000 B archive → 967 data strands vs 679 (+42 % strands/nucleotides).

Pass criterion (fixed now): 0 FALSE_SUCCESS and 0 false frames at every coverage; cov5 EXACT >= 36/40;
cov10 EXACT = 40/40. cov3 is reported only (predicted to fail).
