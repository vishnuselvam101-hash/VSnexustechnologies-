# AB-EXP-01 / 02, AB-FUZZ, AB-DIAG: opt-in retry band (job #80, SIMULATED)

**SIMULATED.** Software strands, the versioned channel models of `experiments/v6/channel` (derived coverage variants in
`AB-EXP-01-retry-band/models/`, each recording its base model) and the decoder of this branch. No DNA was synthesised,
stored or sequenced. Nothing here is biological validation. Pre-registration: [PREREG.md](PREREG.md) (commit `f96d3c0`,
before any run).

## Provenance

| item | value |
|---|---|
| code | implementation `2135461`, harness and analysis `f96d3c0` (work/v6-align-band from build/v6-sprint `29df22a`); `dirty_tracked: false` at run time (only this run's own outputs untracked) |
| AB-EXP-01 | `PYTHONPATH=src nice -n 10 python experiments/v6/phase4/phase4.py run --config experiments/v6/align-band/AB-EXP-01-retry-band/config.json --jobs 3` (1,014 s wall); `summary.json` (harness `summarise`, determinism cells without a baseline); `verdict.json` (`verdict.py`) |
| AB-EXP-02 | `PYTHONPATH=src nice -n 10 python experiments/v6/align-band/cost.py --out experiments/v6/align-band/AB-EXP-02-cost` (1 MiB payload, fresh child process per decode, load average about 6-7 on 8 cores) |
| AB-FUZZ | `PYTHONPATH=src nice -n 10 python experiments/v6/align-band/fuzz_retry.py --rounds 400 --reads 256 --out experiments/v6/align-band/AB-FUZZ/results.json` |
| AB-DIAG | `PYTHONPATH=src nice -n 10 python experiments/v6/align-band/funnel.py --out experiments/v6/align-band/AB-DIAG/results.json` (ground truth; descriptive) |
| backends | align native, reads native, RS native (header line of `trials.jsonl`) |
| seeds | trials 80000-80019 (D panel 80000-80004, X panel 80000-80009); cost 81000-81002; fuzz 80080; data seed 6201 |

## Verdicts (pre-registered criteria)

| id | result | verdict |
|---|---|---|
| C1 no false SUCCESS | 0 of 1910 decodes | ACCEPT |
| C2 nanopore-like gain (cov 3/5/10 pooled) | 0/60 vs 0/60, gain +0.0000 [-0.0602, +0.0602] | REJECT |
| C3 no harm | no cell with upper bound < 0; 0 pairs where only `default` decoded; pool without nanopore-like 482/840 vs 475/840, gain +0.0083 [+0.0022, +0.0145] | ACCEPT |
| C4 time ≤ 1.5× where `default` decodes (≥ 10/20) | 23 cells, median ratio 0.992, max 1.017 | ACCEPT |
| C5 peak RSS ≤ 1.15× (every pair) | max 1.258 (deletion-heavy 1 MiB, both arms failed); models that decode: ≤ 1.032 | REJECT |
| C6 determinism, 1 vs 4 workers | 15 pairs, 0 differences | ACCEPT |
| C7 native = reference | 102,400 reads (22,028 in band, 70,395 in the retry window, 9,977 beyond; 25,600 through the path kernel), 0 mismatches | ACCEPT |
| S1 high-indel gain (deletion-/insertion-heavy, mixed-harsh, nanopore-like) | 7/240 vs 0/240, gain +0.0292 [+0.0074, +0.0590] | ACCEPT |

**Decision (rule of PREREG §6): KEEP OPT-IN.** C2 and C5 REJECT. The retry band stays opt-in (`--retry-band 16`).

## Every grid cell (20 paired seeds each)

| cell | default exact (Wilson 95 %) | retry16 exact (Wilson 95 %) | paired gain [Newcombe 95 %] | median time ratio | reads within band 6 | retry16 failures by stage |
|---|---|---|---|---|---|---|
| b0like-s184/cov10 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.98 | 1.000 | — |
| b0like-s184/cov3 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 1.00 | 1.000 | outer-consensus 20 |
| b0like-s184/cov5 | 7/20 [0.18, 0.57] | 7/20 [0.18, 0.57] | +0.00 [-0.05, +0.05] | 0.98 | 1.000 | outer-consensus 13 |
| burst-loss/cov10 | 1/20 [0.01, 0.24] | 1/20 [0.01, 0.24] | +0.00 [-0.14, +0.14] | 0.97 | 0.995 | outer-loss 19 |
| burst-loss/cov3 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 0.98 | 0.996 | outer-loss 20 |
| burst-loss/cov5 | 1/20 [0.01, 0.24] | 1/20 [0.01, 0.24] | +0.00 [-0.14, +0.14] | 1.00 | 0.995 | outer-loss 19 |
| clean/cov10 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.95 | 1.000 | — |
| clean/cov3 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 1.01 | 1.000 | — |
| clean/cov5 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.96 | 1.000 | — |
| deletion-heavy/cov10 | 0/20 [0.00, 0.16] | 6/20 [0.15, 0.52] | +0.30 [+0.08, +0.52] | 1.39 | 0.648 | outer-consensus 14 |
| deletion-heavy/cov3 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 1.34 | 0.646 | outer-address 17, superblock 3 |
| deletion-heavy/cov5 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 1.31 | 0.646 | outer-consensus 20 |
| dropout-10/cov10 | 19/20 [0.76, 0.99] | 19/20 [0.76, 0.99] | +0.00 [-0.14, +0.14] | 0.99 | 1.000 | outer-loss 1 |
| dropout-10/cov3 | 5/20 [0.11, 0.47] | 5/20 [0.11, 0.47] | +0.00 [-0.08, +0.08] | 0.99 | 1.000 | outer-loss 15 |
| dropout-10/cov5 | 19/20 [0.76, 0.99] | 19/20 [0.76, 0.99] | +0.00 [-0.14, +0.14] | 0.99 | 1.000 | outer-loss 1 |
| dropout-20/cov10 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 0.97 | 1.000 | outer-loss 20 |
| dropout-20/cov3 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 1.00 | 1.000 | outer-loss 20 |
| dropout-20/cov5 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 1.00 | 1.000 | outer-loss 20 |
| dropout-5/cov10 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 1.02 | 1.000 | — |
| dropout-5/cov3 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.99 | 1.000 | — |
| dropout-5/cov5 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 1.00 | 1.000 | — |
| illumina-like/cov10 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.96 | 1.000 | — |
| illumina-like/cov3 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 1.00 | 1.000 | — |
| illumina-like/cov5 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.98 | 1.000 | — |
| insertion-heavy/cov10 | 0/20 [0.00, 0.16] | 1/20 [0.01, 0.24] | +0.05 [-0.12, +0.24] | 1.39 | 0.648 | outer-consensus 19 |
| insertion-heavy/cov3 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 1.39 | 0.646 | outer-address 16, superblock 4 |
| insertion-heavy/cov5 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 1.34 | 0.648 | outer-consensus 19, superblock 1 |
| mixed-harsh/cov10 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 0.97 | 0.998 | outer-consensus 20 |
| mixed-harsh/cov3 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 0.98 | 0.999 | outer-address 14, superblock 6 |
| mixed-harsh/cov5 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 0.97 | 0.998 | outer-address 4, outer-consensus 16 |
| mixed-mild/cov10 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.99 | 1.000 | — |
| mixed-mild/cov3 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.99 | 1.000 | — |
| mixed-mild/cov5 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.97 | 1.000 | — |
| nanopore-like/cov10 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 1.56 | 0.357 | superblock 20 |
| nanopore-like/cov3 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 1.40 | 0.359 | superblock 20 |
| nanopore-like/cov5 | 0/20 [0.00, 0.16] | 0/20 [0.00, 0.16] | +0.00 [-0.16, +0.16] | 1.47 | 0.361 | superblock 20 |
| quality-degradation/cov10 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.99 | 1.000 | — |
| quality-degradation/cov3 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.99 | 1.000 | — |
| quality-degradation/cov5 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 1.01 | 1.000 | — |
| substitution-heavy/cov10 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.99 | 1.000 | — |
| substitution-heavy/cov3 | 18/20 [0.70, 0.97] | 18/20 [0.70, 0.97] | +0.00 [-0.13, +0.13] | 1.01 | 1.000 | outer-consensus 2 |
| substitution-heavy/cov5 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 1.00 | 1.000 | — |
| uneven-coverage/cov10 | 20/20 [0.84, 1.00] | 20/20 [0.84, 1.00] | +0.00 [-0.16, +0.16] | 0.99 | 1.000 | — |
| uneven-coverage/cov3 | 6/20 [0.15, 0.52] | 6/20 [0.15, 0.52] | +0.00 [-0.06, +0.06] | 0.99 | 1.000 | outer-loss 14 |
| uneven-coverage/cov5 | 19/20 [0.76, 0.99] | 19/20 [0.76, 0.99] | +0.00 [-0.14, +0.14] | 1.00 | 1.000 | outer-consensus 1 |

## Exploratory panel (descriptive, 10 seeds)

nanopore-like at coverage 15: 0/10 default, 0/10 retry16, 0/10 smart+soft, 0/10 smart+soft+retry16; coverage 30: 0/0/0/0 of 10. Every failure is detected.

## Cost (AB-EXP-02, 1 MiB, fresh process; time and memory MEASURED on a shared machine)

| model | seed | exact default / retry16 | wall-time ratio | peak RSS ratio (VmHWM) |
|---|---|---|---|---|
| mixed-mild | 81000 | yes / yes | 0.941 | 0.942 |
| mixed-mild | 81001 | yes / yes | 1.019 | 0.918 |
| mixed-mild | 81002 | yes / yes | 1.019 | 0.917 |
| illumina-like | 81000 | yes / yes | 0.999 | 0.987 |
| illumina-like | 81001 | yes / yes | 0.989 | 0.988 |
| illumina-like | 81002 | yes / yes | 1.041 | 1.032 |
| deletion-heavy | 81000 | no / no | 1.484 | 1.223 |
| deletion-heavy | 81001 | no / no | 1.446 | 1.258 |
| deletion-heavy | 81002 | no / no | 1.497 | 1.195 |
| nanopore-like | 81000 | no / no | 2.159 | 1.195 |
| nanopore-like | 81001 | no / no | 2.151 | 1.155 |
| nanopore-like | 81002 | no / no | 2.242 | 1.173 |

The extra time and memory appear only where reads fall beyond the band: the retried reads cost a band-16 DP (33 band
cells per row against 13) and, when they still fail, become pending records (pass-2 spill and consensus). Models whose
reads stay in band pay nothing measurable (ratios 0.92-1.04, noise).

## Why nanopore-like still fails (AB-DIAG, ground truth, mean of 5 seeds; descriptive)

| cell | band | reads aligned | erased bytes per aligned read (of 70; r = 16) | header address exact / within 1 byte | strands an oracle count vote decodes |
|---|---|---|---|---|---|
| nanopore-like/cov10 | 6 | 0.358 | 32.6 | 0.051 / 0.123 | 0.042 |
| nanopore-like/cov10 | 12 | 0.871 | 39.7 | 0.041 / 0.100 | 0.247 |
| nanopore-like/cov10 | 16 | 0.981 | 41.3 | 0.038 / 0.094 | 0.289 |
| nanopore-like/cov10 | 24 | 1.000 | 41.7 | 0.038 / 0.093 | 0.296 |
| nanopore-like/cov15 | 6 | 0.357 | 32.6 | 0.050 / 0.116 | 0.102 |
| nanopore-like/cov15 | 12 | 0.869 | 39.7 | 0.043 / 0.098 | 0.457 |
| nanopore-like/cov15 | 16 | 0.981 | 41.4 | 0.040 / 0.093 | 0.509 |
| nanopore-like/cov15 | 24 | 1.000 | 41.7 | 0.040 / 0.092 | 0.514 |
| deletion-heavy/cov5 | 6 | 0.648 | 25.0 | 0.440 / 0.537 | 0.735 |
| deletion-heavy/cov5 | 12 | 0.992 | 29.8 | 0.375 / 0.469 | 0.870 |
| deletion-heavy/cov5 | 16 | 1.000 | 30.0 | 0.373 / 0.467 | 0.870 |
| deletion-heavy/cov5 | 24 | 1.000 | 30.0 | 0.373 / 0.466 | 0.870 |

The band was the first wall, not the only one. With band 16 almost every nanopore-like read aligns (0.36 → 0.98), but
(1) only about 4 % of aligned reads carry their exact address in the header (9 % within the one byte pass 2 can snap),
because any error in the 40-nt scrambled header corrupts it and a wrong byte 0 (the scrambler seed) garbles all of it; reads are therefore scattered over wrong addresses and the superblock groups never collect enough
reads; and (2) even with perfect grouping by the true strand, the whole-segment erasure rule leaves about 41 of 70
frame bytes erased per read and the count vote decodes only 29 % (coverage 10) to 51 % (coverage 15) of strands, below
what the outer code (and the superblock, 3 of 12 symbols) needs per group. On deletion-heavy the same funnel shows
why the retry band helps there: addresses survive (37 % exact, 47 % within one byte) and oracle consensus reaches 87 %
of strands once the band covers the drift. The next levers for nanopore-like are header-free address recovery
(clustering pending reads without trusting the header) and a finer indel localisation than whole-segment erasure; both
are outside this job.

## Deviations from the pre-registration

1. **Peak RSS method (AB-EXP-02).** PREREG §3 said "peak RSS by `wait4`". The first run showed identical
   `ru_maxrss` for different models and arms (max ratio exactly 1.0): on Linux the exec'd child inherits the spawning
   parent's high-water mark, so every decode smaller than the harness reported the harness's RSS. The committed
   `results.json` keeps both readings: `wait4_ru_maxrss_bytes` is equal within each (model, seed) pair in all 12 pairs
   (296-431 MB, the harness's own size), while the child's VmHWM is 98-217 MB. `cost.py` was changed (after the pre-registration commit, before any criterion
   was evaluated) to have the child record its own `VmHWM`; both values are in `results.json`
   (`peak_rss_bytes` = VmHWM, `wait4_ru_maxrss_bytes`). C5 is evaluated on VmHWM, and REJECTs. The same `wait4`
   caveat applies to P4-EXP-04 (`experiments/v6/phase4/cost.py`), whose RSS ratios are therefore not reliable.
2. `phase4.py summarise` cannot summarise a configuration whose determinism cells lack the baseline arm; `summary.json`
   was produced by calling the harness's own `summarise` separately for the grid + exploratory cells (baseline
   `default`) and the determinism cells (no baseline). The verdict does not use `summary.json`.

## Limitations

Synthetic channel models, i.i.d. apart from their explicit homopolymer, burst and GC terms. 20 seeds per cell: a 0/20
or 20/20 cell has a Wilson 95 % interval of width 0.16; per-cell intervals are not corrected for multiplicity. In-process
decode times share a machine with other work (`nice` 10, 3 trial processes). One retry band value (16) was tested.
