# VNX-DNA V5 — Phase 2: native alignment kernel

All channel results are **SIMULATED**: software-generated strands passed through the V4 channel simulator. No DNA was
synthesised or sequenced. Phase 2 is an optimisation phase only. The marker layout, band, scoring, traceback rules,
erasure rules, RS codes, DNA format and nucleotide overhead are all unchanged. Raw numbers are in
`benchmarks/v5/native_alignment/results/*.json`, produced by `benchmarks/v5/native_alignment/bench_native.py`. Every
file carries a provenance block: V4 control commit `a358ae8`, Phase 1 commit `f3a6e75`, CPU, compiler, Python, NumPy,
seeds, input hashes, channel parameters, the kernel source SHA-256 and the loaded library SHA-256.

## 1. Architecture

```
TemplateAligner.project (src/vnxdna/v4/sync.py, V4 API unchanged)
   │ backend = auto | native | reference      ($VNXDNA_ALIGN_BACKEND or the backend= argument; default auto)
   ├── native, input inside the domain ──► vnxdna.v5.native_alignment.align_usable   (ctypes wrapper)
   │                                          └─► vnx_align_batch  (src/vnxdna/v5/native/align.c, plain C11 ABI)
   └── reference, or input outside the domain ─► V4 NumPy _align + _traceback (untouched; the oracle)
```

* **Kernel:** one C file (382 lines) with no Python headers and no dependencies. The DP runs 4 reads at a time in
  SIMD lanes (GCC/Clang vector extensions, no intrinsics). It runs every lane through exactly the scalar recurrence of
  its own read, and lanes never interact. Traceback and projection are scalar.
  The library is reentrant and holds no global state. ctypes releases the GIL during the call.
* **Binding:** `ctypes`. It needs no binding dependency. The Python side owns every buffer: it allocates the outputs,
  keeps the inputs alive across the call, and the C side never retains a pointer.
* **Build:** `setup.py` declares an `optional=True` setuptools `Extension` compiled with `-O3 -std=c11` (no
  `-march`, so the portable x86-64 baseline ISA). `pip install` builds it whenever a C compiler exists. Without one,
  installation still succeeds and the reference runs. This was verified with `CC=/bin/false pip wheel`.
  `python -m vnxdna.v5.native_alignment build` builds an in-place copy for development.
* **Fallback and diagnostics:** `auto` uses the native kernel if the library loads (ABI version checked), otherwise
  the reference. `native` raises if the library is unavailable. `vnx native` prints the active backend, library path,
  load error and domain. `vnx version` includes `alignment_backend` and `native_alignment`. The behaviour never
  changes silently: any kernel error raises `NativeAlignmentError` and is never swallowed.

## 2. Reference contract

`docs/V5_NATIVE_ALIGNMENT_CONTRACT.md` specifies the V4 aligner in normative form. It covers template geometry, the
read view (padding value 5, missing quality 99), row-0 initialisation, the order of the five steps of a DP cell,
tie-breaking (ties go to DIAG over DEL, and DIAG/DEL win ties against INS), the masking order, the final-cost cell,
traceback order and segment marking (with guard), every output field and dtype, the three record kinds (aligned,
aligned-but-not-ok, unaligned), orientation and failure cases. It also defines the **native domain** (§9): uint8 1-D
reads, B ≤ 64, T ≤ 8192, costs in [0, 65536], guard ≤ 1024. Inside the domain, int32 provably cannot overflow:
values stay below INF + 8192·131072 ≈ 1.34·10⁹ < 2³¹. Inputs outside the domain are routed to the reference by
contract.

## 3. Equivalence methodology

Native/reference disagreement on **any field of any read** is a failure. The fields compared are bases, erasure mask,
ok, insertions, deletions, marker mismatches and cost, each with its dtype (`tests/v5/native_support.assert_identical`).

