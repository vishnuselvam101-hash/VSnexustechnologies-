# VNX-DNA V9 completion report

This report covers the branch `work/v9-integrated`: V9 adaptive recovery (`build/v9-adaptive-recovery`) together with
VNX-Secure (`build/vnx-secure`). Both branch from the closed V8 state 8935217. The branch is not merged into main and not
tagged; any release is the founder's decision.

Each section names the committed results file its numbers come from, under `experiments/v9/` or
`experiments/vnx-secure/`. The pre-registration is `docs/V9_PREREGISTRATION.md`, with amendments A1 and A2; it was
committed before any V9 evaluation decode.

**Physical validation.** VNX-DNA has not physically synthesised, stored or sequenced DNA, and no such experiment has
taken place or is documented. Nothing in V9 is REAL EXPERIMENTAL.

## 1. Executive result

1. **H1 accepted: native consensus kernel.** The C11 polish kernel `vnx_cl_edit_costs` gives byte-identical decodes on
   all 60 benchmark decodes. Its median speed-up is **2.46×**, with a bootstrap 95 % interval of 2.41–2.51.
2. **H2 accepted: oracle gap.** At coverage 3 and 5, the V8 production decoder recovers fewer archives than the
   perfect-consensus oracle under the D13-F1 stress channel. Its pooled ORE is 0.488 (Wilson 95 % 0.444–0.532).
3. **H3 accepted: consensus V2.** Candidate **E** (`c_indel = 4` plus template fill) raises pooled ORE from 0.488 to
   **0.575** over 600 paired EVAL cases.
   - The paired outcomes split 44 vs 1 discordant: exact McNemar, Holm-adjusted p = 5.2 × 10⁻¹².
   - It has 0 false success and 0 false frames, and it is deterministic across workers.
4. **H4 accepted: adaptive computational coverage.** In 200 EVAL trajectories every decode at the stopping point was EXACT,
   with 0 false terminations and 0 false success.
   - It used 41.7 % fewer reads than the smallest fixed coverage with equal EXACT on v4-balanced, and 31.2 % fewer on
     v7-lowcov.
   - This rule decides when enough reads are in hand for decoding. It does not control sequencing hardware.
5. **H5 rejected: channel model G1.** The latent-class model G1 (K = 4) fails the FIT-only pre-check on M6r and M8, both
   stable failures. No DEV look was spent and the held-out data stays closed for V10.
6. **VNX-Secure** is included. It has 16 detectors, and all 10 included simulated attack scenarios are detected and
   contained, with 0 false positives over 270 benign requests. These are synthetic attacks on a local control plane, not
   a real-world security guarantee.
7. **Archive size.** On a clean channel every size from 20 KB to 1 GiB decodes exactly; 1 GiB encodes in 106 s and
   decodes in 195 s.
   - Under the D13-F1 stress channel at coverage 10 (v4-balanced), whole-archive success falls with size: 28/30 at
     20 KB but 2/30 at 1 MiB, with 0 false success.
   - Per-strand success stays near 0.88 at both sizes, but the archive needs every one of its 411 rows, so row failures
     compound.
   - The analytic model gives about 3 × 10⁻¹² for 10 MB and ≈ 0 at 100 MB and 1 GiB in this setting. These are
     ANALYTIC EXTRAPOLATIONS, not decodes.

## 2. Scientific status

| class | V9 results |
|---|---|
| PUBLIC-DATA-DERIVED | G1 parameters fitted on D13 FIT; the real-data side of the FIT pre-check |
| SIMULATED | every decode: native benchmark, oracle-gap EVAL, eligibility, adaptive coverage, envelope, noisy and scale runs; VNX-Secure attack scenarios and fuzzing |
| REAL EXPERIMENTAL | none |

## 3. Hypothesis verdicts

