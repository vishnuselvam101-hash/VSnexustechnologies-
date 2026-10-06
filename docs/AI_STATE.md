# VNX-DNA development state (read first)

Compact working state for development sessions. Update at every completed checkpoint. Details live in the linked docs;
do not repeat them here.

## Baseline
| item | value |
|---|---|
| integration branch | `build/v7-sprint` (local; not pushed) |
| verified head | `85c96d6` (merge ae396c9 of work/v7-nanodata + split-guard test scoping) |
| tests | 3166 passed / 0 failed / 6 skipped at 753b245; earlier 3127 passed / 0 failed / 6 skipped at f9d17ea (365 s, `-n 6`, load 2.8→3.2; skips = CLI-on-PATH + opt-in install); ruff clean; mypy gate clean (135 files) |
| previous baseline | `d06e490`, 2723 passed / 0 failed |
| released | v6.0.0 on `main` (tag untouched; its tree reports `6.0.0.dev0`, recorded in CHANGELOG) |
| package version | 6.0.0.dev0 (bump to 7.0.0.dev0 pending, its own step) |

## Active objective
V7 item A (root cause found: consensus indel placement): decode the SIMULATED nanopore-like channel (V6: 0/20 at cov 3/5/10; A1 clustering: 0/5 at cov 10,
511/679 data frames). Gate: no decoder-behaviour change until the loss funnel and the oracle name the first
irreversible loss (address / clustering / boundary / payload / structural).

## Mode
Main agent only (no subagents unless justified), context under 100k, compact at checkpoints, one task at a time. Heavy experiments run one at a time with ≤4 workers; record the load with every
timing. Long logs go to files. Merge into `build/v7-sprint` only when all of these hold: tests pass, the diff has been
reviewed, the result reproduces, and nothing regresses.

## Architecture (short)
Python orchestration and reference implementations, plus C11 native kernels via ctypes: `v5/native/align.c`,
`v6/native/rs.c`, `v6/native/reads.c`, and (unmerged, A2) `native/c/cluster.c`. No C++ port. See
`docs/V7_ARCHITECTURE.md`, `docs/V7_PROTOCOL.md`.

## Invariants (summary)
- The decoder never returns wrong data. The container SHA-256 decides SUCCESS. FALSE SUCCESS must stay at 0.
- New behaviour is opt-in. V4/V5/V6 archives keep decoding (golden tests). The frame-4 layout is unchanged.
- Native output equals the reference output. Output does not depend on the worker count.
- Held-out data is never used before pre-registration. DEV looks are logged (`experiments/v7/datasets/SPLIT_ACCESS_LEDGER.jsonl`).
- Results are labelled SIMULATED or PUBLIC-DATA-DERIVED. No physical-validation claims.

## Open work (priority order)
| # | item | branch / worktree | state |
|---|---|---|---|
| 1 | Nanopore frozen corpus + loss funnel + oracle | work/v7-nanodecode | DONE c4e6195 (corpus 7e89b82, `tests/nanopore/`; branch suite 2781/0/3) |
| 2 | Consensus fix PHASE 1 (full-template polish, opt-in `consensus_template="full"`) | build/v7-sprint | MERGED 98c7c6d; held-out cov10 EXACT 40/40 vs 0/40 |
| 3 | Low-coverage outer parity (opt-in profile `v7-lowcov`, row code 64+48) | build/v7-sprint | DONE e57d593 + 39e180a: held-out cov5 EXACT 38/40, cov10 40/40, 0 false; cov3 still 0/40 (structural); cost +42 % strands |
| 4 | D3 D13/CAS9 characterisation vs simulator | work/v7-nanodata | IN PROGRESS (owner root-e4): driver 19ad023 (load avg, 3 workers); clean rerun of d13 + cas9 + compare next |
| 5 | A2 native cluster kernel | build/v7-sprint | MERGED 93d491a (e0bd56b, sanitizers PASS) |
| 6 | Python/C boundary audit | work/v7-pycpp | IN PROGRESS: boundary tests committed (4cdc0ba); 4 uncommitted files; doc not finished |
| 7 | Remaining fitter work | — | nanopore models still INADEQUATE (M3); D02 marginal |

## Completed experiments
- A-LOSS (`experiments/v7/a-loss/`, SIMULATED, seeds 82046-82055): the FIRST IRREVERSIBLE LOSS is per-cluster consensus → inner RS (data strands cov10 658→514, cov5 577→275, cov3 453→121). About 90 % of wrong bases are the true base shifted by one position (indel misplacement). ORACLE (true grouping): 0 address-caused, 1.6–2.1 clustering-caused per trial. cov3 hits a structural limit (~207 strands with <2 reads). 0 FALSE SUCCESS.

- A-CONS (`experiments/v7/a-cons/`, SIMULATED, pre-registered, held-out seeds 82060-82099): full-template polish vs reference, data frames/679 cov3 124→295, cov5 280→463, cov10 513→618; EXACT cov10 40/40 (Wilson 0.91–1) vs 0/40; cov3/5 0/40 both; 0 false success/frames; decode ~15x slower, peak RSS ~330 MiB.

- A-RS (`experiments/v7/a-rs/`, DIAGNOSTIC, dev seeds 82043-82055): at cov5 failed 2-read candidates carry ~23 erased + ~7 errored bytes vs 16 inner parity; partial strands add 0 rows; RS policy alone cannot fix cov5.
- A-PAR (`experiments/v7/a-par/`, SIMULATED, pre-registered, held-out 82060-82099): v7-lowcov vs v4-balanced (both full consensus): cov5 EXACT 38/40 (Wilson 0.84-0.99) vs 0/40; cov10 40/40 both; cov3 0/40 both; 0 false success/frames; +42 % strands, decode ~1.4x.

