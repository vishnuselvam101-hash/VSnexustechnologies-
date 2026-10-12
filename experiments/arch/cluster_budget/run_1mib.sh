#!/bin/bash
# job 86 measurements (SIMULATED): 1 MiB dev seeds base vs new (preset E), then 10 MB seed 93100 E on the new tree.
set -u
T=/root/vnx-dna-lab/tmp/nano-10mb; PY=/root/vnx-dna-lab/.venv/bin/python
NEW=${NEW:-/root/vnx-dna-lab/nano-10mb-run}; BASE=/root/vnx-dna-lab/nano-10mb-base
waitload() { while awk '{exit !($1 >= 3)}' /proc/loadavg; do echo "load $(cut -d' ' -f1 /proc/loadavg), waiting"; sleep 60; done; }
ulimit -v $((14 * 1024 * 1024))
echo "== start $(date -Is) new $(git -C $NEW rev-parse --short HEAD) base $(git -C $BASE rev-parse --short HEAD) ulimit -v $(ulimit -v) KiB"
for seed in ${SEEDS_1M:-93020 93021 93022}; do
  W=$T/w1m/$seed; mkdir -p $W
  [ -f $W/sha ] || (cd $NEW && PYTHONPATH=$NEW/src $PY -c "
import sys; sys.argv=['x']; sys.path.insert(0,'experiments/arch/outer_list_eval'); import run
from pathlib import Path; print(run.prepare(Path('$W'), $seed, 1<<20))" > $W/sha)
  for tree in BASE NEW; do
    R=${!tree}; waitload
    echo "== 1MiB seed $seed $tree $(date -Is) load $(cut -d' ' -f1 /proc/loadavg)"
    (cd $R && PYTHONPATH=$R/src /usr/bin/time -v $PY experiments/arch/outer_list_eval/run.py one --work $W --preset E \
       2> $T/w1m/time-$seed-$tree.txt | tail -1 > $T/w1m/one-$seed-$tree.json) || echo "rc $?"
    echo "truth $(cat $W/sha) $(cat $T/w1m/one-$seed-$tree.json | cut -c1-300)"
    grep -E "Maximum resident|Elapsed" $T/w1m/time-$seed-$tree.txt
  done
done
[ -n "${SKIP_10M:-}" ] && exit 0
for seed in ${SEEDS_10M:-93100}; do
  waitload
  echo "== 10 MB seed $seed E $(date -Is) load $(cut -d' ' -f1 /proc/loadavg)"
  (cd $T && PYTHONPATH=$NEW/src /usr/bin/time -v $PY $T/diag10m.py $NEW E $seed 2> $T/diag10m/time-$seed-E.txt | tail -2) || echo "diag rc $?"
  grep -E "Maximum resident|Elapsed|Exit status" $T/diag10m/time-$seed-E.txt
done
echo "== done $(date -Is)"
