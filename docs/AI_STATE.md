# VNX-DNA development state (read first)

Compact working state for development sessions. Update at every completed checkpoint. Details live in the linked docs;
do not repeat them here.

## Baseline
| item | value |
|---|---|
| integration branch | `build/v7-sprint` (local; not pushed) |
| verified head | `1f931e7` (merge of work/v7-fitter; tree identical to 9e3e6d8) |
| tests | 2990 passed / 0 failed / 3 skipped (327 s, `-n 6`); ruff clean; mypy clean (133 files) |
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
| 2 | Consensus fix PHASE 1 (base-level indel placement, marker anchors; no quality, no RS change; old path kept; held-out 82060-82099) | work/v7-nanodecode | GO 2026-10-06; see notes/SPRINT_STATE.md last entry |
| 4 | D3 D13/CAS9 characterisation vs simulator | work/v7-nanodata | IN PROGRESS: prereg, manifest, splitter, driver committed (f761f8c); characterisation run interrupted |
| 5 | A2 native cluster kernel | work/v7-native | review fixes done (e0bd56b, sanitizers PASS); merge pending (full suite) |
| 6 | Python/C boundary audit | work/v7-pycpp | IN PROGRESS: boundary tests committed (4cdc0ba); 4 uncommitted files; doc not finished |
| 7 | Remaining fitter work | — | nanopore models still INADEQUATE (M3); D02 marginal |

## Completed experiments
- A-LOSS (`experiments/v7/a-loss/`, SIMULATED, seeds 82046-82055): the FIRST IRREVERSIBLE LOSS is per-cluster consensus → inner RS (data strands cov10 658→514, cov5 577→275, cov3 453→121). About 90 % of wrong bases are the true base shifted by one position (indel misplacement). ORACLE (true grouping): 0 address-caused, 1.6–2.1 clustering-caused per trial. cov3 hits a structural limit (~207 strands with <2 reads). 0 FALSE SUCCESS.

## Known blockers / facts
- No nanopore channel model is ADEQUATE (`docs/V7_CHANNEL_MODELS.md`).
- `consensus_weighting=quality` and `retry_band` gave no nanopore gain (V6_DEFERRED §3).
