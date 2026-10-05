# MemorySanitizer on the native kernels (V7 Phase G)

Status: **RUN, clean.** All three native C kernels (`src/vnxdna/v5/native/align.c`, `src/vnxdna/v6/native/reads.c`,
`src/vnxdna/v6/native/rs.c`) were built with clang MemorySanitizer and run on golden vectors, the libFuzzer harnesses'
seed corpora and regressions, seeded random inputs and coverage-guided libFuzzer campaigns. MSan reported nothing and
every output matched the Python reference. This closes the "MSan: NOT RUN" item of `docs/V6_COMPLETION_REPORT.md` §7
and `docs/V6_DEFERRED.md` §5 **for the kernels**. MSan of the kernels running inside CPython was not done; the reason
is below.

## 1. Why not inside CPython

MSan needs every instruction that writes memory in the process to be instrumented. Memory written by uninstrumented
code stays "uninitialised" in the shadow and causes false reports. There are two blockers:

1. **No shared runtime.** clang 18 ships the MSan runtime only as a static archive (`libclang_rt.msan-x86_64.a`;
   there is no `libclang_rt.msan-x86_64.so`, unlike ASan's `libclang_rt.asan-x86_64.so`). The runtime has to be
   linked into the main executable. An MSan-built kernel `.so` loaded by the stock interpreter fails at `dlopen`
   (checked on this host: `ctypes.CDLL(...)` gives `undefined symbol: __msan_memcpy`). `LD_PRELOAD` of a runtime is
   not an option because no shared runtime exists.
2. **Everything else in the process.** Even with an MSan-built `python3`, the kernels read buffers that NumPy
   allocates and fills, and NumPy's compiled loops plus its bundled OpenBLAS (C, Fortran and hand-written assembly)
   would need MSan builds too. The ctypes/libffi call path would also need one. Without all of that, a clean run would
   have been noise-suppressed rather than meaningful.

The kernels have no Python dependency: each is a plain C11 file behind a ctypes ABI. So the realistic route is to
`#include` each kernel into small C programs that need neither CPython nor NumPy. Python is used only to *write* input
and expected-output files, outside the sanitized process.

## 2. What the gate runs (`tools/msan.sh`, CI job `msan`)

Build flags: `clang -O1 -g -fsanitize=memory -fsanitize-memory-track-origins=2 -fno-sanitize-recover=memory
-fno-omit-frame-pointer`. clang ≥ 16 also checks parameters and return values eagerly.

| Step | Program | What it exercises |
|---|---|---|
| canary | `tools/msan/canary.c` | The RS kernel decodes a codeword that was never initialised. MSan must stop it with exit 86 inside `syn_scalar`, otherwise the gate fails. This shows the kernel code is instrumented. |
| golden replay | `tools/msan/replay_{align,rs,reads}.c` on files from `tools/msan/make_vectors.py` | Every exported entry point: `vnx_align_batch`, `_path`, `_profiled`; `vnx_rs_decode_batch` at auto and every level the CPU supports (scalar, AVX2, AVX-512), in place and out of place; `vnx_reads_init`, `_feed` and `_new_batch` with growing buffers and 3-record batches. Output buffers are `malloc`'d and never initialised, and every output byte is compared with the expected value, so an output byte the kernel failed to write is reported by MSan. |
| harnesses | `fuzz/native/*_fuzz.c` + `tools/msan/standalone_main.c` | The libFuzzer harnesses, with their own invariants, on the seed corpus, the regression inputs and `MSAN_RANDOM_CASES` splitmix64-seeded random inputs (lengths 0-4096, some up to 65536). |
| libFuzzer | `-fsanitize=fuzzer,memory` | Optional coverage-guided campaigns of `MSAN_FUZZ_SECS` per target (CI: 30 s). |

Expected outputs come from the reference, which is the specification:

- **align**: the 10 Phase 1 golden read sets (`benchmarks/v5/baseline-v4/align.json`). The generator re-checks each
  set's reads SHA-256 and the reference projection SHA-256. On top of those it adds randomized rounds drawn like
  `benchmarks/v5/native_alignment/stress_fuzz.py`. Expected arrays come from `TemplateAligner._align` (NumPy);
  `readpos` comes from the uninstrumented native library.
- **rs**: the 162 committed golden vectors (`tests/v6/native/native_rs_golden.json`) plus randomized batches drawn
  like `benchmarks/v6/native_rs/stress_fuzz.py`, with expected outputs from `vnxdna.v4.rs_fast.decode_batch`.
- **reads**: seeded files from `tests/v6/native/native_reads_support.fuzz_case`, plus 4 clean simulated FASTQ files at
  the production block size, checked against `vnxdna.v4.reads`. Where the reference raises, the native parser must
  return an error code.

`tests/unit/test_msan_tooling.py` builds the replays without a sanitizer in the normal suite. It checks that the file
format agrees on both sides and that a single flipped expected byte makes the replay fail, so the comparison cannot
pass vacuously.

## 3. Results

Run on 2026-10-05 on this host (Intel Xeon Gold 6240, AVX-512 available, so RS `levels_mask` 14 means scalar, AVX2 and
AVX-512 all ran). Toolchain: Ubuntu clang 18.1.3. The tools were as committed in `e54fa50`; the run was started from the
working tree just before that commit, so the log header shows the previous HEAD `810d35e`. The kernels are unchanged
between the two. Full log: `benchmarks/v7/msan/msan-20261005-long.txt`. Settings: `ALIGN_ROUNDS=1000 RS_ROUNDS=5000
READS_CASES=20000 MSAN_RANDOM_CASES=200000 MSAN_FUZZ_SECS=300`.

| Step | Volume | MSan reports | Mismatches |
|---|---|---|---|
| canary | 1 uninitialised decode | 1 (expected) | — |
| replay align | 1,010 records (10 golden sets + 1,000 rounds), 140,399 reads, 3,030 kernel calls | 0 | 0 |
| replay rs | 5,162 records (162 golden + 5,000 batches), 369,262 words, 41,296 kernel calls | 0 | 0 |
| replay reads | 17,062 files (5,708 parsed, 11,354 reference errors), 86.6 MB, 2 batching modes each | 0 | 0 |
| harness align / reads / rs | 12 / 20 / 10 seed and regression files + 200,000 random inputs each (510.6 MB) | 0 | invariants held |
| libFuzzer+MSan align / reads / rs | 300 s each: 357,309 / 2,518,751 / 97,042 executions | 0 | 0 crashes |

A CI-sized run (the defaults, `MSAN_FUZZ_SECS=30` as in the CI job) is logged in
`benchmarks/v7/msan/msan-20261005-ci-size.txt`.

## 4. Limits

- The replays run the kernels as compiled into one C program. The ctypes marshalling in `vnxdna/native/*.py` is not
  covered by MSan. The ASan/UBSan job and valgrind cover that path (`tools/sanitizers.sh`).
- MSan finds reads of uninitialised memory. It does not find out-of-bounds accesses (ASan) or undefined behaviour
  (UBSan); those gates are unchanged.
- On a CPU without AVX-512 (typical CI runners) the AVX-512 path is not executed. On this host it was.
- A clean run means these inputs found nothing. It is not a proof. x86-64 only, as before.
