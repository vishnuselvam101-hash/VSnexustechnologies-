# V14 Raspberry Pi profile (ARM64)

Phase 10 deliverable. Sources: master directive §16 and [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P10.
Build and installation are in [V14_BUILD_AND_DEPLOYMENT.md](V14_BUILD_AND_DEPLOYMENT.md). What was run is in
[V14_PHASE10_REPORT.md](V14_PHASE10_REPORT.md).

> **Pi acceptance: INCOMPLETE.** No Raspberry Pi and no ARM64 machine was available in V14. Everything below that
> concerns a Pi is a **plan**: budgets to confirm, procedures to run and settings to try. No number in this file was
> measured on a Pi. The aarch64 builds were tested only under qemu-user emulation on an x86_64 host. Those runs prove
> that the code builds and gives the same answers. They say nothing about speed, memory use on a Pi, temperature or
> stability, and no timing from them is reported anywhere.

## 1. Target and what is known

| item | status |
|---|---|
| Architecture | `aarch64-unknown-linux-gnu`, 64-bit little-endian, LP64. The same byte order and word size as x86_64. |
| OS images considered | Raspberry Pi OS 64-bit (Debian 12 based) and Ubuntu Server 24.04 for Raspberry Pi. Neither was booted in V14. |
| Candidate boards | Raspberry Pi 4 Model B and Raspberry Pi 5, 4 GB or more. The model is chosen when hardware is available (directive §16), from the measurements in §4. Models differ in core type, memory and I/O, so budgets are per model. |
| Cross-built and emulated (passed) | `vnxd` (library, binaries and all its tests), the Rust decode engine with the C kernels linked in, the Rust integrity kernel, the six C kernels and the C++ kernel. A focused Python subset ran with an aarch64 CPython and the aarch64 kernels. See the Phase 10 report. |
| Not verified | Anything on real hardware: speed, RSS, thermal behaviour, the `nbd` module of the Pi kernel, ext4 over NBD on a Pi, SD-card and USB-SSD behaviour, and the native build on the Pi itself. |

### 1.1 Portability facts the code relies on (audited in P10)

* **No x86-only code path is mandatory.**
  * Every x86 SIMD kernel sits behind `#if defined(__x86_64__)` (C) or `#[cfg(target_arch = "x86_64")]` (Rust) and
    has a scalar or portable fallback.
  * `v6/native/rs.c` (GF(256) Reed-Solomon) runs its scalar kernel on aarch64.
  * `v5/native/align.c` runs its portable LANES=4 level: GCC generic vectors, which become NEON code on aarch64.
  * `native/c/cluster.c` runs its scalar forward-backward level.
  * The engine's newline scan, base conversion and prefetch fall back to scalar code.
  * The level-query functions (`vnx_rs_levels`, `vnx_align_levels`, `vnx_cl_fb_level`) report only the portable level
    on aarch64. Asking for an AVX level returns the existing "unsupported" error code.
* **Golden output is identical across architectures.** The same golden vectors were checked under emulation: the RS
  goldens, the alignment-level goldens, the conformance vectors and the native-vs-reference equivalence tests (Phase 10
  report §3).
* **`char` signedness.** `char` is unsigned on aarch64. The kernels use `uint8_t`/`int8_t` for data. On the Rust side,
  `c_char` is only used for NUL-terminated paths and error strings, never for arithmetic.
* **ioctl and signal ABI.** The NBD ioctl numbers (`_IO(0xab, n)`), the signal numbers (HUP 1, INT 2, TERM 15) and
  the 128-byte glibc `sigset_t` are the same on aarch64. `nbdsys.rs` documents this.
* **Rust toolchain version.** The crates need a newer Rust than Debian 12 ships (1.63): let-else needs 1.65, and
  `OnceLock` and `div_ceil` need 1.73. P10 corrected the declared `rust-version` of `vnxd` and the engine from 1.63
  to 1.75, the version they are built and tested with. On Raspberry Pi OS (Debian 12), build the Rust parts on the
  host and copy them over, or use rustup on the Pi (deployment doc §4).

## 2. Memory and CPU budgets (PLAN; to be confirmed on hardware)

The budget is set in software. The numbers below are the configured limits, not measured usage.

| component | how it is bounded | default | low-memory preset (§3) |
|---|---|---|---|
| VNX-RAM (I/O buffers, cache, staging, codec, metadata) | `--ram` | 256 MiB | 96 MiB |
| VNX-RAM floor | one largest NBD request (32 MiB) twice + 3 extents | — | refused below the floor (typed `INVALID_ARGUMENT`) |
| Extent cache | 40% of VNX-RAM (at least one extent) | ~102 MiB | ~38 MiB |
| NBD payloads in flight | `--max-inflight-bytes`, `--max-inflight` | 64 MiB, 128 requests | 32 MiB, 16 requests |
| I/O workers | `--workers`, `--queue-depth` | min(CPUs, 2 × backend in-flight), depth 64 | 2, depth 16 |
| Reply threads | `--repliers` | 8 | 2 |
| Python codec / sealing workers (DNA simulator, sealing) | `WorkerConfig.workers` (library API) | per adapter | 1 is recommended. Each one is a separate CPython process; its RSS has not been measured on ARM. |

**Planned ceiling for `vnxd` with `--low-memory`.** Up to about 96 MiB of VNX-RAM, plus up to 32 MiB of request
payloads, plus thread stacks and the binary: roughly 140 MiB at most. This is an **estimate from the configured
limits**. It must be checked against measured peak RSS (§4, step 3).

**Planned CPU use.** 2 I/O workers + 2 reply threads + 1 reaper + the main and signal threads. On a 4-core board this
leaves at least 2 cores for the kernel's block layer, ext4 and the Python codec worker. Not measured.

| board (to be confirmed) | suggested start | reason |
|---|---|---|
| 4 GB or more | defaults (`--ram 256M`) | The defaults fit with room for the page cache. Measure before raising them. |
| 2 GB | `--low-memory` | Leaves about 1.8 GB for the OS, the page cache and the codec worker. |
| 1 GB | `--low-memory`, `--cache none` (or write-through), one codec worker, no write-back | A write-back cache adds dirty data that must be flushed under pressure. |
| below 1 GB | not a target | The 32 MiB NBD payload floor and the CPython codec make it impractical. |

## 3. The low-memory configuration and how to set it

`vnxd nbd attach|serve` accepts these flags. All values are validated before the device starts.

```
--low-memory              preset: --ram 96M --workers 2 --queue-depth 16 --max-inflight 16
                          --max-inflight-bytes 32M --repliers 2
--ram N                   VNX-RAM budget (K/M/G suffixes); refused below 2 x 32 MiB + 3 extents
--workers N               I/O worker threads (1..256)
--queue-depth N           queued requests before backpressure (1..4096)
--max-inflight N          NBD requests in flight (>= 1)
--max-inflight-bytes N    their payload bytes (>= 32M, the largest NBD request)
--repliers N              reply threads per connection (1..256)
--cache none|write-through|write-back
```

Options given explicitly override the preset, wherever they appear on the command line. For example, this is a
1 GB-board configuration:

```
sudo modprobe nbd max_part=8
vnxd nbd create --volume /var/lib/vnx/vol --backend /var/lib/vnx/media --size 4G
sudo vnxd nbd attach --volume /var/lib/vnx/vol --backend /var/lib/vnx/media --device auto \
     --low-memory --cache write-through --workers 1
```

* **What happens at a limit.**
  * A full queue or a full in-flight budget makes the reader stop taking requests from the socket. This is
    backpressure, not unbounded allocation.
  * A VNX-RAM reservation that does not fit waits or fails with a typed error.
  * A budget below the floor is refused at start.
* **Tests.** These behaviours are covered by the P2–P5 tests. They passed under qemu on aarch64 (Phase 10 report §3).
  Two P10 tests cover the preset: `nbdctl::tests::low_memory_preset_and_concurrency_limits` and
  `nbdserve::tests::the_low_memory_preset_runs_a_device_round_trip`.
* **The codec on a Pi.**
  * Set `VNXDNA_*_BACKEND=native` only after the native kernels are installed for aarch64. With `auto`, a kernel that
    fails to load falls back to the NumPy reference and logs it. That is correct, but much slower.
  * Use one codec worker.

## 4. Measurement procedure on real hardware (to run; nothing here has been run)

Record the following before any measurement:
* the model (`cat /proc/device-tree/model`), the RAM, the OS image and version, and `uname -a`;
* `lscpu`, `/proc/cpuinfo` features, `gcc --version`, `rustc --version` and the Python version;
* the storage behind the backend directory (SD card, USB SSD or NVMe HAT);
* the cooling setup, and the commit SHA.

Results go to `experiments/v14/pi/<model>-<date>/` with the commands, the raw output and a README. Use the
`vnx-dna-experiment` provenance rules. Every result is evidence category B on C (a software device over an electronic
backend), **on that board only**.

1. **Build and functional check (native).**
   * Run `tools/cross_aarch64.sh check` on the host, or install natively (deployment doc §4).
   * Run the `vnxd` tests on the Pi:
     `cargo test --offline --locked --release -- --test-threads=1`.
   * Run the native golden subset from the Phase 10 report §3 with the Pi's own Python.
   * All of these must pass before any number is taken.
2. **Idle.**
   * Start `vnxd nbd attach` with the profile under test and wait 60 s.
   * Record the RSS (`/proc/<pid>/status` VmRSS and VmHWM), the thread count and CPU% (`pidstat -u -r 1 60`).
3. **Peak RSS under saturation.**
   * `fio --rw=randrw --bs=4k --iodepth=64 --numjobs=4 --direct=1` on `/dev/nbdN` for 300 s, then sequential 1 MiB
     reads and writes.
   * Record VmHWM, the `vnxd` stats JSON (`nbd_max_inflight`, the queue metrics, the VNX-RAM accounting) and
     `free -m` every second.
   * Pass: VmHWM ≤ the planned ceiling (§2), and VNX-RAM accounting ≤ `--ram` at every sample.
4. **Throughput and latency.** Sequential MB/s, and random 4 KiB IOPS with p50/p99 latency, for each `--cache` mode
   and for 1, 2 and 4 workers. Choose the Pi default from this data, as P5 did for x86.
5. **Codec throughput.** Run `vnxdna` encode/decode of a fixed archive (the P6 bench suite command) with native
   kernels, then with `reference`, and record the active kernel backends.
6. **ext4 end to end.** Run the §5 procedure, timed: copy 1 GiB of mixed files, `sync`, `umount`, detach, attach,
   `mount`, `sha256sum -c`.
7. **Startup and recovery.**
   * Measure the time from `attach` to the `attached` event, on an empty volume and on a volume with a 10k-record
     journal.
   * `kill -9` during a write load, then measure re-attach and journal replay time and run `e2fsck -fn`.
8. **Thermal.**
   * Run 30 min of step 3 load while logging `vcgencmd measure_temp` and `vcgencmd get_throttled` (Raspberry Pi OS)
     or `/sys/class/thermal`.
   * Record whether throttling occurred and how throughput changed over time.
9. **Memory pressure.**
   * Run step 3 inside `systemd-run -p MemoryMax=<ceiling>` and with a `stress-ng --vm` neighbour.
   * Pass: no OOM kill of `vnxd`, no I/O error other than typed backpressure or timeouts, and byte-identical readback
     afterwards.
10. **Long run.** 24 h of a mixed workload with a checksum pass every hour. Pass: no corruption, and no RSS growth
    beyond the configured limits.

Budgets in §2 are then replaced by measured values per board. This file only states "Pi acceptance: PASS for
<model>" after steps 1–10 pass on that board.

## 5. ext4 on NBD on a Pi (procedure; unverified on hardware)

These are the same steps as the P8 acceptance on x86 (docs/V14_DEVICE_INTERFACE.md §4, `tests/v14/test_device_nbd.py`).

1. Check the kernel:
   * `modinfo nbd`. If the module is missing, install the kernel's extra-modules package (Ubuntu:
     `linux-modules-extra-raspi`). It is expected in Raspberry Pi OS kernels, but **this was not checked**.
   * `sudo modprobe nbd max_part=8`.
