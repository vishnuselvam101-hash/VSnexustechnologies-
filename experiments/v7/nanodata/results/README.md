# D3 results: D13 and CAS9 characterisation vs the nanopore-like simulator

Pre-registration: `docs/V7_PROTOCOL_AMENDMENT_D3.md`. Driver commit `19ad023` (clean tree), config SHA-256 in every
file. 3 workers, load average 2.3-3.2 on 8 threads. Wall time: D13 22.9 min for 4 runs, CAS9 13.9 min.

Labels: real-read statistics are PUBLIC-DATA-DERIVED (reads produced by Lopez et al. 2019 and Imburgia et al. 2025;
FIT split only, held-out reads counted, never characterised). Comparison reads are SIMULATED from the shipped, unfitted
`nanopore-like` model (`vnx.channel-model/1`, SHA-256 `67513377…0d660`). VNX-DNA synthesised, stored and sequenced
nothing.

| file | content |
|---|---|
| `d13_run15.json`, `d13_run18.json` (apollo), `d13_run20.json` (vitruvian) | D13 per-run characterisation; adequate (§4.4) |
| `d13_run16.json` (365-dishes) | **inadequate** (§4.4: 1,226 FIT references with a segment, < 2,000); excluded from pooled statistics, shown for completeness |
| `cas9.json` | CAS9 repeat-unit characterisation; error basis INFERRED (unit vs leave-one-out consensus of the same molecule, PR-5) |
| `comparison.json` | real vs simulated, M1-M7 and M9 thresholds of `docs/research/V7_CHANNEL_FITTING_PLAN.md` §3.4 |

## Result

The simulator fails every gated metric M1-M6 on every dataset (4 D13 runs and CAS9). M7 and M9 are not evaluated
(coverage is not comparable on concatemers; D13 has no usable qualities in this tally).

Per-base rates, FIT, real / simulated:

| data | substitution | deletion | inserted bases | total edits | deletion events of length ≥ 2 |
|---|---|---|---|---|---|
| run15 | 0.0243 / 0.0209 | 0.0371 / 0.0322 | 0.0282 / 0.0099 | 0.0895 / 0.0629 | 38 % / ~4 % |
| run18 | 0.0237 / 0.0209 | 0.0400 / 0.0322 | 0.0245 / 0.0099 | 0.0882 / 0.0630 | 40 % / ~4 % |
| run20 | 0.0313 / 0.0219 | 0.0464 / 0.0357 | 0.0377 / 0.0107 | 0.1155 / 0.0683 | 27 % / ~5 % |
| run16 (inadequate) | 0.0306 / 0.0204 | 0.0437 / 0.0307 | 0.0375 / 0.0097 | 0.1118 / 0.0608 | 32 % / ~3 % |
| CAS9 (inferred) | 0.0256 / 0.0216 | 0.0312 / 0.0335 | 0.0321 / 0.0146 | 0.0888 / 0.0696 | 29 % / ~4 % |

## Interpretation (decision: simulator INADEQUATE for robustness claims)

Simulator defects, in order of size:
1. **Multi-base deletions.** 27-40 % of real deletion events are runs of ≥ 2 bases, against about 4 % simulated (M5).
2. **Insertions** are 1.7-3.5x under-simulated (M1).
3. **Total edit rate** is 1.3-1.7x lower than real (M1); the edit-distance tail is about half the real p99 (M2).
4. Homopolymer-dependent indels (M6), position profile (M4) and length drift (M3) also differ.

Consequence for item A: every V7 nanopore-like result so far (A-LOSS, A-CONS, A-PAR) was measured on a channel that
is milder than these real reads, and the Phase 1 polish moves single bases, while real reads carry many multi-base
deletions. Those results stay valid as SIMULATED results on the shipped stress model; they do not transfer to these
real datasets. No model parameter was changed here (§7). Fitting a nanopore model that reproduces M1-M6 is the
prerequisite for the Phase 9 channel × coverage matrix (D, after its own PREREG).
