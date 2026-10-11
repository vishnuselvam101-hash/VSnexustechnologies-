# V11 end-to-end benchmark report: V10 (v10.0.0) vs V11 (build/v11)

**Evidence level: SIMULATED.** Every decode input is a simulated read set from the VNX-DNA channel simulator. No DNA was
synthesised, stored or sequenced. All timings come from one shared host (see Environment) and are not reproduced on a
second machine.

Raw data: `experiments/v11/bench/e2e.jsonl` (85 measured rows), `warmup.jsonl` (17 discarded warm-up rows),
`e2e-summary.json` (the numbers below), `run.log` (console). Harness: `experiments/v11/bench/bench.py`.

## 1. Method

- **Trees.**
  - V10 = tag `v10.0.0` (commit `c364264`), checkout `/root/vnx-dna-lab/v10-rel`.
  - V11 = `build/v11` at `5f70316` (merges WP1 SIMD aligner, WP2 dispatcher, WP3 memory, WP4 VNX-Secure gate).
  - All seven native kernels were built in both trees. `native_status()` reported every kernel `native` with no stale
    library in either tree (`stale_libraries` is empty in every summary row).
- **Harness.** `bench.py` reuses the subprocess step of `experiments/arch/compare.py` (`one`, the same decode/encode calls
  used for the V9-vs-V10 comparison) and the process-tree RSS sampler of `experiments/v10/opt/bench.py` (0.5 s poll).
  Each configuration runs in its own Python process, with `PYTHONPATH` pinned to its tree. The harness checks that the
  imported `vnxdna` comes from that tree.
- **Pairing.** For each seed, one input is prepared (archive → strands → simulated reads). Every configuration then
  decodes or encodes that same input. The configuration order reverses on odd seeds.
- **Warm-up.** One discarded run per configuration on the first seed of each scenario.
- **Repetitions.** 5 seeds per scenario, so n = 5 paired runs.
- **Statistic.** The paired ratio is V10 seconds / V11 seconds, so a value above 1 means V11 is faster. The table gives
  the median ratio. The 95 % CI is a percentile bootstrap of the median: 10 000 resamples, seed 11.
  - With n = 5 this interval equals the min–max range of the five ratios, so it is coarse.
- **Correctness per run.** Each run records:
  - outcome;
  - output SHA-256;
  - whether the output matches the input archive;
  - false-success count.
- **Scenarios.**
  - `indel` = 1 % substitution, 0.2 % insertion, 0.2 % deletion, coverage 5.
  - `substitution` = 2 % substitution, coverage 5.
  - `clean` = coverage 3.
  - The indel w4 seeds (95300–95304) and the clean and substitution seeds are the ones used in the V9-vs-V10 comparison.
- **AVX2 check.** `v11-avx2` is the V11 tree with `VNXDNA_ALIGN_BACKEND=avx2`, run on the indel w4 scenario.

## 2. Environment

| item | value |
|---|---|
| source | `experiments/v11/bench/environment.txt` (CPU model also in `e2e-summary.json` `host`) |
| CPU | Intel Xeon Gold 6240 @ 2.60 GHz, 8 logical CPUs (avx2, avx512f, avx512bw) |
| RAM | 31 GiB (MemTotal 32866104 KiB) |
| OS / kernel | Linux 6.8.0-146-generic |
| Python / NumPy / gcc | 3.12.3 / 2.5.3 / 13.3.0 |
| V11 auto aligner level | avx512 (V11 WP1 auto policy) |
| run window | 2026-10-11 22:49–23:01 IST |
| 1-min load at run start | 1.61–3.60 across all rows (per-scenario ranges below) |

**Host-load disclosure.** This is a shared production host, not a quiet one.
- Operator-recorded (in `environment.txt`, not machine-recorded): other sessions were active during the run. A release-preparation session ran a pytest job pinned to CPUs 6–7 at nice 19 when the benchmark started.
- The benchmark waited until that session's libFuzzer campaign ended before starting.
- Pairing and alternating order spread this background load over both trees. It is not removed.