| | hypothesis | verdict | evidence |
|---|---|---|---|
| H1 | native consensus kernel bit-identical and faster | ACCEPTED | `consensus/results/benchmark-d13f1.jsonl`, `results/report-numbers.json` |
| H2 | measurable oracle gap at coverage 3 and 5 | ACCEPTED | `oracle/results/eval-summary-v8.json` |
| H3 | a consensus candidate beats V8 on pooled ORE, 0 false success | ACCEPTED (winner E) | `consensus/results/selection.json`, `consensus/results/eligibility.jsonl` |
| H4 | adaptive coverage uses fewer reads, false-termination upper bound ≤ 0.05 | ACCEPTED | `coverage/results/eval.jsonl`, `coverage/FREEZE.json`, `results/report-numbers.json` |
| H5 | a latent-structure channel model passes the V8 gating metrics | REJECTED | `d13/results/VERDICT.json`, `d13/results/precheck-g1.json` |

## 4. H1: native consensus kernel (`consensus/results/benchmark-d13f1.jsonl`)

- **Kernel:** `vnx_cl_edit_costs` (cluster ABI 2) in `src/vnxdna/native/c/cluster.c`. The Python reference
  `polish.edit_costs_reference` stays in place as the correctness oracle.
- **Correctness:** checked before any timing.
  - 50 golden hashes and 100,000 randomised reads were checked against the reference, plus hypothesis tests, thread
    invariance and whole-decode identity.
  - ASan/UBSan builds under gcc and clang: §11.
  - libFuzzer: §11.
- **Benchmark:** 60 decodes on seeds 94000–94009 (amendment A1), across six cells: D13-F1 at coverage 3, 5 and 10, for
  both profiles. Each decode ran the V8 decoder (NumPy polish) and the V9 decoder (native polish) on the same read file.
  - **Results:** 60 of 60 outputs byte-identical. Median speed-up 2.462×, bootstrap 95 % 2.414–2.508 (seed 94000, 10,000
    resamples), range 1.06–2.98×.
  - Host load during the benchmark was 0.87–7.39. The ratio is paired per read file.

## 5. H2 and H3: oracle gap and consensus V2 (`oracle/results/eval.jsonl`, `consensus/results/selection.json`)

- **Design:** EVAL seeds 91000–91099 (100 per cell) over the six primary cells: D13-F1, coverage 3, 5 and 10, profiles
  v4-balanced and v7-lowcov.
  - V8 ran at the production level and at both oracle levels.
  - The frozen candidates A (`c_indel = 4`) and E (A plus template fill) ran at the production level.
  - Each candidate was evaluated once: 3,000 rows in all, 600 per production comparison.
- **Oracle gap (V8):**
  - Coverage 3, v7-lowcov: the oracle with two-read consensus recovers 100 of 100; production recovers 0.
  - Coverage 5, v4-balanced: oracle 92, production 0.
  - Coverage 5, v7-lowcov: oracle 100, production 61.
  - Coverage 3, v4-balanced: the oracle recovers nothing either, so ORE is undefined.

  The oracle levels are upper bounds, not achievable production performance.

EXACT out of 100, by cell:

| cell (D13-F1) | V8 | A | E |
|---|---|---|---|
| cov 3, v4-balanced | 0 | 0 | 0 |
| cov 3, v7-lowcov | 0 | 0 | 0 |
| cov 5, v4-balanced | 0 | 0 | 0 |
| cov 5, v7-lowcov | 61 | 83 | **89** |
| cov 10, v4-balanced | 79 | 94 | **94** |
| cov 10, v7-lowcov | 100 | 100 | 100 |
| total of 600 | 240 | 277 | **283** |
| pooled ORE (Wilson 95 %) | 0.488 (0.444–0.532) | 0.563 (0.519–0.606) | **0.575 (0.531–0.618)** |
| discordant vs V8 (candidate only / V8 only) | — | 38 / 1 | 44 / 1 |
| McNemar p, Holm-adjusted | — | 1.5 × 10⁻¹⁰ | 5.2 × 10⁻¹² |
| median / p95 decode seconds | 13.5 / 32.0 | 12.8 / 31.0 | 12.8 / 31.0 |

