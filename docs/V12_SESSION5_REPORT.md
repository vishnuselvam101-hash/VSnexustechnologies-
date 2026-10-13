# VNX-DNA V12 — Performance Session 5 report: memory, pass-1 profiling, release readiness

Every performance and memory number here is from **SIMULATED** channel data on one host (Xeon Gold 6240, 4 cores /
8 threads, Linux 6.8, Python 3.12.3; `experiments/v12/bench/ab-session5/e2e-summary.json` → `host`). Nothing in this
report measures DNA synthesis, storage or sequencing, and no claim about biological or nanopore performance follows
from it.

## A. Repository and commit state

| item | value |
|---|---|
| branch | `build/v12` (worktree `/root/vnx-dna-lab/v12`); base release v11.0.0 = 51550df on `main` |
| HEAD found at the start | **09d0600** `release: finalize VNX-DNA v12.0.0` — *not* f712d91 as the directive states. A previous session added this commit at 23:04 on 2026-10-12 (version string 12.0.0, CHANGELOG entry, `docs/V12_COMPLETION_REPORT.md`, `docs/V12_BENCHMARK_REPORT.md`, README, release test). It is local only; **no tag exists, nothing was pushed**. It was kept as found (no history rewrite) and the session-5 commits sit on top; the founder decides whether a release-finalize commit belongs on the branch before the release review. |
| f712d91 | the merge the directive names (native orientation kernel); an ancestor of HEAD; the 4,142-test suite, 125,405-read fuzz and sanitizer results it quotes are recorded in `results/full-suite-f712d91-20261012-2252.txt` and the lab notes |
| session-5 commits | f9c09fd memory (workstream A), 0d275e0 pass-1 fast paths (workstream B), 521adb0 layer-rule fix (helper moved to `vnxdna.native.heap`), then the bench + docs commit that contains this report |
| working tree | clean before and after every commit; no uncommitted or unrelated work was present |
| pushed / tagged / merged / published | **nothing**; `main` untouched |

## B. Root cause of the memory overhead (T5)

Method: a per-process RSS timeline (50 ms sampler over the process tree with main and workers separated and the
decoder's events on the same clock), `tracemalloc` around every pass-1 batch (`_process`) and every pass-2 bucket
(`_bucket_consensus`), on the exact bench fixtures (clean seed 95100, substitution 95200, indel 95300; 4 MiB;
`DecodeOptions(workers=4)`), pristine v11.0.0 against build/v12 at 09d0600. Memray was not needed: the two numbers
that mattered (per-batch and per-bucket allocation peaks) came from tracemalloc, and the process-tree timeline located
them in time.

Two measured causes, both inside the worker processes:

1. **Pass 1 — the exact-length gather index.** `_try` built one `(8192, strand_nt)` int64 index matrix per batch to
   gather the exact-length reads from the flat buffer: 20 MiB at 313 nt. NumPy frees it at once, but glibc keeps the
   arena pages resident, so each worker's RSS stayed up by that amount (tracemalloc peak per batch 19.6 → 39.1 MiB;
   worker RSS after 40 batches 87 → 104 MiB; four workers: +70–90 MiB on the tree sum). v11 stacked the exact reads
   view by view and never built the index. This was the clean-w4 peak, reached at the end of pass 1.
2. **Pass 2 — buckets on top of the pass-1 residue.** V12's parallel pass 2 loads each spill bucket in a worker whose
   heap still holds its freed pass-1 temporaries (57–75 MiB), and adds the bucket's own transients: two filtered
   copies of the verified records, the sorted copy, an `(n, 4)` int64 key matrix, a first-record gather of every
   payload and a list of key lists inside `resolve_duplicates`, then the symbol dict. Worker RSS went 57 → 97–123 MiB
   while a bucket was in flight. v11 did this work serially in the main process after the pool had exited, so its tree
   sum never contained four such workers at once.

Checked and not causes: the main process (peak 108–118 MiB in both trees), the pass-1 result window (2 × workers in
both), the spill files, the native orientation kernel, the in-flight bucket count (each worker holds one bucket;
the 2 × workers window only queues futures).

## C. Memory changes and before/after measurements

Commit f9c09fd (helper relocated in 521adb0):

- `recovery.pass1._gather_exact` / `_gather_rows`: gathers in row chunks of 512 (index ≤ 1.3 MiB at 313 nt). Same
  values (`tests/v12/test_pass2_memory.py`, `tests/v12/test_pass1_gather.py`), same speed (0.167 s per batch before
  and after on the clean fixture).
