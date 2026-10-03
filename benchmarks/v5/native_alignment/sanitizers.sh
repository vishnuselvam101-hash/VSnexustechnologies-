#!/usr/bin/env bash
# V5 Phase 2: memory-safety checks of the native aligner (development only; users never need these toolchains).
#
#   1. gcc AddressSanitizer + UndefinedBehaviorSanitizer build (runtime preloaded into the Python process)
#   2. clang UBSan in trap mode with all integer, bounds and nullability checks (needs no sanitizer runtime)
# For each build: the V5 test suite and the differential stress fuzz (400 rounds x 250 reads, every Projection
# field compared with the NumPy reference). Finally a canary that misuses the C ABI on purpose (offsets one entry
# short); ASan must report it, which proves the instrumentation is live.
#
# usage: benchmarks/v5/native_alignment/sanitizers.sh [workdir]     (run from the repository root)
set -u
W=${1:-$(mktemp -d)}
mkdir -p "$W"
PY=${PY:-.venv/bin/python}
SRC=src/vnxdna/v5/native/align.c
fail=0
echo "kernel source sha256: $(sha256sum $SRC | cut -d' ' -f1)"

gcc -O1 -g -std=c11 -fPIC -shared -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all \
    -fno-omit-frame-pointer $SRC -o "$W/gcc_asan_ubsan.so" || exit 1
clang -O1 -g -std=c11 -fPIC -shared -Wall -Wextra -Werror -fsanitize=undefined,integer,bounds,nullability \
    -fsanitize-trap=all $SRC -o "$W/clang_ubsan_trap.so" || exit 1
ASAN_PRE="$(gcc -print-file-name=libasan.so) $(gcc -print-file-name=libubsan.so)"
export ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:halt_on_error=1:detect_stack_use_after_return=1:strict_init_order=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1

run() {  # label lib preload
    local label=$1 lib=$2 pre=$3
    VNXDNA_NATIVE_LIB=$lib LD_PRELOAD=$pre $PY -m pytest -q -p no:cacheprovider tests/v5/ > "$W/$label-pytest.log" 2>&1
    local rc=$?
    echo "$label: tests/v5 exit=$rc ($(tail -1 "$W/$label-pytest.log"))"
    [ $rc -eq 0 ] || fail=1
    VNXDNA_NATIVE_LIB=$lib LD_PRELOAD=$pre $PY benchmarks/v5/native_alignment/stress_fuzz.py --rounds 400 2>&1 | tail -1
    [ "${PIPESTATUS[0]}" -eq 0 ] || fail=1
}
run gcc-asan-ubsan "$W/gcc_asan_ubsan.so" "$ASAN_PRE"
run clang-ubsan-trap "$W/clang_ubsan_trap.so" ""

cat > "$W/canary.py" <<'EOF'
import numpy as np
from vnxdna.v4.frame import PROFILES
from vnxdna.v4.sync import TemplateAligner
from vnxdna.v5 import native_alignment as na
lay = PROFILES["v4-balanced"][0]
al = TemplateAligner(lay, 6, backend="native"); g = na.geometry(al); T = al.T
n = 2
codes = np.zeros(2 * T, np.uint8)
offsets = np.array([0, T], np.int64)          # one entry short: the kernel reads offsets[2]
outs = [np.zeros(n * lay.frame_nt, np.uint8), np.zeros(n * lay.frame_nt, np.uint8), np.zeros(n, np.uint8)] + [np.zeros(n, np.int64) for _ in range(4)]
p = na._ptr
rc = na._load().vnx_align_batch(n, p(codes), p(offsets), 2 * T, None, None, T, p(g["tpl"]), p(g["seg_of"]), p(g["prev_seg"]),
                               p(g["next_seg"]), al.n_segments, lay.frame_nt, p(g["frame_pos"]), p(g["seg_frame"]), 6, 4, 6, 6, 1, 0, 0,
                               *(p(o) for o in outs))
print("returned", rc, "(sanitizer did NOT fire)")
EOF
if VNXDNA_NATIVE_LIB="$W/gcc_asan_ubsan.so" LD_PRELOAD=$ASAN_PRE $PY "$W/canary.py" 2>&1 | grep -q "ERROR: AddressSanitizer"; then
    echo "canary: AddressSanitizer fired as expected"
else
    echo "canary: AddressSanitizer did NOT fire"; fail=1
fi
exit $fail
