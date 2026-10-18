# V14 language and runtime strategy

Phase 0 document (master directive §3; written 2026-10-18 at 58171b8 before any V14 code). It extends
[HYBRID_ARCHITECTURE.md](HYBRID_ARCHITECTURE.md) (the V10–V13 policy for the codec) to the storage platform of V14 and
records the runtime decisions with their reasons. Nothing here is a performance claim; every "measured" statement
points at a committed file.

## 1. What exists at 58171b8 (audited)

| layer | language | where | build |
|---|---|---|---|
| codec references, pipeline orchestration, formats, SDK, CLI, security control plane, simulation, benchmarks | Python 3.12 + NumPy | `src/vnxdna/{core, archive, codec, dnaenc, sync, recovery, pipeline, simulation, physical, providers, benchmark, secure, volume, sdk, conformance, commands}` | pure Python; `pyproject.toml` deps numpy, cryptography, zstandard, reedsolo, pydantic, typer |
| CPU kernels (alignment DP, read parsing, inner RS, clustering, strand building, orientation/projection/packing) | C11 (+ AVX2/AVX-512 paths with scalar fallbacks) | `v5/native/align.c`, `v6/native/{reads,rs}.c`, `native/c/*.c` | `python -m vnxdna.<kernel> build` (system gcc), ctypes |
| outer-code list decoder | C++20 | `native/cpp/outerlist.cpp` | g++, ctypes |
| integrity kernel (batched CRC-32) | Rust, std only | `native/rust/integrity` (cdylib, C ABI) | cargo 1.75 `--offline --locked` |
| streaming decode engine (pass 1: IO, scanner, scheduler, attempts, spill, thread pool; the C kernels linked in by `build.rs`) | Rust, std only | `native/rust/engine` (cdylib, C ABI 1) | cargo 1.75 `--offline --locked`, no crate cache |
| web console (typed contract clients, event follower, dashboard) | TypeScript | `web/` | `npm ci && npm run typecheck && npm run build` (CI only; no `node_modules` in the lab) |

Rules already enforced by tests: the layer order and purity rules of `tests/architecture/test_layers.py`
(`core < native < archive/codec < dnaenc/sync < recovery < pipeline/simulation/physical < providers/benchmark/secure/volume
< sdk/conformance < commands`; the pure packages never import simulation, providers, sdk or commands), the public
path list, the kernel equivalence tests against the Python references, sanitizer and fuzz scripts.

## 2. Host and target facts (Phase 0, this host)

* Build/test host: x86_64, Intel Xeon Gold 6240 (8 vCPU), 31 GiB RAM, Linux 6.8.0-146 (Ubuntu 24.04), root.
  `CONFIG_IO_URING=y`, `CONFIG_BLK_DEV_NBD=m` (`nbd.ko` present, not loaded), `CONFIG_BLK_DEV_UBLK=m` (`ublk_drv.ko`
  present, not loaded; `/dev/ublk-control` absent until `modprobe ublk_drv`), `CONFIG_BLK_DEV_LOOP=y`. The host's
  real block devices (the root disk and the snap loop devices listed by `lsblk`) are never touched by V14 code or
  tests; only `/dev/nbd*` or `/dev/ublkb*` devices that V14 itself creates over disposable image files under the lab
  directory are used, and they are torn down after every test.
* Toolchains: gcc 13.3, clang 18, rustc/cargo 1.75.0 (Ubuntu packages; the only installed target is
  `x86_64-unknown-linux-gnu`), Python 3.12.3, node 24.21. Not installed: `aarch64-linux-gnu-gcc`, `qemu-aarch64`,
  `nbd-client`, `rustup`. Available from the Ubuntu archive (checked with `apt-cache policy`, not installed in
  Phase 0): `gcc-aarch64-linux-gnu` 13.2, `qemu-user-static` 8.2, `nbd-client` 3.26, `libstd-rust-dev` 1.75 (amd64;
  the arm64 build of the same package needs `dpkg --add-architecture arm64` and the ports archive).
* No ARM64 hardware and no Raspberry Pi is available. ARM64 work in V14 is cross-compilation plus emulation
  (`qemu-user-static`) and is reported as such; Pi acceptance stays INCOMPLETE until real hardware runs the tests.

## 3. Decisions

### D1. Rust is the control plane of the storage device; the codec stays as it is

