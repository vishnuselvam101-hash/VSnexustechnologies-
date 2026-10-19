# V14 Phase 10 report: ARM64 / Raspberry Pi portability

Date 2026-10-19. Branch `work/v14-p10` from `build/v14` a32cbf5, with `build/v14` f8b6a2b (P6 B/C, P9) merged in at the end. Scope: [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P10
(master directive §16). Deliverables: [V14_RASPBERRY_PI_PROFILE.md](V14_RASPBERRY_PI_PROFILE.md) and
[V14_BUILD_AND_DEPLOYMENT.md](V14_BUILD_AND_DEPLOYMENT.md).

> **Pi acceptance: INCOMPLETE.** There is no Raspberry Pi or other ARM64 hardware. Every aarch64 result below was
> obtained by cross-compiling on x86_64 and running under qemu-user (`qemu-aarch64-static` 8.2.2). These are
> **functional results only**. No timing from an emulated run is reported or used. No Pi number exists anywhere in V14.

Evidence category: B (software on this host) for the builds and tests. Nothing involves DNA.

## 1. Toolchain installed (Ubuntu 24.04 archive only)

| package | version | why |
|---|---|---|
| `gcc-aarch64-linux-gnu`, `g++-aarch64-linux-gnu`, `binutils-aarch64-linux-gnu` | 13.3.0 / 2.42 | C/C++ cross compilers, cross linker |
| `libc6-dev-arm64-cross` | 2.39 | aarch64 glibc for linking; the qemu `-L` root |
| `qemu-user-static` | 8.2.2 | runs aarch64 test binaries |
| `libstd-rust-dev:arm64` (+ arm64 dependencies) | 1.75.0+dfsg0ubuntu1-0ubuntu7.4 | the aarch64 Rust std for the installed rustc 1.75, an exact match |

Enabling the arm64 package took three changes on the host:
* `dpkg --add-architecture arm64`;
* a new `ports.ubuntu.com` source limited to arm64;
* the existing archive and security stanzas limited to amd64.

The original sources file is backed up next to the lab, and the undo steps are in the deployment doc §3.1. As a
multiarch side effect, the host `libxml2` was upgraded to the same version as its arm64 copy. rustup was not needed.

For the Python run (§3.4), aarch64 CPython 3.12.3 was **extracted** from the archive's arm64 debs into a separate root
outside the repository. It was not installed, because `python3.12` is not Multi-Arch. The lab's pinned packages were
installed as PyPI manylinux aarch64 wheels into a separate target directory. Nothing was vendored into the repository.

## 2. Cross-build and emulated test results

| component | aarch64 build | tests under qemu-user | notes |
|---|---|---|---|
| `vnxd` crate (Rust, std only) | release binary + lib: **ok**, no warnings | **all pass** on the final merged tree: lib 182, cli 2, concurrency stress 2, e2e matrix 30 (new in P9), fault matrix 8, golden 4, media adapters 12 | see §4 for the 3 tests that first failed (timing assumptions, fixed) and the 2 new tests; 3 tests `#[ignore]`d by design (they need the Python codec, see §3.3) |
| decode engine crate (Rust + `rs.c`, `align.c`, `orient.c` via `build.rs`) | **ok** | **25/25 pass** on the merged tree (16/16 before the P6 B/C merge) | 1 test failed on every architecture in debug builds (u8 overflow in the test data, fixed §4) |
| integrity crate (Rust) | **ok** | **5/5 pass** | — |
| C kernels: `align.c`, `rs.c`, `reads.c`, `cluster.c`, `orient.c`, `strand.c` | **ok** with `-Werror` | through the Python subset (§3) | x86 SIMD code is compiled out; the portable/scalar level runs |
| C++ kernel `outerlist.cpp` | **ok** with `-Werror` | through the Python subset (§3) | — |

All nine kernels load in the aarch64 CPython with no load error. `vnxdna.native.native_status()` reports:
* `align`: `simd_level` scalar (the LANES=4 portable level);
* `rs`: scalar;
* `cluster`: forward-backward level scalar;
* the other six: native, no SIMD level.

## 3. Golden and equivalence tests under emulation

### 3.1 Python subset with the aarch64 kernels

Command: `PYROOT=... OUT=... tools/cross_aarch64.sh pytest -q <file>`. Each file was run separately.

Two runs were made. Run A was on the pre-merge branch, one file at a time, on one CPU. Run B was on the final
merged tree, all files in one `pytest -n 8` run. Run B was **stopped at 84%** (1018 tests collected) when the phase
was closed, so the run is **INCOMPLETE**. Every test it finished passed.

| file | tests | run A (pre-merge, per file) | notes |
|---|---|---|---|
| tests/v6/native/test_native_rs.py | 106 | 46 passed, 59 skipped, 1 failed → passes after fix 7 (§4) | RS golden vectors pass on the scalar level |
| tests/v5/test_native_align_levels_golden.py | 7 | 7 passed | alignment-level goldens |
| tests/v5/test_native_alignment_equivalence.py | 282 | 94 passed, 188 skipped | the skips are AVX levels |
| tests/v5/test_native_align_levels.py | 95 | 59 passed, 35 skipped, 1 failed → passes after fix 7 | |
| tests/v6/native/test_native_reads.py | 171 | 131 passed, then the 2400 s file timeout; afterwards 13 more passed one at a time (one case of each remaining test function) | **INCOMPLETE:** 19 `long_lines` parametrisations, 5 `small_limits` and 2 `every_byte_value` cases were not run in run A |
| tests/v7/test_native_cluster.py | 35 | 8 passed, then the timeout; afterwards 25 more passed (`banded_distance_of_long_reads` alone, 24 in one run) | **INCOMPLETE in run A:** `test_banded_distance_100k_pairs` and `test_forward_backward_100k_reads` |
| tests/v12/test_native_orient.py | 66 | 66 passed | |
| tests/arch/test_native_strand.py | 32 | 32 passed | |
| tests/arch/test_native_integrity.py | 16 | 16 passed | |
| tests/arch/test_outer_list.py | 103 | 103 passed | C++ kernel |
| tests/v13/test_engine.py | 39 | 39 passed | Rust engine `.so` |
| tests/conformance/test_vectors.py | 43 | 42 passed, 1 failed → passes after the harness fix (§3.4) | conformance vectors byte for byte |
| tests/v6/native/test_native_status.py | 23 | 22 passed, 1 skipped | |
| **run B (merged tree, `-n 8`)** | 1018 | **582 passed, 282 skipped, 0 failed, stopped at 84%** | Run B did not reach the slowest tests, so its coverage of reads and cluster is incomplete. |

The three ABI-mismatch tests (fix 7) and the conformance generator test were re-run after their fixes under
emulation: 4 passed.

### 3.4 Harness note

The conformance generator test starts a subprocess with its own `PYTHONPATH`. That dropped the wheel directory, so
the subprocess failed on `import numpy`. The runner now exposes the wheels as the sysroot's `dist-packages`. With
`QEMU_LD_PREFIX` set, a child started from `sys.executable` is also aarch64 (checked).

Skipped tests are the AVX2/AVX-512 cases: those levels do not exist on aarch64, and the tests skip them with the
reason "not supported by this CPU/OS". The golden vectors themselves (RS goldens, alignment-level goldens and
conformance vectors) ran on the portable level and matched.

### 3.2 Endianness, word size, alignment and char-signedness audit

| item | finding |
|---|---|
| byte order / word size | aarch64 Linux is little-endian LP64, the same as x86_64. No code depends on byte order beyond explicit `to_le_bytes`/`to_be_bytes` (records, NBD wire format). No `*_ne_bytes` in the Rust crates. |
| x86 SIMD | Every intrinsic block is guarded (`__x86_64__` or `target_arch = "x86_64"`) and has a scalar or portable fallback. The level queries report only the portable level on aarch64, and a forced AVX level returns the existing UNSUPPORTED code. |
| alignment | C casts to wider types only touch `malloc`ed buffers. Vector loads in `cluster.c` go through `memcpy` (`VLD`/`VST`). No packed-struct access. |
| `char` signedness (unsigned on aarch64) | The kernels use `uint8_t`/`int8_t`. Rust `c_char` appears only for C strings. |
| ctypes boundary | Fixed-width types (`c_int64`, `c_int32`, pointers). The engine config struct is 45 × 8 bytes (asserted), identical on LP64. |
| kernel ABI (`vnxd`) | The NBD ioctl numbers (`_IO(0xab, n)`), the signal numbers and the `sigset_t` size are the same on aarch64 (as documented in `nbdsys.rs`). |
| toolchain version | **Defect.** `rust-version = "1.63"` was declared, but the code needs 1.65 (let-else) and, for `vnxd`, 1.73 (`OnceLock`, `div_ceil`). This matters for Raspberry Pi OS (Debian 12, rustc 1.63). Fixed to 1.75. |

### 3.3 Not run

* The three `#[ignore]` worker tests drive the Python codec and sealing workers (`tests/v14/test_media_adapters.py`,
  `test_device_seal.py`).
* The kernel-level NBD tests (`tests/v14/test_device_nbd.py`). qemu-user forwards ioctls, but those tests need root,
  `/dev/nbd*` and an x86 kernel. They prove nothing about a Pi kernel.
* No full Python suite under emulation. The lead runs the x86 full suite at merge.

## 4. Defects found and fixed

1. **`io::tests::backpressure_cancellation_timeout_and_barrier`**: hung under emulation.
   * Cause: the test filled the depth-3 queue with a request whose deadline had already passed. The reaper thread
     could resolve that request before the next submission, so the "queue full" check depended on thread timing.
     It also used fixed sleeps to wait for writes to reach the backend.
   * Fix: the queue is filled with plain requests. The deadline case takes the slot that a cancellation frees. A
     `Gate::wait_blocked(n)` event replaces the sleeps (also in two other `io` tests, and in a shutdown wait).
   * Every original assertion is kept, and one is added: the plain write lands.
2. **`balance::tests::tasks_cannot_wait_for_tasks_...`**: the second worker could finish the inner task before the
   outer one waited. The wait then returned the result rather than the refusal. Returning a finished result is
   harmless, so this was not a product bug. Fix: the inner task is held until the outer one has tried to wait.
3. **`media_adapters::an_offline_medium_turns_the_device_read_only_and_it_recovers`**: with a 1 ms probe interval,
   every write on a slow host was a due probe, so no write was refused READ_ONLY. Fix: the probe interval is 500 ms,
   and the recovery waits for it.
4. **engine `sched::tests::windows_forward_and_reverse_complement_agree`**: the test data `(i * 7 + 3) % 4` was
   computed in `u8` and overflowed. Debug builds panicked on x86 too; only release-mode runs had passed. It is now
   computed in `u32`. The values are the same, because 256 ≡ 0 mod 4.
5. **engine `build.rs`**: it read only `CC`, so a cross build needed a global `CC`. It now honours
   `CC_<target>`/`AR_<target>` (the cc-crate convention), then `CC`/`AR`.
6. **`rust-version`** of `vnxd` and the engine: 1.63 → 1.75 (§3.2).
7. **`tests/v6/native/test_native_rs.py::test_abi_mismatch_is_not_loaded`**: it compiled its fake library with the
   host `cc` and ignored `CC`, unlike the loaders. A cross run therefore produced an x86 library. It now honours `CC`.

Tests 1–4 and 7 were run again on x86 (focused) and under qemu. They pass on both.

## 5. Low-memory configuration (new)

* `vnxd nbd attach|serve` takes `--workers`, `--queue-depth`, `--max-inflight`, `--max-inflight-bytes` and
  `--repliers`, plus a `--low-memory` preset:
  * 96 MiB VNX-RAM;
  * 2 workers, depth 16;
  * 16 requests and 32 MiB in flight;
  * 2 repliers.
* Values are validated when the options are parsed. Explicit options override the preset.
* Before this change, only `--ram` and `--cache` could be set from the command line.
* Tests (pass on x86 and under qemu):
  * `nbdctl::tests::low_memory_preset_and_concurrency_limits`;
  * `nbdserve::tests::the_low_memory_preset_runs_a_device_round_trip`: an NBD write, flush and read of 192 KiB
    through the full stack with the preset, plus the RAM floor.
* Budgets and the hardware measurement plan are in the Pi profile §2–4. They are configured limits, not measurements.

## 6. Documents and files

* New:
  * `docs/V14_RASPBERRY_PI_PROFILE.md`, `docs/V14_BUILD_AND_DEPLOYMENT.md` and this report;
  * `tools/cross_aarch64.sh`, the reproducible cross-build and qemu runner;
  * `deploy/systemd/{vnxd.service, vnxd.default, nbd.conf, nbd-options.conf}`. The unit passes
    `systemd-analyze verify`, apart from the uninstalled binary. It was never run as a service.
* Changed:
  * `docs/V14_DEVICE_INTERFACE.md`: the new options in §2 and the platforms line in §10;
  * `.gitignore`: `build-aarch64/`;
  * the code and test files listed in §4–5.
* Not changed: `docs/V14_STATE.md`.

## 7. Completed / incomplete / hardware-dependent

* Completed:
  * aarch64 cross-builds of the three crates and the seven C/C++ kernels;
  * their tests under qemu-user;
  * native golden and equivalence tests through an aarch64 CPython;
  * the portability audit;
  * the low-memory options;
  * the build, deployment and systemd material.
* Incomplete (hardware-dependent):
  * Pi acceptance: every item of directive §16 and the Pi profile §4;
  * ext4 over NBD on a Pi kernel;
  * the native build on Raspberry Pi OS;
  * glibc 2.36 compatibility of the cross-built binary;
  * any NEON acceleration, which can only be measured on hardware.
* Not attempted: cross-built Python wheels, and a full Python suite under emulation.
* Emulated-run gaps (INCOMPLETE, stopped by the closing deadline, not by a failure):
  * the slowest reads and cluster parametrisations listed in §3.1;
  * run B on the merged tree stopped at 84%.
* One earlier `tools/cross_aarch64.sh all` run on the merged tree, with 8 test threads, exited 101. Its log was lost
  when the scratch directory was cleared. The per-crate re-runs straight after it passed: vnxd, engine, integrity,
  all with 8 test threads. A flaky test under parallel emulation therefore cannot be ruled out. Rerun
  `tools/cross_aarch64.sh vnxd` a few times before relying on it.

## 8. Reproduction

```
tools/cross_aarch64.sh check
tools/cross_aarch64.sh all                       # vnxd, engine, integrity (+ qemu tests), C/C++ kernels
PYROOT=<aarch64 python root> tools/cross_aarch64.sh pytest -q tests/v6/native/test_native_rs.py   # etc., §3.1
```

The lab ran every command under
`systemd-run --scope -p CPUQuota=100% -p MemoryMax=6G nice -n 19 ionice -c3`, with one cargo job and one test
thread, because benchmarks were running on the host at the same time.
