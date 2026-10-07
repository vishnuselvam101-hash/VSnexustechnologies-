#!/usr/bin/env bash
# ASan + UBSan check of all four native C kernels (CI job `sanitizers`; also usable locally).
#
# Runs the per-kernel sanitizer scripts, which build each kernel with gcc ASan+UBSan, clang ASan+UBSan (when clang
# and its ASan runtime are installed) and clang UBSan trap mode, check that the sanitized library is the one loaded,
# run the kernel's native equivalence tests and differential stress fuzz against the NumPy reference, and finish
# with a canary that ASan must catch:
#   align  benchmarks/v5/native_alignment/sanitizers.sh   tests/v5/test_native_alignment*.py + stress_fuzz.py
#   reads  benchmarks/v6/native_reads/sanitizers.sh       tests/v6/native/test_native_reads*.py + stress_fuzz.py
#   rs     benchmarks/v6/native_rs/sanitizers.sh          tests/v6/native/test_native_rs*.py + stress_fuzz.py
#   cluster benchmarks/v7/native_cluster/sanitizers.sh   tests/v7/test_native_cluster.py + stress_fuzz.py
# The fuzz budgets are CI-sized (override with ALIGN_ROUNDS, READS_CASES, RS_ROUNDS, CLUSTER_ROUNDS); the per-kernel scripts'
# own defaults are the larger development budgets.
#
# usage: bash tools/sanitizers.sh [workdir]      (run from the repository root; PY selects the interpreter)
set -u
W=${1:-$(mktemp -d)}
mkdir -p "$W"
export PY=${PY:-python}
export PYTHONPATH=${PYTHONPATH:-src}
fail=0
summary=""
step() {  # label command...
    local label=$1; shift
    echo "==================== $label"
    "$@"
    local rc=$?
    summary="$summary$label: $([ $rc -eq 0 ] && echo PASS || echo "FAIL (exit $rc)")"$'\n'
    [ $rc -eq 0 ] || fail=1
}
step align env TESTS="tests/v5/test_native_alignment.py tests/v5/test_native_alignment_equivalence.py" \
    bash benchmarks/v5/native_alignment/sanitizers.sh "$W/align" "${ALIGN_ROUNDS:-100}"
step reads bash benchmarks/v6/native_reads/sanitizers.sh "$W/reads" "${READS_CASES:-5000}"
step rs bash benchmarks/v6/native_rs/sanitizers.sh "$W/rs" "${RS_ROUNDS:-200}"
step cluster bash benchmarks/v7/native_cluster/sanitizers.sh "$W/cluster" "${CLUSTER_ROUNDS:-30}"
echo "==================== summary"
printf '%s' "$summary"
echo "overall: $([ $fail -eq 0 ] && echo PASS || echo FAIL)"
exit $fail
