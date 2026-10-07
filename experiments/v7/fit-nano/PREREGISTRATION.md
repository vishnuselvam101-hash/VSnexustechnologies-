# Fitted nanopore model: adequacy criteria (pre-registration, step 7B)

Committed before any model is fitted under `docs/V7_NANOPORE_MODEL_DESIGN.md`. The criteria below are final for this
model generation; a change needs a new pre-registration, and results under the old one are reported too.

## Data and splits (fixed)

| condition | FIT / DEV | HELD-OUT (one evaluation, after the model-freeze commit) |
|---|---|---|
| D03 guppy HAC pass (primary) | files 0, 2; reference buckets per protocol 4.1 | file-1 whole |
| D03 guppy fast pass | same | file-1 whole |
| D13 (runs 15, 18 apollo; 20 vitruvian) | reference buckets 0-5 / 6-7 (D3 §3.1) | run 13 (space_shuttle) whole, and buckets 8-9 of runs 15/18/20 |

Read-level statistics (read length, per-read burden) use the read-ID split of D3 §3.1. Simulated comparison reads:
the same references, 5 seeds (82200-82204), passed through the same alignment and tally as real reads.

## Metrics and thresholds (each on held-out data)

| # | metric | threshold | gating |
|---|---|---|---|
| M1a | total edit rate (per base) | within 5 % relative or 3 bootstrap SE, whichever is larger | yes |
| M1b | substitution, inserted-base and deleted-base rates, separately | same rule, each | yes |
| M1c | substitution spectrum (12 off-diagonal shares) | total-variation distance ≤ 0.05 | yes |
| M2 | per-read edit distance (error burden per read) | KS D ≤ 0.03, TV ≤ 0.05, p90 and p99 within 10 % relative | yes |
| M2b | per-read error burden: per-read edit rate distribution (edits / aligned length) | KS D ≤ 0.05 | yes |
| M3 | length drift P(\|drift\| ≤ 0, ≤ 3, ≤ 6) | within 1 percentage point each | yes |
| M5 | deletion run-length histogram 1…8+ | TV ≤ 0.05 **and** share of runs ≥ 2 within 3 percentage points | yes |
| M5i | insertion run-length histogram 1…8+ | TV ≤ 0.05 **and** share of runs ≥ 2 within 3 percentage points | yes |
| M6 | homopolymer-conditioned indel rate (run length 1…6+) | within 10 % relative per run length with ≥ 10,000 sites | yes |
| M8 | functional: VNX consensus per-base error vs cluster size k ∈ {2, 5, 10, 20} on real held-out clusters vs simulated | inside each other's Wilson 95 % CI or within 2 pp | yes |
| RL | read length (raw reads, D03; segments, D13) | KS D ≤ 0.05 | yes |
| M4 | position profile | per-bin ratio within ± 10 % | reported |
| M7 | coverage per reference | as fitting plan §3.4 | reported (coverage is an experimental input, not channel) |
| M9 | quality calibration | as fitting plan §3.4 | reported (decoder path does not use qualities) |
| M10 | round trip (simulate, refit) | every parameter within 5 σ | yes (a model that cannot be re-identified is not adequate) |

## Verdict rule

- **ADEQUATE** for a condition: every gating metric passes on that condition's held-out data.
- **INADEQUATE** otherwise; the report names each failed metric and the effect behind it.
- Matching the aggregate edit rate (M1a) alone never makes a model ADEQUATE; M5 and M5i (multi-base indel structure)
  are gating because D3 identified them as the main mismatch.
- DEV looks: at most two per model, logged; the held-out evaluation happens once, after the model files are committed
  with their SHA-256.
- Labels: real-read statistics PUBLIC-DATA-DERIVED; simulated reads SIMULATED; no physical claim.
