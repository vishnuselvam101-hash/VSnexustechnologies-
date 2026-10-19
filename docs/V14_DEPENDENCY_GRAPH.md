# V14 dependency graph

Phase 0 document (master directive §2, §21). It records what VNX-DNA depends on at 58171b8 (the V13 release, the
base of V14), the internal layer order that tests enforce, and how the V14 storage-platform parts plug into it.
Parts marked PLANNED do not exist yet; they are added by the phase named next to them
([V14_MASTER_PLAN.md](V14_MASTER_PLAN.md)). Versions are the ones installed in the lab environment on 2026-10-18.

## 1. Python package layers (enforced by `tests/architecture/test_layers.py`)

A package may import only packages of a lower or equal layer; the pure packages (layers 0–5 codec path) never import
simulation, providers, physical, benchmark, sdk, commands or legacy code.

```
 8  commands                                   CLI entry points (vnx …)
 7  sdk · conformance                          public SDK, conformance suites
 6  providers · benchmark · secure · volume    service-level packages
    device (PLANNED, P1)                       Python side of the storage device: mgmt client, codec worker
 5  pipeline · simulation · physical           stream encode/decode, channel simulator, physical-order export
 4  recovery                                   outer code, pass 2, superblocks
 3  dnaenc · sync                              strand encoding, markers, indel sync
 2  archive · codec                            container format, inner codes
 1  native                                     ctypes loaders for the C/C++/Rust kernels
 0  core                                       errors, config, canonical JSON, hashing
```

`vnxdna.device` (P1) joins layer 6: it uses `pipeline` (encode/decode an extent for the codec worker), `secure`
(sealing and authorisation) and `volume` (the existing V13 file-backed volume stays the reference for the extent
format); `sdk` and `commands` use it. It is added to `LAYERS` and to the public-path list in the same commit as its
first module, so the layer test covers it from the start.

## 2. Native components

| component | language | location | links | consumers |
|---|---|---|---|---|
| alignment DP | C11 (AVX2 path + scalar) | `src/vnxdna/v5/native/align.c` | libc | `v5.native_alignment`, engine |
| read parser | C11 | `src/vnxdna/v6/native/reads.c` | libc | `v6.native_reads`, engine |
| inner RS | C11 | `src/vnxdna/v6/native/rs.c` | libc | `v6.native_rs`, engine |
| cluster / strand / orient | C11 | `src/vnxdna/native/c/*.c` | libc | `native.cluster/strand/orient`, engine |
| outer list decoder | C++20 | `src/vnxdna/native/cpp/outerlist.cpp` | libstdc++ | `native.outerlist` |
| batched CRC-32 | Rust 2021, std only | `src/vnxdna/native/rust/integrity` (cdylib, C ABI) | std | `native.integrity` |
| pass-1 decode engine | Rust 2021, std only | `src/vnxdna/native/rust/engine` (cdylib, C ABI 1) | std + the C kernels via `build.rs` | `native.engine`, `pipeline.decode` |
| `vnxd` storage service | Rust 2021, std only | `src/vnxdna/native/rust/vnxd` (PLANNED, P1) | std, libc symbols through audited `extern "C"` | `vnxdna.device`, the host kernel (NBD, then ublk) |

Every Python kernel wrapper keeps a pure-Python reference; native and reference results are compared by the
equivalence tests. Both existing Rust crates have empty `[dependencies]`, build with `cargo --offline --locked`, and
declare `rust-version = "1.63"` (std::thread::scope); `vnxd` follows the same rule (decision D2 of
[V14_LANGUAGE_AND_RUNTIME_STRATEGY.md](V14_LANGUAGE_AND_RUNTIME_STRATEGY.md)).

## 3. Planned V14 internal graph (Rust service, P1–P8)

```
 device (NBD server, ublk later) ──► io ──► volume ──► secure-boundary ──► cache ──► ram
                                      │        │                             │
                                      │        └──► journal                  ▼
                                      └──────────────────────────────► balancer ──► media::{image, faulty, sim}
 fallback ◄── every module reports typed errors; fallback owns retry/health/degraded state
 mgmt ◄── reads metrics and health from every module; never mutates data paths directly
 contracts (P1): addresses, generations, write states, error classes, record formats, IPC schemas — used by all
```

The `sim` media adapter does not link the codec: it sends staged extent files to Python codec workers over the
versioned codec IPC (`vnx.codec-ipc/1`), so the Python/Rust boundary is a process boundary with no shared pointers.

## 4. Third-party dependencies and licences

The project licence is MIT (`LICENSE`, `pyproject.toml`).

| dependency | kind | constraint (`pyproject.toml`) | installed | licence |
|---|---|---|---|---|
| numpy | runtime | >=2.0,<3 | 2.5.3 | BSD-3-Clause (bundled parts 0BSD, MIT, Zlib, CC0-1.0) |
| cryptography | runtime | >=44,<47 | 46.0.7 | Apache-2.0 OR BSD-3-Clause (links OpenSSL, Apache-2.0) |
| zstandard | runtime | >=0.23,<1 | 0.25.0 | BSD-3-Clause |
| reedsolo | runtime | >=1.7,<2 | 1.7.0 | Public Domain / Unlicense / MIT-0 (package metadata) |
| pydantic | runtime | >=2.10,<3 | 2.13.5 | MIT |
| typer | runtime | >=0.15,<1 | 0.27.2 | MIT |
| edlib | optional `fit` | >=1.3.9,<2 | not in the lab venv | MIT |
| pytest, pytest-xdist | dev | pytest >=8,<9 | 8.4.2, 3.8.0 | MIT |
| hypothesis | dev | >=6.100,<7 | 6.168.3 | MPL-2.0 (test-time only, not distributed) |
| ruff, mypy | dev tools | ruff >=0.6,<1 | 0.16.10, 2.4.0 | MIT |
| Rust crates | — | none (std only) | — | — |

System dependencies: Linux with glibc; gcc/g++ (C11/C++20) and rustc/cargo >= 1.63 to build the kernels; for the
block device (P8) the kernel modules `nbd` (Stage A) and `ublk_drv` (Stage A'), root or CAP_SYS_ADMIN to attach a
device, and `util-linux`/`e2fsprogs` (`fdisk`, `mkfs.ext4`, `mount`) for the end-to-end tests only.

## 5. Licence metadata of the Rust crates

At 58171b8 the `integrity` and `engine` crates declared `license = "Proprietary"` while the project is MIT (metadata
only; the crates are not published). Fixed on `build/v14` after Phase 1: all three crates (`integrity`, `engine`,
`vnxd`) declare `license = "MIT"`.
