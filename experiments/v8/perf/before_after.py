"""V8.14 before/after: the vectorised ``polish.edit_costs`` against the V7 reference on real decodes (SIMULATED D13-F1 stress
channel). Each case runs in a fresh child process twice, once with the reference patched in ("before") and once as
shipped ("after"). Wall time, peak RSS and the full result row (outcome, container and read hashes, frames, taxonomy) are
recorded; the rows must be identical.

    PYTHONPATH=src python experiments/v8/perf/before_after.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CASES = [("d13-f1", 10, "v7-lowcov", 83000), ("d13-f1", 5, "v7-lowcov", 83001), ("d13-f1", 10, "v4-balanced", 83002)]
CODE = r"""
import json, resource, sys, time
sys.path.insert(0, 'experiments/v8/matrix')
import run as R
from vnxdna.recovery.cluster import polish
if sys.argv[1] == 'before':
    polish.edit_costs = polish._edit_costs_reference
job = tuple(json.loads(sys.argv[2]))
t = time.perf_counter()
row = R.run_one(job)
row['wall_total_s'] = time.perf_counter() - t
row['peak_rss_mib_process'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(json.dumps(row, sort_keys=True))
"""
VOLATILE = ("seconds", "decode_seconds", "peak_rss_bytes", "wall_total_s", "peak_rss_mib_process")


def run(mode: str, job) -> dict:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    p = subprocess.run([sys.executable, "-c", CODE, mode, json.dumps(job)], cwd=ROOT, env=env, capture_output=True, text=True,
                       check=True)
    return json.loads(p.stdout.strip().splitlines()[-1])


def main() -> int:
    rows = []
    for job in CASES:
        b, a = run("before", job), run("after", job)
        same = {k: v for k, v in b.items() if k not in VOLATILE} == {k: v for k, v in a.items() if k not in VOLATILE}
        rows.append({"case": list(job), "identical_result": same, "outcome": a["outcome"],
                     "decode_s_before": b["decode_seconds"], "decode_s_after": a["decode_seconds"],
                     "speedup": round(b["decode_seconds"] / a["decode_seconds"], 2),
                     "peak_rss_mib_before": round(b["peak_rss_mib_process"], 1), "peak_rss_mib_after": round(a["peak_rss_mib_process"], 1)})
        print(json.dumps(rows[-1]), flush=True)
    out = {"experiment": "V8.14 before/after: vectorised polish.edit_costs", "label": "SIMULATED decodes (D13-F1 stress channel)",
           "load_average": list(os.getloadavg()), "rows": rows, "all_identical": all(r["identical_result"] for r in rows)}
    (HERE / "results" / "before_after.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    return 0 if out["all_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