- **Eligibility** (`consensus/results/eligibility.jsonl`): A and E both have 0 false frames and are deterministic across
  worker counts. A guard checked that eligibility decoded the same read pools as EVAL (identical `reads_sha256`).
- **Winner:** E. It is shipped as an opt-in configuration: `ClusterConfig(consensus_template="full", c_indel=4,
  fill="template")`.
  - The library default is unchanged (`consensus_template="wildcard"`, `c_indel=6`, `fill="none"`). The V8 baseline arm
    of this evaluation was also an explicit configuration (`consensus_template="full"`).
  - Making E the default changes decoding behaviour for every caller. It is listed in §12 as a founder decision.
- **Coverage 3:** no candidate closes the gap. Every production decoder recovers 0 of 100 in both cells.
  - On v7-lowcov the two-read consensus oracle recovers 100 of 100, so the remaining gap there is all consensus.
  - On v4-balanced the oracles also recover 0 (`oracle/results/eval-summary-v8.json`).

## 6. H4: adaptive computational coverage (`coverage/results/eval.jsonl`, `coverage/FREEZE.json`)

- **Rule:** fixed by prereg §6.
- **Tuning:** on the DEV seeds 90000–90009 with decoder E, then frozen at level `all`, margin m = 0 (`FREEZE.json`, DEV
  mean 5,463 reads, 0 false terminations).
- **Evaluation:** EVAL seeds 91000–91099. Each seed has a pool at mean coverage 15 (NB dispersion 4), consumed in batches
  of 0.5×.

| | v4-balanced | v7-lowcov |
|---|---|---|
| EXACT at the stopping point | 100 / 100 | 100 / 100 |
| false terminations (Wilson 95 % upper) | 0 (0.037) | 0 (0.037) |
| false success | 0 | 0 |
| mean coverage at stop (range) | 8.70× (7.51–10.52) | 4.82× (4.00–5.51) |
| mean reads at stop | 6,010 | 4,719 |
| EXACT at fixed coverage 3 / 5 / 7 / 10 / 15 (same seeds) | 0 / 0 / 0 / 99 / 100 | 0 / 96 / 100 / 100 / 100 |
| smallest fixed coverage with equal EXACT | 15× (10,303 reads) | 7× (6,860 reads) |
| reads avoided | **41.7 %** | **31.2 %** |

Acceptance (0 false success, and a false-termination Wilson upper bound ≤ 0.05) is met in each profile.

## 7. H5: channel model G1 (`d13/results/VERDICT.json`; PUBLIC-DATA-DERIVED fit, SIMULATED validation)

- **Model:** G1 replaces F1's gamma read multiplier with K latent read classes (amendment A2). BIC chose K = 4. The fit
  used 266,883 FIT segments (`d13/results/fit-g1.json`, model 6ebd8c04).
- **FIT-only pre-check:**
  - Passes: M1, M1a, M1c, M2, M2b, M3, M5, M5i and RL.
  - M6r and M8 fail as stable failures. M10 fails in a way that noise could explain.
  - Verdict: **INADEQUATE**.
- **G2:** prereg §4.1 fits G2 only if G1 fails M3 or M2b. G1 passes both, so G2 was not fitted.
- **DEV comparison** (`d13/results/comparison.json`): G1 fails only M6r and M8; F1 fails six metrics.
- **Held-out data:** not opened (prereg §4.4); carried to V10.
- **Consequence:** D13-F1 remains a stress channel, not a validated model of nanopore reads.

## 8. Coverage envelope (`coverage/results/envelope.json`, `coverage/results/envelope-cases.jsonl`)

- **Setup:** decoder E, D13-F1 channel, seeds 92000–92029 (30 per cell), with the analytic row model beside the simulated
  decodes. The q-rule was pre-specified (prereg §9).
