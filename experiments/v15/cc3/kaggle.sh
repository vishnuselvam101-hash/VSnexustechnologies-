#!/usr/bin/env bash
# CC-3 job body on a Kaggle CPU session (cwd = a clone of work/v15-cc3 with its native kernels built; $OUT set).
# Usage: experiments/v15/cc3/kaggle.sh "CASE SEEDS TREE CONFIGS" ["CASE SEEDS TREE CONFIGS" ...]
# TREE = new (this clone) or base (the V15 head before CC-3, BASE below, built in ./base). SIMULATED.
set -u
BASE=cfd6ee0733c70bbe4f1cd576668700d2fbb8631b
ROOT=$(pwd)
export VNX_CC3_OUT=$OUT VNX_EVAL_TMP=/kaggle/working/tmp OPENBLAS_NUM_THREADS=1
mkdir -p "$VNX_EVAL_TMP"
SPECS=("$@")
need_base=0
for spec in "${SPECS[@]}"; do read -r _c _s tree _k <<< "$spec"; [ "$tree" = base ] && need_base=1; done
if [ $need_base = 1 ] && [ ! -d base ]; then
  git fetch -q --depth 1 origin $BASE && git worktree add -q --detach base FETCH_HEAD
  for k in align reads rs cluster strand orient integrity outerlist engine; do
    (cd base && PYTHONPATH=src python -m vnxdna.native.$k build >/dev/null 2>&1) || echo "base build-skip $k"
  done
  (cd base && PYTHONPATH=src python -m vnxdna.native | grep -o '"all_native": [a-z]*')
fi
nproc; free -g | head -2
for spec in "${SPECS[@]}"; do
  read -r case seeds tree configs <<< "$spec"
  src=$ROOT/src; [ "$tree" = base ] && src=$ROOT/base/src
  VNX_CC3_SRC=$src timeout 41000 python experiments/v15/cc3/run.py run --case "$case" --seeds "$seeds" --tree "$tree" \
    --configs "$configs"
done
python experiments/v15/cc3/run.py summary --out "$OUT" > /dev/null
echo CC3-DONE
