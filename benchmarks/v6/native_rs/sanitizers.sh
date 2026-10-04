#!/usr/bin/env bash
# V6 Phase 1 items 6+7: memory-safety and warning checks of the native inner RS decoder (src/vnxdna/v6/native/rs.c).
# Development only; users never need these toolchains.
#
#   0. warning report: gcc and clang, -O3 -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wpedantic, and the
#      gcc static analyser (-fanalyzer)
#   1. gcc   AddressSanitizer + UndefinedBehaviorSanitizer (runtimes preloaded into the Python process)
#   2. clang AddressSanitizer + UndefinedBehaviorSanitizer (shared compiler-rt runtime preloaded)
#   3. clang UBSan trap mode with the integer, bounds and nullability groups (no runtime needed)
# For each sanitizer build: the native RS tests (tests/v6/native/test_native_rs*.py, every dispatch level) and the
# differential stress fuzz, after checking that the sanitized library is the one actually loaded. Finally a canary
# misuses the C ABI on purpose (a buffer one row short); ASan must report it, which proves the instrumentation is live.
#
# usage: bash benchmarks/v6/native_rs/sanitizers.sh [workdir] [fuzz-rounds]     (run from the repository root)
set -u
W=${1:-$(mktemp -d)}
ROUNDS=${2:-600}
mkdir -p "$W"
PY=${PY:-/root/vnx-dna-lab/.venv/bin/python}
export PYTHONPATH=${PYTHONPATH:-src}
SRC=src/vnxdna/v6/native/rs.c
fail=0
echo "kernel source sha256: $(sha256sum $SRC | cut -d' ' -f1)"
echo "gcc:   $(gcc --version | head -1)"
echo "clang: $(clang --version 2>/dev/null | head -1 || echo 'not available')"
echo "commit: $(git rev-parse HEAD 2>/dev/null)  dirty: $(git status --porcelain --untracked-files=no 2>/dev/null | wc -l) tracked files modified"

echo "== warnings (-O3 -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wpedantic)"
WFLAGS="-O3 -std=c11 -fPIC -shared -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wpedantic"
for cc in gcc clang; do
    command -v $cc >/dev/null || { echo "$cc: not available"; continue; }
    out=$($cc $WFLAGS $SRC -o "$W/warn_$cc.so" 2>&1); rc=$?
    echo "$cc: exit=$rc warnings=$(printf '%s' "$out" | grep -c 'warning:')"
    [ -n "$out" ] && printf '%s\n' "$out"
    [ $rc -eq 0 ] || fail=1
done
out=$(gcc -O2 -std=c11 -fPIC -shared -fanalyzer $SRC -o "$W/analyzer.so" 2>&1); rc=$?
echo "gcc -fanalyzer: exit=$rc warnings=$(printf '%s' "$out" | grep -c 'warning:')"
[ -n "$out" ] && printf '%s\n' "$out"

echo "== sanitizer builds"
gcc -O1 -g -std=c11 -fPIC -shared -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all \
    -fno-omit-frame-pointer $SRC -o "$W/gcc_asan_ubsan.so" || exit 1
GCC_PRE="$(gcc -print-file-name=libasan.so) $(gcc -print-file-name=libubsan.so)"
CLANG_PRE=""
if command -v clang >/dev/null; then
    clang -O1 -g -std=c11 -fPIC -shared -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all \
        -fno-omit-frame-pointer -shared-libsan $SRC -o "$W/clang_asan_ubsan.so" || exit 1
    CLANG_PRE="$(clang -print-file-name=libclang_rt.asan-x86_64.so)"
    [ -f "$CLANG_PRE" ] || { echo "clang ASan runtime not found ($CLANG_PRE)"; CLANG_PRE=""; }
    clang -O1 -g -std=c11 -fPIC -shared -Wall -Wextra -Werror -fsanitize=undefined,integer,bounds,nullability \
        -fsanitize-trap=all $SRC -o "$W/clang_ubsan_trap.so" || exit 1
fi
export ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:halt_on_error=1:detect_stack_use_after_return=1:strict_init_order=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1

run() {  # label lib preload
    local label=$1 lib=$2 pre=$3
    loaded=$(VNXDNA_RS_LIB=$lib LD_PRELOAD=$pre $PY -c "from vnxdna.v6 import native_rs as n; print(n.status()['library'])" 2>&1 | tail -1)
    if [ "$loaded" != "$lib" ]; then echo "$label: sanitized library NOT loaded ($loaded)"; fail=1; return; fi
    VNXDNA_RS_LIB=$lib LD_PRELOAD=$pre $PY -m pytest -q -p no:cacheprovider -o addopts= \
        tests/v6/native/test_native_rs.py tests/v6/native/test_native_rs_fuzz.py > "$W/$label-pytest.log" 2>&1
    local rc=$?
    echo "$label: native RS tests exit=$rc ($(tail -1 "$W/$label-pytest.log"))"
    [ $rc -eq 0 ] || { fail=1; grep -m5 -E "ERROR|runtime error|FAILED" "$W/$label-pytest.log"; }
    VNXDNA_RS_LIB=$lib LD_PRELOAD=$pre $PY benchmarks/v6/native_rs/stress_fuzz.py --rounds "$ROUNDS" --seed 7 \
        > "$W/$label-fuzz.log" 2>&1
    rc=$?
    echo "$label: stress fuzz exit=$rc $(tail -1 "$W/$label-fuzz.log" | cut -c1-200)"
    [ $rc -eq 0 ] || fail=1
}
run gcc-asan-ubsan "$W/gcc_asan_ubsan.so" "$GCC_PRE"
if [ -n "$CLANG_PRE" ]; then run clang-asan-ubsan "$W/clang_asan_ubsan.so" "$CLANG_PRE"; else echo "clang-asan-ubsan: NOT RUN"; fi
if [ -f "$W/clang_ubsan_trap.so" ]; then run clang-ubsan-trap "$W/clang_ubsan_trap.so" ""; else echo "clang-ubsan-trap: NOT RUN"; fi

cat > "$W/canary.py" <<'EOF'
import numpy as np
from vnxdna.v6 import native_rs as nr
cw = np.zeros((3, 70), np.uint8)           # three rows, but the call claims four
out = np.zeros((4, 70), np.uint8); ok = np.zeros(4, np.uint8); errata = np.zeros(4, np.int64)
p = nr._ptr
rc = nr._load().vnx_rs_decode_batch(4, 70, 16, p(cw), None, p(out), p(ok), p(errata), 1)
print("returned", rc, "(sanitizer did NOT fire)")
EOF
canary() {  # label lib preload
    if VNXDNA_RS_LIB=$2 LD_PRELOAD=$3 $PY "$W/canary.py" 2>&1 | grep -q "ERROR: AddressSanitizer"; then
        echo "$1 canary: AddressSanitizer fired as expected"
    else
        echo "$1 canary: AddressSanitizer did NOT fire"; fail=1
    fi
}
canary gcc "$W/gcc_asan_ubsan.so" "$GCC_PRE"
[ -n "$CLANG_PRE" ] && canary clang "$W/clang_asan_ubsan.so" "$CLANG_PRE"
echo "overall: $([ $fail -eq 0 ] && echo PASS || echo FAIL)"
exit $fail
