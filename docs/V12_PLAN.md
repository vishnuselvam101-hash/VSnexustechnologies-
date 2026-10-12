# VNX-DNA V12 plan: performance, reliability and hybrid architecture

Status: **in progress** on `build/v12`, based on tag `v11.0.0`. This file states the verified V11 baseline, the measured
starting profile and the **pre-registered targets**. It was committed before any V12 optimisation was measured. Every
channel number is **SIMULATED**: reads come from the VNX-DNA channel simulator, and no DNA was synthesised, stored or
sequenced.

## 1. Verified V11 baseline (Phase 0, 2026-10-12)

| item | verified value |
|---|---|
| release tag | `v11.0.0` → `51550df` (merge of PR #15, head `299f099`), on `origin/main` |
| PR #15 | MERGED 2026-10-12 03:41 UTC |
| CI on `51550df` | `ci` (push) success |
| `release` workflow on the tag | **failure in the `pypi` job only**: `invalid-publisher` (no PyPI trusted publisher matches this repository). `image` and `dist` succeeded and the GitHub release exists. This is an external account setting, not a code defect. Fixing it needs the founder to configure PyPI. |
| `97076d6` | exists, ancestor of `origin/main` (included in V11) |
| version string | `src/vnxdna/_version.py` = `11.0.0` |
| full suite on `51550df` (this host, 7 native kernels) | **4051 passed, 4 skipped, 0 failed**, 360.9 s. Skips: opt-in install tests (`VNXDNA_INSTALL_TEST=1`). Command: `pytest -q -p no:cacheprovider -o addopts= -n 6 tests` |

### Correction to the V12 brief's reading of the V11 numbers

The brief describes the V11 "four-worker speedup 1.174×" as parallel scaling. That is wrong. In
`docs/V11_BENCHMARK_REPORT.md` it is the **V10 → V11 ratio**, with both trees decoding at 4 workers (4 MiB indel decode,
V10 4.592 s / V11 3.920 s). The founder's V11 target, ≥ 1.25× end to end against the previous version at w4, was NOT
MET. V11's own scaling on that workload was 10.598 s at w1 against 3.920 s at w4, which is **2.70×** on 4 cores / 8
threads.

The "6.14 → 3.40 s" (alignment) and "14.76 → 12.31 s" (decode) figures are cProfile-instrumented single-worker times
(`experiments/v11/wp1/profile-summary.json`). They are not wall-clock benchmarks.

## 2. Environment

| item | value |
|---|---|
| CPU | Intel Xeon Gold 6240 @ 2.60 GHz, 4 cores / 8 threads (VM), avx2 + avx512f/bw |
| RAM / disk | 31 GiB; 23 GiB free on `/` (88 % used). The disk constrains large experiments. |
| toolchain | gcc 13.3.0, clang 18.1.3, rustc/cargo 1.75.0, Node 24.21, Python 3.12.3 (lab venv), Go 1.22, OpenJDK 17 |
| host | shared production host. Other sessions and services run here. The 1-min load is recorded with every timing. |

## 3. Starting profile (4 MiB indel decode, seed 95500; sub 1 %, ins 0.2 %, del 0.2 %, coverage 5)

Measured on the `v11.0.0` tree. Stage timers come from the decoder's own `stage_seconds`. Pass-1 sub-stages come from
uninstrumented wrappers (scratch harness, not committed).

| stage | w1 s | w4 s | note |
|---|---|---|---|
| total | 11.05 | 3.90 | |
| pass 1 (parse → orient → align → inner RS → pending) | 9.72 | 2.63 | worker CPU 9.0 s / 9.4 s; w4 parallel efficiency 0.90 |
| — native alignment (`align_usable`) | ≈ 3.25 (cProfile) | | in C, AVX-512 |
| — forward `_try` | 5.72 | | 655 755 reads, 479 391 accepted |
| — reverse-complement retry `_try` | 1.23 | | 107 540 reads, 20 397 accepted |
| — orientation pre-pass | 0.40 | | |
| — per-read Python bookkeeping in `_process` | ≈ 1.7 | | slicing, pending packing |
| pass 2 (resolve duplicates, consensus, outer RS, write) | 1.12 | **1.06** | **serial at every worker count**: 27 % of w4 wall time |
| verify | 0.01 | 0.01 | |

**Finding: orientation misclassification.**
- The benchmark channel produces no reverse-complement reads (`reverse_complement_rate` = 0).
- Even so, the orientation pre-pass flipped 29 789 reads (4.5 %). All 29 789 failed in the flipped orientation, and the
  RC retry flipped them back (20 397 recovered).
- Cause: the pre-pass compares markers only at their unshifted positions. One indel early in a read shifts every later
  marker, so the forward score collapses.
- The retry keeps this from affecting correctness, but it wastes alignment work.

## 4. Pre-registered targets

The baseline is `v11.0.0` on this host. Method: `experiments/v12/bench/bench.py`, paired per seed, alternating order,
median ratio with a bootstrap CI, n = 5 seeds. A target is MET only if the median ratio reaches it **and** outputs are
identical with zero false successes. Anything else is reported as NOT MET with the measured value.

| id | target |
|---|---|
| T1 | 4 MiB indel decode, **w4**: median ratio v11/v12 **≥ 1.25** (the founder's V12 directive), CI lower bound > 1.0 |
| T2 | 4 MiB indel decode, **w1**: median ratio ≥ 1.15 |
| T3 | no scenario slower by more than 5 % (median ratio ≥ 0.95): clean, substitution, RC, dropout, 64 KiB, 16 MiB, encode |
| T4 | byte-identical output and identical outcome to v11.0.0 on every benchmark seed; false successes = 0 |
| T5 | peak RSS (process tree) ratio ≤ 1.10 on every decode scenario |
| T6 | w4/w1 scaling of the 4 MiB indel decode reported for both trees, with the serial fraction explained |
| T7 | full suite: no failures; the test count does not drop below 4051 passed |

Stretch, investigated but not promised: ≥ 1.5× at w4 (founder directive, objective A).

## 5. Work plan, ordered by measured contribution

1. **Parallel pass 2.** Run duplicate resolution and consensus per spill bucket on the existing worker pool. Merge
   results in bucket order on the main process, so output does not depend on worker count. The bucket count is
   unchanged, because snapping is per bucket. Estimate: −0.5 to −0.7 s at w4.
2. **Pass-1 bookkeeping.** Remove per-read Python work in `_process` / `_try`: list slicing, repeated
   stack/copy, and the `frame_erasures_to_bytes` / `nt_to_bytes` round trips. This helps w1 and w4.
3. **Shift-tolerant orientation test.** Score markers in both orientations within a small shift window. The RC retry
   stays as the safety net, so a wrong flip still costs only time.
4. Re-profile after each step; native code only where the profile shows Python dominating a hot loop.

Language policy: Python orchestration plus the existing C11 / Rust / C++ kernels from V10–V11. A new language or kernel
needs a profile that justifies it (V12 brief §6). No format, mapping, encryption or ECC parameter changes are planned;
any change would be versioned and opt-in.
