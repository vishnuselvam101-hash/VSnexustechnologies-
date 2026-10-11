# VNX-DNA hybrid execution architecture

Branch `arch/hybrid-native` (from `cc42747`, the V10 work tip). Compatibility and correctness reference: v9.0.0
(`e76e24b`). This document is the language-selection policy and the contract of the native execution layer. Measured
results are in the final comparison report, never here.

## 1. Layers

```
  SDK / CLI / API (Python)        vnxdna.sdk, vnxdna.api, vnxdna.cli, vnxdna.commands
  Pipeline orchestration (Python) vnxdna.pipeline (encode, decode, schedules), vnxdna.recovery (stages, budgets)
  Scientific models (Python)      vnxdna.simulation (SIMULATED channels), vnxdna.simulation.fit, experiments/
  References (Python/NumPy)       the specification of every native kernel; also the fallback backend
  Native execution layer (C11)    vnxdna.native.{align, reads, rs, cluster, strand} via ctypes
  Native execution layer (Rust)   vnxdna.native.integrity (crate native/rust/integrity, C ABI) via ctypes
  Native execution layer (C++20)  vnxdna.native.outerlist (native/cpp/outerlist.cpp, C ABI) via ctypes
  Web layer (TypeScript)          web/: typed contract clients, event-log follower, static dashboard (no DNA computation)
  Third-party native code         NumPy, hashlib (SHA-256), cryptography (AES-256-GCM, OpenSSL), zstandard, zlib
```

Every native kernel accelerates one Python/NumPy reference that remains the specification. The reference also runs
when a kernel is missing or deselected, and `vnxdna.native_status()` reports which implementation runs.

## 2. Language-selection policy

A component moves out of Python only when a profile of the integrated pipeline shows it as a hot path, the work can be
batched at a subsystem boundary (thousands of reads, strands or cells per call, not one per base), and a bit-exact
reference exists to test it against. The language follows from what the component needs:

| language | used for | rule |
|---|---|---|
| Python | SDK, CLI, configuration, pipeline orchestration, error handling and reporting, archive/container format logic, scientific models, experiments, references | default; stays wherever profiles do not show a hot path |
| NumPy (C under the hood) | vectorised table operations: outer Cauchy RS encode/decode, CRC rows, inner RS parity reference, constraint reference | used where a whole-batch array expression already runs at native speed |
| C11 | stable, flat-array CPU kernels: marker alignment, read parsing, inner RS decoding, read clustering, banded DP, forward-backward, polish/trial costs, strand building | one authoritative native implementation per algorithm, loaded through ctypes, no Python C API |
| Rust | integrity and validation of untrusted data: batched frame CRC-32 (`native/rust/integrity`) | checked arithmetic before any slice exists, scoped threads with disjoint outputs, panics caught at the ABI boundary; standard library only (builds offline). Not used to duplicate a working C kernel: the fuzzed, sanitized C parsers and DP kernels stay in C. Next Rust candidates: a newly designed untrusted-input parser |
| C++20 | outer-code list decoding of short rows (`native/cpp/outerlist.cpp`, recovery on nanopore-like reads) | a combinatorial search with small dense GF(256) linear algebra per subset: std::vector-owned workspaces and exception-safe cleanup are simpler and safer than manual C allocation; every entry point is `noexcept` and maps bad_alloc/any exception to an error code. Not used to rewrite the existing C kernels (flat arrays and loops, already fuzzed and sanitized) |
| TypeScript | web layer (`web/`): typed clients of the published JSON contracts, decode-event follower, static dashboard | no DNA encoding, alignment or decoding logic; WebAssembly only if browser-side computation becomes a requirement. No network service yet (see §9) |
| Java | not used | no JVM integration requirement exists; adding a JVM would be a runtime with no consumer |
| Go, other | not used | no subsystem needs their runtime |

Not a language-count target: a component stays in Python when moving it would not change end-to-end time.

## 3. Native execution layer contract

All kernels follow the same contract (`native/c/*.c`, `v5/native/align.c`, `v6/native/{reads,rs}.c`):

* **Binding.** C11 and Rust shared libraries with a C ABI, loaded with `ctypes` (one binding mechanism for the whole
  layer; ctypes releases the GIL during a call, so kernel threads run in parallel). Rust exports `extern "C"` functions
  instead of a PyO3 module, so it uses the same loader, ABI check, backend selection and sanitizer/test tooling as the
  C kernels and needs no extra Python build dependency (maturin). Rust kernels catch panics (`catch_unwind`) and return
  an error code; no panic or exception crosses the boundary.
* **Versioned ABI.** Every library exports `<prefix>_abi_version()`; a library with a different ABI is skipped and
  named in `load_error`. ABI bumps on any signature or semantic change (cluster ABI 5 added `vnx_cl_polish_deltas`;
  strand ABI 1).
