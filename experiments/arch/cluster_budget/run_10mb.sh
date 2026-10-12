#!/bin/bash
# job 86: 10 MB preset E, dev seed 93100 then pre-registered held-out 93110-93112, on the run tree (SIMULATED)
set -u
T=/root/vnx-dna-lab/tmp/nano-10mb; PY=/root/vnx-dna-lab/.venv/bin/python; NEW=/root/vnx-dna-lab/nano-10mb-run
waitload() { while awk '{exit !($1 >= 3)}' /proc/loadavg; do echo "load $(cut -d' ' -f1 /proc/loadavg), waiting"; sleep 60; done; }
ulimit -v $((14 * 1024 * 1024))
echo "== start $(date -Is) new $(git -C $NEW rev-parse --short HEAD) ulimit -v $(ulimit -v) KiB"
for seed in ${SEEDS_10M:-93100}; do
  waitload
  echo "== 10 MB seed $seed E $(date -Is) load $(cut -d' ' -f1 /proc/loadavg)"
  drv=$T/fresh10m.py; [ $seed = 93100 ] && drv=$T/diag10m.py
  (cd $T && PYTHONPATH=$NEW/src /usr/bin/time -v $PY $drv $NEW E $seed 2> $T/diag10m/time-$seed-E.txt | tail -2) || echo "diag rc $?"
  grep -E "Maximum resident|Elapsed|Exit status" $T/diag10m/time-$seed-E.txt
done
echo "== done $(date -Is)"
