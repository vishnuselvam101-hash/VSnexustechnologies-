# VNX-DNA V7 completion report

Status: **V7 CLOSED with a final statistical decision** (2026-10-07). Branch `build/v7-sprint`. Not merged to `main`, not
tagged; the release is the founder's decision. No DNA was synthesised, stored or sequenced. Every result is either
**SIMULATED** (reads from a software channel model) or **PUBLIC-DATA-DERIVED** (statistics of other groups' published
sequencing reads). Nothing here is REAL EXPERIMENTAL, and nothing is called experimentally validated.

## 1. Executive result

1. **Decoder, SIMULATED (nanopore-like model shipped with V6):** the indel-aware full-template consensus ("Phase 1") is
   confirmed on fresh, pre-registered seeds. The `v7-lowcov` outer-parity profile adds 5-read coverage at +42 % strands.
   Below 5 reads the architecture is not sufficient. 0 false success in 480 decodes.
2. **Channel model, PUBLIC-DATA-DERIVED:** none of the fitted D03 nanopore models is ADEQUATE under the 7B
   pre-registration. DEV look 1 was spent (all INADEQUATE). The FIT-only pre-check of the improved a7c candidates blocked
   DEV look 2, the last one. **The held-out data was never opened.** Under the V7 rules the model-selection cycle stops
   with this decision: no further tuning.
3. **Consequence:** the V7 decoder matrix on a fitted public-data model is **not run**. There is no adequate model to run
   it on. The decoder results above are therefore statements about the SIMULATED nanopore-like model only.

## 2. Decoder results (SIMULATED)

A-CONF (pre-registration `experiments/v7/a-conf/PREREGISTRATION.md`, commit b8f3338; seeds 82100–82139; results SHA-256
`2870708d…f7188bce`):

| cov | arm | EXACT | Wilson 95 % | FALSE SUCCESS | false frames |
|---|---|---|---|---|---|
| 3 | v4-balanced, old consensus | 0/40 | 0.00–0.09 | 0 | 0 |
| 3 | Phase 1 | 0/40 | 0.00–0.09 | 0 | 0 |
| 3 | v7-lowcov | 0/40 | 0.00–0.09 | 0 | 0 |
| 5 | Phase 1 | 0/40 | 0.00–0.09 | 0 | 0 |
| 5 | v7-lowcov | 39/40 | 0.87–1.00 | 0 | 0 |
| 10 | old consensus | 0/40 | 0.00–0.09 | 0 | 0 |
| 10 | Phase 1 | 40/40 | 0.91–1.00 | 0 | 0 |
| 10 | v7-lowcov | 40/40 | 0.91–1.00 | 0 | 0 |

A-PAR (held-out seeds 82060–82099, pre-registration e57d593): v7-lowcov cov5 38/40 (Wilson 0.84–0.99), cov10 40/40,
cov3 0/40, 0 false success. Strand cost +42 % (979 vs 691 strands); decode about 1.4× slower.

Failure taxonomy (A-FAIL, `experiments/v7/a-fail/README.md`): all 283 failed decodes of A-CONF and A-PAR were reproduced
and classified. Every one is an explicit outer-code failure (typed error), with no implementation failures. cov3 is
structurally inadequate for v4-balanced: about 207 strands have fewer than 2 reads. v7-lowcov cov3 is unsupported.

**Coverage envelope (SIMULATED nanopore-like model):** cov10 SUPPORTED (Phase 1, either profile); cov5 SUPPORTED only with
v7-lowcov (39/40 and 38/40); cov3 NOT SUPPORTED.

## 3. Channel-model results (PUBLIC-DATA-DERIVED vs SIMULATED)

- **D3 characterisation** (`experiments/v7/nanodata/README.md`): the shipped nanopore-like simulator fails M1–M6 on all 4
  D13 runs and CAS9. Insertions are under-simulated 1.7–3.5×.
- **Schema `/2` additions** (opt-in; /1 models and their SHA-256 unchanged): empirical insertion/deletion run-length pmf
  (7.2) and per-homopolymer-length indel multipliers `homopolymer.indel_by_length` (7.4). Fitter estimation, calibration
  and tests come with both.
- **DEV look 1 (a7b, 2026-10-06):** all four D03 models INADEQUATE. M5/M5i (multi-base indels) pass, and M6r failed
  structurally (`experiments/v7/fit-nano/d03/README.md`).
- **FIT-only pre-check of the a7c candidates:** M6r is fixed. Every candidate still fails in sample, stably: M3 on three
  candidates and M10 on all four. Decision **`DEV_LOOK_2_BLOCKED — FIT PRE-CHECK INADEQUATE`**
  (`experiments/v7/fit-nano/d03-a7c/README.md`, `DIAGNOSIS.json`).
- **Diagnosed mechanisms:**
  - M3 is genuine misspecification: co-located insertion+deletion pairs are 3–4× more frequent in real reads.
  - M1 is genuine misspecification, stratified: insertions after the reference end are almost absent from the simulation.
  - M10 mixes four causes: censored deletion tail, a bootstrap SE that omits calibration noise, sparse pmf bins, and
    non-identifiability of the substitution matrix/context in confounded designs.
  - No threshold was changed after seeing results. One pre-registration amendment (A1, M6 noise floor) was approved
    before any fit.

## 4. Provenance

| item | value |
|---|---|
| final V7 commit | see `git log build/v7-sprint`; closing commit carries this report (parent 753b245) |
| V6 base | v6.0.0 (16b5811 on `main`) |
| datasets | `experiments/v7/datasets/MANIFEST.json` (file SHA-256 per dataset; split manifest SHA in every fit) |
| split ledger | `experiments/v7/datasets/SPLIT_ACCESS_LEDGER.jsonl` (every FIT/DEV request); `ACCESS_LOG.jsonl` (held-out: none granted) |
| models | `experiments/v7/fit-nano/d03{,-a7c}/models/*.json` (canonical SHA-256 in each validation/pre-check record; relabels in `RELABEL-*.json`) |
| seeds | fits 20261005, DEV validation 20261006, pre-check 20261007, diagnosis 20261007/20261008; A-CONF 82100–82139, A-PAR 82060–82099 |
| software | Python 3.12.3, numpy 2.5.3, edlib (fit extra), gcc 13.3.0; Linux 6.8, x86-64, 8 cores, 31 GiB |
| tests | 3166 passed / 0 failed / 6 skipped (environment-only: 3 need the installed CLI on PATH, 3 opt-in install tests); ruff, mypy clean |

## 5. What V7 does NOT establish

- That any channel model reproduces real nanopore reads: none is ADEQUATE.
- Decoder performance on real or public-data-derived reads: the decoder results are SIMULATED.
- Anything physical: no DNA was synthesised, stored or sequenced by VNX-DNA.

## 6. Hand-over to V8

V8 (`build/v8-public-channel`) starts from this commit. It must not reuse the V7 DEV decisions or touch the V7 held-out
material (D03 file-1; D13 run 13 and reference buckets 8–9; CAS9 held-out buckets) without its own pre-registration. The
V7 diagnosis gives the V8 model candidates: paired indels, read-end insertions, uncensored tail tally, calibration-aware
M10 SE, sparse-bin rule, and substitution identifiability.
