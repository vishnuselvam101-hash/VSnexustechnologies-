#!/usr/bin/env bash
# Hybrid architecture: memory-safety and warning checks of the native strand builder (src/vnxdna/native/c/strand.c).
# Development and CI only.
#
#   0. warning report: gcc and clang with -Wconversion -Wsign-conversion -Wshadow -Wpedantic (reported, not gating)
#   1. gcc   ASan + UBSan, 2. clang ASan + UBSan, 3. clang UBSan trap mode (integer, bounds, nullability)
# Each sanitizer build runs tests/arch/test_native_strand.py (native = NumPy reference on every profile and constraint
# set, C argument checks) after checking that the sanitized library is the one loaded. With clang's libFuzzer, the
# harness benchmarks/arch/native_strand/strand_fuzz.c then runs for FUZZ_SECONDS (default 60).
#
# usage: bash benchmarks/arch/native_strand/sanitizers.sh [workdir]     (run from the repository root)
set -u
W=${1:-$(mktemp -d)}
mkdir -p "$W"
PY=${PY:-python}
export PYTHONPATH=${PYTHONPATH:-src}
SRC=src/vnxdna/native/c/strand.c
TESTS=${TESTS:-tests/arch/test_native_strand.py}
fail=0
echo "kernel source sha256: $(sha256sum $SRC | cut -d' ' -f1)"
echo "== warnings (reported, not gating)"
WFLAGS="-O3 -std=c11 -fPIC -shared -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wpedantic"
for cc in gcc clang; do
    command -v $cc >/dev/null || { echo "$cc: not available"; continue; }
    out=$($cc $WFLAGS $SRC -o "$W/warn_$cc.so" 2>&1); rc=$?
    echo "$cc: exit=$rc warnings=$(printf '%s' "$out" | grep -c 'warning:')"
    [ $rc -eq 0 ] || fail=1
done
echo "== sanitizer builds"
SAN="-O1 -g -std=c11 -fPIC -shared -Wall -Wextra -Werror -fno-omit-frame-pointer"
gcc $SAN -fsanitize=address,undefined -fno-sanitize-recover=all $SRC -o "$W/gcc_asan_ubsan.so" || exit 1
builds="gcc-asan-ubsan:$W/gcc_asan_ubsan.so:$(gcc -print-file-name=libasan.so) $(gcc -print-file-name=libubsan.so)"
if command -v clang >/dev/null; then
    clang $SAN -fsanitize=address,undefined -fno-sanitize-recover=all -shared-libsan $SRC -o "$W/clang_asan_ubsan.so" || exit 1
    pre="$(clang -print-file-name=libclang_rt.asan-x86_64.so)"
    [ -f "$pre" ] && builds="$builds;clang-asan-ubsan:$W/clang_asan_ubsan.so:$pre"
    clang $SAN -fsanitize=undefined,integer,bounds,nullability -fno-sanitize=unsigned-integer-overflow,unsigned-shift-base \
        -fsanitize-trap=all $SRC -o "$W/clang_ubsan_trap.so" || exit 1
    builds="$builds;clang-ubsan-trap:$W/clang_ubsan_trap.so:"
fi
export ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:halt_on_error=1:detect_stack_use_after_return=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
IFS=';' read -ra list <<< "$builds"
for b in "${list[@]}"; do
    IFS=':' read -r label lib pre <<< "$b"
    loaded=$(VNXDNA_STRAND_LIB=$lib LD_PRELOAD=$pre $PY -c "from vnxdna.native import strand as n; print(n.status()['library'])" 2>&1 | tail -1)
    if [ "$loaded" != "$lib" ]; then echo "$label: sanitized library NOT loaded ($loaded)"; fail=1; continue; fi
    VNXDNA_STRAND_LIB=$lib VNXDNA_STRAND_BACKEND=native LD_PRELOAD=$pre $PY -m pytest -q -p no:cacheprovider -o addopts= \
        $TESTS > "$W/$label-pytest.log" 2>&1
    rc=$?
    echo "$label: native strand tests exit=$rc ($(tail -1 "$W/$label-pytest.log"))"
    [ $rc -eq 0 ] || { fail=1; grep -m5 -E "ERROR|runtime error|FAILED" "$W/$label-pytest.log"; }
done
if command -v clang >/dev/null && clang -g -O1 -fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined \
        benchmarks/arch/native_strand/strand_fuzz.c $SRC -o "$W/strand_fuzz" 2>/dev/null; then
    mkdir -p "$W/corpus"
    (cd "$W" && ./strand_fuzz -max_total_time="${FUZZ_SECONDS:-60}" -max_len=4096 corpus > fuzz.log 2>&1); rc=$?
    echo "libFuzzer: exit=$rc ($(grep -E '^Done' "$W/fuzz.log" | tail -1))"
    [ $rc -eq 0 ] || { fail=1; tail -20 "$W/fuzz.log"; }
else
    echo "libFuzzer: not available"
fi
echo "overall: $([ $fail -eq 0 ] && echo PASS || echo FAIL)"
exit $fail