New performance-sensitive, OS-facing or concurrency-sensitive parts are written in Rust: the device controller
service (host block interface), VNX-IO (logical block requests, mapping, bounded async queues, cancellation, ordering,
flush), the block volume (extent map, generations, transactions, journal, GC), the cache (read-through, dirty tracking,
write staging, checksums), VNX-RAM (budgets, quotas, pools, backpressure, accounting), the load balancer (queues,
priorities, fairness), the fallback coordinator (retry classes, health, degraded mode), the media-adapter trait with
the electronic image backend and the fault-injection backend, and the management/diagnostics surface of the service.
The V13 codec (Python orchestration, C/C++ kernels, the Rust pass-1 engine) is not rewritten: V14 changes it only where
the Phase 6 profile says so ([V14_PLAN.md](V14_PLAN.md)). The DNA simulator media adapter calls the existing codec.

Why Rust and not C++ for these parts: memory safety on every byte that comes from the host block layer or from a
backend, scoped threads with borrow-checked sharing for the queues and pools, `catch_unwind` at every boundary, and
the two crates already in the tree build the same way. Why not Python: the per-block path (a 4 KiB write arriving
from the kernel, the mapping lookup, the cache update, the staging copy, the completion) must not take the interpreter
lock or a Python object allocation per request; Python keeps the per-extent codec work, the SDK, the CLI and the tests.

### D2. Standard library only, offline, no third-party crates (kept from V13)

The master directive allows established crates. Phase 0 examined what the V14 Rust parts need and found no component
that requires one:

