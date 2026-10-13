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

## Performance (SIMULATED; docs/V12_BENCHMARK_REPORT.md, experiments/v12/bench/ab-session5)

Median ratio v11.0.0 seconds / v12 seconds, 95 % bootstrap CI, 5 seeds (3 for 16 MiB), paired per seed. Session 5
(memory and pass-1 fast paths, docs/V12_SESSION5_REPORT.md) superseded the ab-orient numbers of the first release
draft (in parentheses):

| scenario | ratio | verdict vs A/A band |
|---|---|---|
| 4 MiB indel, 1 worker | 1.238 [1.178, 1.259] (was 1.114) | faster beyond noise |
| 4 MiB indel, 2 workers | 1.257 [1.180, 1.321] (1.148) | faster beyond noise |
| 4 MiB indel, 4 workers | 1.377 [1.289, 1.385] (1.228) | faster beyond noise |
| 4 MiB indel, 8 workers | 1.412 [1.391, 1.512] (1.344) | faster beyond noise |
| reverse complement 50 %, 4 workers | 1.339 [1.329, 1.417] (1.244) | faster beyond noise |
| dropout 5 %, 4 workers | 1.327 [1.272, 1.385] (1.269) | faster beyond noise |
| substitution 2 %, 4 workers | 1.179 [1.157, 1.209] (1.171) | faster beyond noise |
| 16 MiB indel, 4 workers | 1.486 [1.441, 1.525] (1.319) | faster beyond noise |
| clean, 4 workers | 1.120 [1.069, 1.252] (1.042) | faster beyond noise |
| 64 KiB indel, 1 worker | 1.086 [0.998, 1.103] (1.035) | faster beyond noise |
| encode 1 MiB / 4 MiB w1 / 4 MiB w4 | 1.003 / 0.975 / 0.982 | within noise |

Pre-registered targets (`docs/V12_PLAN.md` §4):

| target | required | result | status |
|---|---|---|---|
| T1 | 4 MiB indel w4 ≥ 1.25, CI lower bound > 1.0 | 1.377 [1.289, 1.385] | met |
| T2 | 4 MiB indel w1 ≥ 1.15 | 1.238 [1.178, 1.259] | met |
| T3 | no scenario slower than 0.95 | lowest row encode-4m-w1 0.975 (within the A/A band); every decode row ≥ 1.086 | met |
| T4 | byte-identical output, 0 false successes | identical on all 63 rows, 0 false successes | met |
| T5 | peak process-tree RSS ≤ 1.10x on every decode scenario | highest 1.017 (16 MiB); clean 1.014, substitution 0.967, dropout 1.010, w2 1.004 | met |
| T6 | w4/w1 scaling reported for both trees with the serial fraction explained | v11 2.84x, v12 3.13x (w8: 3.94x / 4.51x); measured serial parts ≈ 0.6–0.7 s of the 8.64 s w1 decode, Amdahl estimate 0.09 at w4 (docs/V12_SESSION5_REPORT.md §E) | met |
| T7 | full suite 0 failures, ≥ 4051 passed | 4209 passed, 0 failed (521adb0) | met |

Where the time went (stage timers, cProfile and py-spy, single runs): the orientation scan fell from 1.37 s to 0.27 s
of a w1 decode; pass 2 at w4 from 1.07 s serial to 0.45 s parallel; the byte-erasure packing from 12.1 ms to 0.16 ms
per batch; the remaining pass-1 worker CPU of the 4 MiB indel decode is dominated by native alignment (about 22 % of
the w1 samples) and the per-read Python glue of the sync path and the reverse-complement retry.

Peak memory: the first release draft's +20–23 % at 4 workers (clean, substitution) came from a 20 MiB per-batch
int64 gather index resident in every worker and from pass-2 buckets loaded on top of the workers' pass-1 residue; both
are fixed (docs/V12_SESSION5_REPORT.md §B–C) and every decode scenario is within 2 % of v11.0.0.

## Limitations

- `trim_heap` (heap pages returned to the OS between stages) is glibc-specific; elsewhere it is a no-op and the
  workers' pass-1 residue stays resident through pass 2 (not measured on such a platform).
- The serial fraction of the worker scaling is an Amdahl estimate; the measured 8–12 % worker-CPU inflation at 4 and
  8 workers is not attributed to one cause (SMT, memory bandwidth, the single feeder thread are hypotheses).
- All benchmarks are on one host and one channel family; no real sequencing data was used.
- The 10 MB nanopore-like case still fails explicitly (deferred); low-coverage robustness is not addressed.

## Next

1. Founder release review of build/v12 (docs/V12_SESSION5_REPORT.md §I lists the release-engineer gates still to run).
2. Optional single-worker margin: a flat `project` → `align_usable` path without per-read lists (about 7 % of the w1
   samples).
3. V12-DESIGN-LOWCOV (low-coverage and real-data robustness) as its own release.
