#!/usr/bin/env bash
# CLI smoke test: every command once, then the documented error cases (exit codes, no tracebacks, no output on failure).
# Usage: research/v3/cli_smoke.sh [path/to/vnx-dna]   (run in an empty scratch directory; software simulation only)
set -u
[ -z "$(ls -A)" ] || { echo "cli_smoke.sh: run it in an empty directory (outputs must not exist yet)" >&2; exit 2; }
V=${1:-vnx-dna}
t() { exp=$1; shift; out=$("$@" 2>&1); code=$?; tb=$(echo "$out" | grep -c Traceback); r=OK; [ "$code" != "$exp" ] && r=MISMATCH; [ "$tb" != 0 ] && r=TRACEBACK; printf "%-9s exit=%-3s expect=%-3s %s\n" "$r" "$code" "$exp" "$(echo "$*" | sed "s#$V#vnx-dna#" | cut -c1-110)"; }
$V benchmark generate --size 300KB --pattern mixed --seed 1 -o in.bin >/dev/null
t 0 $V --help
t 0 $V version --json
t 0 $V keygen -o key.txt
t 0 $V store in.bin -o a.vxdna
t 0 $V store in.bin -o e.vxdna --key-file key.txt
t 0 $V info a.vxdna
t 0 $V verify a.vxdna
t 0 $V extract a.vxdna --offset 1000 --length 5000 -o x.bin
t 0 $V extract e.vxdna --chunk 0 -o c.bin --key-file key.txt
t 0 $V encode a.vxdna -o s.fasta
t 0 $V encode a.vxdna -o s.vxs
t 0 $V sequence s.fasta -o r.fastq --coverage 5 --substitution-rate 0.002 --insertion-rate 0.0005 --deletion-rate 0.0005 --seed 3
t 0 $V simulate s.fasta -o sim.fasta --burst-rate 0.2 --burst-kind deletion --seed 3
t 0 $V reads r.fastq
t 0 $V cluster r.fastq -o c.jsonl
t 0 $V consensus c.jsonl -o cons.fasta
t 0 $V decode cons.fasta -o d.vxdna
t 0 $V restore d.vxdna -o d.bin
t 0 $V recover sim.fasta -o b.bin --burst-repair 24
t 0 $V recover s.vxs -o v.bin
t 0 $V extract s.vxs --offset 100 --length 100 -o xv.bin
t 0 $V pipeline in.bin -o p.bin --coverage 5 --substitution-rate 0.001 --seed 1
t 0 $V experiment run -i in.bin -o exp --trials 2 --coverage 5
t 0 $V simulate-errors in.bin -o sweep --sweep substitution=0.002 --trials 2
t 0 $V v1 store in.bin -o old.vxdna
t 0 $V migrate old.vxdna -o m.vxdna
t 0 $V v1 --help
cmp in.bin d.bin && cmp in.bin b.bin && cmp in.bin v.bin && cmp in.bin p.bin && echo "ALL-RECOVERED-IDENTICAL"
t 3 $V restore missing.vxdna -o z.bin
t 2 $V store in.bin
t 2 $V nonsense-command
t 4 $V restore e.vxdna -o z.bin
$V keygen -o wrong.txt >/dev/null; t 4 $V restore e.vxdna -o z.bin --key-file wrong.txt
head -c 5000 a.vxdna > trunc.vxdna; t 3 $V restore trunc.vxdna -o z.bin
t 8 $V store in.bin -o a.vxdna
t 7 $V store in.bin -o q.vxdna --data-shards 0
t 7 $V simulate-errors in.bin -o sw2 --sweep bogus=1
t 8 $V restore a.vxdna -o d.bin
python3 - <<'PY'
lines = open("s.fasta").read().split()
pairs = list(zip(lines[0::2], lines[1::2]))
keep = [p for i, p in enumerate(pairs) if not (":d:0:" in p[0] and i % 3)]   # drop 2/3 of group 0 (> M = 16)
open("drop.fasta", "w").write("".join(f"{a}\n{b}\n" for a, b in keep))
PY
t 5 $V recover drop.fasta -o z.bin
ls z.bin 2>/dev/null && echo "UNEXPECTED OUTPUT z.bin" || echo "no output written on failures"
t 8 $V store in.bin -o in.bin/x.vxdna