## 3. Results

| scenario | V10 median s | V11 median s | ratio V10/V11 median [95 % CI] | tree peak RSS MiB, median V10 → V11 | RSS ratio | identical output (all seeds) | load range |
|---|---|---|---|---|---|---|---|
| decode indel 4 MiB w4 | 4.592 | 3.920 | **1.174** [1.136, 1.233] | 412.6 → 409.6 | 0.994 | yes | 2.27–3.19 |
| decode indel 4 MiB w4, V11 avx2 pinned | 4.592 | 4.358 | 1.055 [1.042, 1.078] | 412.6 → 410.4 | 0.993 | yes | 2.27–3.19 |
| decode indel 4 MiB w1 | 13.597 | 10.598 | **1.283** [1.244, 1.321] | 107.1 → 101.0 | 0.969 | yes | 1.61–2.55 |
| decode clean 4 MiB w4 | 1.450 | 1.440 | 0.981 [0.966, 1.038] | 410.1 → 377.7 | 0.923 | yes | 2.08–2.92 |
| decode substitution 4 MiB w4 | 2.468 | 2.443 | 0.999 [0.938, 1.066] | 407.0 → 406.3 | 0.998 | yes | 2.06–3.01 |
| encode 1 MiB w1 | 0.346 | 0.329 | 1.025 [0.972, 1.063] | 51.4 → 51.7 | 1.005 | yes | 2.22–2.33 |
| encode 1 MiB w4 | 0.160 | 0.163 | 0.995 [0.955, 1.020] | not measured (the 0.5 s sampler returned 0 in 9 of 10 rows) | — | yes | 2.22–2.85 |
| encode 4 MiB w1 | 1.332 | 1.285 | 0.999 [0.992, 1.059] | 52.6 → 53.1 | 0.994 | yes | 2.86–3.60 |
| encode 4 MiB w4 | 0.421 | 0.432 | 1.003 [0.942, 1.041] | 187.9 → 190.0 | 1.011 | yes | 3.43–3.51 |

Correctness:
- All 85 measured runs returned SUCCESS (decode) or ENCODED (encode).
- Decoded output matched the input archive in every decode run.
- Output SHA-256 was identical between V10 and V11, and the avx2-pinned V11, for every seed of every scenario.
- False successes: 0.

**AVX2 check.** Pinned to AVX2, V11 is still faster than V10 (1.055x) but slower than the auto (AVX-512) level. Its
median is 4.358 s against 3.920 s. This SIMULATED run shows no sign that AVX-512 downclocking makes avx512 the worse
choice end to end on this host, so the auto policy (avx512 > avx2 > scalar) stands.

## 4. Regressions and limits, disclosed

- **No speed-up outside the indel path.** Clean and substitution decodes, and every encode, are unchanged within noise: every median lies in 0.98–1.03, and every CI contains 1.0.
  - Two medians fall just below 1.0: clean decode w4 0.981 and encode 1 MiB w4 0.995. Their CIs contain 1.0, so no regression is shown and none is ruled out at this n.
  - This is expected. WP1 changed only the banded aligner, and the aligner is the dominant cost only when indels force re-alignment.
- **RSS.** Tree peak RSS is within −8 % to +1.1 % of V10 in every measured scenario.
  - Peaks are sampled every 0.5 s, so short spikes can be missed.
  - In all 10 encode 1 MiB w4 runs (about 0.16 s each) the sampler returned 0 in 9 rows (one V10 row recorded 50.6 MiB), so their RSS is reported as not measured.
- **Statistics.** n = 5 per scenario. The bootstrap CI of a 5-sample median is the min–max of the five ratios.
- **Host.** One shared host under moderate background load (section 2). The run was not repeated on a quiet or second machine.
- **`report_peak_rss_bytes`.** The decoder's own report gives 146.8–146.9 MiB for every configuration. It is the parent process's peak and does not reflect the worker processes, so the tree peak above is used.