- **Result:** 0 false success in all 16 cells.

| coverage | v4-balanced | v7-lowcov |
|---|---|---|
| 1, 2, 3, 4 | 0/30 NOT SUPPORTED | 0/30 NOT SUPPORTED |
| 5 | 0/30 NOT SUPPORTED | 26/30 MARGINALLY SUPPORTED (0.70–0.95) |
| 7 | 0/30 NOT SUPPORTED | 30/30 SUPPORTED (0.89–1.00) |
| 10 | 27/30 MARGINALLY SUPPORTED (0.74–0.97) | 30/30 SUPPORTED |
| 15 | 30/30 SUPPORTED (0.89–1.00) | 30/30 SUPPORTED |

## 9. Archive size (`scale/results/noisy.jsonl`, `scale/results/noisy-projection.json`, `scale/results/scale.json`)

**Noisy decodes** (decoder E, 4 workers, D13-F1, mean coverage 10, NB dispersion 4, v4-balanced, seeds 93000–93029; the
summary is in `results/report-numbers.json`):

| size | strands / rows | EXACT (Wilson 95 %) | false success | median per-strand / per-row success | median decode s |
|---|---|---|---|---|---|
| 20 KB | 691 / 9 | 28/30 (0.79–0.98) | 0 | 0.881 / 1.000 | 22.8 |
| 1 MiB | 32,837 / 411 | 2/30 (0.02–0.21) | 0 | 0.888 / 0.995 | 1,059 |

- **How the run was scoped:** a size is decoded only if its projected single decode takes ≤ 2 h on this host (prereg §9).
  10 MB is projected at 3.5 h, so 10 MB, 100 MB and 1 GiB were not decoded.
- **Analytic row model:** with the per-strand loss q = 0.111 measured at 1 MiB and M = 16 parity strands per 80-strand row,
  the model gives P(archive) = 3.4 × 10⁻¹² at 10 MB and ≈ 0 at 100 MB and 1 GiB. This is an ANALYTIC EXTRAPOLATION
  (SIMULATED), not a decode.
- **Reading the numbers:** at this coverage, per-row success of ≈ 0.995 is enough for 9 rows but not for 411. Large
  archives under this stress channel need more coverage or more parity per row. That is a parameter choice the envelope
  (§8) and the adaptive rule (§6) can inform. It is not a claim that V9 recovers them.
- **Host load:** 1.0–4.3 during the 1 MiB decodes, which partly overlapped the sanitizer and fuzz runs. The timings are
  indicative.

**Clean channel** (every strand read once, no errors; V9 pipeline, v4-balanced, 4 workers; each stage in its own process):

| size | strands | nt / byte | encode s (peak RSS MiB) | decode s (peak RSS MiB) | exact |
|---|---|---|---|---|---|
| 20 KB | 691 | 10.81 | 0.06 (45) | 0.08 (46) | yes |
| 1 MiB | 32,838 | 9.80 | 0.21 (54) | 0.44 (87) | yes |
| 10 MiB | 327,763 | 9.78 | 1.25 (61) | 2.06 (263) | yes |
| 100 MiB | 3,277,129 | 9.78 | 10.9 (64) | 18.4 (287) | yes |
| 1 GiB | 33,557,316 | 9.78 | 105.8 (143) | 195.4 (323) | yes |

- **Worker scaling at 10 MiB decode:** 3.49 s with 1 worker, 2.60 s with 2, 2.08 s with 4.
- **Comparison with V8** (`experiments/v8/scale/results/scale.json`): 1 GiB took 105 s to encode and 187 s to decode.
  The clean channel does not run the cluster stage, so V9's consensus changes do not apply here.
- **Host load:** 2.2 at the start of the run and 1.7 at the end.

## 10. VNX-Secure (`experiments/vnx-secure/results/simulation.json`, `fuzz-campaign.json`)

