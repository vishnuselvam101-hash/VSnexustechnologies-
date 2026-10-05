#!/usr/bin/env bash
# MemorySanitizer gate for the three native C kernels (align, reads, rs). CI job `msan`; also usable locally.
#
# Why standalone programs: clang ships the MSan runtime only as a static archive linked into the main executable (no
# libclang_rt.msan-*.so, unlike ASan), and MSan needs every instruction that writes memory in the process to be
# instrumented. An MSan-built kernel .so therefore cannot be loaded by a stock CPython (undefined __msan_* symbols),
# and running it in-process would need CPython, NumPy and NumPy's BLAS rebuilt with -fsanitize=memory. Instead every
# kernel is #included into small C programs that need neither CPython nor NumPy:
#
#   1. canary      tools/msan/canary.c: the RS kernel decodes an uninitialised codeword; MSan must stop it (exit 86).
#                  If it does not, the build is not instrumented and the gate fails.
#   2. golden      tools/msan/make_vectors.py writes inputs + expected outputs with the Python reference (Phase 1 golden
#                  read sets and their projection SHA-256s, the committed RS golden vectors, randomized rounds);
#                  tools/msan/replay_{align,rs,reads}.c call every exported entry point (vnx_align_batch/_path/_profiled;
#                  vnx_rs_decode_batch at auto and every CPU-supported level, in and out of place; vnx_reads_init/
#                  feed/new_batch with two batching modes) into never-initialised output buffers and compare every byte.
#   3. harnesses   fuzz/native/*_fuzz.c (their invariants included) through tools/msan/standalone_main.c: the seed
#                  corpus, the regression inputs and MSAN_RANDOM_CASES seeded random inputs per target.
#   4. libFuzzer   optional coverage-guided campaigns (-fsanitize=fuzzer,memory) of MSAN_FUZZ_SECS seconds per target
#                  (0 skips them), corpus in the work directory.
#
# Flags: clang -O1 -g -fsanitize=memory -fsanitize-memory-track-origins=2 -fno-sanitize-recover=memory (clang >= 16
# also checks parameters and return values eagerly). Exit 0 only if every step passes.
#
# usage: bash tools/msan.sh [workdir]   (from the repository root; PY selects the interpreter, CC the clang)
# env:   ALIGN_ROUNDS (200) RS_ROUNDS (1000) READS_CASES (3000) MSAN_RANDOM_CASES (20000) MSAN_FUZZ_SECS (0)
set -u
W=${1:-$(mktemp -d)}
mkdir -p "$W/bin" "$W/vectors"
PY=${PY:-python}
CC=${CC:-clang}
export PYTHONPATH=${PYTHONPATH:-src}
FLAGS=(-g -O1 -std=c11 -D_DEFAULT_SOURCE -fsanitize=memory -fsanitize-memory-track-origins=2 -fno-sanitize-recover=memory
       -fno-omit-frame-pointer -Isrc -Itools/msan)
export MSAN_OPTIONS=${MSAN_OPTIONS:-exitcode=86:print_stats=0}
fail=0
summary=""
record() {  # label rc
    summary="$summary$1: $([ "$2" -eq 0 ] && echo PASS || echo "FAIL (exit $2)")"$'\n'
    [ "$2" -eq 0 ] || fail=1
}

echo "==================== toolchain"
"$CC" --version | head -1
echo "commit $(git rev-parse --short HEAD 2>/dev/null || echo unknown)  cpu levels: see rs replay levels_mask"

echo "==================== canary"
if "$CC" "${FLAGS[@]}" tools/msan/canary.c -o "$W/bin/canary" -lm; then
    "$W/bin/canary" > "$W/canary.log" 2>&1
    rc=$?
    grep -m1 'use-of-uninitialized-value' "$W/canary.log"
    if [ $rc -eq 86 ] && grep -q 'use-of-uninitialized-value' "$W/canary.log"; then record canary 0
    else echo "canary did not fire (exit $rc): MSan is not active"; record canary 1; fi
else
    echo "MSan build failed (is the clang MSan runtime installed? apt: libclang-rt-dev)"; record canary 2
fi

echo "==================== golden vectors (reference outputs)"
"$PY" tools/msan/make_vectors.py "$W/vectors" --align-rounds "${ALIGN_ROUNDS:-200}" --rs-rounds "${RS_ROUNDS:-1000}" \
    --reads-cases "${READS_CASES:-3000}" | tee "$W/vectors/summary.json"
record vectors "${PIPESTATUS[0]}"

for k in align rs reads; do
    echo "==================== replay $k"
    if "$CC" "${FLAGS[@]}" "tools/msan/replay_$k.c" -o "$W/bin/replay_$k" -lm; then
        "$W/bin/replay_$k" "$W/vectors/$k.bin"
        record "replay-$k" $?
    else record "replay-$k" 2; fi
done

for k in align reads rs; do
    echo "==================== harness $k (corpus + regressions + random)"
    if "$CC" "${FLAGS[@]}" "fuzz/native/${k}_fuzz.c" tools/msan/standalone_main.c -o "$W/bin/harness_$k" -lm; then
        MSAN_RANDOM_CASES=${MSAN_RANDOM_CASES:-20000} "$W/bin/harness_$k" "fuzz/corpus/$k" "fuzz/regressions/$k"
        record "harness-$k" $?
    else record "harness-$k" 2; fi
done

secs=${MSAN_FUZZ_SECS:-0}
if [ "$secs" -gt 0 ]; then
    for k in align reads rs; do
        echo "==================== libFuzzer+MSan $k ${secs}s"
        mkdir -p "$W/corpus/$k" "$W/crashes/$k"
        if "$CC" -g -O1 -std=c11 -fsanitize=fuzzer,memory -fsanitize-memory-track-origins=2 -fno-omit-frame-pointer -Isrc \
            "fuzz/native/${k}_fuzz.c" -o "$W/bin/libfuzzer_$k" -lm; then
            "$W/bin/libfuzzer_$k" -max_total_time="$secs" -timeout=25 -rss_limit_mb=2048 -print_final_stats=1 \
                -artifact_prefix="$W/crashes/$k/" "$W/corpus/$k" "fuzz/corpus/$k" "fuzz/regressions/$k" > "$W/libfuzzer_$k.log" 2>&1
            rc=$?
            grep -E 'stat::number_of_executed_units|stat::new_units_added' "$W/libfuzzer_$k.log" | tr '\n' ' '; echo
            record "libfuzzer-$k" $rc
        else record "libfuzzer-$k" 2; fi
    done
fi

echo "==================== summary"
printf '%s' "$summary"
echo "overall: $([ $fail -eq 0 ] && echo PASS || echo FAIL)"
exit $fail
