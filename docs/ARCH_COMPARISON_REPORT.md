# Hybrid architecture vs v9.0.0 — comparison report (SIMULATED channels)

Run 2026-10-11 06:49–08:05 IST, job `arch-bench`, on the 8-thread Xeon Gold 6240 host (load 1–3.8 recorded per run).
Trees: **v9** = v9.0.0 `e76e24b` (/root/vnx-dna-lab/v9-base), **arch** = `arch/hybrid-native` `4700165`.
The optional v10-base configuration was not run (founder asked for the V9 comparison only, time-boxed).
Raw rows: `experiments/arch/results/compare.jsonl`, `nanopore.jsonl`; summaries `*-summary.json`.
Label: SIMULATED — reads from software channel models; no DNA was synthesised, stored or sequenced.

## Acceptance (fixed in experiments/arch/compare.py before the run)
- Encode: byte-identical FASTA from both trees for every seed — **met** (10/10).
- Decode: identical outcome and identical recovered container on every seed — **met** (31/31).
- False success: **0**.

## Speed (paired per seed; speed-up = v9 seconds / arch seconds, geometric mean [range])
| scenario | n | input | workers | v9 median s | arch median s | speed-up |
|---|---|---|---|---|---|---|
| encode-w1 | 5 | 16 MiB | 1 | 6.30 | 5.27 | 1.15× [1.00–1.21] |
| encode-w4 | 5 | 16 MiB | 4 | 1.86 | 1.48 | 1.23× [1.15–1.29] |
| clean decode | 5 | 4 MiB | 4 | 1.61 | 1.59 | 1.02× [1.00–1.06] |
| substitution 2 % | 5 | 4 MiB | 4 | 2.78 | 2.62 | 1.07× [1.03–1.10] |
| indel (1 % sub, 0.2 % ins/del) | 5 | 4 MiB | 4 | 5.35 | 4.67 | 1.14× [1.12–1.16] |
| low coverage (2×) | 5 | 4 MiB | 4 | 1.41 | 1.36 | 1.04× [1.00–1.06] |
| indel, 1 worker | 3 | 4 MiB | 1 | 15.19 | 14.16 | 1.07× [1.06–1.09] |
| indel, 2 workers | 3 | 4 MiB | 2 | 8.68 | 7.81 | 1.10× [1.08–1.14] |
| **nanopore-like D13-F1, cov 10, decoder E** | 3 | 1 MiB | 4 | **1,017** | **140** | **7.2–7.4×** |

The native strand builder accounts for the encode gain (`arch-strand-ref`, the same tree with the Python strand
builder, runs at v9 speed: 1.01× / 1.00×). Peak RSS is within ±10 % of v9 in every scenario.

## Nanopore-like stress case
Seeds 93000–93002, 1 MiB, D13-F1 coverage 10, decoder preset E: both trees return EXPLICIT_FAILURE on all three
(same outcome, as in V10), v9 in 1,012–1,034 s, arch in 140–141 s. Recovery on this channel is the job of the opt-in
preset F (outer-assisted list decoding, C++ kernel): held-out evaluations 2 and 3 recovered 15/20 and 15/20 exactly
versus 1/20 and 2/20 for E, 0 false success (experiments/arch/outer_list_eval/README.md). Preset F is not part of
this V9 comparison because v9.0.0 has no equivalent.

## Limits
One host, one run per (scenario, seed); background load 1–3.8. Timings are wall-clock including interpreter start-up.
All channels are simulated.