* **Ownership.** The caller (Python) allocates every input and output buffer (NumPy arrays, C-contiguous, explicit dtype)
  and passes its capacity; a kernel never keeps a pointer after it returns and never frees caller memory. Scratch memory
  is allocated and freed inside the call (or in an opaque handle with an explicit `free` call where a kernel needs
  state across calls, e.g. the cluster candidate step).
* **Validation.** Every length, offset, capacity and count is range-checked in C before use; products are formed in
  `int64_t` after the range checks. Invalid arguments return `-1` (`EARG`), allocation failure `-2` (`ENOMEM`); the
  binding raises a typed error. Nothing is clamped silently.
* **Determinism and thread safety.** No global mutable state in reusable kernels apart from one-time CPU feature
  detection and the RS decoder's test-only SIMD restriction hook (see NATIVE_KERNELS.md). Disjoint row/read ranges can run concurrently;
  results never depend on the thread count, the split points or the SIMD level (tests check all three).
* **Backends.** `VNXDNA_<KERNEL>_BACKEND=auto|native|reference` (`auto` = native if it loads, else the reference with
  one logged warning; `native` raises if unavailable); `VNXDNA_<KERNEL>_LIB` loads an explicit library first (sanitizer
  builds). `python -m vnxdna.native` prints the backend, ABI, SIMD level and library origin of every kernel, and every
  decode report records them in `native_backends`.
* **SIMD.** No `-march`. Kernels with SIMD paths (RS decoder: scalar/AVX2/AVX-512BW; cluster forward-backward and polish:
  baseline/AVX2) select the level at run time from cpuid + xgetbv, so one wheel runs on any x86-64 CPU.
* **New kernels** use `vnxdna.native._loader.Kernel` (C), `CppKernel` (C++20) or `RustKernel` (Rust, built with cargo) — shared lookup
  order, ABI check, backend selection, status and build; the four older kernels keep their own loaders with the same
  contract.

## 4. Encoding pipeline

| stage | implementation |
|---|---|
| input ingestion, chunking, compression (zstd), encryption (AES-256-GCM), integrity metadata (SHA-256, Merkle) | Python orchestration over native libraries (zstandard, cryptography/OpenSSL, hashlib) |
| framing, payload partitioning, outer Cauchy RS parity | NumPy (`codec/cauchy.py`) |
| frame header | NumPy (`dnaenc/frame4.plain_rows`) |
| frame CRC-32 rows | **Rust**, `native/rust/integrity` (`vnx_ri_crc32_rows`); reference `core.crc.crc32_rows` |
| scrambler variant search, inner RS parity, 2-bit mapping, sync markers, constraint screen | **C**, `native/c/strand.c` (`vnx_st_build`); reference `frame4._build_reference` |
| superblocks, strand records, FASTA output, verification | Python |

Encode groups run in worker processes (`performance` profiles); the strand kernel runs single-threaded inside each
worker. The scrambler search keeps the reference order (smallest passing variant), so the strands are the reference's
(checked against v9.0.0 in the final validation).

## 5. Decoding pipeline

| stage | implementation |
|---|---|
| FASTQ/FASTA parsing and read validation | **C**, `v6/native/reads.c` |
| marker alignment, orientation, indel-aware coordinates | **C**, `v5/native/align.c`; recovery strategies in Python (`recovery/`) |
| read clustering: sketches, candidate pairs, banded edit distance, verification | **C**, `native/c/cluster.c` |
| full-template consensus: forward-backward calls, polish edit costs (fused per candidate, ABI 5), trial costs | **C**, `native/c/cluster.c`; control flow in `recovery/cluster/consensus.py` |
| inner RS decoding (errors + erasures) | **C**, `v6/native/rs.c` |
| CRC-32 check | **Rust**, `native/rust/integrity` |
| frame/address recovery (vectorised address snapping), missing-data detection | NumPy/Python |
| outer Cauchy RS decoding, pass-2 recovery | NumPy/Python (`codec/cauchy.py`, `codec/outer.py`) |
| short-row recovery from failed consensus frames (opt-in `outer_list`, decoder preset F) | **C++** column list decoding, header assignment and frame-level RANSAC (`native/cpp/outerlist.cpp`); Python orchestration, inner RS + CRC-32 and tail verification (`recovery/outer_list.py`) |
| container integrity (SHA-256 + Merkle), decryption, decompression, output | Python over native libraries |

Success, partial recovery and failure stay distinct: a decode reports SUCCESS only after the container SHA-256 and
Merkle validation; no performance profile disables it.

## 6. Concurrency policy

* One level of parallelism at a time. Worker *processes* (encode groups, decode batches, consensus batches) run their
  kernels single-threaded (`cluster.thread_scope(1)`; strand builder default 1 thread). Kernel *threads*
  (`VNXDNA_CLUSTER_THREADS`, `strand.build_rows(nthreads=…)`) are used only by in-process callers.
