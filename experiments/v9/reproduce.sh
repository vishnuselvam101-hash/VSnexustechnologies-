#!/usr/bin/env bash
# experiments/v9/reproduce.sh [spot|all|channel|decoder|coverage|scale|verify]
#
# Reproduction of the V9 results from pinned code, models, configuration and seeds (docs/V9_REPRODUCTION.md). Needs the
# Python environment with vnxdna's dependencies (python3, or set PYTHON) and a C compiler; `channel` also needs the public D13 FIT files (see experiments/v9/d13/README.md).
#   spot      pre-specified subset, written to a scratch directory (VNX_V9_REPRO_OUT, default ./repro-out-v9) and
#             compared row by row with the committed rows: EVAL seeds 91000-91001 for v8 and E at the production level
#             (all six primary cells), adaptive-coverage EVAL seed 91000, noisy 20 KB seeds 93000-93002. About 1 h on 4 cores.
#   channel   G1 fit + FIT pre-check (rewrites experiments/v9/d13/results in place; then `verify`)
#   decoder   full oracle-gap EVAL (600 cases), eligibility, selection (in place)
#   coverage  DEV trajectories, tune, EVAL, envelope (in place)
#   scale     noisy decodes and the archive-size benchmark (in place; 1 GiB needs up to 1 h per stage)
#   verify    compare the in-place regenerated files with git HEAD (deterministic fields only)
#   all       channel, decoder, coverage, scale, verify (several days on 4 cores)
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
PY=${PYTHON:-python3}
export PYTHONPATH="$REPO/src"
OUT=${VNX_V9_REPRO_OUT:-$REPO/repro-out-v9}
W=$("$PY" -c "import json;print(json.load(open('$HERE/consensus/results/selection.json'))['winner'])")  # frozen winner
cd "$REPO"
step() { echo "== $*" >&2; }

native() {
  step "native kernels"
  for m in align rs reads cluster; do "$PY" -m vnxdna.native.$m build >/dev/null; done
}

pick() {  # pick SRC DST COND: copy the committed rows for which the Python expression COND (over r) holds
  "$PY" - "$1" "$2" "$3" <<'EOF'
import json, sys
src, dst, cond = sys.argv[1:]
with open(dst, "w") as f:
    for line in open(src):
        r = json.loads(line)
        if eval(cond, {}, {"r": r}):
            f.write(line)
EOF
}

spot() {
  native
  mkdir -p "$OUT"
  rm -f "$OUT"/*.jsonl
  step "oracle-gap EVAL subset (v8, $W; seeds 91000-91001)"
  for c in v8 "$W"; do
    "$PY" experiments/v9/oracle/run.py --seeds 91000-91001 --candidate "$c" --levels production --out "$OUT/eval.jsonl" --jobs 3
  done
  pick experiments/v9/oracle/results/eval.jsonl "$OUT/eval-committed.jsonl" \
    "r['seed'] <= 91001 and r['level'] == 'production' and r['candidate'] in ('v8', '$W')"
  step "adaptive coverage EVAL subset (seed 91000)"
  "$PY" experiments/v9/coverage/run.py --mode eval --seeds 91000-91000 --candidate "$W" --out "$OUT/coverage-eval.jsonl"
  pick experiments/v9/coverage/results/eval.jsonl "$OUT/coverage-committed.jsonl" "r['seed'] == 91000"
  step "noisy 20 KB subset (seeds 93000-93002)"
  "$PY" experiments/v9/scale/noisy.py run --sizes 20000 --seeds 93000-93002 --candidate "$W" \
    --out "$OUT/noisy.jsonl" --projection "$OUT/noisy-projection.json"
  pick experiments/v9/scale/results/noisy.jsonl "$OUT/noisy-committed.jsonl" "r['size'] == 20000 and r['seed'] <= 93002"
  "$PY" experiments/v9/verify_repro.py "$OUT/eval.jsonl" "$OUT/eval-committed.jsonl" \
    "$OUT/coverage-eval.jsonl" "$OUT/coverage-committed.jsonl" "$OUT/noisy.jsonl" "$OUT/noisy-committed.jsonl"
}

channel() {
  step "G1 fit + FIT pre-check"
  "$PY" experiments/v9/d13/g1.py fit --workers 3
  "$PY" experiments/v9/d13/g1.py precheck --workers 3
}

decoder() {
  native
  step "oracle-gap EVAL (v8 all levels, A and E production), eligibility, selection"
  local O=experiments/v9/oracle/results/eval.jsonl
  rm -f "$O"
  "$PY" experiments/v9/oracle/run.py --seeds 91000-91099 --candidate v8 --out "$O" --jobs 3
  for c in A E; do "$PY" experiments/v9/oracle/run.py --seeds 91000-91099 --candidate $c --levels production --out "$O" --jobs 3; done
  rm -f experiments/v9/consensus/results/eligibility.jsonl
  for c in A E; do "$PY" experiments/v9/consensus/eligibility.py --candidate $c --jobs 3; done
  "$PY" experiments/v9/consensus/evaluate.py
}

coverage() {
  native
  local R=experiments/v9/coverage/results
  rm -f $R/dev.jsonl $R/eval.jsonl $R/envelope-cases.jsonl
  step "adaptive coverage DEV, tune, EVAL; envelope"
  "$PY" experiments/v9/coverage/run.py --mode trajectory --seeds 90000-90009 --candidate "$W" --out $R/dev.jsonl
  "$PY" experiments/v9/coverage/run.py --tune --out $R/dev.jsonl
  "$PY" experiments/v9/coverage/run.py --mode eval --seeds 91000-91099 --candidate "$W" --out $R/eval.jsonl
  "$PY" experiments/v9/coverage/envelope.py run --candidate "$W" --jobs 3
  "$PY" experiments/v9/coverage/envelope.py summarise
}

scale() {
  native
  rm -f experiments/v9/scale/results/noisy.jsonl
  step "noisy decodes by size; archive-size benchmark"
  "$PY" experiments/v9/scale/noisy.py run --candidate "$W"
  "$PY" experiments/v9/scale/noisy.py project
  "$PY" experiments/v9/scale/bench.py --sizes 20000,1048576,10485760,104857600,1073741824 --max-seconds 3600
}

verify() {
  step "verify in-place regenerated files against git HEAD"
  "$PY" experiments/v9/verify_repro.py --head experiments/v9/oracle/results/eval.jsonl \
    experiments/v9/consensus/results/eligibility.jsonl experiments/v9/consensus/results/selection.json \
    experiments/v9/coverage/results/dev.jsonl experiments/v9/coverage/results/eval.jsonl \
    experiments/v9/coverage/results/envelope-cases.jsonl experiments/v9/scale/results/noisy.jsonl
}

case "${1:-spot}" in
  spot) spot ;; channel) channel ;; decoder) decoder ;; coverage) coverage ;; scale) scale ;; verify) verify ;;
  all) channel; decoder; coverage; scale; verify ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
