#!/usr/bin/env bash
# V13: memory-safety checks of the streaming engine library (src/vnxdna/native/rust/engine). Development and CI only.
#
# The engine is a Rust cdylib (std only) that links the reused C kernels (v6/native/rs.c, v5/native/align.c,
# native/c/orient.c) compiled by its build.rs. Stable Rust has no sanitizer, so the sanitized build covers:
#   1. the C kernels inside the library: gcc ASan + UBSan (VNX_ENGINE_CFLAGS, -fno-sanitize-recover=all), the engine
#      tests (tests/v13/test_engine.py: scanner fuzz, pass-1 equality, scheduler determinism, whole decodes) run
#      with the sanitized library (LD_PRELOAD of the ASan/UBSan runtimes) after checking that it is the one loaded;
#   2. the Rust side: `cargo test` with RUSTFLAGS=-D warnings (unit tests of the scanner, frame CRC, fingerprint
#      tables, pool, config checks), plus the release build with debug assertions (overflow and bounds checks on every
#      arithmetic and slice operation of the engine) running the same engine tests;
#   3. a canary: an out-of-bounds write in a throwaway C object linked the same way must be caught by ASan.
#
# usage: bash benchmarks/v13/native_engine/sanitizers.sh [workdir]     (run from the repository root; PY = interpreter)
set -u
W=${1:-$(mktemp -d)}
mkdir -p "$W"
PY=${PY:-python}
export PYTHONPATH=${PYTHONPATH:-src}
CRATE=src/vnxdna/native/rust/engine
TESTS=${TESTS:-tests/v13/test_engine.py}
fail=0
echo "engine sources sha256: $(cat $CRATE/src/*.rs $CRATE/build.rs | sha256sum | cut -d' ' -f1)"

echo "== 1. gcc ASan + UBSan of the C kernels linked into the engine"
export ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:halt_on_error=1:detect_stack_use_after_return=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
pre="$(gcc -print-file-name=libasan.so) $(gcc -print-file-name=libubsan.so)"
lib="$W/engine_asan_ubsan.so"
# Never `python -m vnxdna.native.engine build` here: that command only builds in place (the production library);
# the sanitized library is built by cargo into its own target directory and copied next to the logs.
VNX_ENGINE_CFLAGS="-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all" \
RUSTFLAGS="-C link-arg=-fsanitize=address,undefined" \
cargo build --release --offline --locked --manifest-path $CRATE/Cargo.toml --target-dir "$W/target-asan" > "$W/build-asan.log" 2>&1 \
    && cp "$W/target-asan/release/libvnx_engine.so" "$lib" || { echo "asan build failed"; tail -20 "$W/build-asan.log"; exit 1; }
nm -D "$lib" | grep -q __asan || { echo "asan build has no ASan symbols"; exit 1; }
loaded=$(VNXDNA_ENGINE_LIB=$lib LD_PRELOAD="$pre" $PY -c "from vnxdna.native import engine as n; print(n.status()['library'])" 2>&1 | tail -1)
if [ "$loaded" != "$lib" ]; then echo "sanitized library NOT loaded ($loaded)"; fail=1; else
    VNXDNA_ENGINE_LIB=$lib VNXDNA_ENGINE_BACKEND=native LD_PRELOAD="$pre" $PY -m pytest -q -p no:cacheprovider -o addopts= $TESTS \
        > "$W/asan-pytest.log" 2>&1
    rc=$?
    echo "gcc-asan-ubsan: engine tests exit=$rc ($(tail -1 "$W/asan-pytest.log"))"
    [ $rc -eq 0 ] || { fail=1; grep -m5 -E "ERROR|runtime error|FAILED" "$W/asan-pytest.log"; }
fi

echo "== 2. Rust: cargo test (-D warnings) and a debug-assertions build running the engine tests"
RUSTFLAGS="-D warnings" cargo test --release --offline --locked --manifest-path $CRATE/Cargo.toml --target-dir "$W/target-test" \
    > "$W/cargo-test.log" 2>&1
rc=$?
echo "cargo test: exit=$rc ($(grep -m1 'test result' "$W/cargo-test.log"))"
[ $rc -eq 0 ] || { fail=1; tail -20 "$W/cargo-test.log"; }
RUSTFLAGS="-C debug-assertions=yes -C overflow-checks=yes" cargo build --release --offline --locked --manifest-path $CRATE/Cargo.toml \
    --target-dir "$W/target-dbg" > "$W/build-dbg.log" 2>&1 || { echo "debug-assertions build failed"; fail=1; }
dbg="$W/target-dbg/release/libvnx_engine.so"
if [ -f "$dbg" ]; then
    VNXDNA_ENGINE_LIB=$dbg VNXDNA_ENGINE_BACKEND=native $PY -m pytest -q -p no:cacheprovider -o addopts= $TESTS > "$W/dbg-pytest.log" 2>&1
    rc=$?
    echo "debug-assertions: engine tests exit=$rc ($(tail -1 "$W/dbg-pytest.log"))"
    [ $rc -eq 0 ] || { fail=1; grep -m5 -E "panic|FAILED|overflow" "$W/dbg-pytest.log"; }
fi

echo "== 3. canary (ASan must catch an out-of-bounds write in the same build configuration)"
cat > "$W/canary.c" <<'EOF'
#include <stdlib.h>
int vnx_canary(void) { char *p = malloc(8); p[9] = 1; int r = p[0]; free(p); return r; }
EOF
gcc -O1 -g -fPIC -shared -fsanitize=address,undefined -fno-sanitize-recover=all "$W/canary.c" -o "$W/canary.so" \
    && LD_PRELOAD="$pre" $PY -c "import ctypes; ctypes.CDLL('$W/canary.so').vnx_canary()" > "$W/canary.log" 2>&1
# with -fno-sanitize-recover=all the first sanitizer to see the write halts the process: UBSan's object-size check
# ("runtime error: store to address ... insufficient space") or ASan's heap-buffer-overflow report; either is a catch
if grep -q "heap-buffer-overflow" "$W/canary.log"; then echo "canary: caught (ASan heap-buffer-overflow)"
elif grep -q "runtime error: store to address" "$W/canary.log"; then echo "canary: caught (UBSan object-size check)"
else echo "canary: NOT caught"; fail=1; fi
echo "overall: $([ $fail -eq 0 ] && echo PASS || echo FAIL)"
exit $fail
