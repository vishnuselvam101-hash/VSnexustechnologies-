#!/usr/bin/env bash
# G-MEM stage attribution with memray (heap only; SIMULATED channel, MEASURED on this host).
# usage: bash experiments/v7/g-memory/memray.sh OUTDIR   (from the repository root; PY = interpreter with memray)
# Profiles two runs, at the G-MEM seeds: the 16 MiB encode (payload seed 7016) and the 1 MiB deletion-heavy decode
# (payload seed 7001, simulate seed 71001), which has the largest peak RSS at every size. Writes memray `stats`
# (peak heap, top allocators) and `summary` (allocations live at the heap peak, by own memory) as text.
set -euo pipefail
OUT=$1
PY=${PY:-python}
MEMRAY=${MEMRAY:-$(dirname "$PY")/memray}
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
export PYTHONPATH=${PYTHONPATH:-src}
mkdir -p "$OUT"
printf 'import sys\nfrom vnxdna.commands import main\nsys.argv = ["vnx"] + sys.argv[1:]\nmain()\n' > "$W/vnx.py"
vnx() { "$PY" "$W/vnx.py" "$@" > /dev/null; }
strip() { sed 's/\x1b\[[0-9;]*m//g' | sed "s#$W#<work>#g"; }

vnx generate "$W/p16.bin" --size 16MiB --pattern random --seed 7016
"$MEMRAY" run -q -f -o "$W/enc.bin" "$W/vnx.py" encode "$W/p16.bin" "$W/s16.fasta" --workers 1 --compression none --force > /dev/null
{ "$MEMRAY" stats "$W/enc.bin"; COLUMNS=200 "$MEMRAY" summary -s 3 -r 15 "$W/enc.bin"; } 2>&1 | strip > "$OUT/memray-encode-16MiB.txt"

vnx generate "$W/p1.bin" --size 1MiB --pattern random --seed 7001
vnx encode "$W/p1.bin" "$W/s1.fasta" --workers 1 --compression none --force
vnx channel simulate "$W/s1.fasta" "$W/r1.fastq" --model deletion-heavy --seed 71001 --workers 4 --force
"$MEMRAY" run -q -f -o "$W/dec.bin" "$W/vnx.py" decode "$W/r1.fastq" -o "$W/o.vnx" --workers 1 --force > /dev/null || true
{ "$MEMRAY" stats "$W/dec.bin"; COLUMNS=200 "$MEMRAY" summary -s 3 -r 15 "$W/dec.bin"; } 2>&1 | strip > "$OUT/memray-decode-deletion-heavy-1MiB.txt"
echo "wrote $OUT/memray-encode-16MiB.txt $OUT/memray-decode-deletion-heavy-1MiB.txt"
