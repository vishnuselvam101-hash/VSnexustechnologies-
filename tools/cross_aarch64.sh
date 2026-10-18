#!/usr/bin/env bash
# aarch64 (ARM64 / Raspberry Pi OS 64-bit) cross-build and qemu-user test runner for VNX-DNA V14.
# Reference: docs/V14_BUILD_AND_DEPLOYMENT.md (prerequisites, packaging) and docs/V14_RASPBERRY_PI_PROFILE.md.
#
# Everything here is FUNCTIONAL ONLY: emulated runs prove that the code builds for aarch64 and gives the same answers;
# they never produce performance numbers (qemu-user timing says nothing about a real ARM core).
#
#   tools/cross_aarch64.sh check        toolchain check (no build)
#   tools/cross_aarch64.sh vnxd         cross-build vnxd (release binary) and run its tests under qemu
#   tools/cross_aarch64.sh engine       cross-build + test the Rust decode engine (links the C kernels via build.rs)
#   tools/cross_aarch64.sh integrity    cross-build + test the Rust integrity kernel
#   tools/cross_aarch64.sh ckernels     cross-build the C/C++ ctypes kernels into $OUT/lib (same flags as pip install)
#   tools/cross_aarch64.sh pytest ARGS  run a focused pytest subset with an aarch64 CPython + the aarch64 kernels
#   tools/cross_aarch64.sh all          vnxd, engine, integrity, ckernels
#
# Environment: OUT (build directory, default ./build-aarch64), PYROOT (aarch64 CPython sysroot + site, see the
# deployment doc §3.4), JOBS (default 1). The caller decides how to throttle (nice, systemd-run); this script does not.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="$ROOT/src/vnxdna"
TARGET=aarch64-unknown-linux-gnu
OUT="${OUT:-$ROOT/build-aarch64}"
JOBS="${JOBS:-1}"
QEMU="${QEMU:-qemu-aarch64-static}"
SYSROOT_LIBC=/usr/aarch64-linux-gnu

export CARGO_BUILD_JOBS="$JOBS"
export CARGO_TARGET_DIR="$OUT/target"
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUNNER="$QEMU -L $SYSROOT_LIBC"
export CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc
export AR_aarch64_unknown_linux_gnu=aarch64-linux-gnu-ar

need() { command -v "$1" >/dev/null || { echo "missing: $1 ($2)" >&2; return 1; }; }

check() {
    local ok=0
    need aarch64-linux-gnu-gcc "apt install gcc-aarch64-linux-gnu libc6-dev-arm64-cross" || ok=1
    need aarch64-linux-gnu-g++ "apt install g++-aarch64-linux-gnu" || ok=1
    need "$QEMU" "apt install qemu-user-static" || ok=1
    need cargo "apt install cargo rustc" || ok=1
    if [ ! -d "$(rustc --print sysroot)/lib/rustlib/$TARGET/lib" ]; then
        echo "missing: the $TARGET Rust standard library (apt install libstd-rust-dev:arm64, deployment doc §3.2)" >&2
        ok=1
    fi
    [ $ok -eq 0 ] && echo "aarch64 toolchain: ok ($(aarch64-linux-gnu-gcc -dumpfullversion), $(rustc --version), $($QEMU --version | head -1))"
    return $ok
}

crate() { # crate DIR: cross-build release artefacts, then build + run the tests under qemu
    (cd "$PKG/native/rust/$1" && cargo build --offline --locked --release --target $TARGET)
    (cd "$PKG/native/rust/$1" && cargo test --offline --locked --no-fail-fast --target $TARGET -- --test-threads=1)
}

ckernels() {
    mkdir -p "$OUT/lib"
    local cflags=(-O3 -std=c11 -fPIC -shared -Wall -Wextra -Werror)
    local cxxflags=(-O3 -std=c++20 -Wall -Wextra -fPIC -shared -Werror)
    # name -> source, as loaded by vnxdna.native.<name> (library override: the env var in `pyenv`)
    aarch64-linux-gnu-gcc "${cflags[@]}" "$PKG/v5/native/align.c" -o "$OUT/lib/libvnx_align.so"
    aarch64-linux-gnu-gcc "${cflags[@]}" "$PKG/v6/native/rs.c" -o "$OUT/lib/libvnx_rs.so"
    aarch64-linux-gnu-gcc "${cflags[@]}" "$PKG/v6/native/reads.c" -o "$OUT/lib/libvnx_reads.so"
    for k in cluster orient strand; do
        aarch64-linux-gnu-gcc "${cflags[@]}" "$PKG/native/c/$k.c" -o "$OUT/lib/libvnx_$k.so"
    done
    aarch64-linux-gnu-g++ "${cxxflags[@]}" "$PKG/native/cpp/outerlist.cpp" -o "$OUT/lib/libvnx_outerlist.so"
    cp "$CARGO_TARGET_DIR/$TARGET/release/libvnx_engine.so" "$OUT/lib/" 2>/dev/null || echo "note: run 'engine' first for libvnx_engine.so"
    cp "$CARGO_TARGET_DIR/$TARGET/release/libvnx_integrity.so" "$OUT/lib/" 2>/dev/null || echo "note: run 'integrity' first for libvnx_integrity.so"
    file "$OUT"/lib/*.so
}

pytest_aarch64() {
    : "${PYROOT:?set PYROOT to the aarch64 CPython root (sysroot/ and site/), deployment doc §3.4}"
    local L="$OUT/lib"
    export VNXDNA_NATIVE_LIB="$L/libvnx_align.so" VNXDNA_RS_LIB="$L/libvnx_rs.so" VNXDNA_READS_LIB="$L/libvnx_reads.so"
    export VNXDNA_CLUSTER_LIB="$L/libvnx_cluster.so" VNXDNA_ORIENT_LIB="$L/libvnx_orient.so"
    export VNXDNA_STRAND_LIB="$L/libvnx_strand.so" VNXDNA_OUTERLIST_LIB="$L/libvnx_outerlist.so"
    export VNXDNA_ENGINE_LIB="$L/libvnx_engine.so" VNXDNA_INTEGRITY_LIB="$L/libvnx_integrity.so"
    export CC=aarch64-linux-gnu-gcc CXX=aarch64-linux-gnu-g++     # any build a test triggers targets aarch64 too
    export QEMU_LD_PREFIX="$PYROOT/sysroot" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
    export PYTHONPATH="$ROOT/src:$ROOT/tests:$PYROOT/site"
    cd "$ROOT"
    "$QEMU" -L "$PYROOT/sysroot" "$PYROOT/sysroot/usr/bin/python3.12" -m pytest -p no:cacheprovider "$@"
}

cmd="${1:-check}"; shift || true
case "$cmd" in
    check) check ;;
    vnxd) check && crate vnxd ;;
    engine) check && crate engine ;;
    integrity) check && crate integrity ;;
    ckernels) check && ckernels ;;
    pytest) pytest_aarch64 "$@" ;;
    all) check && crate vnxd && crate engine && crate integrity && ckernels ;;
    *) sed -n '2,20p' "$0"; exit 2 ;;
esac
