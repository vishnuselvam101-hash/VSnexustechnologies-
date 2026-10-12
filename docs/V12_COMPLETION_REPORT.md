# VNX-DNA V12 — completion report (performance release)

Version 12.0.0, tag `v12.0.0`, branch `build/v12` merged into `main`. Every channel result in this report is
SIMULATED (VNX-DNA channel simulator on one 4C/8T Linux host); no DNA was synthesised, stored or sequenced. Numbers
come from the committed files named next to them.

## Release status

- Scope delivered: the performance part of the V12 brief (`docs/V12_PLAN.md` §5, items 1–4): parallel pass 2,
  pass-1 bookkeeping without per-read Python work, the shift-tolerant orientation pre-pass and its native kernel, exact
  report statistics across worker counts, and the A/A + A/B benchmark harness.
- **Not in this release:** the low-coverage and real-data robustness design (V12-DESIGN-LOWCOV: UNACORM-style
  channels, coverage below 2, nanopore-like reads) and the 10 MB nanopore-like case, which remains an explicit decode
  failure (deferred since V10). No format change: archive, container and strand formats equal v11.0.0, and decode
  output is byte-identical to v11.0.0 on every benchmark seed.
- Pre-release merge f712d91; this release commit changes only docs and release metadata.

## Implementation

| item | commits | where |
|---|---|---|
| Parallel pass 2 on the pass-1 worker pool; two arrays per bucket | 3f62578, ad9bff6 | `vnxdna.recovery` pass 2 |
| Shift-tolerant orientation pre-pass (±2 nt) with early exit | 605a726, 6a830c4 | `recovery/pass1.py` `_orientation_reference` |
| Native orientation kernel (C11, ABI 1, bit-exact, fallback) | 58d40c9, 812ed7d | `native/c/orient.c`, `native/orient.py` |
| Flat pass-1 gathers (exact-length rows, raw headers, pending) | 6a830c4 | `recovery/pass1.py` `_gather_rows` |
| Exact `false_accept_bound` / `entropy_bits_sum` across worker counts | 9aa98d0 | smart/soft summaries |
| Goldens re-pinned for the new pre-pass (SHAs unchanged) | 00bd160 | `tests/v7` |
| Benchmark harness, A/A band, `compare_aa.py` | 8d030fa, be27e2d, 15a68ad | `experiments/v12/bench/` |

## Validation

- Full suite at the pre-release merge f712d91: **4142 passed, 4 skipped, 0 failed** (378 s, 6 workers, all eight
  native kernels built). v11.0.0 baseline at 51550df: 4051 passed, 4 skipped. Skips: one aligner-level test (this CPU
  supports every level) and three opt-in install tests.
- Native orientation kernel: 0 decision differences on 655 755 fixture reads; differential stress fuzz 1000 rounds =
  125 405 reads, 0 mismatches (`benchmarks/v12/native_orient/stress_fuzz.py`); gcc ASan+UBSan, clang ASan+UBSan and
  clang UBSan-trap builds run the kernel tests and the fuzz, all PASS, 0 warnings with `-Wconversion -Wpedantic`
  (`benchmarks/v12/native_orient/sanitizers.sh`, wired into `tools/sanitizers.sh`).
- Parallel pass 2, gathers and early exit: one-off equivalence of every pass-1 output on 5 channels × 5 worker
  configurations × 2 quality thresholds, 0 differences; `tests/v12/test_parallel_pass2.py`,
  `tests/v12/test_pass1_gather.py`.
- `ruff check src tests research` clean.

## Performance (SIMULATED; docs/V12_BENCHMARK_REPORT.md, experiments/v12/bench/ab-orient)

Median ratio v11.0.0 seconds / v12 seconds, 95 % bootstrap CI, 5 seeds (3 for 16 MiB), paired per seed:

| scenario | ratio | verdict vs A/A band |
|---|---|---|
| 4 MiB indel, 1 worker | 1.114 [1.094, 1.146] | faster beyond noise |
| 4 MiB indel, 2 workers | 1.148 [1.114, 1.163] | faster beyond noise |
| 4 MiB indel, 4 workers | 1.228 [1.205, 1.312] | faster beyond noise |
| 4 MiB indel, 8 workers | 1.344 [1.290, 1.423] | faster beyond noise |
| reverse complement 50 %, 4 workers | 1.244 [1.217, 1.256] | faster beyond noise |
| dropout 5 %, 4 workers | 1.269 [1.254, 1.314] | faster beyond noise |
| substitution 2 %, 4 workers | 1.171 [1.146, 1.190] | faster beyond noise |
| 16 MiB indel, 4 workers | 1.319 [1.312, 1.338] | faster beyond noise |
| clean, 4 workers | 1.042 [1.041, 1.118] | within noise |
| 64 KiB indel, 1 worker | 1.035 [0.936, 1.102] | within noise |
| encode 1 MiB / 4 MiB w1 / 4 MiB w4 | 1.008 / 0.993 / 0.940 | within noise (encode-4m-w4 re-run: 0.972 [0.924, 1.051]) |

Pre-registered targets (`docs/V12_PLAN.md` §4):

| target | required | result | status |
|---|---|---|---|
| T1 | 4 MiB indel w4 ≥ 1.25, CI lower bound > 1.0 | 1.228 [1.205, 1.312] | **not met** (CI lower bound > 1.0 holds) |
| T2 | 4 MiB indel w1 ≥ 1.15 | 1.114 | **not met** |
| T3 | no scenario slower than 0.95 | lowest committed row encode-4m-w4 0.940, re-run 0.972; every decode row ≥ 1.035 | met on the re-run, marginal in the committed run (noise) |
| T4 | byte-identical output, 0 false successes | identical on all 63 rows, 0 false successes | met |
| T5 | peak process-tree RSS ≤ 1.10x on every decode scenario | indel-w4 1.05, 16 MiB 1.09, rc 1.07; dropout 1.19, clean 1.20, substitution 1.23, w2 1.11 | **not met** |
| T6 | w4/w1 scaling reported for both trees with the serial fraction explained | v11 2.85x, v12 3.20x (w8: 3.76x / 4.53x); serial fraction not yet broken down | partly met |
| T7 | full suite 0 failures, ≥ 4051 passed | 4142 passed, 0 failed | met |

Where the time went (stage timers and cProfile, single runs): the orientation scan fell from 1.37 s to 0.27 s of a
w1 decode; pass 2 at w4 from 1.07 s serial to 0.45 s parallel; the remaining pass-1 worker CPU of the 4 MiB indel
decode is dominated by native alignment (about 3.2 s) and the per-read `_try` bookkeeping.

## Limitations

- T1, T2 and T5 are not met; the release ships the measured 1.23x (w4) / 1.11x (w1), not the directive's 1.25x.
- Peak RSS at 4 workers grew by up to 23 % on clean and substitution inputs (384 → 462 MiB and 404 → 497 MiB): the
  worker pool now outlives pass 1 and the parallel pass 2 keeps two buckets per worker in flight.
- All benchmarks are on one host and one channel family; no real sequencing data was used.
- The 10 MB nanopore-like case still fails explicitly (deferred); low-coverage robustness is not addressed.

## Next

1. T5: measure clean-w4 with memray; cap in-flight pass-2 buckets or release pass-1 buffers before pass 2.
2. T1/T2: profile `_try` bookkeeping and the reverse-complement retry in pass 1 (alignment is the floor).
3. T6: break the serial fraction down from the stage timers.
4. V12-DESIGN-LOWCOV (low-coverage and real-data robustness) as its own release.
