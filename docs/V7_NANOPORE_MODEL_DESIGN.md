# V7 fitted nanopore channel model: design (step 7A)

Status: DESIGN. Nothing fitted under this design yet. Adequacy criteria are fixed separately, before any fit, in
`experiments/v7/fit-nano/PREREGISTRATION.md`.

## 1. Why

D3 (`work/v7-nanodata` 5386080, `experiments/v7/nanodata/results/README.md`) compared real public nanopore reads with
the shipped `nanopore-like` model: the model fails M1-M6 on all four D13 runs and on CAS9. Real reads have 1.3-1.7x more
edits, 1.7-3.5x more inserted bases and 27-40 % of deletion events of length ≥ 2 (simulated about 4 %). Every V7
nanopore result so far (A-LOSS, A-CONS, A-PAR, A-CONF) is on that milder model. A fitted model that passes a held-out
adequacy test is the prerequisite for any channel × coverage matrix and for protocol criterion P2.

## 2. Data already available (all outside the repository; manifests committed)

| ID | data | licence | FIT / DEV | HELD-OUT (fixed before any look) | use here |
|---|---|---|---|---|---|
| D03 | Zenodo 10943282, ONT guppy HAC and fast, pass reads, forward/backward | CC BY 4.0 | files 0 and 2, reference buckets | **file-1 whole** (protocol amendment 1) | primary fit (HAC pass), harsher variant (fast pass) |
| D13 | Lopez et al. 2019 (uwmisl data-ncomms19-nanopore), runs 13, 15, 16, 18, 20, concatemers split by PR-4 | per manifest | runs 15/16/18/20, buckets 0-5 / 6-7 | **run 13 (space_shuttle) whole**; buckets 8-9 | second, independent platform era; deletion-run structure |
| CAS9 | Imburgia et al. 2025 (uwmisl cas9-random-access) | per manifest | read-ID buckets | **address g13 whole** | descriptive only: its error basis is INFERRED (leave-one-out consensus), not reference-aligned |
| D04 | CNR ONT reads | MIT | reference buckets | reference buckets 8-9 | not used: basecaller not stated |

Statistics already computed on FIT/DEV (no held-out look): D3 per-run JSON (rates with bootstrap CIs, deletion and
insertion run histograms 1-8+, homopolymer-conditioned indel rates, position profiles, length drift, edit distance per
read, read lengths) and the D03 round-2 fits (`experiments/v7/fit-d03/a2/`), which fail only M3.

Data size: D13 adequate runs give 0.36-1.08 M segments per split per run (FIT ≥ 60,000 references for run 15), D03
FIT holds tens of thousands of reads per condition. Both are large enough for a three-way split; D13 run 16 is too
small for segment statistics (§4.4 of D3: 1,226 FIT references) and stays excluded.

## 3. What the model must represent, and the gap

| effect | measured in | `/2` schema today | fitter today | needed change |
|---|---|---|---|---|
| substitutions, spectrum | D3, D03 | per-base rate + 4×4 matrix + 3-mer context | fitted (context: substitutions only) | none |
| single-base insertions / deletions | D3, D03 | per-base start rates | fitted | none |
| **multi-base deletions** | D3 M5: 1:2.44 M, 2:1.18 M, 3:0.21 M, 4:0.07 M (run 15) — not geometric | `deletion` run: geometric only | geometric when mean > 1.10 | **empirical run-length pmf** (lengths 1…8, 8 = 8+) |
| **multi-base insertions** | D3 insertion runs | `insertion.run_length`: single or geometric | geometric when mean > 1.02 | **empirical run-length pmf**, same form |
| homopolymer effect on indels | D3 M6, D03 | homopolymer multiplier (run-level) | fitted | none |
| indel 3-mer context | — | allowed | not fitted: not identifiable after leftmost normalisation (estimate.py) | none; documented limit |
| position profile | D3 M4 | relative bins | fitted | none |
| per-read error burden | D3 M2 (edit distance per read) | `read_heterogeneity` gamma | fitted when Var(m) > 0.01 | none; checked by M2 + new burden metric |
| error-type correlation | D3 burstiness (descriptive) | `correlation` (lag, p(event\|event)) | not fitted | fit only if FIT burstiness exceeds the 10 % descriptive band; else documented |
| read length / drift | D3 M3, D03 M3 (the D03 fits' only failure) | follows from indel balance | indirect | expected to improve with empirical indel runs; M3 stays gating |
| quality values | D03 FASTQ | Gaussian quality model | fitted | not gating (decoder does not use qualities on this path) |

The only schema change is an **empirical run-length distribution** for insertion and deletion runs:
`{"distribution": "empirical", "pmf": [p1 … p8]}` (sums to 1; the last bin is "8 or more", drawn as 8 + geometric
tail with the fitted tail mean). It is a simulation-side, opt-in extension of `vnx.channel-model/2`: `/1` and every
existing `/2` model keep their bytes and SHA-256; the codec format is untouched.

Estimation: from FIT only. Observed run histograms are biased by alignment merges of adjacent events and by rate
heterogeneity; the fitter already inverts this for the geometric case (`deletion_from_observed`). For the empirical
case the inversion is done by simulation calibration on FIT: simulate with candidate pmf, re-align with the same
pipeline, adjust bin-wise until the simulated observed histogram matches the FIT histogram (fixed iterations, fixed
seed, recorded). Every parameter records dataset, split, reference set, extraction (alignment) method, fitting method
and seed (the `/2` provenance and `parameters.basis` fields).

## 4. Train / dev / held-out procedure

1. FIT: estimate parameters (per dataset condition: D03 HAC pass, D03 fast pass, D13 runs pooled by file).
2. DEV: compute the adequacy metrics; at most **two** DEV looks per model (logged in `SPLIT_ACCESS_LEDGER.jsonl`).
   Changing the model after a DEV look counts as a new look.
3. PREREG commit fixing the model files (SHA-256) — then one HELD-OUT evaluation per model (D03 file-1, D13 run 13).
   The held-out verdict is final: ADEQUATE or INADEQUATE. No refit after the held-out look.
4. Only a model ADEQUATE on held-out data may be used for decoder evaluation (protocol 5.4); the others run only as
   labelled stress conditions.

## 5. Implementation steps (in order; each its own commit with tests)

1. Merge `work/v7-nanodata` (D3 driver, splitter, manifest) after review, so the D13 data path is on build/v7-sprint.
2. `/2` schema: empirical run-length pmf (validation, canonical SHA-256 stability tests for existing models, simulator
   sampling, determinism, round-trip tests M10).
3. Fitter: empirical pmf estimation by FIT calibration; insertion-length and per-read burden metrics added to
   `validate.py` next to M1-M10.
4. Fit on FIT, two DEV looks at most, PREREG of the frozen models, one held-out evaluation, report.

## 6. Risks and limits stated in advance

- D03 and D13 are each one lab and one synthesis; ADEQUATE on them is not a statement about nanopore in general.
- D13 is 2019-era chemistry; D03 basecaller versions are as published. Neither is current-generation ONT.
- Insertions at repeat boundaries and indel contexts remain only partly identifiable; the residual misfit is reported.
- If no model passes on held-out data, the result is INADEQUATE and the matrix benchmark stays BLOCKED; this is a
  valid negative result.
