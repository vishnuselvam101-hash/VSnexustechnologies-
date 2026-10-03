# Experiment findings

Each entry is an **EXPERIMENTAL OBSERVATION**: measured on this host with the command and seed shown. Full per-trial
records live in `/opt/vnx-dna/experiments/<suite>/<run-id>/results.jsonl`.

## 2026-10-02 — error-models suite (V3.0.0 code, 64 KB mixed input, Poisson coverage 10× unless stated)

`vnxdna experiment error-models` → 38 trials: 34 RECOVERED, 4 FAILED_CLEAN, **0 WRONG_OUTPUT**.

* FAILED_CLEAN at `decode`: strand dropout 20 % (seeds 1, 2) — at/over the per-group guarantee "any 16 of 80 strands
  may be lost" (20 %), so failure is expected; it was detected, not silent.
* FAILED_CLEAN at `decode`: coverage 2× Poisson with 0.5 % substitutions (seeds 1, 2). ENGINEERING ASSUMPTION: the
  ≈ e⁻² ≈ 13.5 % of strands with zero reads plus erroneous reads exceed the group budget; not yet analysed per group.
* Recovered: substitutions up to 3 %, insertions/deletions up to 0.5 %, dropout up to 5 %, duplication up to 50 %,
  log-normal abundance σ ≤ 1.0, truncation up to 20 %, mixed channel, mixed bursts (rate ≤ 0.2).

## 2026-10-02 — constraints suite (32 KB, coverage 8×, 0.2 % substitutions)

`vnxdna experiment constraints` → 5 trials: 4 RECOVERED, 1 FAILED_CLEAN, 0 WRONG_OUTPUT.

* `--gc-min 45 --gc-max 55 --gc-window 50` (seed 1): `encode` refuses with
  `CONFIGURATION_ERROR: 96 strand(s) cannot satisfy the DNA constraints with any of 256 scrambler variants`.
  The container holds 96 metadata strands at this size (`vnx-dna info`), so HYPOTHESIS: the metadata strands are the
  ones that cannot be screened into a tight windowed GC band. Input for V6–V8 (advanced encoding): constraint-aware
  metadata encoding or more scrambler variants. The refusal is clean (no output, explicit error).
* GC 40–60 %, max homopolymer 3, forbidden EcoRI/BamHI sites (+ reverse complements), and all combined: recovered.