2. Use a disposable volume on a USB SSD where possible. The backend directory must not live on an NBD device that
   `vnxd` serves; `vnxd` refuses that.
3. Run the device:
   * `vnxd nbd create ...`, then `sudo vnxd nbd attach ... --device auto --low-memory`.
   * Wait for the `attached` line on stdout.
4. Make and fill the filesystem:
   * `sudo sfdisk /dev/nbdN` (one partition), then `sudo mkfs.ext4 /dev/nbdNp1`.
   * `mount`, copy files, `sync`, `umount`.
5. Restart and check:
   * Stop with SIGTERM. The service fsyncs, disconnects and flushes.
   * Attach again, mount, and compare checksums.
   * Run `e2fsck -fn /dev/nbdNp1` after an unclean stop.
6. Kernel timeout: keep `--kernel-timeout-s` ≥ 2 × the request timeout + 10. On slow SD cards, raise
   `--request-timeout-s` rather than disabling the timeout. 0 is refused.

Known risks to check on hardware:
* SD-card write latency spikes against the kernel timeout;
* the page cache competing with VNX-RAM on 1–2 GB boards;
* swap on SD (not recommended: use zram or none);
* undervoltage throttling under USB-SSD load (`get_throttled` bit 0).

## 6. What is unverified (open items for a hardware run)

* Every number of §4, on every board. No Pi value exists in V14.
* The native build on Raspberry Pi OS. Its rustc is 1.63, which is too old (§1.1), so use the cross-built artefacts
  or rustup. Its gcc 12 is not tested; only gcc 13.3 (cross) is.
* Python wheels for aarch64 on the Pi's Python. The emulated run used CPython 3.12.3 from Ubuntu 24.04 and the
  PyPI manylinux aarch64 wheels. Raspberry Pi OS ships Python 3.11, which is untested.
* NBD module availability and `max_part` behaviour on Pi kernels; ext4 over NBD on a Pi.
* ARM acceleration.
  * No NEON-specific kernel exists. RS runs the scalar table kernel.
  * The aligner's portable level compiles to NEON through generic vectors. Whether a hand-written NEON `TBL` RS
    kernel helps must be measured on a Pi (directive §16: "acceleration only where supported + measured").
  * Runtime ARM feature detection (`getauxval(AT_HWCAP)`) is not needed until such a kernel exists.
* The `ublk` path (Stage A') does not exist on any architecture.