## 5. V11_BASELINE §9 verdict (WP1 acceptance, the benchmarked criteria)

| criterion | result | status |
|---|---|---|
| 4 MiB indel decode ≥ 1.25x faster than V10, median of 5 paired runs, CI excludes 1.0, **w4** (V10 4.67 s in baseline) | 1.174x [1.136, 1.233] | **NOT MET** (CI excludes 1.0, but the median and the whole CI are below 1.25) |
| same, **w1** (V10 14.16 s in baseline) | 1.283x [1.244, 1.321] | **MET** (median ≥ 1.25, CI excludes 1.0; the lower CI bound is 1.244) |
| output SHA-256 identical to V10 on all seeds | 10/10 indel seeds, and every other scenario | MET |
| peak RSS ≤ V10 + 10 % | ratio 0.969–0.994 on indel | MET |
| native vs reference equivalence suites unchanged and green | full suite on `5f70316`: 3981 passed, 7 skipped, 0 failed (uncommitted lab log `results/v11-integrate-20261011-223701.log`; baseline expectation ≥ 3634) | MET |
| ASan/UBSan clean for touched C | `experiments/v11/wp1/sanitizers-run.log`: gcc and clang ASan+UBSan, clang UBSan-trap, overall PASS; its recorded kernel source SHA-256 (`c5a81b7a…`) equals the merged `align.c`, and no native source changed after it | MET |
| cProfile before/after committed under `experiments/v11/` | not committed | **NOT MET** |

**Overall WP1 §9 verdict: NOT MET.**
- The w1 indel speed-up reaches the 1.25x target.
- The w4 indel speed-up (1.174x) does not.
- The cProfile before/after artefact is missing.
- Correctness, RSS and equivalence criteria are met.

Hypothesis, not measured here: at w4 the aligner is a smaller share of wall time than at w1, so the same kernel speed-up
yields a smaller end-to-end gain. A w4 profile (the missing cProfile artefact) is needed to confirm it.

## 6. Trend V9 → V10 → V11 (indicative only)

V9→V10 comes from `experiments/arch/results/compare-summary.json`:
- V9 = `e76e24b`; V10 = pre-release commit `4700165`.
- Geomean of paired ratios: n = 5 at w4, n = 3 at w1.
- Different session and load (1-min load 1.17–3.45 across the four scenarios below).

V10→V11 is this report. The two steps were measured in different sessions, so the product is **not** a paired measurement.

Ratio columns are per-seed paired ratios (V9→V10 geomean, V10→V11 median), not ratios of the median seconds shown.

| scenario | V9 s | V10 s (arch run) | V10 s (this run) | V11 s | V9→V10 | V10→V11 |
|---|---|---|---|---|---|---|
| decode indel 4 MiB w4 | 5.347 | 4.674 | 4.592 | 3.920 | 1.144x | 1.174x |
| decode indel 4 MiB w1 | 15.192 | 14.160 | 13.597 | 10.598 | 1.073x | 1.283x |
| decode clean 4 MiB w4 | 1.613 | 1.594 | 1.450 | 1.440 | 1.016x | 0.981x |
| decode substitution 4 MiB w4 | 2.783 | 2.615 | 2.468 | 2.443 | 1.066x | 0.999x |

## 7. Reproduction

```
for t in /root/vnx-dna-lab/v10-rel /root/vnx-dna-lab/v11; do for m in vnxdna.v5.native_alignment vnxdna.v6.native_reads \
  vnxdna.v6.native_rs vnxdna.native.cluster vnxdna.native.strand vnxdna.native.integrity vnxdna.native.outerlist; do
  PYTHONPATH=$t/src python -m $m build; done; done
uptime    # record the load; quiet host preferred
V10_TREE=/root/vnx-dna-lab/v10-rel PYTHONPATH=src python experiments/v11/bench/bench.py run
PYTHONPATH=src python experiments/v11/bench/bench.py summary
```