- `recovery.consensus.resolve_duplicates`: address runs from field-wise comparisons of the sorted records (no key
  matrix); run uniformity from neighbour comparisons in 16k-row chunks (no first-record gather); keys built once as
  tuples. Same runs, same majority rule, same order, same conflict count — pinned against the v11 form, including the
  chunk edge.
- `recovery.outer._bucket_consensus`: one filtered copy (none at all when every record is this archive's data, the
  usual case); records dropped as soon as the symbols view the sorted copy; keys packed with `fromiter`;
  `trim_heap()` before loading and after packing.
- `pipeline.decode`: `trim_heap()` in the main process after pass 1.
- `vnxdna.native.heap.trim_heap()` = glibc `malloc_trim(0)` through ctypes (False where the call does not exist).
  Memory only: no decode result can depend on it. Calling it after every pass-1 batch was measured too (+8 % per
  batch from re-faulting the pages) and rejected; the pass-1 worker RSS is already at the v11 level.

Single-run peaks on the fixtures (50 ms tree sampler, MiB):

| scenario (w4) | v11.0.0 | build/v12 at 09d0600 | at f9c09fd (3 runs) |
|---|---|---|---|
| clean | 384 | 479 (end of pass 1) | 376, 385, 385 (peak back in pass 1) |
| substitution | 401 | 407 | 402, 406, 406 |
| indel | 402 | 479 (pass 2) | 407, 402, 409 |

Established bench (`ab-session5`, 0.5 s sampler, median of the seeds; v11 → v12 MiB; ratio): indel-w1 104 → 102
(0.97), w2 248 → 249 (1.00), w4 410 → 416 (1.01), w8 736 → 728 (0.99), reverse complement 416 → 418 (1.00), dropout
412 → 420 (1.01), clean 388 → 389 (1.01), substitution 414 → 402 (0.97), 16 MiB 417 → 427 (1.02), encode unchanged.
Before this session (`ab-orient`): clean 1.20, substitution 1.23, dropout 1.19, w2 1.11.

Cost check (directive §3.8): decode wall times after the change are equal or lower on every scenario (section D and
G), so the memory did not move into allocation churn; the chunk loop replaces one 20 MiB allocation by sixteen of
1.3 MiB; no swap on this host (headroom > 20 GiB during the runs); pass 2 at w4 is 0.45 s before and after.

## D. Indel bottlenecks and optimisations (T1, T2)

Baseline profile, reproducible: `py-spy record --native --rate 500` on the single-worker 4 MiB indel decode at
f9c09fd (9,418 samples; self time by leaf frame and by the nearest `vnxdna` Python frame):

| share | where | note |
|---|---|---|
| 21.6 % | `align_groups_l16` (libvnx_align.so) | native alignment — the floor |
| 7.2 % | `decode_rows_avx2` (libvnx_rs.so) | native inner RS |
| 5.9 % | `sync.template.frame_erasures_to_bytes` | `reshape(n, -1, 4).any(axis=2)`: 12.1 ms per 8192 × 312 batch; NumPy's reduce iterator over a length-4 axis |
| 4.8 % | `pass1._process` lines building the per-read view list and the reverse-complement retry reads | per-read Python loops — not changed |
| 4.4 % | `native.reads.iter_reads_native` | FASTQ parsing in the main process (serial) |
| 3.7 % | `consensus.snap_addresses` | pass 2 |
| 3.1 % | `np.minimum` (UBYTE_minimum) | `np.minimum(fb, 3)` computed twice per path |
| 2.9 % | `dnaenc.mapping.nt_to_bytes` | already vectorised; a table-lookup form was tried and was slower (rejected) |
| 2.5 % | `_reads_in_domain`, size generators, list comprehensions in `project` / `align_usable` | per-read glue — not changed |
| 2.1 % | `vnx_or_scan` (libvnx_orient.so) | orientation kernel |

Changes (commit 0d275e0), each the smallest form with a test pinning it to the form it replaces:

- `frame_erasures_to_bytes`: the four flags of a frame byte viewed as one uint32 and compared with zero (0.16 ms per
  batch; the reduction remains for non-contiguous or oddly shaped input). `tests/v12/test_pass1_fastpaths.py`.
- `_try`: the hard-base clamp computed once per path (frames and `hard_bases` share it).