| layer | what | where |
|---|---|---|
| golden | the Phase 1 `projection_sha256` of every stored case | `tests/v5/test_native_alignment_equivalence.py::test_phase1_golden_fingerprints` |
| targeted | substitutions; single/multiple/adjacent insertions and deletions at the start, middle and end; mixed indels; independent corruption of each marker; reverse complements; N calls and out-of-alphabet bytes (5, 200, 255); 9 layouts (67–932 nt; marker periods 8, 24, 32, 256 and a no-marker frame) × bands 0, 1, 3, 6, 12 | same file |
| boundaries | read lengths 0, 1, T−B−1, T−B, T−1, T, T+1, T+B, T+B+1 on bands 0, 2, 6, 64 | same file |
| costs and quality | zero costs (maximal ties), asymmetric costs, guard segments, the domain maximum (65536); min_quality 0–100 with mixed present/absent quality arrays | same file |
| fuzz (suite) | 12 seeds × 4 rounds × 100 reads: random layout, band (0–20), costs (0–9), guard, qualities and edit mix | same file |
| batch independence | a read's result is the same alone, in any batch position and in a ragged final SIMD group | same file |
| fuzz (stress) | 250 reads/round; a random layout, band, costs, quality setting and min_quality each round; random edits, reverse complements, random lengths, N and out-of-alphabet bytes | `benchmarks/v5/native_alignment/stress_fuzz.py` |
| decode | full archive → encode → channel → decode with each backend: identical container | `tests/v5/test_native_alignment.py::test_decode_identical_with_either_backend` |
| ABI | every invalid argument (null pointers, bad offsets, out-of-band reads, out-of-domain band/costs/template, corrupt geometry) returns a defined error code | `tests/v5/test_native_alignment.py` |

Before it times anything, each benchmark case checks that native and reference produce the same projection SHA-256,
and that it equals the Phase 1 golden hash where one exists. A mismatch aborts the case without a timing.

## 4. Golden-test results

All **10 / 10** Phase 1 golden cases are reproduced bit for bit by the native kernel: 178 nt clean and 0.5 %+0.5 %;
313 nt clean, 0.5 % sub, 0.1 %+0.1 %, 0.5 %+0.5 %, 1 %+1 % and mixed-L2; 494 nt clean and 0.5 %+0.5 %. Each case uses
4,096 reads, and the read SHA-256 is asserted equal to Phase 1's, so the inputs did not drift. The 6 cases of the
Phase 2 matrix that Phase 1 did not store are identical to the reference (§8).

## 5. Fuzz-test results

| run | build | rounds | reads | mismatches |
|---|---|---|---|---|
| stress fuzz, seed 99 | production (`-O3`) | 2,000 | 500,000 | **0** |
| stress fuzz, seed 20261003 | gcc ASan + UBSan | 400 | 100,000 | **0** |
| stress fuzz, seed 7 | clang UBSan (trap) | 400 | 100,000 | **0** |
| V5 test suite (golden, targeted, boundary, cost, quality, fuzz) | production, ASan+UBSan, UBSan-trap | — | — | **0** (all pass on all three builds) |

## 6. Sanitizer and memory-safety results

`benchmarks/v5/native_alignment/sanitizers.sh` reproduces all of this. Its output is recorded in
`results/sanitizers.txt`, and the production fuzz run in `results/stress_fuzz.txt`. Both name kernel source SHA-256
`921194fd…`, the committed source.