## Known blockers / facts
- No nanopore channel model is ADEQUATE (`docs/V7_CHANNEL_MODELS.md`).
- `consensus_weighting=quality` and `retry_band` gave no nanopore gain (V6_DEFERRED §3).

## V7 completion plan (founder 2026-10-06 18:0x: "abc ... fully complete the v7, next v8"; owner root-e4)
Read as V7 items A, B, C to the §25 acceptance gates, then V8. One task at a time, merge gate each.
| step | item | done when |
|---|---|---|
| 1 | D3 D13/CAS9 characterisation (rerun from 19ad023) | results + comparison committed, merged |
| 2 | Python/C boundary audit (v7-pycpp, rebase on A2) | audit run on idle host, docs/V7_PYTHON_CPP_ARCHITECTURE.md, merged |
| 3 | A close-out: cov3 analysis + v7-lowcov opt-in decision, docs/V7_NANOPORE_DECODING.md | doc + decision recorded |
| 4 | C soft decisions (§13): prereg, re-run on the fitted (ADEQUATE) model + held-out; opt-in unless significant | results committed |
| 5 | B frame 6 + superblock 3 (§15): EXP-F6-1 first; implement only if it earns it, else documented deferral | experiment + decision; impl with goldens/negatives/compat if go |
| 6 | F primers/flanks, `vnx order-export`, short-strand profile (§16-17), CRC-16 | spec + tests |
| 7 | E benchmark round 2 HEDGES/YYC/VNX (§18) | results with CIs |
| 8 | §21 fuzz targets, §22 security review/threat model | no new crashes, findings fixed |
| 9 | §24 docs + release gate (vnx release check) | SHA handed to founder; NO push/tag without founder approval |
| 10 | V8 scope + plan | founder picks scope |

## V7 reset directive (2026-10-06 18:30) — SUPERSEDES the "V7 completion plan" above. Ledger from here.
Governing notes: /root/vnx-dna-lab/notes/V7-RESET-DIRECTIVE-2026-10-06.md + founder step list of 19:5x (steps 3-8).
| step | state | evidence |
|---|---|---|
| D3 real-data check | DONE 5386080, MERGED ae396c9 (+85c96d6 guard tests: manifest may only gain datasets; ACCESS_LOG holds only disclosed D3 entries) | simulator fails M1-M6 on D13 ×4 + CAS9 |
| 1 Phase 1 regression test | DONE 0a05444 | 12/12 frozen cases = A-CONS record |
| 2 reproduction | DONE 3d45b78 | A-CONS 240 + A-PAR 120 rows identical (deterministic fields) |
| 3 confirmatory A-CONF | DONE prereg b8f3338, results 807e3f6 | Phase 1 CONFIRMED (cov10 40/40), lowcov cov5 39/40, 0 FS / 360 |
| 4 failure taxonomy A-FAIL | DONE aa62d61 + 5b10920 + 57f0a30 | 283 failed decodes, all reproduced; categories quantified |
| 5 cov-5 lowcov failures | DONE (A-FAIL README) | same mechanism, STOCHASTIC: 1 row over budget by 1-3 symbols |
| 6 cov-3 conclusion | DONE (A-FAIL README) | v4-balanced STRUCTURALLY INADEQUATE; v7-lowcov UNSUPPORTED (C+B) |
| 7A fitted model design | DONE f8d617a docs/V7_NANOPORE_MODEL_DESIGN.md | gap: empirical indel run-length pmf |
| 7B adequacy prereg | DONE f8d617a experiments/v7/fit-nano/PREREGISTRATION.md | M5/M5i multi-base indel metrics gating |
| 7 implementation | CLOSED: a7b DEV look 1 all INADEQUATE; a7c FIT-only pre-check → DEV_LOOK_2_BLOCKED — FIT PRE-CHECK INADEQUATE (be2e70d); DEV look 2 not spent; held-out never opened | experiments/v7/fit-nano/d03-a7c/README.md, DIAGNOSIS.json |
| 7 M6 noise floor | DONE b82bc81 (amendment A1, founder yes) | prereg M6: 10 % relative per run length with >= 10,000 sites. At the floor (~200 events) real-data sampling noise alone is ~7 % relative, so a perfect model can fail M6 on the sparsest tested bins (same-channel SIMULATED check: 5.8 % at 19.5k sites). Option: add a noise-aware rule (10 % or 3 bootstrap SE, as M1) by a new PREREG before step 4 |
| 7 matrix | NOT RUN: no ADEQUATE model (final decision) | docs/V7_COMPLETION_REPORT.md |
| 8 final report | DONE docs/V7_COMPLETION_REPORT.md — V7 CLOSED 2026-10-07 | V8 starts on build/v8-public-channel |
V7 CLOSED. V8 continues on build/v8-public-channel (docs/V8_PLAN.md).

## V8 (build/v8-public-channel, from V7 close 0fd7c54) — founder 2026-10-07: "complete the v8, decide yourself"
| phase | state | evidence |
|---|---|---|
| V8.0–V8.18 | DONE (not merged, not tagged) | docs/V8_COMPLETION_REPORT.md; experiments/v8/results/audit.json |
| D13 model | INADEQUATE: FIT pre-check blocks DEV (A1 matched selection); F2 rule not triggered; 0 DEV looks, held-out unopened | experiments/v8/d13/README.md |
| decoder matrix | 360 decodes, 0 false success, 88 failures all classified | experiments/v8/matrix/results/summary.json |
| tests | 3206 passed / 0 failed / 6 env skips | |
Next: founder review of V8; V9 candidates: native polish kernel (50 % of noisy decode), paired-indel/read-level heterogeneity model, per-run D13 models.