Single runs on the fixtures before the bench (seconds, v11.0.0 / build/v12; output identical, SUCCESS, output file
equals the input container): w1 10.81 / 8.98 · w2 6.12 / 4.80 · w4 3.91 / 2.85 · w8 2.81 / 2.00. The established
bench confirms them (section G).

Measured but not changed (candidates, by size): a flat path through `TemplateAligner.project` → `align_usable`
taking `(codes, lengths)` so that the sync path and the reverse-complement retry run no per-read list, size generator,
domain loop or `np.concatenate` (about 7 % of the w1 samples); the reverse-complement retry only for reads the
pre-pass left undecided (this changes decisions, so it needs its own equivalence argument — not attempted); the
FASTQ feeder and result pickling in the main process (serial, see T6).

## E. Updated targets (established bench `experiments/v12/bench/ab-session5/`, SIMULATED)

| target | required (`docs/V12_PLAN.md` §4) | measured | status |
|---|---|---|---|
| T1 | 4 MiB indel, 4 workers: median ratio ≥ 1.25, CI lower bound > 1.0 | **1.377 [1.289, 1.385]** (was 1.228) | **PASS** |
| T2 | 4 MiB indel, 1 worker: median ratio ≥ 1.15 | **1.238 [1.178, 1.259]** (was 1.114) | **PASS** |
| T3 | no scenario below 0.95 | lowest row encode-4m-w1 0.975 [0.935, 1.071], within the A/A band; every decode row ≥ 1.086 | PASS |
| T4 | byte-identical output and outcome on every seed, 0 false successes | identical on all 63 rows; 0 false successes | PASS |
| T5 | peak process-tree RSS ratio ≤ 1.10 on every decode scenario | highest 1.017 (16 MiB); clean 1.014, substitution 0.967, dropout 1.010, w2 1.004 (were 1.20 / 1.23 / 1.19 / 1.11); 64 KiB 0.768 from one sampled pair (decode shorter than the sampler period) | **PASS** |
| T6 | w4/w1 scaling for both trees with the serial fraction explained | below | PASS (documented; the Amdahl fraction is an estimate, the stage parts are measured) |
| T7 | full suite 0 failures, ≥ 4051 passed | 4,209 passed, 4 skipped, 0 failed at 521adb0 | PASS |

Every status above is from the measured bench rows; none rests on an estimate.

### T6 — serial fraction

Measured speedups of the 4 MiB indel decode vs 1 worker (bench medians): v11 w2/w4/w8 = 1.78 / 2.84 / 3.94;
v12 = 1.75 / 3.13 / 4.51 (v12 w1 8.64 s, w2 4.93 s, w4 2.76 s, w8 1.91 s).

Amdahl estimate — an inference, not a measurement — solving S(p) = 1 / (s + (1 − s)/p) for s from each point:
v12 s = 0.141 (w2), 0.093 (w4), 0.110 (w8); v11 s = 0.122, 0.136, 0.147. The points do not give one s: the host
has 4 physical cores with SMT, so p = 8 runs two workers per core, and the model assumes a perfectly parallel part,
which the measurements below contradict. The w4 value (0.09, about 0.8 s of 8.64 s) is the best-supported figure.

Measured parts (stage timers of single v12 runs, `report["stage_seconds"]`):

| part | w1 | w4 | w8 | measured or hypothesised |
|---|---|---|---|---|
| start-up, layout detection, pool spawn (wall − stages) | ~0.2 s | ~0.2 s | ~0.2 s | measured |
| pass 1 wall | 7.56–7.93 | 2.13–2.20 | 1.31–1.35 | measured |
| pass-1 worker CPU (sum) | 6.95–7.27 | 7.54–7.67 | 7.74–7.83 | measured: +8 % at w4, +12 % at w8 over w1 |
| pass 2 (bucket consensus in the pool + row decode and writes in main) | 1.01 | 0.45–0.48 | 0.44 | measured; the w8 value is the serial floor of the main-side row decode and output writes (~0.4 s) |
| verify | 0.013 | 0.013 | 0.013 | measured |

