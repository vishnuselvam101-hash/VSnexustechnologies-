#!/usr/bin/env bash
# V6 Phase 1: memory-safety checks of the native streaming read parser (development only).
#
#   1. gcc   AddressSanitizer + UndefinedBehaviorSanitizer (runtime preloaded into the Python process)
#   2. clang AddressSanitizer + UndefinedBehaviorSanitizer (clang runtime preloaded)
#   3. clang UBSan in trap mode with integer, bounds and nullability checks (needs no runtime)
#   4. valgrind memcheck over the directed tests with the normal -O3 build (if valgrind is installed)
# For each sanitizer build: the native read-parser tests (directed + fuzz + hypothesis; the RSS test is excluded
# because sanitizer shadow memory distorts RSS) with VNXDNA_READS_BACKEND=native, then the differential stress
# fuzz. Finally a canary misuses the C ABI on purpose (capacity larger than the buffer); ASan must report it,
# which proves the instrumentation is live.
#
# usage: benchmarks/v6/native_reads/sanitizers.sh [workdir] [fuzz_cases]   (run from the repository root)
set -u
W=${1:-$(mktemp -d)}
CASES=${2:-20000}
mkdir -p "$W"
PY=${PY:-.venv/bin/python}
SRC=src/vnxdna/v6/native/reads.c
TESTS="tests/v6/native/test_native_reads.py tests/v6/native/test_native_reads_fuzz.py"
export PYTHONPATH=src VNXDNA_READS_BACKEND=native
fail=0
echo "kernel source sha256: $(sha256sum $SRC | cut -d' ' -f1)"
echo "gcc: $(gcc --version | head -1)"
echo "clang: $(clang --version | head -1)"

gcc -O1 -g -std=c11 -fPIC -shared -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all \
    -fno-omit-frame-pointer $SRC -o "$W/gcc_asan_ubsan.so" || exit 1
clang -O1 -g -std=c11 -fPIC -shared -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all \
    -fno-omit-frame-pointer -shared-libsan $SRC -o "$W/clang_asan_ubsan.so" || exit 1
clang -O1 -g -std=c11 -fPIC -shared -Wall -Wextra -Werror -fsanitize=undefined,integer,bounds,nullability \
    -fsanitize-trap=all $SRC -o "$W/clang_ubsan_trap.so" || exit 1
GCC_PRE="$(gcc -print-file-name=libasan.so) $(gcc -print-file-name=libubsan.so)"
CLANG_PRE="$(clang -print-file-name=libclang_rt.asan-x86_64.so)"
export ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:halt_on_error=1:detect_stack_use_after_return=1:strict_init_order=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1

run() {  # label lib preload
    local label=$1 lib=$2 pre=$3
    VNXDNA_READS_LIB=$lib LD_PRELOAD=$pre $PY -c "from vnxdna.v6 import native_reads as nr; s = nr.status(); \
assert s['library'] == '$lib', s; print('$label: library', s['library'])" || { fail=1; return; }
    VNXDNA_READS_LIB=$lib LD_PRELOAD=$pre $PY -m pytest -q -p no:cacheprovider -o addopts= --color=no $TESTS > "$W/$label-pytest.log" 2>&1
    local rc=$?
    echo "$label: native read-parser tests exit=$rc ($(tail -1 "$W/$label-pytest.log"))"
    grep -E "ERROR: AddressSanitizer|runtime error" "$W/$label-pytest.log" | head -5
    [ $rc -eq 0 ] || fail=1
    VNXDNA_READS_LIB=$lib LD_PRELOAD=$pre $PY benchmarks/v6/native_reads/stress_fuzz.py --cases "$CASES" --workers 4 \
        > "$W/$label-fuzz.log" 2>&1
    rc=$?
    echo "$label: stress fuzz exit=$rc $(tail -1 "$W/$label-fuzz.log")"
    grep -E "ERROR: AddressSanitizer|runtime error" "$W/$label-fuzz.log" | head -5
    [ $rc -eq 0 ] || fail=1
}
run gcc-asan-ubsan "$W/gcc_asan_ubsan.so" "$GCC_PRE"
run clang-asan-ubsan "$W/clang_asan_ubsan.so" "$CLANG_PRE"
run clang-ubsan-trap "$W/clang_ubsan_trap.so" ""

if command -v valgrind > /dev/null; then
    $PY -m vnxdna.v6.native_reads build > /dev/null && \
    PYTHONMALLOC=malloc valgrind --error-exitcode=99 --errors-for-leak-kinds=none --suppressions=/dev/null -q \
        $PY -m pytest -q -p no:cacheprovider -o addopts= --color=no tests/v6/native/test_native_reads.py \
        -k "not long_lines and not multi_block and not small_limits and not committed" > "$W/valgrind.log" 2>&1
    rc=$?
    echo "valgrind memcheck (-O3 build, directed tests): exit=$rc ($(grep -E 'passed|failed' "$W/valgrind.log" | tail -1))"
    grep -E "Invalid (read|write)|uninitialised|Conditional jump" "$W/valgrind.log" | grep -v "libpython\|_multiarray" | head -5
    [ $rc -eq 0 ] || fail=1
else
    echo "valgrind: not installed (skipped)"
fi

cat > "$W/canary.py" <<'EOF'
import numpy as np
from vnxdna.v6 import native_reads as nr
lib = nr._load()
st = np.zeros(lib.vnx_reads_state_size(), np.uint8)
lib.vnx_reads_init(st.ctypes.data, st.size, 1, 100, 4296, 1 << 23)
data = np.frombuffer(b"@r\n" + b"A" * 50 + b"\n+\n" + b"I" * 50 + b"\n", np.uint8).copy()
codes = np.zeros(16, np.uint8)                   # claims capacity 200: the kernel writes past the 16 bytes
quals = np.zeros(200, np.uint8)
lens, inv, info = np.zeros(4, np.int64), np.zeros(4, np.uint8), np.zeros(4, np.int64)
rc = lib.vnx_reads_feed(st.ctypes.data, data.ctypes.data, data.size, 1, codes.ctypes.data, quals.ctypes.data, 200,
                        lens.ctypes.data, inv.ctypes.data, 4, info.ctypes.data, None)
print("returned", rc, "(sanitizer did NOT fire)")
EOF
for pair in "gcc_asan_ubsan:$GCC_PRE" "clang_asan_ubsan:$CLANG_PRE"; do
    name=${pair%%:*}; pre=${pair#*:}
    if VNXDNA_READS_LIB="$W/$name.so" LD_PRELOAD=$pre $PY "$W/canary.py" 2>&1 | grep -q "ERROR: AddressSanitizer: heap-buffer-overflow"; then
        echo "canary ($name): AddressSanitizer fired as expected"
    else
        echo "canary ($name): AddressSanitizer did NOT fire"; fail=1
    fi
done
echo "overall: $([ $fail -eq 0 ] && echo PASS || echo FAIL)"
exit $fail