| need | how it is met without a crate |
|---|---|
| host block interface | the kernel NBD protocol over a Unix-domain socket (std `UnixStream`) and the `/dev/nbdN` ioctls (`NBD_SET_SOCK`, `NBD_SET_BLKSIZE`, `NBD_SET_SIZE_BLOCKS`, `NBD_SET_FLAGS`, `NBD_DO_IT`, `NBD_CLEAR_SOCK`, `NBD_DISCONNECT`), declared as `extern "C"` symbols of the C library that std already links (`ioctl`, `open`); every such declaration is an audited unsafe boundary |
| async, bounded queues, cancellation | `std::thread::scope`, `std::sync::mpsc::sync_channel` (bounded), `Condvar`, atomics, a cancellation token per request; one thread per NBD connection, worker pools with atomic chunk claiming (the engine's `pool.rs` pattern) |
| serialisation of persisted records | explicit, versioned, little-endian binary records with CRC-32 (documented byte layouts, as the journal and spill formats already are); JSON only in manifests written and read by Python with `canonical_json` |
| logging and metrics | JSON-lines structured log written by the service (one record per event, documented fields), counters in a metrics struct exported through the management socket; the same shape the Python `observe` events have |
| crypto | stays in the Python security layer (`cryptography`/OpenSSL, AES-256-GCM, scrypt): extents are sealed as archives before they reach a backend, exactly as the V13 volume does; the Rust path moves sealed bytes and verifies CRC-32/SHA-256 (SHA-256 implemented in the Rust service for integrity records, tested against hashlib) |
| errors | an explicit error enum per crate with retryable/permanent classes, mapped to the Python `VNX*Error` taxonomy at the IPC boundary |
| io_uring / ublk | not needed for Stage A (NBD); if ublk is attempted (Stage A', §4), the io_uring ring setup and `IORING_OP_URING_CMD` submissions are a few syscalls and memory maps that std-only Rust can issue through the same `extern "C"` boundary (`syscall`, `mmap`) — a crate would only wrap them |

Consequences: the offline `--locked` build keeps working with no vendoring, no licence inventory of third-party Rust
code is needed (the inventory of Python and system dependencies is in [V14_DEPENDENCY_GRAPH.md](V14_DEPENDENCY_GRAPH.md)),
and every unsafe boundary is ours to document. Re-evaluation trigger: if a later phase needs TLS, a kernel interface
with a large binding surface, or an algorithm whose correct implementation is a project in itself, the decision is
reopened in `DECISION_LOG.md` with vendoring (`cargo vendor`, pinned `Cargo.lock`, licence inventory).

### D3. Host block interface: NBD first, ublk as the documented upgrade

Stage A of the device controller (master directive §14) exposes a real Linux block device through the kernel's
**NBD** driver: the V14 service is the NBD server on a Unix-domain socket and attaches `/dev/nbdN` to that socket
itself through the documented ioctl interface (no `nbd-client` needed; the netlink interface is an alternative that
is not required). Reasons: NBD is a documented, long-supported kernel mechanism (`CONFIG_BLK_DEV_NBD=m` on this host),
its wire protocol is small and public (NBD protocol, fixed newstyle negotiation + transmission phase: read, write,
flush, trim, FUA), it needs nothing beyond sockets and ioctls, it is the same mechanism `qemu-nbd` and `nbdkit` use,
and it gives the kernel a standard block device that `fdisk`, `mkfs`, `mount` and ordinary applications use. Costs,
documented in `V14_DEVICE_INTERFACE.md` (P8) when it is written: one socket hop and copy per
request (bytes travel through the socket), a known hazard when the backend's own writes are on the same host under
memory pressure (never put swap or the backend image on the exported device), and per-device attach requires root
(or CAP_SYS_ADMIN) for the ioctls.

**ublk** (`ublk_drv.ko` present) is the lower-overhead path (shared memory between kernel and server, io_uring
commands, no socket copy) and is the documented upgrade (Stage A'): it is attempted after Stage A passes its
end-to-end tests, with the same VNX-IO behind it, and only measured differences are reported. If `modprobe` or
device creation is refused by the host's permission system, the refusal is recorded with the exact command the
founder must run; no workaround.

Stage B (hosts without the kernel interface) is the same VNX-IO behind a userspace API (the Python SDK and a CLI),
explicitly not called a block device.

### D4. Process model and the Python/Rust boundary

```
 host kernel (block layer)
    │ /dev/nbdN  (NBD protocol over a Unix socket; ioctl attach)
    ▼
 vnxd (Rust binary, one process per device)
    device controller ─ VNX-IO ─ block volume ─ cache/VNX-RAM ─ load balancer ─ fallback ─ media adapter
    │ management socket (Unix, JSON-lines request/response, versioned)       │
    │                                                                        │ codec IPC (Unix socket, versioned)
    ▼                                                                        ▼
 Python SDK/CLI (`vnx device …`, `vnxdna.device`)                 Python codec workers (`vnxdna codec-worker`)
                                                                  encode_extent / decode_extent on staged files
```

* Data between the service and the codec workers moves as **staged extent files** in a bounded staging directory
  (the codec already reads and writes files; no raw pointers cross a process; the staging budget is a VNX-RAM
  quota). Requests and completions are explicit, versioned messages (job id, extent id, generation, paths, SHA-256,
  status, error class). The electronic backend and the fault-injection backend need no codec worker.
* The management interface is a Unix-domain socket with JSON-lines messages (create/attach/detach, capacity,
  config, stats, health, scrub, shutdown), versioned (`vnx.mgmt/1`), used by the Python SDK and CLI.
* Ownership: the service owns every buffer it allocates; a request's buffer lives until its completion is sent;
  cancellation marks the request and the worker drops the buffer when it observes the mark; the staging directory
  is owned by the service and cleaned on start (orphans are logged, never silently reused).
* Version negotiation: every socket protocol starts with a hello carrying the protocol id and version; a mismatch
  is an explicit error.

### D5. Portability

The Rust service and all new code compile for `aarch64-unknown-linux-gnu` with no x86 assumption (no inline SIMD;
the C kernels keep their scalar fallbacks and runtime feature detection). The cross-build route on this host is
`gcc-aarch64-linux-gnu` + the arm64 Rust standard library package (or a rustup toolchain with the aarch64 target) +
`qemu-user-static` for running the unit tests; it is set up and documented in Phase 10 (`V14_RASPBERRY_PI_PROFILE.md`,
`V14_BUILD_AND_DEPLOYMENT.md`). Emulated runs are functional checks only, never performance numbers.

## 4. What stays in Python, and why

The SDK and CLI (public API kept), configuration validation at the user surface (pydantic models already exist),
the codec orchestration and references, the simulation models, experiments and benchmarks, the security control
plane (`vnxdna.secure`: policy, identity, audit; extended, not replaced), the stream and volume helpers of V13
(kept for compatibility; the block volume of V14 is a new, separate versioned format), tests and tooling. Python is
never on the per-block path of the device: a block request is served entirely inside `vnxd`; Python is reached once
per extent encode/decode through the codec IPC, and for management.

## 5. Evidence categories used in every V14 report

A software codec; B software storage device (NBD/ublk device on this host); C electronic-backend persistence (image
file backend); D simulated DNA channel (the channel simulator); E actual synthesis/preservation/retrieval/sequencing;
F complete physical device. V14 produces evidence in A–D only; E and F are not claimed anywhere.