So the directly measured serial work is about 0.2 + 0.4 + 0.01 ≈ 0.6–0.7 s (7–8 % of the w1 wall), and the
remaining gap to ideal scaling comes from the parallel part itself: at w8 the workers burn 7.8 s of CPU for 1.3 s of
wall (= 6.0 effective workers of 8) and the CPU inflation is measured, while its cause — shared physical cores (SMT),
memory bandwidth of the gathers and spill writes, and the single feeder thread that parses FASTQ and pickles batches
and results — is hypothesised, not separated. Python overhead inside the workers is part of the parallel section and
scales; it limits w1 (section D), not the scaling. Synchronisation is bounded: the result window is 2 × workers and
results are consumed in order, so a slow batch stalls the window (imbalance), which the stage timers cannot see.

## F. Test, fuzz, sanitizer and conformance results

| check | result | evidence |
|---|---|---|
| full suite at 0d275e0 (before the layer fix) | 4,208 passed, 4 skipped, **1 failed**: `tests/architecture/test_layers.py` R5 (`vnxdna.core.util` imported ctypes) | `results/full-suite-0d275e0-20261013-0604.txt` |
| full suite at 521adb0 (code of the bench) | **4,209 passed, 4 skipped, 0 failed** (396.6 s, 6 workers); skips = the opt-in install tests (3) and the SIMD-level skip on a CPU that supports every level (1) | `results/full-suite-521adb0-20261013-0637.txt` |
| test count | 4,142 → 4,209 (+67: `tests/v12/test_pass2_memory.py` 49, `tests/v12/test_pass1_fastpaths.py` 18); no test weakened, skipped or removed | |
| focused tests for the modified components | gathers vs stacked views and vs the loop across the 512-row chunk edge; `resolve_duplicates` vs the v11 form (0–2000 records, conflicts, ties, the 2¹⁴-row chunk edge); byte erasures vs the reduction (bool, non-zero uint8, non-contiguous, single positions, empty); `trim_heap` | the two files above |
| conformance vectors, golden hashes, every orientation layout and channel, V4/V5/V6 archive goldens, malformed input, ABI mismatch and fallback, VNX-Secure gate | all inside the suite above (0 failures) | |
| native kernel sanitizers (orient; C unchanged this session) | gcc ASan+UBSan, clang ASan+UBSan, clang UBSan-trap: 66 tests each PASS, 100 differential fuzz rounds each (13,595 / 11,989 / 12,736 cases, 0 mismatches); 0 warnings with -Wconversion -Wpedantic on gcc and clang | `results/orient-sanitizers-521adb0-*.txt` |
| stress fuzz (release build) | 1,000 rounds, **125,405 reads, 0 mismatches** | same file |
| determinism / byte identity | output and outcome identical to v11.0.0 on all 63 bench rows; `matches_input` true on every decode row | `ab-session5/e2e.jsonl` |
| no false SUCCESS | 0 on 126 rows | `e2e-summary.json` |
| lint | `ruff check src tests` clean | |

The aligner, RS, reads, cluster, strand, integrity and outer-list kernels were not touched; their sanitizer evidence
from V11 and earlier V12 sessions stands.

## G. All 63 A/B rows (`ab-session5`, v11.0.0 51550df vs build/v12 521adb0)

Ratio = v11 s / v12 s (> 1: V12 faster), median of the paired seeds with the percentile-bootstrap 95 % CI (equal to
the min–max range at n = 5); A/A = the noise band of `baseline-aa`; RSS = process-tree peak ratio v12/v11.

