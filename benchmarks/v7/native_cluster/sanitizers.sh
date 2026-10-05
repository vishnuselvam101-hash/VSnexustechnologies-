#!/usr/bin/env bash
# V7 A2: memory-safety and warning checks of the native read-clustering kernels (src/vnxdna/native/c/cluster.c).
# Development and CI only; users never need these toolchains.
#
#   0. warning report: gcc and clang, -O3 -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wpedantic, and the
#      gcc static analyser (-fanalyzer); reported, not gating (the gating build is -Wall -Wextra -Werror)
#   1. gcc   AddressSanitizer + UndefinedBehaviorSanitizer (runtimes preloaded into the Python process)
#   2. clang AddressSanitizer + UndefinedBehaviorSanitizer (shared compiler-rt runtime preloaded)
#   3. clang UBSan trap mode with the integer, bounds and nullability groups (no runtime needed)
# For each sanitizer build: the native = reference tests (tests/v7/test_native_cluster.py: golden hashes, randomized
# and hypothesis equivalence, every forward-backward variant, C argument checks, a whole decode) and the differential
# stress fuzz, after checking that the sanitized library is the one actually loaded. Finally a canary misuses the C
# ABI on purpose (a buffer size larger than the buffer); ASan must report it, which proves the instrumentation is live.
# MemorySanitizer is not run here (it needs an instrumented Python and NumPy).
#
# usage: bash benchmarks/v7/native_cluster/sanitizers.sh [workdir] [fuzz-rounds]     (run from the repository root)
set -u
W=${1:-$(mktemp -d)}
ROUNDS=${2:-100}
mkdir -p "$W"
PY=${PY:-/root/vnx-dna-lab/.venv/bin/python}
export PYTHONPATH=${PYTHONPATH:-src}
SRC=src/vnxdna/native/c/cluster.c
TESTS=${TESTS:-tests/v7/test_native_cluster.py}
fail=0
echo "kernel source sha256: $(sha256sum $SRC | cut -d' ' -f1)"
echo "gcc:   $(gcc --version | head -1)"
echo "clang: $(clang --version 2>/dev/null | head -1 || echo 'not available')"
echo "commit: $(git rev-parse HEAD 2>/dev/null)  dirty: $(git status --porcelain --untracked-files=no 2>/dev/null | wc -l) tracked files modified"

echo "== warnings (-O3 -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wpedantic; reported, not gating)"
WFLAGS="-O3 -std=c11 -fPIC -shared -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wpedantic"
for cc in gcc clang; do
    command -v $cc >/dev/null || { echo "$cc: not available"; continue; }
    out=$($cc $WFLAGS $SRC -o "$W/warn_$cc.so" 2>&1); rc=$?
    echo "$cc: exit=$rc warnings=$(printf '%s' "$out" | grep -c 'warning:')"
    [ $rc -eq 0 ] || fail=1
done
out=$(gcc -O2 -std=c11 -fPIC -shared -fanalyzer $SRC -o "$W/analyzer.so" 2>&1); rc=$?
echo "gcc -fanalyzer: exit=$rc warnings=$(printf '%s' "$out" | grep -c 'warning:')"
[ -n "$out" ] && printf '%s\n' "$out" | grep 'warning:' | head -20

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
    # integer group minus the two unsigned checks: unsigned wrap-around (defined behaviour) is intended in splitmix64
    # and in the Myers bit-vector add and shifts (myers); everything else is trapped,
    # including every implicit integer conversion check
    clang -O1 -g -std=c11 -fPIC -shared -Wall -Wextra -Werror -fsanitize=undefined,integer,bounds,nullability \
        -fno-sanitize=unsigned-integer-overflow,unsigned-shift-base \
        -fsanitize-trap=all $SRC -o "$W/clang_ubsan_trap.so" || exit 1
fi
export ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:halt_on_error=1:detect_stack_use_after_return=1:strict_init_order=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1

run() {  # label lib preload
    local label=$1 lib=$2 pre=$3
    loaded=$(VNXDNA_CLUSTER_LIB=$lib LD_PRELOAD=$pre $PY -c "from vnxdna.native import cluster as n; print(n.status()['library'])" 2>&1 | tail -1)
    if [ "$loaded" != "$lib" ]; then echo "$label: sanitized library NOT loaded ($loaded)"; fail=1; return; fi
    VNXDNA_CLUSTER_LIB=$lib LD_PRELOAD=$pre $PY -m pytest -q -p no:cacheprovider -o addopts= $TESTS \
        > "$W/$label-pytest.log" 2>&1
    local rc=$?
    echo "$label: native cluster tests exit=$rc ($(tail -1 "$W/$label-pytest.log"))"
    [ $rc -eq 0 ] || { fail=1; grep -m5 -E "ERROR|runtime error|FAILED" "$W/$label-pytest.log"; }
    VNXDNA_CLUSTER_LIB=$lib LD_PRELOAD=$pre $PY benchmarks/v7/native_cluster/stress_fuzz.py --rounds "$ROUNDS" --seed 7 \
        > "$W/$label-fuzz.log" 2>&1
    rc=$?
    echo "$label: stress fuzz exit=$rc $(tail -1 "$W/$label-fuzz.log" | cut -c1-260)"
    [ $rc -eq 0 ] || fail=1
}
run gcc-asan-ubsan "$W/gcc_asan_ubsan.so" "$GCC_PRE"
if [ -n "$CLANG_PRE" ]; then run clang-asan-ubsan "$W/clang_asan_ubsan.so" "$CLANG_PRE"; else echo "clang-asan-ubsan: NOT RUN"; fi
if [ -f "$W/clang_ubsan_trap.so" ]; then run clang-ubsan-trap "$W/clang_ubsan_trap.so" ""; else echo "clang-ubsan-trap: NOT RUN"; fi

cat > "$W/canary.py" <<'PYEOF'
import numpy as np
from vnxdna.native import cluster as nc
buf = np.zeros(4096, np.uint8)[:16].copy()   # a 16-byte buffer, but the call claims 4096 bytes
off = np.array([0, 0], np.int64); ln = np.array([16, 3000], np.int64)
ia = np.array([0], np.int64); ib = np.array([1], np.int64); out = np.zeros(1, np.int64)
p = nc._ptr
rc = nc._load().vnx_cl_banded(2, p(buf), 4096, p(off), p(ln), 1, p(ia), p(ib), None, 4, p(out))
print("returned", rc, "(sanitizer did NOT fire)")
PYEOF
canary() {  # label lib preload
    if VNXDNA_CLUSTER_LIB=$2 LD_PRELOAD=$3 $PY "$W/canary.py" 2>&1 | grep -q "ERROR: AddressSanitizer"; then
        echo "$1 canary: AddressSanitizer fired as expected"
    else
        echo "$1 canary: AddressSanitizer did NOT fire"; fail=1
    fi
}
canary gcc "$W/gcc_asan_ubsan.so" "$GCC_PRE"
[ -n "$CLANG_PRE" ] && canary clang "$W/clang_asan_ubsan.so" "$CLANG_PRE"
echo "overall: $([ $fail -eq 0 ] && echo PASS || echo FAIL)"
exit $fail
