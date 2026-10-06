# V8 D13 public-data channel model (V8.1–V8.6)

Evidence classes: **PUBLIC-DATA-DERIVED** (D13 reads produced by Lopez et al. 2019; VNX-DNA sequenced nothing) and
**SIMULATED** (reads from fitted models). Pre-registration: `docs/V8_PREREGISTRATION.md`, amendment A1. Access ledger:
`experiments/v8/datasets/ACCESS_LEDGER.jsonl`. No held-out data was opened.

## Data (V8.1)

The first 100,000 read IDs in SHA-256 order were taken from each of runs 15, 16, 18 and 20 (run 18: all 79,449). They
were split into reference segments with the frozen V7 splitter, giving FIT 2,673,871 segments (126,708 references) and
DEV 894,901 segments (42,263 references). Held-out-bucket segments were discarded, and run 13 was not opened. The run-15
counts equal the V7 D3 characterisation exactly (1,076,103 FIT / 361,490 DEV), so the pinned pipeline reproduces.
Hashes are in `results/pipeline-run*.json` and `results/tables.json`.

## Error extraction (V8.2, FIT; `results/extraction.json`)

| statistic | value (95 % bootstrap CI over references) |
|---|---|
| substitution / insertion / deletion per base | 2.58 % / 2.93 % / 4.00 % (CI widths ≤ 0.02 pp) |
| per run (sub / ins / del) | run15 2.43/2.82/3.71 · run16 3.07/3.75/4.36 · run18 2.37/2.45/4.00 · run20 3.14/3.77/4.63 % |
| indel rate by homopolymer run length 1 / 2 / 3 / 4 / 5 / 6+ | 4.34 / 4.13 / 2.68 / 8.43 / 13.97 / 11.0 % (all ≥ 118,579 sites) |
| runs ≥ 2 among insertion / deletion events | 35 % / 40 % (means 1.60 / 1.64 bases) |
| error probability by Phred | Q0–4 28 %, Q10–14 4.8 %, Q20–24 1.5 %, Q30–39 0.9 %; Q45+ rises to 2.7–3.2 % (miscalibrated top) |
| position (oligo) | insertions depleted at both ends (0.011 and 0.008 per base in the first and last decile vs 0.021 inside) |
| zero-drift segments | 9.8 % |
| references without any segment | 0.04–1.9 % per run: an upper bound on dropout (NOT IDENTIFIABLE as molecule loss) |

Not identifiable from these data: dropout as molecule loss, synthesis vs sequencing errors, basecaller or chemistry
dependence. Whole-read length is a concatemer length (about 4.6 kb for apollo), not a single-oligo read length.

## Model (V8.3–V8.4) and verdict

F1 is the V7 a7c structure plus a quality model, fitted to the pooled FIT tables
(`models/d13-nanopore-f1.json`, parameter hash in `provenance.fitting.parameter_sha256`). It has empirical run lengths,
per-run-length indel multipliers 1.00 / 1.26 / 0.93 / 2.82 / 7.41 / 5.51, `min_run` 4, a substitution 3-mer context,
per-read heterogeneity and position profiles.

FIT-only pre-check (in sample; `results/precheck-f1-a1.json`, under amendment A1):
- **Passes:** M1a, M1, M5, M5i and RL. M6r and M8 also pass, but unstably across halves.
- **Fails stably:** M1c, M2, M2b and M3. M10 fails, but the failure is plausibly noise.

A1 corrected a selection mismatch: real segments are selected at edit distance ≤ 45, and simulated reads were not.
The correction fixed the edit-distance means but not the distribution shapes.

The F2 (paired-indel) rule did not trigger: the pair ratio is 1.44, not above the pre-registered 1.5.

**Verdict: INADEQUATE.** `DEV_LOOK_BLOCKED — FIT PRE-CHECK INADEQUATE` (`results/VERDICT.json`). No DEV look was spent,
the held-out data was not opened, and no model was frozen.

## Comparison (V8.6, one DEV evaluation, selects nothing; `results/comparison.json`)

| model | 7B metrics failed on D13 DEV (M10 excluded) |
|---|---|
| A: V6 shipped nanopore-like | all 11 |
| B: V7 a7b d03-hac-fwd | all 11 |
| C: V7 a7c d03-hac-fwd | all 11 |
| D: V8 F1 (D13 FIT) | M1c, M2, M2b, M3, M6r, M8 (passes M1a, M1, M5, M5i, RL) |

Fitting to D13 makes the aggregate rates and the run-length distributions match on DEV. The read-level distributions
(edit distance, per-read rate, drift), the substitution spectrum and the homopolymer stratification still do not.
