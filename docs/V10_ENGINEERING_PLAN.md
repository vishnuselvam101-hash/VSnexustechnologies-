# VNX-DNA V10 engineering plan (baseline assessment)

Status: PROPOSAL. Nothing in this document is implemented. V10 starts from the v9.0.0 release (e76e24b); V9 stays the
reproducible baseline (`experiments/v9/reproduce.sh`). Every decode number below is SIMULATED (software channel
models). The D13 channel fit is PUBLIC-DATA-DERIVED. No DNA has been synthesised, stored or sequenced.

## 1. Baseline (v9.0.0)

| Area | V9 value | Source |
|---|---|---|
| Full suite | 3337 passed, 0 failed, 6 skipped (local, 6 workers, 354 s) | `experiments/v9/results/verification.json`, release merge run |
| Line coverage (`vnxdna`) | 88.3 % overall. Lowest: `benchmark` 63.7 %, `legacy` 73.4 %, `api.py` 85.2 %. `cli.py` / `cli_v1.py` show 0 % because they run in subprocesses that coverage does not follow | lab run of `pytest --cov=vnxdna` on the release tree (not committed) |
| Clean-channel throughput, 1 GiB, 4 workers | encode 105.8 s, decode 195.4 s, 9.78 nt/byte, peak RSS 143 / 323 MiB | `experiments/v9/scale/results/scale.json` |
| Noisy decode, D13-F1 stress channel, cov 10, decoder E | 20 KB: median 22.8 s, 28/30 EXACT. 1 MiB: median 1,059 s, 2/30 EXACT. 0 false success | `experiments/v9/scale/results/noisy.jsonl` |
| Consensus E vs V8 (600 paired EVAL cases) | EXACT 283 vs 240; pooled ORE 0.575 vs 0.488 | `experiments/v9/consensus/results/selection.json` |
| Coverage 3 (D13-F1) | 0/100 EXACT for every production decoder, both profiles | `experiments/v9/oracle/results/eval.jsonl` |
| Adaptive coverage | 0 false terminations, 41.7 % / 31.2 % fewer reads | `experiments/v9/coverage/results/eval.jsonl` |
| Channel model G1 | INADEQUATE (fails M6r, M8) | `experiments/v9/d13/results/VERDICT.json` |
| Security | 0 critical; VNX-Secure 10/10 simulated scenarios; 2 dependency advisories (cryptography 46.0.7, pytest 8.4.2) | V9 completion report §10–11 |

## 2. Bottlenecks found (with evidence)

**B1. Noisy decoding is consensus-bound and does not use the requested workers.**
- A profile of one 20 KB noisy decode (decoder E, D13-F1, cov 10, v4-balanced, seed 93000) took 24.7 s.
  - 98 % of the time is in `cluster_consensus` → `_polish`.
  - Native forward-backward (`native/cluster.py:fb`) takes 7.4 s, native `edit_costs` takes 6.5 s, and Python polish
    glue about 6–7 s. Inner/outer RS is under 2 %.
- The native cluster kernels take their thread count from `VNXDNA_CLUSTER_THREADS` (default 1). The decoder's
  `workers` setting does not reach them.
  - With 4 threads the same decode took 15.3 s, a 1.6× speed-up, and the Python glue became the main cost.
  - The 1 MiB noisy decode spends about 32 ms per strand with `workers=4`, the same as the single-threaded 20 KB
    profile.
- Consequence: 10 MB under noise is projected at 3.5 h per decode, so V9 could not measure it.

**B2. Polish re-scores whole reads for local moves.**
- Each trial template (one segment changed) is scored by a full-length banded forward-backward over every read of the
  candidate. A move changes only one segment, so most of that work recomputes unchanged cells.
- The base cost is also recomputed by a separate `fb_calls` pass, after `edit_costs` has already aligned the same reads.

**B3. Large archives fail under the stress channel even though rows nearly always succeed.**
- At 1 MiB, per-row success is 0.995, but the archive needs all 411 rows, so whole-archive success is 2/30.
- The analytic model gives ≈ 0 at 100 MB. The limit comes from per-row parity and the lack of any cross-row
  redundancy, not from decoder speed.

**B4. Coverage 3 is unsolved.**
- On v7-lowcov the two-read consensus oracle recovers 100/100 while production recovers 0/100, so the gap there is in
  consensus.
