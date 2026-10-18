"""A/A noise band for the V13 harness: run `bench.py` with BOTH trees at the same commit (BASE_TREE and AA_TREE, two
checkouts of V12 6bf7749), so the measured ratio is pure noise of this host and session.

    BASE_TREE=/root/vnx-dna-lab/v13-base AA_TREE=/root/vnx-dna-lab/v13-base2 VNX_BENCH_OUT=.../baseline-aa/e2e.jsonl \\
        PYTHONPATH=src python experiments/v13/bench/aa.py run --scenarios indel-w1,indel-w4
    VNX_BENCH_OUT=.../baseline-aa/e2e.jsonl PYTHONPATH=src python experiments/v13/bench/aa.py summary

Everything else (scenarios, seeds, warm-ups, order alternation, RSS sampler, summary) is bench.py's; only the "v13"
tree is replaced by AA_TREE. SIMULATED channel data only.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("v13_bench", HERE / "bench.py")
bench = importlib.util.module_from_spec(spec)
sys.modules["v13_bench"] = bench
spec.loader.exec_module(bench)

aa = os.environ.get("AA_TREE")
if aa:
    bench.ROOT = Path(aa)          # the "v13" label runs this tree (a second checkout of the baseline commit)

if __name__ == "__main__":
    raise SystemExit(bench.main())
