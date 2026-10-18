# V14 build and deployment (x86_64 and ARM64)

Phase 10 deliverable (master directive §16 "Packaging", [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P10). Related
documents:
* [V14_RASPBERRY_PI_PROFILE.md](V14_RASPBERRY_PI_PROFILE.md): budgets, the low-memory configuration and the hardware
  plan;
* [V14_DEVICE_INTERFACE.md](V14_DEVICE_INTERFACE.md): the device commands and the kernel interface;
* [V14_PHASE10_REPORT.md](V14_PHASE10_REPORT.md): what was run.

Cross-built aarch64 artefacts were tested only under qemu-user emulation. That is a functional check, never a
performance measurement. Nothing in this file was run on a Raspberry Pi.

## 1. Components

| component | language | source | output | consumer |
|---|---|---|---|---|
| `vnxd` storage service | Rust (std only) | `src/vnxdna/native/rust/vnxd` | `vnxd` binary (and `vnxd-lb-bench`) | systemd (§6), tests |
| decode engine | Rust (std only) + the C kernels `rs.c`, `align.c`, `orient.c` through `build.rs` | `src/vnxdna/native/rust/engine` | `libvnx_engine.so` | `vnxdna.native.engine` (ctypes) |
| integrity kernel | Rust (std only) | `src/vnxdna/native/rust/integrity` | `libvnx_integrity.so` | `vnxdna.native.integrity` |
| C kernels | C11 | `v5/native/align.c`, `v6/native/{rs,reads}.c`, `native/c/{cluster,orient,strand}.c` | `.so` each | `vnxdna.native.*` (ctypes) |
| C++ kernel | C++20 | `native/cpp/outerlist.cpp` | `.so` | `vnxdna.native.outerlist` |
| SDK / CLI / codec workers | Python 3.12 | `src/vnxdna` | package | users, `vnxd` worker processes |

No crate is downloaded: the three crates have empty `[dependencies]`, and every cargo command runs with
`--offline --locked`. Python runtime dependencies are those of `pyproject.toml`; the compiled ones are numpy,
cryptography, zstandard and pydantic-core. All of them publish aarch64 manylinux wheels on PyPI. Every one of the
kernels is optional. A kernel that is missing or fails to load is logged, and the NumPy reference runs instead.

## 2. Native build on x86_64 (reference)

The commands are unchanged from earlier phases. The lab runs them throttled (`nice`, `systemd-run`) while
benchmarks run.

```
pip install -e .                                   # C/C++ extensions + the Rust kernels when cargo is present
cargo build --offline --locked --release --manifest-path src/vnxdna/native/rust/vnxd/Cargo.toml
cargo test  --offline --locked --manifest-path src/vnxdna/native/rust/vnxd/Cargo.toml
```

Toolchain verified: gcc 13.3, rustc and cargo 1.75.0 (Ubuntu 24.04 packages), and Python 3.12.3.

## 3. Cross-build for aarch64 on an Ubuntu 24.04 x86_64 host

### 3.1 Packages (installed on the lab host in P10, all from the Ubuntu archive)

| package | version | why |
|---|---|---|
| `gcc-aarch64-linux-gnu`, `binutils-aarch64-linux-gnu` | 13.3.0 / 2.42 | C cross compiler, linker and `ar` for the C kernels, and the linker for Rust |
| `g++-aarch64-linux-gnu` | 13.3.0 | the C++ kernel (`outerlist.cpp`) |
| `libc6-dev-arm64-cross` | 2.39 | the aarch64 glibc headers and libraries (`/usr/aarch64-linux-gnu`), also the qemu `-L` root |
| `qemu-user-static` | 8.2.2 | `qemu-aarch64-static` runs aarch64 test binaries; the package registers binfmt handlers |
| `libstd-rust-dev:arm64` (+ its arm64 dependencies, among them `libstd-rust-1.75:arm64`, `libllvm17t64:arm64`, `libc6:arm64`, `libstdc++6:arm64`) | 1.75.0+dfsg0ubuntu1-0ubuntu7.4 | the aarch64 Rust standard library, matching the installed rustc 1.75 exactly (`/usr/lib/rustlib/aarch64-unknown-linux-gnu`) |

Why multiarch rather than rustup:
* The archive provides the arm64 build of the very same `libstd-rust-dev` version as the host rustc. Rust requires
  the standard library to match the compiler exactly, and this package does.
* So no toolchain is downloaded from outside the archive, and the build stays on the one verified compiler.

The arm64 package needs dpkg multiarch and the Ubuntu ports mirror, because arm64 packages are not on
`archive.ubuntu.com`. The existing `archive` and `security` stanzas were limited to amd64 so that apt does not look
for arm64 indexes there:

```
sudo cp /etc/apt/sources.list.d/ubuntu.sources /root/ubuntu.sources.bak
# add "Architectures: amd64" to both stanzas of /etc/apt/sources.list.d/ubuntu.sources, then:
sudo tee /etc/apt/sources.list.d/ubuntu-ports-arm64.sources <<'EOF'
Types: deb
URIs: http://ports.ubuntu.com/ubuntu-ports/
Suites: noble noble-updates noble-security
Components: main restricted universe
Architectures: arm64
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
sudo dpkg --add-architecture arm64
sudo apt-get update
sudo apt-get install --no-install-recommends gcc-aarch64-linux-gnu g++-aarch64-linux-gnu binutils-aarch64-linux-gnu \
     libc6-dev-arm64-cross qemu-user-static libstd-rust-dev:arm64
```

Installing `libstd-rust-dev:arm64` also upgraded the host's `libxml2` to the same (security) version as its arm64
copy. Multiarch requires the two copies to be identical.

To undo: `sudo apt-get purge '.*:arm64'`, `sudo dpkg --remove-architecture arm64`, remove the ports file, restore the
sources backup and run `apt-get update`.

**Alternative (not used): rustup.** `rustup target add aarch64-unknown-linux-gnu` from the official installer at
<https://rustup.rs>. Use it when the archive has no matching `libstd-rust-dev:arm64`, for example on a distribution
whose rustc is not packaged for arm64, or for a newer compiler. Record the toolchain version it installs.

### 3.2 Rust crates

`engine/build.rs` compiles the C kernels with `CC_<target>`/`AR_<target>`, then `CC`/`AR`, then `cc`/`ar` (fixed in
P10). Before the fix, it read only `CC`, so a cross build had to set a global `CC` and archived with the host `ar`.
The host GNU `ar` of Ubuntu 24.04 does archive aarch64 objects correctly; this was checked.

```
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUNNER="qemu-aarch64-static -L /usr/aarch64-linux-gnu"
export CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc AR_aarch64_unknown_linux_gnu=aarch64-linux-gnu-ar
for c in vnxd engine integrity; do
  (cd src/vnxdna/native/rust/$c &&
   cargo build --offline --locked --release --target aarch64-unknown-linux-gnu &&
   cargo test  --offline --locked --no-fail-fast --target aarch64-unknown-linux-gnu -- --test-threads=1)
done
```

`cargo test` with the runner above executes every aarch64 test binary under qemu. The same steps are scripted:
`tools/cross_aarch64.sh vnxd|engine|integrity` (output in `build-aarch64/`, `OUT=` to move it).

Outputs:
* `target/aarch64-unknown-linux-gnu/release/vnxd`: a dynamically linked PIE that needs glibc ≥ 2.34 (Ubuntu 24.04
  has 2.39). **Not checked:** the glibc 2.36 of Raspberry Pi OS (Debian 12). A binary linked against 2.39 can need
  newer symbols. If it does, build on the Pi or in a Debian 12 arm64 chroot (§4).
* `libvnx_engine.so` and `libvnx_integrity.so`.

### 3.3 C and C++ kernels

The flags are those of `pip install`, with `-Werror`. All seven build without a warning:

```
tools/cross_aarch64.sh ckernels      # -> build-aarch64/lib/libvnx_{align,rs,reads,cluster,orient,strand,outerlist}.so
```

### 3.4 Python under emulation (focused subset)

This is a functional check of the ctypes boundary with real aarch64 libraries. CPython for aarch64 is taken from the
Ubuntu archive and **extracted**, not installed: `python3.12` is not Multi-Arch and installing `python3.12:arm64`
would replace the host interpreter. The wheels come from PyPI at the versions pinned in the lab environment.

```
R=/path/to/aarch64-pyroot; mkdir -p $R/debs && cd $R/debs
apt-get download python3.12-minimal:arm64 libpython3.12-minimal:arm64 libpython3.12-stdlib:arm64 libexpat1:arm64 \
     libsqlite3-0:arm64 libbz2-1.0:arm64 libssl3t64:arm64 libncursesw6:arm64 libreadline8t64:arm64 \
     libdb5.3t64:arm64 libcrypt1:arm64
for d in *.deb; do dpkg-deb -x $d $R/sysroot; done
pip install --target $R/site --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 \
     --platform manylinux_2_28_aarch64 --python-version 3.12 --implementation cp --only-binary=:all: --no-deps \
     numpy==2.5.3 pydantic==2.13.5 pydantic_core==2.46.5 cryptography==50.0.2 cffi==2.1.1 zstandard==0.25.0 \
     typer==0.27.2 click rich ... pytest==8.4.2 hypothesis==6.168.3 ...   # the lab pins (pip freeze)
cp <venv>/site-packages/reedsolo.py $R/site/                   # pure Python; PyPI has only an sdist
PYROOT=$R tools/cross_aarch64.sh pytest tests/v6/test_native_rs.py ...
```

`tools/cross_aarch64.sh pytest` points every `VNXDNA_*_LIB` at the aarch64 kernels and sets `CC`/`CXX` to the cross
compilers. A test that builds a kernel therefore builds an aarch64 one. The subset that was run is listed in the
Phase 10 report §3.

## 4. Native build on the Pi (plan; not run)

On Ubuntu Server 24.04 for Raspberry Pi, the packages are the same versions as on the lab host:

```
sudo apt install build-essential cargo rustc python3-venv python3-dev
python3 -m venv ~/vnx && . ~/vnx/bin/activate && pip install -e .        # builds every kernel natively
cargo build --offline --locked --release --manifest-path src/vnxdna/native/rust/vnxd/Cargo.toml
```

On Raspberry Pi OS (Debian 12), the archive's rustc 1.63 cannot build the crates. They need 1.75: let-else needs
1.65, and `OnceLock`/`div_ceil` need 1.73.

Choose one of these:
* **(a)** Copy the cross-built `vnxd`, `libvnx_engine.so` and `libvnx_integrity.so` from §3. Check them with
  `ldd`/`objdump -T` against the Pi's glibc 2.36 first.
* **(b)** Install rustup on the Pi and pin `rustup toolchain install 1.75.0`.
* **(c)** Build with `CARGO=/bin/false pip install -e .`. This skips the Rust kernels, and the Python reference
  replaces them.

The C/C++ kernels build with the Pi's gcc 12. That compiler is **not tested**.

## 5. Packaging

* **Python wheel.**
  * `pip wheel .` on the target produces a platform wheel with the kernels inside. Cross wheels are not produced in
    V14: setuptools cross-compilation of extensions is not set up.
  * For a Pi, build the wheel on the Pi (or in an arm64 container under qemu-user, which is slow but functional),
    or install from source as in §4.
* **`vnxd` tarball.**
  * Contents: `vnxd`, `deploy/systemd/*`, `docs/V14_DEVICE_INTERFACE.md`, this file and `LICENSE`.
  * Example: `tar czf vnxd-<ver>-aarch64.tar.gz -C build-aarch64/target/aarch64-unknown-linux-gnu/release vnxd -C
    $PWD deploy docs/V14_DEVICE_INTERFACE.md docs/V14_BUILD_AND_DEPLOYMENT.md LICENSE`.
  * Record `sha256sum` of the binary and the commit SHA next to the tarball.
* **Reproducibility.**
  * Builds are offline and `--locked`, with no crate downloads and a pinned compiler package version (§3.1).
  * Binaries are not bit-for-bit reproducible across hosts: build paths and the build ID are embedded. Not measured.
* **Containers.**
  * A container alone gives no host block device (directive §16).
  * `vnxd nbd attach` in a container needs `--cap-add SYS_ADMIN`, `--device /dev/nbdN` and the host's `nbd`
    module.
  * `vnxd nbd serve` (socket only) needs neither.
  * The repository's `Dockerfile` is the codec image. It does not run `vnxd`.

## 6. Service management (systemd) and kernel modules

Files in `deploy/systemd/`. `systemd-analyze verify` passes, apart from the binary not being installed on the lab
host. The unit has **not been run as a service**, here or on a Pi.

| file | install as | purpose |
|---|---|---|
| `vnxd.service` | `/etc/systemd/system/vnxd.service` | runs `vnxd nbd attach` on a fixed `/dev/nbdN` |
| `vnxd.default` | `/etc/default/vnxd` | volume, backend and device paths; `VNXD_OPTS` (memory profile, cache mode) |
| `nbd.conf` | `/etc/modules-load.d/nbd.conf` | loads `nbd` at boot |
| `nbd-options.conf` | `/etc/modprobe.d/nbd-options.conf` | `max_part=8`, so that partitions appear |

```
sudo install -m 0755 vnxd /usr/local/bin/vnxd
sudo install -m 0644 deploy/systemd/vnxd.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/vnxd.default /etc/default/vnxd
sudo install -m 0644 deploy/systemd/nbd.conf /etc/modules-load.d/
sudo install -m 0644 deploy/systemd/nbd-options.conf /etc/modprobe.d/
sudo vnxd nbd create --volume /var/lib/vnx/vol --backend /var/lib/vnx/media --size 8G   # once
sudo systemctl daemon-reload && sudo systemctl enable --now vnxd
```

Design of the unit:
* **Stop path.**
  * `KillSignal=SIGTERM` starts the service's own detach path: fsync of the device, `NBD_DISCONNECT`, an answer to
    every request, a flush of the store.
  * `TimeoutStopSec=300` exceeds the kernel timeout, so the final flush is not cut short.
  * `Restart=on-failure`: a restart replays the journal.
* **Privileges.**
  * Only `CAP_SYS_ADMIN`, which the NBD ioctls need.
  * `ProtectSystem=strict` with `/var/lib/vnx` writable, and `RestrictAddressFamilies=AF_UNIX`.
  * `MemoryMax` is a ceiling above the configured budgets. `MemorySwapMax=0`, because a swapped-out I/O path stalls
    the filesystem above it.
* **Filesystem on top.** The mount must depend on the service, so that it is unmounted before `vnxd` stops. For
  example, in fstab:
  `/dev/nbd0p1 /srv/vnx ext4 noauto,x-systemd.automount,x-systemd.requires=vnxd.service,nofail 0 2`.
  * Never put the backend directory on a filesystem served by `vnxd`; the service refuses it.
* **Diagnostics.**
  * `journalctl -u vnxd`: one canonical-JSON event per line, including the recovery report at open and the final
    counters.
  * With `--mgmt /run/vnxd/mgmt.sock --policy FILE` added to `VNXD_OPTS`, the management socket serves `health`,
    `stats` and `capacity` (V14_DEVICE_INTERFACE.md §6–7). `--mgmt` without a policy is refused.
* **Dependency checks before enabling.**
  * `modinfo nbd`; `vnxd version`;
  * `ldd /usr/local/bin/vnxd` (glibc only);
  * for the codec: `python3 -c "import vnxdna, vnxdna.native as n; print(n.status())"`. It lists which kernels are
    native and which are reference.

Kernel modules: only `nbd` (in-tree). `ublk_drv` is not used (Stage A' is not built).

## 7. Verification checklist for a new host or architecture

1. `tools/cross_aarch64.sh check` (cross) or the §4 native build.
2. Rust tests of the three crates: all pass, with `--test-threads=1` under emulation.
3. The native golden subset of the Phase 10 report §3: identical results with the native kernels and the reference.
4. On real hardware only: the Pi profile §4 measurement procedure, then the P8 ext4 acceptance on a disposable
   device.