- On v4-balanced even the oracle recovers 0/100, so that profile is limited by code rate, not by the decoder.

**B5. The channel model is not validated.** G1 fails M6r and M8, so D13-F1 remains a stress channel. The held-out set
is still closed.

**B6. Test and CI hygiene.**
- `tests/v2/test_streaming_scale_v2.py::test_peak_memory_does_not_grow_with_file_size` is marginal on CI runners.
  - Locally, V8 and V9 measure the same: recover peaks at about 147 MiB at 64 MB against a limit of about 165–173 MiB.
  - On CI the 8 MB baseline sometimes measures about 81 MiB, which gives a limit of about 150 MiB, and the run fails at
    about 155 MiB. It failed on 3 of 4 V9 CI runs and passed on V8's.
- The `vnx security` commands print their own JSON shapes instead of the `vnx.result/1` envelope.

## 3. Proposed V10 work (in priority order)

Each item lists its hypothesis, method and acceptance test. Each is pre-registered before any evaluation decode, as in
V9.

| # | Item | Priority it serves | Acceptance |
|---|---|---|---|
| W1 | Route decode `workers` to the native cluster kernels, keeping results thread-invariant (already a documented kernel property) | 1 | Byte-identical decodes for threads 1/2/4 on the V9 benchmark seeds. Paired speed-up with bootstrap CI on seeds 94000–94009 |
| W2 | Incremental polish: score a trial move only over the band window its segment touches, and reuse the `edit_costs` optimum as the base cost. Move the remaining hot Python loop (`shift`, move generation) to C11 | 1, 4 | Byte-identical to the V9 polish on 100k randomised reads plus the 50 golden hashes. ASan/UBSan and libFuzzer harness. Target: noisy 1 MiB decode under 300 s (measured, not assumed) |
| W3 | Cross-row (outer-of-outer) parity or an archive-level erasure layer as an opt-in format version | 2, 7 | Old archives decode unchanged (V4/V5/V9 golden fixtures). The analytic model and SIMULATED decodes at 1 MiB and 10 MB (now feasible after W1/W2) show whole-archive success with 0 false success |
| W4 | Low-coverage consensus on v7-lowcov (cov 3): close part of the oracle gap with a pre-registered candidate set (e.g. pairwise read-to-read alignment before templating) | 3, 4 | McNemar vs E on fresh EVAL seeds, Holm-adjusted. 0 false success and 0 false frames. Deterministic across workers |
| W5 | Channel model G2 or a new latent family targeting M6r/M8; open the held-out set only if a candidate is ADEQUATE on DEV | 2, 5 | V8 gating metrics; the prereg §4.4 rule for the held-out set |
| W6 | Benchmark methodology: one statistics module (Wilson, exact McNemar, Holm, paired bootstrap) shared by all experiment runners; record host load and refuse comparisons above a load threshold; cache by commit + corpus + seed + params | 5 | Unit tests against known values. Every V10 report table cites a results file and its method |
| W7 | Fix the CI memory test margin by measuring the baseline robustly (median of repeated runs, or a fixed reference process) without loosening what it checks; put `vnx security` output on the `vnx.result/1` envelope behind a compatible flag | 8, 6 | The test is stable over 10 CI reruns. The JSON contract test covers the new shapes. VNX-Secure suite unchanged |
| W8 | Dependency advisories: assess reachability of the cryptography and pytest advisories and bump where safe | 6 | Full suite + VNX-Secure suite + container compatibility tests |

Not proposed: a version bump without changes, a C++ port (factory rule: Python + C11), or any physical-DNA claim.

## 4. V10 quality gate

V10 is not complete until all of these hold:
- 0 test failures, with the test count not lower than V9's 3337 + 6.
- ASan/UBSan (gcc and clang) PASS on every changed kernel; libFuzzer smoke on every new native entry point.
- Regression against V9: V9 golden archives decode; `experiments/v9/reproduce.sh spot` still REPRODUCED; the V9 EVAL
  cells re-run with the V10 default show no unexplained degradation.
- Reproducible benchmarks (results files + reproduce script) and a written statistical method for every comparison.
- VNX-Secure suite and simulated scenarios pass unchanged (security regression).
- A completion report with every number traced to a committed results file and the SIMULATED / PUBLIC-DATA-DERIVED
  labels intact.
- No tag until the founder accepts it.
