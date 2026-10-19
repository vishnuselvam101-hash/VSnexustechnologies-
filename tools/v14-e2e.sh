#!/usr/bin/env bash
# V14 Phase 9 end-to-end matrix runner (docs/V14_VALIDATION_PLAN.md §6).
#
#   tools/v14-e2e.sh [--integration] [--soak N] [--out DIR]
#
# Runs, from the repository root of the worktree:
#   1. the Rust rows: cargo test --release --test e2e_matrix (--integration also runs ignored rows: --include-ignored,
#      and the registry test then requires that none is ignored);
#   2. the Python rows: pytest tests/v14/integration, including the block-device rows, which attach their own
#      /dev/nbdN through tests/v14/nbd_support.py (root + the nbd module; otherwise they skip with the reason, never
#      counted as passed). Afterwards the summary lists any nbd device still attached and any nbd mount.
# --soak N repeats the randomized concurrency rows (R06, R06b) over N seeds (VNX_E2E_SEEDS).
# Logs and a summary (summary.txt) go to --out (default $TMPDIR/vnx-e2e/<UTC timestamp>; the lab keeps them in /root/vnx-dna-lab/results).
# With VNX_LAB_GOVERN=systemd every step runs in a systemd scope of vnxdna.slice (CPUQuota 200 %, MemoryMax 6G);
# CARGO_BUILD_JOBS defaults to 2. PY selects the interpreter (default: python3).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
ROOT=$PWD
INTEGRATION=0
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --integration) INTEGRATION=1 ;;
    --soak) shift; export VNX_E2E_SEEDS="$1" ;;
    --out) shift; OUT="$1" ;;
    *) echo "usage: $0 [--integration] [--soak N] [--out DIR]" >&2; exit 2 ;;
  esac
  shift
done
OUT=${OUT:-${TMPDIR:-/tmp}/vnx-e2e/$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$OUT"
PY=${PY:-python3}
export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-2} PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

run() {
  if [ "${VNX_LAB_GOVERN:-}" = systemd ] && command -v systemd-run >/dev/null 2>&1; then
    local envs=()
    for v in PYTHONPATH CARGO_BUILD_JOBS VNX_E2E_RUST_LOG VNX_E2E_INTEGRATION VNX_E2E_SEEDS PATH HOME; do
      [ -n "${!v:-}" ] && envs+=("--setenv=$v=${!v}")
    done
    systemd-run --scope --quiet --slice=vnxdna.slice -p CPUQuota=200% -p MemoryMax=6G "${envs[@]}" "$@"
  else
    "$@"
  fi
}

{
  echo "commit $(git rev-parse HEAD 2>/dev/null) $(git status --porcelain 2>/dev/null | wc -l | tr -d ' ') dirty files"
  echo "host $(uname -srm), $(nproc) CPUs; rustc $(rustc --version 2>/dev/null | cut -d' ' -f2); $($PY --version 2>&1)"
  echo "integration=$INTEGRATION seeds=${VNX_E2E_SEEDS:-default}"
} > "$OUT/summary.txt"

ignored=()
[ "$INTEGRATION" = 1 ] && ignored=(--include-ignored)
(cd src/vnxdna/native/rust/vnxd && run cargo test --release --offline --locked --test e2e_matrix -- --test-threads=2 "${ignored[@]}") \
  > "$OUT/rust.log" 2>&1
rust_rc=$?
export VNX_E2E_RUST_LOG="$OUT/rust.log" VNX_E2E_INTEGRATION=$INTEGRATION
run "$PY" -m pytest tests/v14/integration -o addopts="" -p no:cacheprovider -rA -q > "$OUT/python.log" 2>&1
py_rc=$?

{
  echo "rust   rc=$rust_rc  $(grep -h 'test result' "$OUT/rust.log" | tail -1)"
  echo "python rc=$py_rc  $(grep -E '[0-9]+ (passed|failed)' "$OUT/python.log" | tail -1)"
  echo "skipped/ignored rows (NOT passes):"
  grep -h "ignored, needs" "$OUT/rust.log" | sed 's/^/  rust   /'
  grep -h "^SKIPPED" "$OUT/python.log" | sed 's/^/  python /'
  grep -h "^XFAIL\|^XPASS" "$OUT/python.log" | sed 's/^/  known defect /'
  echo "teardown: attached nbd devices: $(cat /sys/block/nbd*/pid 2>/dev/null | wc -l); nbd mounts: $(grep -c '^/dev/nbd' /proc/mounts)"
} >> "$OUT/summary.txt"
cat "$OUT/summary.txt"
[ $rust_rc -eq 0 ] && [ $py_rc -eq 0 ]