* Default worker counts are bounded by `vnxdna.core.concurrency.available_cpus()`: the CPU affinity mask capped by a
  cgroup v2 `cpu.max` quota (`os.cpu_count()` counts every host CPU and oversubscribes under taskset, containers or
  systemd slices).
* Bounded queues: encode and decode pass 1 keep at most `2 × workers` tasks in flight; the consensus stage partitions
  clusters into fixed batches and cancels its pending futures when a batch fails. Worker pools exit with their stage.
* Determinism: results never depend on worker or thread counts (partitioning is by index, merges are ordered).
* No GPU path: there is no accelerator implementation, so none is advertised.

## 7. Build, packaging, installation

`pip install .` compiles every C kernel as an optional extension (`setup.py`, sources and flags in
`src/vnxdna/_native_build.py`: `-O3 -std=c11 -Wall -Wextra`) and, when cargo is present, builds the Rust kernels
(`RUST_KERNELS`, `cargo build --release --offline --locked`, no third-party crates) into `vnxdna/_vnx_<name>*.so`;
without a toolchain the install still succeeds and the references run (`CARGO=/bin/false` skips Rust explicitly).
Toolchains: a C11 compiler, optionally Rust >= 1.63 (cargo); for `web/`, Node >= 22.18 and `npm ci` (TypeScript
5.9.3, dev only). Development builds: `python -m vnxdna.native.<kernel> build [--strict]` (and the older module paths in
NATIVE_KERNELS.md). `python -m vnxdna.native --require-native` is the post-install check. No manual patching of
installed files.

## 8. Correctness gates per kernel

For every native change: strict build (`-Werror`), native = reference tests (golden vectors, randomized and hypothesis
cases, every SIMD level and thread count, C argument checks), gcc and clang ASan+UBSan and clang UBSan-trap runs of
those tests (`tools/sanitizers.sh`), libFuzzer harnesses for the new entry points
(`benchmarks/v7/native_cluster/polish_deltas_fuzz.c`, `benchmarks/arch/native_strand/strand_fuzz.c`), then the
affected pipeline tests and the full suite.

## 9. Limitations

* The channel models are SIMULATED (including the nanopore-like models); no physical DNA was synthesised, stored or
  sequenced, and no simulated result is a claim about real nanopore performance.
* Sanitizers and fuzzing reduce, but do not exclude, memory-safety defects in the C kernels.
* x86-64 is the only architecture with SIMD paths; other architectures run the portable C paths (not measured here).
* No performance claim is made for the Rust integrity kernel, the strand builder or the TypeScript layer: they are
  correctness-tested only; throughput must be measured in a separately authorised benchmarking phase.
* No HTTP/WebSocket service exists; live progress for web clients comes from following the `--events` log. A network
  service is a security-relevant addition (VNX-Secure) and is not part of this change.
* The dashboard is type-checked and built, not exercised in a browser by the automated tests.

## 10. V11 hardening

The V11 security review ([security/V11_SECURITY_REVIEW.md](security/V11_SECURITY_REVIEW.md), job #90) tightened the
boundary contract of §3:

* **Loader.** A library comes only from the package directory (`_HERE`, an absolute path), the in-place build, or an
  explicit `VNXDNA_<KERNEL>_LIB` override, and that override must be an absolute path. `native/_loader.open_library`
  refuses relative paths in all five loaders (`Kernel` and the align, reads, rs and cluster loaders), so `dlopen`
  never searches the library path or the current directory for a bare name. The built libraries carry no RPATH or
  RUNPATH. The ABI is checked before any other symbol is bound, and a refused library is named in `load_error`. In
  `auto` mode the reference runs; in `native` mode the call raises.
* **Buffer sizes.** Every wrapper derives each length it passes from the arrays it passes, or checks the array
  against the length. The C++ outer-list entry points take k, m and P from `parity` and `sym`, so their wrappers now
  check `known` (n), `sym` (n × P), `mul` (256 × 256), `inv` (256) and the candidate rows before the call; a wrong
  size is a `ValueError`, not a read past the buffer.
* **Fuzzing.** `fuzz/native/align_fuzz.c` also checks the ABI-3 SIMD entry points: `vnx_align_batch_lv` at 8 and 16
  lanes must equal the 4-lane path, and a level the CPU lacks must be refused. `fuzz/native/rs_fuzz.c` already
  compared the scalar, AVX2 and AVX-512 paths. Run a campaign with `fuzz/run.sh <align|rs|reads> <seconds>`.
* **Sanitizers.** Always pass `PY` explicitly, as an absolute path to a Python with the test dependencies. The
  defaults differ: `python` from `PATH` (`tools/sanitizers.sh`, strand, outer-list), a relative `.venv/bin/python`
  that does not exist in a git worktree (align, reads), or the lab venv (rs, cluster). `tools/sanitizers.sh` exports
  `PY` to every kernel script. For example:
  `PY=/root/vnx-dna-lab/.venv/bin/python bash benchmarks/v6/native_rs/sanitizers.sh /tmp/san-rs 50`.
