"""V14 P6 workstream B: paired A/B of a shuffled-pool stream decode, baseline tree vs this tree (SIMULATED input).

    BASE_SRC=<baseline src dir> PYTHONPATH=src python experiments/v14/bench/p6b-profile/ab_bench.py \\
        --pool DIR [--pool DIR ...] --workers 1,4 --reps 5 --out FILE.jsonl [--aa]

Each decode is one process running profile_stages.py --no-profile with PYTHONPATH = the tree's src (the decode
wall time is measured inside that process, imports excluded). Order alternates per rep (A,B then B,A). --aa runs the
baseline against itself (noise floor). Every pool DIR holds in.bin, r.fastq (channel simulator) and
s.fasta.stream.json, as written by pool_bench.py's _prepare. Rows: one JSON line per decode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", action="append", required=True)
    ap.add_argument("--workers", default="1,4")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--aa", action="store_true")
    a = ap.parse_args(argv)
    base = Path(os.environ["BASE_SRC"]).resolve()
    trees = {"base": base, "new": ROOT / "src"} if not a.aa else {"base": base, "base2": base}
    for pool in map(Path, a.pool):
        want = _sha(pool / "in.bin")
        out = pool / "o.bin"
        for w in [int(x) for x in a.workers.split(",")]:
            for r in range(a.reps):
                order = list(trees) if r % 2 == 0 else list(trees)[::-1]
                for c in order:
                    env = {**os.environ, "PYTHONPATH": str(trees[c])}
                    p = subprocess.run([sys.executable, str(HERE / "profile_stages.py"), "pool", "--no-profile",
                                        "--reads", str(pool / "r.fastq"), "--manifest", str(pool / "s.fasta.stream.json"),
                                        "--out", str(out), "--workers", str(w), "--want-sha", want],
                                       capture_output=True, text=True, env=env, check=True)
                    row = json.loads(p.stdout.strip().splitlines()[-1])
                    row.update(config=c, pool=pool.name, rep=r, order=order, time=time.time(),
                               load1=round(os.getloadavg()[0], 2))
                    with open(a.out, "a") as f:
                        f.write(json.dumps(row) + "\n")
                    print(pool.name, w, r, c, row["wall_s"], row["matches"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
