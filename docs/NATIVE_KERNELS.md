# Native kernels: installation, backend selection and diagnostics

VNX-DNA has four optional C kernels. Each one is an accelerator for a Python/NumPy reference implementation, which
remains the specification: the native output is bit-identical (golden hashes and randomized equivalence fuzzing in
`tests/v5/test_native_alignment*.py`, `tests/v6/native/` and `tests/v7/test_native_cluster.py`). Without a kernel, VNX-DNA runs the reference and gives
the same results more slowly.

| kernel | module | C source | packaged extension | reference |
|---|---|---|---|---|
| `align`: V5 marker-template aligner | `vnxdna.v5.native_alignment` | `src/vnxdna/v5/native/align.c` | `vnxdna/v5/_vnx_align*.so` | `vnxdna.v4.sync` (NumPy) |
| `reads`: V6 FASTQ/FASTA read parser | `vnxdna.v6.native_reads` | `src/vnxdna/v6/native/reads.c` | `vnxdna/v6/_vnx_reads*.so` | `vnxdna.v4.reads` |
| `rs`: V6 inner Reed-Solomon (GF(256)) decoder | `vnxdna.v6.native_rs` | `src/vnxdna/v6/native/rs.c` | `vnxdna/v6/_vnx_rs*.so` | `vnxdna.v4.rs_fast` (NumPy) |
| `cluster`: V7 read clustering (sketch, candidate pairs, banded edit distance, verification) and forward-backward consensus calls | `vnxdna.native.cluster` | `src/vnxdna/native/c/cluster.c` | `vnxdna/_vnx_cluster*.so` | `vnxdna.recovery.cluster` (NumPy; the `*_reference` functions) |

## Installation

`pip install .` (and `pip wheel .`) compiles all four kernels as *optional* extensions (`setup.py`; sources and flags
in `src/vnxdna/_native_build.py`). If no C compiler works, or one kernel fails to compile, the installation still
succeeds and the affected kernels use the reference. Check the result after installing:

```bash
python -m vnxdna.native                     # JSON: backend, SIMD level, library, origin, ABI, load error per kernel
python -m vnxdna.native --require-native    # same, exit status 1 unless every kernel runs natively
vnx native                                  # the same per-kernel data under "kernels" (plus the V5 aligner fields)
```

From Python: `vnxdna.native_status()`. Every decode report records the backends that ran in `native_backends`
(backend, ABI version, library origin, and the RS SIMD level; no paths), and so does the `decode_start` event of
`vnx decode --events`.

Build flags: `-O3 -std=c11 -Wall -Wextra`, the same for `pip install` and the explicit builds below (which add
`-fPIC -shared`). There is no `-march`: the RS decoder compiles its AVX2 and AVX-512BW paths (the cluster kernel its AVX2
forward-backward path) with per-function target attributes and chooses a level at run time from cpuid + xgetbv, so one library or wheel runs on any x86-64 CPU.
`-Werror` is used only by *strict* builds (CI and the sanitizer scripts), so a warning from a newer compiler cannot
disable a kernel in an installation.

Explicit (in-place) builds for development, e.g. after changing a C file in an editable checkout:

```bash
python -m vnxdna.v5.native_alignment build [--strict]   # src/vnxdna/v5/native/libvnx_align.so
python -m vnxdna.v6.native_reads build [--strict]       # src/vnxdna/v6/native/libvnx_reads.so
python -m vnxdna.v6.native_rs build [--strict]          # src/vnxdna/v6/native/libvnx_rs.so
python -m vnxdna.native.cluster build [--strict]        # src/vnxdna/native/c/libvnx_cluster.so
```

Lookup order of every loader: the explicit `*_LIB` path, then the packaged extension, then the in-place library. A
library whose ABI version differs from the module's is skipped (and named in `load_error`). The ABI number does not
detect an in-place library that is older than a changed C source; `native_status()` flags that case with
`library_older_than_source` (in-place libraries only) so it can be rebuilt.

## Environment variables

| variable | values | effect |
|---|---|---|
| `VNXDNA_ALIGN_BACKEND` | `auto` (default), `native`, `reference` | aligner: `auto` = native if the library loads, else the reference; `native` raises if it does not load |
| `VNXDNA_NATIVE_LIB` | path | aligner library to load first (e.g. a sanitizer build) |
| `VNXDNA_READS_BACKEND` | `auto` (default), `native`, `reference` | read parser, same rules; `auto` logs one warning when it falls back |
| `VNXDNA_READS_LIB` | path | read-parser library to load first |
| `VNXDNA_RS_BACKEND` | `auto` (default), `native`, `avx512`, `avx2`, `scalar`, `reference` | inner RS decoder: `auto`/`native` = AVX2 if the CPU and OS support it, else scalar (AVX-512 only when forced: it measured 0.94x of AVX2 on the development host, `benchmarks/v6/native_rs/results/bench.json`); a forced level the CPU lacks degrades to the next lower level with one warning; `native` raises if the library does not load |
| `VNXDNA_RS_LIB` | path | RS library to load first |
| `VNXDNA_CLUSTER_BACKEND` | `auto` (default), `native`, `reference` | read clustering (only used with `--read-clustering fallback`): same rules as the read parser; inputs outside the kernel's domain (e.g. costs above 1024, codes >= 8 where a reverse complement is needed) run the reference, which is part of the contract |
| `VNXDNA_CLUSTER_LIB` | path | cluster library to load first |
| `VNXDNA_CLUSTER_THREADS` | 1-64 (default 1) | threads of the forward-backward kernel within one decode process; results do not depend on it. Memory scales with it: up to about 0.5 GB per call at the domain extremes (template 8192, band 512), times this count (`benchmarks/v7/native_cluster/README.md`) |
| `VNX_RS_REFERENCE` | `1` | the inner RS decoder (`vnxdna.v4.codecs.InnerRS.decode`) uses the V3 decoder `vnxdna.ecc.rs_batch` and bypasses `vnxdna.v6.native_rs` and `VNXDNA_RS_BACKEND` entirely. Read once when `vnxdna.v4.codecs` is imported. For reference comparisons and debugging; results are identical (tested), only slower. `native_status()` reports the RS backend as `reference` with `requested: "VNX_RS_REFERENCE=1"` |
| `VNXDNA_NATIVE_STRICT` | `1` | explicit builds add `-Werror` (same as `build --strict`) |
| `CC` | compiler | compiler for the explicit builds (and for `pip install`, via setuptools) |

Worker processes inherit the environment, so the backends recorded in a report are the ones the workers used.

## Test-only hook: `vnx_rs_restrict_levels`

`rs.c` exports `vnx_rs_restrict_levels(mask)`, reachable from Python only through
`vnxdna.v6.native_rs._restrict_levels_for_tests(names)`. It makes the library behave as if the CPU supported only the
given SIMD levels so that the tests can exercise every level and the fallbacks on one machine. The mask is
**process-wide**: it affects every later decode in the process, in all threads, until it is reset with `None`.
Production code must never call it; `tests/v6/native/test_native_status.py` checks that no module except
`native_rs.py` refers to it, and `native_status()` reports `levels_restricted: true` while a restriction is active.

## Docker

The `Dockerfile` builds a wheel with the four kernels in a separate stage (with gcc) and installs it into the slim
runtime image, which carries no compiler. The image build runs `python -m vnxdna.native --require-native` and fails if
a kernel did not compile. `docker run --rm --entrypoint python vnx-dna -m vnxdna.native` shows the backends.
