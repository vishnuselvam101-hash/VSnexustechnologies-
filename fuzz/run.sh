#!/usr/bin/env bash
# fuzz/run.sh <target> <seconds> [extra libFuzzer flags...]
#
# Long fuzz campaign for one target. Native targets (fuzz/native/<target>_fuzz.c) are built with clang
# -fsanitize=fuzzer,address,undefined (UBSan non-recoverable); Python targets (tests/fuzz/harnesses.py, run through
# atheris) are run with the worktree's own src/ on PYTHONPATH. `fuzz/run.sh list` prints the targets.
#
# Seeds come from fuzz/corpus/<target>/ and fuzz/regressions/<target>/ (read-only); the growing corpus, crashes and the
# log go to $FUZZ_OUT/<target>/ (default fuzz/out/, git-ignored). Crash files are never deleted. Exit 1 on a new crash.
# Env: FUZZ_OUT, PYTHON (default: the lab venv, else python3), CC (default clang), RSS_MB (default 2048),
#      GOVERN=0 to skip the systemd-run resource slice (1 CPU, 4 GiB).
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
OUT_ROOT=${FUZZ_OUT:-$HERE/out}
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  if [ -x /root/vnx-dna-lab/.venv/bin/python ]; then PY=/root/vnx-dna-lab/.venv/bin/python; else PY=python3; fi
fi
CC=${CC:-clang}
RSS_MB=${RSS_MB:-2048}

native_targets() { for f in "$HERE"/native/*_fuzz.c; do [ -e "$f" ] && basename "$f" _fuzz.c; done; }
python_targets() { PYTHONPATH="$REPO/src:$REPO/tests" "$PY" -c 'from fuzz import harnesses; print("\n".join(sorted(harnesses.TARGETS)))'; }

governed() {
  if [ "${GOVERN:-1}" = 1 ] && command -v systemd-run >/dev/null 2>&1 && [ "$(id -u)" = 0 ]; then
    systemd-run --scope --slice=vnxdna.slice -p MemoryMax=4G -p CPUQuota=100% -q "$@"
  else "$@"; fi
}

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

[ "${1:-}" = list ] && { echo "native:"; native_targets | sed 's/^/  /'; echo "python:"; python_targets | sed 's/^/  /'; exit 0; }
[ $# -ge 2 ] || usage
target=$1 secs=$2; shift 2
case $secs in ''|*[!0-9]*) usage ;; esac

d=$OUT_ROOT/$target
mkdir -p "$d/corpus" "$d/crashes"
seeds=()
for s in "$HERE/corpus/$target" "$HERE/regressions/$target"; do [ -d "$s" ] && seeds+=("$s"); done
before=$(find "$d/crashes" -type f | wc -l)
log=$d/campaign-$(date +%Y%m%d-%H%M%S).log
flags=(-max_total_time="$secs" -rss_limit_mb="$RSS_MB" -timeout=25 -artifact_prefix="$d/crashes/" -print_final_stats=1)

if [ -f "$HERE/native/${target}_fuzz.c" ]; then
  bin=$d/${target}_fuzz
  "$CC" -g -O1 -std=c11 -fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined -fno-omit-frame-pointer \
    -I"$REPO/src" "$HERE/native/${target}_fuzz.c" -o "$bin" -lm || { echo "build failed: $target"; exit 2; }
  echo "# native $target secs=$secs commit=$(git -C "$REPO" rev-parse --short HEAD) cc=$("$CC" --version | head -1)" > "$log"
  governed env ASAN_OPTIONS=detect_leaks=1:abort_on_error=1 UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=1 \
    "$bin" "${flags[@]}" -max_len="${MAX_LEN:-65536}" "$@" "$d/corpus" "${seeds[@]}" >> "$log" 2>&1
  rc=$?
elif python_targets | grep -qx "$target"; then
  echo "# python $target secs=$secs commit=$(git -C "$REPO" rev-parse --short HEAD) python=$("$PY" -V 2>&1)" > "$log"
  (cd "$d" && governed env PYTHONPATH="$REPO/src:$REPO/tests" "$PY" -m fuzz.atheris_run "$target" "${flags[@]}" \
     -max_len="${MAX_LEN:-262144}" "$@" "$d/corpus" "${seeds[@]}" >> "$log" 2>&1)
  rc=$?
else
  echo "unknown target: $target (fuzz/run.sh list)"; exit 2
fi
after=$(find "$d/crashes" -type f | wc -l)
runs=$(grep -oE 'stat::number_of_executed_units: *[0-9]+' "$log" | grep -oE '[0-9]+$' | tail -1)
cov=$(grep -oE 'cov: [0-9]+ ft: [0-9]+' "$log" | tail -1)
echo "$target secs=$secs rc=$rc runs=${runs:-?} ${cov:-cov: ?} corpus=$(find "$d/corpus" -type f | wc -l) new_crashes=$((after - before)) log=$log"
[ "$after" -gt "$before" ] && exit 1
exit 0