| check | result |
|---|---|
| gcc 13.3 `-fsanitize=address,undefined -fno-sanitize-recover=all`: V5 suite + 400-round fuzz | clean |
| clang 18.1 `-fsanitize=undefined,integer,bounds,nullability -fsanitize-trap=all` (every integer check, including implicit conversions and unsigned wrap): V5 suite + 400-round fuzz | clean |
| canary: a deliberate ABI misuse (offsets one entry short) under ASan | **ASan fires** (heap-buffer-overflow), so the instrumentation is live |
| `-Wall -Wextra -Werror` (gcc and clang) | clean |
| clang ASan | not run: the clang ASan runtime is not installed on this machine (gcc's is) |

**Audit (manual).** Buffer lengths: every read is bounded by `offsets[k+1] ≤ total_len`, offsets are checked
monotone, and `offsets[n] = total_len`. Every read is checked to lie within T ± B before packing.
Indexing: DP column indices are bounded by construction (`B + col < B + T + B + 2`). Traceback state is re-checked
on every step (`0 ≤ w < W`, `0 ≤ i ≤ T`, `0 ≤ j < width`), and the step count is capped at 4(T+B)+8.
Geometry arrays are range-checked before use. Allocations: sizes are bounded by the domain (DP pointer matrix ≤ 8193
× 129 × 4 B = 4.2 MB), with overflow checks on `n × frame_nt`, and every allocation is checked and freed on every path.
Integer arithmetic: bounded as in §2 and confirmed by the clang integer sanitizer. Ownership: no pointer outlives
the call, and there are no callbacks into Python. Malformed, oversized and zero-length inputs return
`VNX_E_ARG/DOMAIN/READ`, and the wrapper raises `NativeAlignmentError` (tested). **One defect was found and fixed
during the audit:** an empty usable read (possible only when T ≤ B) that had its quality flag set but no quality buffer
was rejected with `VNX_E_ARG` instead of aligned. No real `Layout` can reach it (T ≥ 67 > MAX_BAND); it now has an
ABI-level test.

## 7. Benchmark methodology

* The same harness and inputs as Phase 1: 4,096 reads per case from `phase1_baseline.make_reads`, the same payload
  and channel seeds, band 6, the same three layouts. The read SHA-256 is asserted equal to Phase 1's.
* Both backends run **in the same session on the same machine**, each through `TemplateAligner.project`, so the
  native times include the Python wrapper.
* Every reported time is the **median of 5 repetitions**. Min, max and every repetition are stored, and nothing is
  best-of. Phase 1 reported best-of-3, so the V4 column here is re-measured rather than copied.
* Stage split: for V4, the Phase 1 timed subclass (DP fill = total − traceback). For V5, the kernel's profiled entry
  point (`vnx_align_batch_profiled`: pack, DP, traceback, projection, plus the wrapper time). It is checked to return
  identical arrays.
* Peak memory: each backend in a fresh spawned process. `malloc_trim(0)` first, then reset the kernel's VmHWM
  (`/proc/self/clear_refs`), and report VmHWM − VmRSS around one `project()` on all reads. A tracemalloc peak of a
  second call is recorded as a cross-check.
* Machine: Intel Xeon Gold 6240 (8 logical CPUs), Linux 6.8, Python 3.12.3, NumPy 2.5.3, gcc 13.3.0. The load
  average was ≈ 1 at the start and nothing else ran during the benchmarks.

## 8. V4 vs V5 alignment results (4,096 reads, band 6, 1 core)

| layout | channel | V4 reads/s | V5 reads/s | V4 µs/read | V5 µs/read | V4 Mbases/s | V5 Mbases/s | V4 DP s | V4 traceback s | V5 DP s | V5 traceback s | speed-up | output |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 178 nt | clean | 16,428 | 157,816 | 60.9 | 6.34 | 2.92 | 28.1 | 0.179 | 0.071 | 0.0152 | 0.0045 | ×9.61 | = reference = golden |
| 178 nt | 0.5 % sub | 16,300 | 157,537 | 61.3 | 6.35 | 2.90 | 28.0 | 0.179 | 0.072 | 0.0152 | 0.0045 | ×9.66 | = reference |
| 178 nt | 0.1 %+0.1 % | 15,681 | 158,621 | 63.8 | 6.30 | 2.79 | 28.2 | 0.180 | 0.081 | 0.0152 | 0.0045 | ×10.12 | = reference |
| 178 nt | 0.5 %+0.5 % | 15,045 | 155,719 | 66.5 | 6.42 | 2.68 | 27.7 | 0.185 | 0.087 | 0.0152 | 0.0046 | ×10.35 | = reference = golden |
| 178 nt | 1 %+1 % | 14,974 | 152,616 | 66.8 | 6.55 | 2.67 | 27.2 | 0.180 | 0.094 | 0.0155 | 0.0048 | ×10.19 | = reference |
| **313 nt** | clean | 8,739 | 90,310 | 114.4 | 11.07 | 2.73 | 28.3 | 0.331 | 0.138 | 0.0272 | 0.0082 | **×10.33** | = reference = golden |
| 313 nt | 0.5 % sub | 9,280 | 93,479 | 107.8 | 10.70 | 2.90 | 29.3 | 0.312 | 0.129 | 0.0267 | 0.0078 | ×10.07 | = reference = golden |
| 313 nt | 0.1 %+0.1 % | 8,901 | 93,704 | 112.4 | 10.67 | 2.79 | 29.3 | 0.314 | 0.146 | 0.0265 | 0.0078 | ×10.53 | = reference = golden |
| 313 nt | 0.5 %+0.5 % | 8,520 | 93,112 | 117.4 | 10.74 | 2.67 | 29.1 | 0.323 | 0.158 | 0.0265 | 0.0080 | ×10.93 | = reference = golden |
| 313 nt | 1 %+1 % | 8,708 | 94,443 | 114.8 | 10.59 | 2.73 | 29.6 | 0.305 | 0.164 | 0.0263 | 0.0080 | ×10.85 | = reference = golden |
| 494 nt | clean | 5,203 | 58,539 | 192.2 | 17.08 | 2.57 | 28.9 | 0.528 | 0.258 | 0.0432 | 0.0127 | ×11.25 | = reference = golden |
| 494 nt | 0.5 % sub | 5,673 | 59,837 | 176.3 | 16.71 | 2.80 | 29.6 | 0.504 | 0.218 | 0.0427 | 0.0125 | ×10.55 | = reference |
| 494 nt | 0.1 %+0.1 % | 5,399 | 60,264 | 185.2 | 16.59 | 2.67 | 29.8 | 0.510 | 0.248 | 0.0421 | 0.0123 | ×11.16 | = reference |
| 494 nt | 0.5 %+0.5 % | 5,423 | 60,585 | 184.4 | 16.51 | 2.68 | 29.9 | 0.484 | 0.272 | 0.0417 | 0.0124 | ×11.17 | = reference = golden |
| 494 nt | 1 %+1 % | 5,517 | 61,445 | 181.3 | 16.27 | 2.73 | 30.4 | 0.471 | 0.272 | 0.0411 | 0.0124 | ×11.14 | = reference |

**Speed-up: ×9.6 – ×11.3** (median-based), **≈ 28–30 Mbases/s against 2.6–2.9** for V4. Throughput per base is flat
across read lengths, as in V4. Where the time goes at 313 nt clean (45.4 ms for 4,096 reads): DP 27.2 ms (60 %),
traceback 8.2 ms (18 %), projection 4.9 ms (11 %), packing 1.0 ms, Python wrapper 2.2 ms, ctypes/other ≈ 1.9 ms.

**Error-rate scaling** (`scaling.json`, 313 nt, insertions = deletions):

| ins + del | V4 µs/read | V4 DP s | V4 traceback s | V5 µs/read | V5 DP s | V5 traceback s | speed-up |
|---|---|---|---|---|---|---|---|
| 0 % | 110.6 | 0.317 | 0.136 | 10.86 | 0.0269 | 0.0079 | ×10.18 |
| 0.1 % + 0.1 % | 116.5 | 0.323 | 0.154 | 10.94 | 0.0272 | 0.0080 | ×10.66 |
| 0.5 % + 0.5 % | 121.5 | 0.332 | 0.167 | 10.94 | 0.0269 | 0.0081 | ×11.11 |
| 1 % + 1 % | 118.5 | 0.313 | 0.173 | 10.79 | 0.0266 | 0.0082 | ×10.98 |
| 2 % + 2 % | 114.0 | 0.298 | 0.169 | 10.22 | 0.0252 | 0.0078 | ×11.15 |

V4 traceback grows with the indel rate (+27 % from 0 % to 1 %+1 %) because each extra step is a Python iteration.
The native traceback is essentially flat (+4 %): a step costs nanoseconds, and the path length is ≈ T either way. At
2 %+2 %, 7 % of reads fall outside the band and are not aligned by either backend, which lowers both per-read times.

**Peak memory** (`memory.json`, peak growth during one `project()` on 4,096 mixed-L2 reads, fresh process):

| layout | V4 RSS growth | V5 RSS growth | V4 tracemalloc | V5 tracemalloc |
|---|---|---|---|---|
| 178 nt | 11.8 MB | 3.7 MB | 10.9 MB | 3.6 MB |
| 313 nt | 19.2 MB | 6.0 MB | 18.3 MB | 6.0 MB |
| 494 nt | 29.2 MB | 9.3 MB | 28.1 MB | 9.2 MB |

V5 peak memory is ≈ ⅓ of V4's and consists almost entirely of the output arrays and the packed input. The kernel's
own scratch is 13–35 KB per call; V4's pointer matrix is 4.8–13.2 MB per 2,048-read chunk.

## 9. Worker scaling (`workers.json`)

The Phase 1 workload: 32,768 mixed-L2 reads, 2,048-read chunks, a process pool, pool start-up included, best of 2
(as in Phase 1).

| workers | V4 reads/s | V4 speed-up | V4 efficiency | V5 reads/s | V5 speed-up | V5 efficiency | V5 / V4 |
|---|---|---|---|---|---|---|---|
| 1 | 8,209 | 1.00 | 1.00 | 80,646 | 1.00 | 1.00 | ×9.8 |
| 2 | 15,163 | 1.85 | 0.93 | 122,178 | 1.51 | 0.76 | ×8.1 |
| 4 | 28,387 | 3.46 | 0.87 | 181,559 | 2.25 | 0.56 | ×6.4 |
| 8 | 49,585 | 6.04 | 0.76 | 183,816 | 2.28 | 0.29 | ×3.7 |

The result SHA-256 (`8913c84b…`) is **identical for both backends and every worker count**; the kernel adds no
nondeterminism. V5 scales poorly here because the whole workload takes 0.41 s on one worker. Pool start-up and
pickling of reads and results are a fixed cost (the entire 8-worker run takes 0.18 s), which the native kernel no
longer hides. That is
Amdahl in the harness, not contention in the kernel: the kernel has no shared state. On real decodes the per-process
work is larger (§10).

## 10. End-to-end decode (`e2e.json`, `profile.json`)

4 MiB random input (seed 42), v4-balanced, EXP-0011 channel (0.2 % sub, 0.05 % ins, 0.05 % del, 2 % dropout,
Poisson coverage 3, seed 1011). Each run is a fresh process. Every run returns SUCCESS with output SHA-256 =
input SHA-256 (`d8658297…`), and the decoded containers are identical between backends.

| run | V4 decode s | V5 decode s | speed-up | V4 peak RSS | V5 peak RSS |
|---|---|---|---|---|---|
| clean, coverage 1, 1 worker | 1.61 | 1.63 | ×0.99 (aligner not used) | 167 MB | 167 MB |
| EXP-0011, 1 worker | 25.50 | **10.20** | **×2.50** | 167 MB | 167 MB |
| EXP-0011, 2 workers | 13.20 | 5.72 | ×2.31 | 167 MB | 167 MB |
| EXP-0011, 4 workers | 7.69 | 3.99 | ×1.93 | 171 MB | 167 MB |
| EXP-0011, 8 workers | 5.58 | **4.01** | ×1.39 | 194 MB | 178 MB |

The Phase 1 control numbers were 24.96 s (1 worker) and 5.44 s (8 workers); the V4 re-measurement agrees within
2–3 %. With the native kernel, 8 workers are no faster than 4 at this input size, because the serial stages
(parsing, pass 2) now dominate the wall time.

**Re-profile** (1 worker, cProfile, cumulative time of the owning function; the unprofiled time is the median of 3):

| stage | V4 s | V4 share | V5 s | V5 share |
|---|---|---|---|---|
| marker alignment (`TemplateAligner.project`) | 17.18 | 57.7 % | **1.32** | **9.6 %** |
| read parsing (`reads.iter_reads`) | 4.28 | 14.4 % | 4.23 | **30.7 %** |
| inner RS decode (`rs_fast.decode_batch`) | 4.11 | 13.8 % | 4.09 | **29.7 %** |
| pass 2 (consensus, snapping, outer, verify) | 1.69 | 5.7 % | 1.68 | 12.2 % |
| orientation pre-pass | 0.22 | 0.8 % | 0.22 | 1.6 % |
| everything else | 2.28 | 7.7 % | 2.24 | 16.3 % |
| total (profiled) | 29.76 | 100 % | 13.78 | 100 % |
| **unprofiled decode (median of 3)** | **24.98** | | **10.33** | |

Every non-alignment stage is unchanged to within 1 %, so the whole improvement comes from the aligner.

## 11. Amdahl analysis and the new bottleneck

* **Inside the decoder,** marker alignment fell from 17.18 s to 1.32 s under cProfile (×13.0). That is more than
  the ×10.3 of the unprofiled micro-benchmark, because cProfile adds a cost to every Python call and the V4 aligner
  makes far more of them.
* **End to end,** single-worker noisy decode went from 24.98 s to 10.33 s (unprofiled medians, **×2.42**).
  Amdahl's law with the profiled share (57.7 %) and ×13 predicts ×2.14. The observed figure is higher because
  cProfile's per-call overhead inflates the Python-heavy V4 aligner more than the other stages. In unprofiled terms,
  V4 alignment took ≈ 15.6 s of the 24.98 s (≈ 63 %).
* **Remaining ceiling from alignment:** it is now 9.6 % of decode. Removing it *entirely* would give at most
  1 / (1 − 0.096) = **×1.11** more (10.3 s → ≈ 9.3 s). Further aligner work has a low ceiling.
* **New bottleneck: read parsing (30.7 %) and inner RS decoding (29.7 %), effectively tied,** together 60 % of
  decode. Eliminating both would bound the speed-up at 1 / 0.40 = ×2.5. Halving both gives ≈ ×1.4. Pass 2 (12 %) and
  "everything else" (16 %) come next.
* For multi-worker decode, the serial share is now the limiter: 4 → 8 workers gives nothing at 4 MiB.

Phase 3 is defined as smart indel recovery (RQ1), not more speed. These numbers show that Phase 3 can afford more
alignment work per read: alignment is now 1.3 s of a 10.3 s decode, so even a 3× more expensive recovery pass
would raise decode time by only ≈ 25 %. If throughput becomes a goal again, the targets are FASTQ parsing and the
batched inner RS decoder.

## 12. Regressions

| gate | result |
|---|---|
| V4/V3 suite (`tests/`, excluding `tests/v5`) | **723 / 723 pass**: the V4 tests now run with the native backend active (auto) |
| V5 tests (`tests/v5`) | **116 / 116 pass** (production build); also pass on the ASan+UBSan and UBSan-trap builds |
| full suite (`pytest`, 839 tests) | **839 / 839 pass**: 836 in one run (4 min 51 s). The 3 README CLI tests skip unless `vnx-dna` is on `PATH`; they pass with the venv on `PATH` |
| native/reference equivalence | 0 mismatches (golden, targeted, boundary, 700,000 fuzz reads) |
| security / sanitizers | 0 findings |
| `ruff check` | clean |

V4 source changes are limited to two hooks. `TemplateAligner.__init__` gains an optional `backend=` argument, and
`project()` dispatches in-domain batches to the native kernel. `_align`/`_traceback` are byte-identical to `v4.0.0`.
The CLI gains `vnx native` and two fields in `vnx version`. No format, codec or layout change; no V4 history rewritten.

## 13. Limitations

* **SIMULATED data only.** Every input comes from the V4 channel simulator.
* **One machine.** All timings are from one 8-CPU Xeon VM; other CPUs will differ, especially in SIMD lowering. The
  production build targets baseline x86-64 (SSE2). 4 lanes were chosen because GCC lowers 8-lane vectors poorly
  without AVX. `-march=native` builds were measured during development but are not shipped. Non-x86 targets compile
  through the generic vector extensions but were not benchmarked or tested here.
* **Compiler required for acceleration.** Without a C compiler at install time, VNX-DNA runs the reference at V4
  speed. This is reported by `vnx native`.
* **Domain limits.** B ≤ 64, T ≤ 8192, costs ≤ 65536. Configurations outside them use the reference at V4 speed.
  No V4 profile comes close to these limits.
* **Worker scaling** of the alignment-only benchmark is limited by pool start-up and pickling, not the kernel. End-to-end
  multi-worker decode is now limited by serial stages.
* **clang ASan** could not be run (runtime not installed). gcc ASan and clang UBSan (trap) both ran.
* The benchmark results were produced from the working tree (provenance `f3a6e75-dirty`) before the commit that
  contains them. The `source_sha256` in each file identifies the exact kernel source, which is the committed one.
