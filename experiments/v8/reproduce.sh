#!/usr/bin/env bash
# experiments/v8/reproduce.sh [all|data|model|decoder|verify]
#
# One-command reproduction of the V8 headline results from pinned code, data, models, configuration and seeds
# (docs/V8_REPRODUCTION.md). Needs: the lab venv (or PYTHON), a C compiler, and the public D13 files at
# $VNX_PUBLIC_DATA/d13/ (default /root/vnx-dna-lab/data/public), downloaded from the URLs in
# experiments/v7/datasets/MANIFEST.json; every file is SHA-256 checked before use. Outputs are written to a scratch copy
# (VNX_V8_REPRO_OUT, default ./repro-out) and then compared with the committed results by `verify`.
#   data     D13 pipeline (segments, FIT/DEV tables) + extraction
#   model    F1 fit, FIT pre-check (A1), F2 rule, verdict inputs, A-D comparison
#   decoder  decoder matrix, coverage envelope, oracle bounds, archive check
#   verify   compare deterministic outputs with the committed ones (hashes, verdicts, outcomes)
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
PY=${PYTHON:-/root/vnx-dna-lab/.venv/bin/python}
export PYTHONPATH="$REPO/src"
OUT=${VNX_V8_REPRO_OUT:-$REPO/repro-out}
export VNX_V8_DERIVED=${VNX_V8_DERIVED:-$OUT/derived}
cd "$REPO"
step() { echo "== $*" >&2; }

native() {
  step "native kernels"
  for m in align rs reads cluster; do "$PY" -m vnxdna.native.$m build >/dev/null; done
}

data() {
  native
  step "D13 pipeline: raw -> segments -> observations -> tables"
  for r in run15 run16 run18 run20; do "$PY" experiments/v8/d13/pipeline.py segment $r --workers 4; done
  "$PY" experiments/v8/d13/pipeline.py tables --split FIT --workers 4
  "$PY" experiments/v8/d13/extract.py
}

model() {
  step "F1 fit, FIT pre-check (A1), F2 rule, comparison"
  "$PY" experiments/v8/d13/fit.py fit F1 --workers 4
  "$PY" experiments/v8/d13/fit.py precheck F1 --workers 4
  "$PY" experiments/v8/d13/f2_rule.py
  "$PY" experiments/v8/d13/pipeline.py tables --split DEV --workers 4
  "$PY" experiments/v8/d13/compare.py
}

decoder() {
  native
  step "decoder matrix, envelope, oracle, archive"
  "$PY" experiments/v8/matrix/run.py --jobs 4
  "$PY" experiments/v8/matrix/run.py --summarise
  "$PY" experiments/v8/coverage/envelope.py
  "$PY" experiments/v8/oracle/run.py --jobs 4
  "$PY" experiments/v8/archive/check.py
}

verify() {
  step "verify against committed results"
  "$PY" experiments/v8/verify_repro.py
}

case "${1:-all}" in
  data) data ;; model) model ;; decoder) decoder ;; verify) verify ;;
  all) data; model; decoder; verify ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
