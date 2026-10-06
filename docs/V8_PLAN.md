# VNX-DNA V8 plan: public-data-grounded nanopore channel model

Branch `build/v8-public-channel`, created from the closed V7 state (`build/v7-sprint` 0fd7c54, docs/V7_COMPLETION_REPORT.md).
This is a plan. It contains no results and no validation claim.

## Objective and priorities

Build a nanopore channel model grounded in public data (D13, Lopez et al. 2019) and evaluate it independently. Then
evaluate the VNX-DNA recovery pipeline under it, and under controlled simulated channels. Priorities, in order: scientific
validity, provenance, model adequacy, independent evaluation, decoder performance, reproducibility, optimisation. Every
result is labelled SIMULATED, PUBLIC-DATA-DERIVED or REAL EXPERIMENTAL; V8 produces no REAL EXPERIMENTAL result.

## Starting point (inspection of V7, 2026-10-07)

| area | V7 component (reused) | frozen for V7 |
|---|---|---|
| schema | `vnx.channel-model/2` (`simulation/model2.py`): empirical run-length pmf, `homopolymer.indel_by_length`, context, read heterogeneity, parameters + basis labels, provenance (datasets, split, fitting) | /1 and existing /2 SHA-256 values; `to_v1` refusals |
| simulator | `simulation/engine.py` + `errormodels.py` (joint per-base sub/ins/del, run lengths, homopolymer, context, heterogeneity, quality) | V4/V5/V6/V7 draws (golden tests) |
| fitter | `simulation/fit/` (tally, estimate, calibrate, fit, model_out, validate, precheck) | metric definitions of 7B + A1 as applied in V7 |
| metrics | `validate.py` M1–M10, 7B set, `precheck.py` | V7 results |
| decoder | v4-balanced / v7-lowcov profiles, Phase 1 full consensus, `tests/nanopore` corpus harness | frame-4 layout, archive formats |
| provenance | dataset MANIFEST, split guard + access ledger, held-out PREREG gate, fitting block (commit, dirty, seed) | V7 ledgers, ACCESS_LOG, V7 held-out material |
| public data | D13 splitter `experiments/v7/nanodata/nanolib.py` (k-mer index, semi-global segments, frozen constants PR-4.2), D13 FIT/DEV aggregate characterisation (V7 D3) | V7 D3 results |

Frozen for V7: nothing in `experiments/v7/**` is edited by V8, and nothing V7 held out (D03 file-1, D13 run 13 and
reference buckets 8–9, CAS9 g13 and ID buckets 8–9) is read without the V8 pre-registration's own gate.

## Identifiability from D13 (before fitting; from the manifest and the V7 D3 aggregate characterisation)

| statistic | D13 | why |
|---|---|---|
| substitution rate, spectrum, position | IDENTIFIABLE | segments aligned to known 150-nt references |
| quality dependence | IDENTIFIABLE | FASTQ qualities (Phred+33, 90 distinct values) |
| insertion/deletion rate, run lengths, homopolymer dependence, 3-mer context | IDENTIFIABLE | as above (FIT ≈ 1 M segments per run) |
| position dependence within an oligo | IDENTIFIABLE (segment coordinates) | the read-end effect of a whole read is not the oligo end (concatemers) |
| read-length distribution (whole reads) | IDENTIFIABLE, but **not comparable** with single-oligo reads | reads are Gibson/OE-PCR concatemers of ~4.6 kb |
| coverage per reference | IDENTIFIABLE (segments per reference) | includes assembly and PCR effects |
| missing-read / dropout | PARTLY: references with 0 segments | dropout is mixed with segmentation loss: an upper bound only |
| synthesis vs sequencing split | NOT IDENTIFIABLE FROM DATA | no sequencing-only control |
| basecaller version, flow-cell chemistry details | NOT IDENTIFIABLE FROM DATA | not stated by the source |

## Phases (one commit or more per phase; tests and provenance updated at each)

| phase | deliverable |
|---|---|
| V8.0 | this plan, `V8_PREREGISTRATION.md`, `V8_DATA_PROVENANCE.md`, `V8_COMPLETION_REPORT.md` (skeleton), `experiments/v8/manifest.py` → `V8_MANIFEST.json`, baseline suite |
| V8.1 | D13 driver `experiments/v8/d13/`: V8 access guard + ledger, pinned pipeline RAW → SEGMENTS → NORMALISED OBSERVATIONS (local cache, hashed, never committed) → FIT TABLES (tallies), every stage logged with input/output hashes |
| V8.2 | error extraction report (aggregate, FIT): rates, spectra, run lengths, homopolymer, position, context, quality, coverage; identifiability table filled |
| V8.3 | schema additions (opt-in /2): V8 basis vocabulary (OBSERVED / FITTED / DERIVED / ASSUMED) and model identity (parameter hash); paired-indel field only if the pre-registered rule triggers |
| V8.4 | D13 fitter (FIT only): bootstrap, sparse bins, stability (`precheck`), tail tally to 32 |
| V8.5 | adequacy harness: per-metric observed / model / abs / rel error / uncertainty / n / pass / identifiability; V8 metric amendments as pre-registered |
| V8.6 | comparison A–D on one DEV evaluation with the same harness |
| V8.7 | simulator support for the new fields; per-run record (model hash, seed, source dataset, configuration, archive hash, output hash) |
| V8.8 | decoder matrix: cov 3/5/10 × channels clean / substitution / insertion / deletion / mixed / D13-derived (labelled with its adequacy) |
| V8.9 | automated failure taxonomy (`classified == total`) |
| V8.10 | oracle / upper-bound experiments under the D13-derived channel |
| V8.11 | coverage envelope (analytic + empirical) |
| V8.12 | archive / random-access / integrity checks, clean and noisy |
| V8.13 | scale benchmark (small, 1, 10, 100 MiB, 1 GiB where feasible) |
| V8.14 | profiling; optimise only measured bottlenecks |
| V8.15 | fuzz / adversarial runs |
| V8.16 | one-command reproduction `experiments/v8/reproduce.sh` + `V8_REPRODUCTION.md` |
| V8.17 | independent audit |
| V8.18 | `V8_COMPLETION_REPORT.md` |

Resource rule (lab): heavy jobs one at a time, ≤ 4 workers, load average recorded. Nothing is pushed, merged or tagged
automatically.