- **Scope:** an access-control and anomaly-response layer around archive access: tokens, policies, 16 detectors, risk
  scoring, containment playbooks, hash-chained audit and an integrity baseline. The documents are
  `docs/VNX_SECURE_ARCHITECTURE.md`, `_THREAT_MODEL.md`, `_SECURITY_MODEL.md`, `_BENCHMARKS.md` and `_LIMITATIONS.md`.
- **Attack scenarios:** 10 of 10 included simulated attack scenarios were detected and contained. Missed-attack rate 0;
  median detection 4.2 ms, median recovery 12.1 ms.
- **Benign traffic:** 270 requests produced 0 findings and 0 denials.
- **Overhead:** gated chunk reads cost +0.92 ms. Audit writes average 419 B per request.
- **Fuzzing:** a hypothesis campaign of 25,100 examples, with 0 failing. One bug was found during development and fixed in
  3549f76: base64 errors escaped from the token parser.
- **Claim scope:** VNX-Secure is validated against the included simulated attack scenarios. It is not a guarantee
  against real-world attacks.

## 11. Verification

All numbers in this section come from `experiments/v9/results/verification.json`.

| Check | Result |
|---|---|
| Full suite (6 workers) | 3337 passed, 0 failed, 6 skipped, 385 s |
| Sanitizers (ASan/UBSan, gcc and clang) at adba34e | align, reads, rs and cluster kernels PASS. The native cluster tests pass under clang ASan+UBSan and UBSan-trap (48 each) |
| libFuzzer `edit_costs`, 600 s | 11,496,918 runs, 0 crashes |
| libFuzzer native reads parser, 600 s | 3,859,900 runs, 0 new crashes |
| Python hypothesis fuzzing | 10 rounds, 0 failed |
| Reproduction spot check (`experiments/v9/reproduce.sh spot`) | 29 of 29 rows reproduced (24 oracle EVAL, 2 coverage EVAL, 3 noisy 20 KB). Timings, load and environment fields are not compared |
| Security scan at adba34e | 0 CRITICAL. All 39 HIGH are false positives: hash values in V7 trial rows and `key=` variables in VNX-Secure. The MEDIUM `align.c:322` finding is a false positive, because every lane is written before use. The MEDIUM dependency advisories for cryptography 46.0.7 and pytest 8.4.2 are reported and not bumped in V9 |

The first full-suite run had one failure. `test_every_command_is_covered` found that the `vnx security` commands had no
JSON case. `test_security_commands_print_json` now covers them. Those commands print their own JSON shapes, not the
`vnx.result/1` envelope. Moving them onto the envelope is left for a later version.

## 12. Deviations and decisions

- **VNX-Secure merged into the V9 branch** (6c50160), so that the pushed V9 contains it.
- **Consensus E not made the library default.** E is selected and available as a configuration. The default
  `ClusterConfig` is unchanged, so existing callers decode exactly as in V8. Promoting E is a founder decision.
- **Package version string kept at 6.0.0.dev0.** A bump changes archive bytes, which the compatibility tests pin. The
  founder decides at release time.
- **Post-EVAL automation fix** (6a40666): the coverage `--tune` step crashed on a relative `--out` path. It was fixed and
  re-run, and no result rows were affected.
- **Derived numbers:** the H1 bootstrap interval and the H4 reads-avoided figures come from `experiments/v9/report_numbers.py`
  after the runs. The methods are the ones pre-specified in A1 and §6.
- **Held-out step K:** not run. Its precondition (an ADEQUATE model on DEV) was not met.

## 13. Limitations (what V9 does not establish)

- No physical DNA work of any kind.
- D13-F1 is INADEQUATE as a channel model. Every decoder number is measured under a stress channel, not under validated
  nanopore behaviour.
- At coverage 3 under D13-F1, no V9 decoder recovers an archive.
- Adaptive coverage is a computational stopping rule over a read pool already drawn. It says nothing about steering a
  sequencer.
- VNX-Secure was tested only against the included simulated scenarios, on one host.