| scenario | workers | n | v11 median s | v12 median s | ratio [CI95] | A/A [CI95] | verdict | RSS |
|---|---|---|---|---|---|---|---|---|
| indel 4 MiB | 1 | 5 | 10.721 | 8.640 | 1.238 [1.178, 1.259] | 0.964 [0.915, 1.002] | faster beyond noise | 0.97 |
| indel 4 MiB | 2 | 5 | 6.014 | 4.927 | 1.257 [1.180, 1.321] | 0.988 [0.964, 0.998] | faster beyond noise | 1.00 |
| indel 4 MiB | 4 | 5 | 3.771 | 2.760 | 1.377 [1.289, 1.385] | 1.008 [0.978, 1.046] | faster beyond noise | 1.01 |
| indel 4 MiB | 8 | 5 | 2.718 | 1.914 | 1.412 [1.391, 1.512] | 0.992 [0.981, 1.005] | faster beyond noise | 0.99 |
| indel + reverse complement 50 % | 4 | 5 | 4.177 | 3.119 | 1.339 [1.329, 1.417] | 1.024 [0.995, 1.048] | faster beyond noise | 1.00 |
| indel + dropout 5 % | 4 | 5 | 4.418 | 3.327 | 1.327 [1.272, 1.385] | 0.989 [0.979, 1.008] | faster beyond noise | 1.01 |
| clean, coverage 3 | 4 | 5 | 1.433 | 1.289 | 1.120 [1.069, 1.252] | 0.956 [0.941, 1.047] | faster beyond noise | 1.01 |
| substitution 2 % | 4 | 5 | 2.387 | 1.984 | 1.179 [1.157, 1.209] | 1.000 [0.931, 1.039] | faster beyond noise | 0.97 |
| indel 64 KiB | 1 | 5 | 0.303 | 0.280 | 1.086 [0.998, 1.103] | 0.973 [0.937, 0.991] | faster beyond noise | 0.77 (1 pair) |
| indel 16 MiB | 4 | 3 | 14.409 | 9.696 | 1.486 [1.441, 1.525] | 1.005 [0.946, 1.017] | faster beyond noise | 1.02 |
| encode 1 MiB | 1 | 5 | 0.340 | 0.337 | 1.003 [0.957, 1.016] | 0.987 [0.888, 1.109] | within noise | 1.01 |
| encode 4 MiB | 1 | 5 | 1.294 | 1.309 | 0.975 [0.935, 1.071] | 1.010 [0.996, 1.071] | within noise | 1.00 |
| encode 4 MiB | 4 | 5 | 0.420 | 0.421 | 0.982 [0.910, 1.017] | 1.007 [0.935, 1.146] | within noise | 0.97 |

63 seed rows (5 × 12 + 3), 126 measured runs: outcome and output identical in every pair, 0 false successes,
every kernel native in both trees, no stale library. Per-seed ratios, dispersion (`seconds_range`), load averages
(1.0–3.0) and absolute RSS are in `e2e-summary.json`; `vs-aa.txt` is the `compare_aa.py` output. Protocol deviations:
none (same seeds, inputs, workers, warm-ups and sampler as `ab-orient`); the run started while the host load was
still decaying from the full test suite (1.0 at start), which if anything penalises the first scenario equally for both
trees since pairs alternate.

## H. Regressions, limitations, unresolved risks

- No scenario is slower beyond noise; the three encode rows (no V12 code path) are within the A/A band.
- `trim_heap` is glibc-specific. On musl or macOS it is a no-op and the worker RSS would again carry the pass-1
  residue into pass 2 (then at most the 09d0600 level, since the gather index fix does not depend on it). Not
  measured on such a platform.
- The 64 KiB row's RSS ratio rests on one sampled pair (decode 0.3 s, sampler 0.5 s); it is reported, not relied on.
- `tests/v12/test_pass2_memory.py::test_trim_heap_is_safe_to_call` checks the call, not a memory effect; the effect is
  evidenced by the bench and the timelines only.
- The Amdahl serial fraction is an estimate (section E); the cause of the measured 8–12 % worker-CPU inflation at
  w4/w8 is hypothesised, not isolated.
- The 09d0600 release commit already labels the tree "12.0.0 (tag v12.0.0)" in `_version.py`, CHANGELOG and README
  although no tag exists and the founder has not approved a release; the release docs' T1/T2/T5 "not met" statements
  were updated in this session's docs commit to the measured session-5 results. Whether the version bump should stay on
  the branch before the release review is the founder's call.
- All results are SIMULATED, one host, one channel family; the 10 MB nanopore-like case and low-coverage robustness
  (notes V12-DESIGN-LOWCOV) remain outside V12 as before.

## I. Exact next steps and release readiness

1. Founder review of this report and of the 09d0600 release-finalize commit (keep, or replace by a release commit
   made after the review). No push, tag or merge has been made.
2. If the founder wants more single-worker margin: the flat `project` → `align_usable` path (section D, ≈ 7 % of w1
   samples), with the same equivalence tests as the gathers.
3. Optional: a memray run with `--follow-fork` on clean-w4 to archive per-allocation evidence of the fixed state
   (tracemalloc and the RSS timeline were sufficient for the root cause).
4. Release readiness: with T1–T7 all PASS, the suite at 4,209/0 failures, sanitizers and fuzz clean, and output
   byte-identical to v11.0.0 on every benchmark seed, build/v12 is ready for the founder's release review. Release
   gates still to run by the release engineer at the final SHA: `vnxdna release check`, packaging install check,
   secret scan, claims audit of the docs.
