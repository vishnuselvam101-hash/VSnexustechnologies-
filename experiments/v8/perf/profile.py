"""V8.14 profiling of the slowest realistic decode (D13-F1 stress channel, coverage 10, v7-lowcov; matrix median 89 s).

Runs one matrix case (seed 83000) under py-spy (raw stack samples, 50 Hz, native frames off), then aggregates the samples:
share of samples per function (self time = the innermost frame; inclusive = anywhere on the stack) and per pipeline
stage (parsing, pass 1, clustering, consensus, outer RS, archive). Output: results/profile.json (+ the raw collapsed
stacks, local only). No code is optimised here: V8.14 changes code only for a measured bottleneck, with regression tests,
golden hashes and before/after numbers.

    PYTHONPATH=src python experiments/v8/perf/profile.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PYSPY = "/root/vnx-dna-lab/.venv/bin/py-spy"
CASE = ("d13-f1", 10, "v7-lowcov", 83000)
STAGES = {"parsing": ("native/reads", "reads.py", "iter_reads"), "pass1": ("pass1", "decode_frames", "frame4"),
          "clustering": ("cluster/cluster", "cluster_reads", "minhash", "native/cluster"),
          "consensus": ("consensus", "editdist", "fb_calls", "_polish"), "outer_rs": ("outer", "rs.py", "native/rs"),
          "archive": ("archive", "container", "merkle", "crypto"), "simulation": ("simulation/",)}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vnx-v8p-") as tmp:
        raw = Path(tmp) / "stacks.txt"
        code = ("import sys; sys.path.insert(0, 'experiments/v8/matrix'); import run as R; "
                f"r = R.run_one({CASE!r}); print(r['outcome'], r['decode_seconds'])")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        p = subprocess.run([PYSPY, "record", "-f", "raw", "-r", "50", "-o", str(raw), "--", sys.executable, "-c", code],
                           cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        lines = raw.read_text().splitlines() if raw.exists() else []
    selfc, incl, stage = Counter(), Counter(), Counter()
    total = 0
    for ln in lines:
        stack, _, n = ln.rpartition(" ")
        if not n.isdigit():
            continue
        k = int(n)
        total += k
        frames = stack.split(";")
        selfc[frames[-1]] += k
        for f in set(frames):
            incl[f] += k
        hit = next((s for s, keys in STAGES.items() if any(key in stack for key in keys)), "other")
        stage[hit] += k
    out = {"case": list(CASE), "label": "SIMULATED decode under the D13-F1 stress channel", "samples": total,
           "py_spy_returncode": p.returncode, "run_output": p.stdout.strip()[-200:],
           "top_self": [{"frame": f, "share": round(c / total, 4)} for f, c in selfc.most_common(15)] if total else [],
           "top_inclusive": [{"frame": f, "share": round(c / total, 4)} for f, c in incl.most_common(25)] if total else [],
           "by_stage": {s: round(c / total, 4) for s, c in stage.most_common()} if total else {}}
    (HERE / "results").mkdir(parents=True, exist_ok=True)
    (HERE / "results" / "profile.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: out[k] for k in ("samples", "by_stage", "run_output")}, indent=1))
    for r in out["top_self"][:10]:
        print(r["share"], r["frame"][:140])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
