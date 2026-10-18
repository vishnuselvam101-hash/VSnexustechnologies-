"""Zero-run payload repro (workstream E's payload-zeros-4m workloads, SIMULATED): wall seconds and peak RSS of whole
decodes (process peak RSS, and the largest worker process's) with build/v14's default (the V13 rule), this tree's default ("auto": the V13 rule + one strand retry) and this
tree with engine_scheduler="strand" only. Inputs from E's suite (prepare); every decode is a separate process, configs
interleaved, `reps` runs each. PROVISIONAL timings.

    E_TREE=/root/vnx-dna-lab/v14-e BUILD_TREE=/root/vnx-dna-lab/v14 PYTHONPATH=src \\
        python experiments/v14/bench/p6c-scheduler/zero_repro.py OUT.jsonl [reps]
"""
from __future__ import annotations

import importlib.util
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
E_TREE = Path(os.environ.get("E_TREE", "/root/vnx-dna-lab/v14-e"))
BUILD = Path(os.environ.get("BUILD_TREE", "/root/vnx-dna-lab/v14"))
CASES = (("payload-zeros-4m-w1", 98085, True), ("payload-zeros-4m-w4", 98080, False))
CONFIGS = {"build": (BUILD, None), "auto": (ROOT, None), "strand": (ROOT, "strand")}
ONE = r"""
import json, resource, sys, time
from pathlib import Path
from vnxdna.v4 import decoder as de
from vnxdna.recovery.options import DecodeOptions
w, workers, sched = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
kw = {} if sched == "-" else {"engine_scheduler": sched}
t0 = time.perf_counter()
res = de.decode_reads(w / "reads.fastq" if (w / "reads.fastq").exists() else next(w.glob("*.fastq")), w / "o.vnx",
                      DecodeOptions(workers=workers, **kw), overwrite=True)
sec = time.perf_counter() - t0
eng = res.report.get("engine") or {}
print(json.dumps({"seconds": sec, "status": res.status, "rung": eng.get("rung"), "scheduler": eng.get("scheduler"),
                  "fallbacks": [f.get("reason") for f in eng.get("fallbacks") or []],
                  "fallback_seconds": [f.get("seconds") for f in eng.get("fallbacks") or []],
                  "sha": __import__("hashlib").sha256((w / "o.vnx").read_bytes()).hexdigest(),
                  "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                  "children_peak_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024,
                  "vnxdna_file": de.__file__}))
"""


def main() -> int:
    out, reps = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 3
    spec = importlib.util.spec_from_file_location("p6e_suite", E_TREE / "experiments/v14/bench/p6e-channels/suite.py")
    suite = importlib.util.module_from_spec(spec)
    sys.modules["p6e_suite"] = suite
    spec.loader.exec_module(suite)
    m = suite.load_manifest()
    for name, seed, smoke in CASES:
        w = suite.workload(m, name)
        size = suite.effective_size(w, m["method"]["smoke"] if smoke else None)
        with tempfile.TemporaryDirectory(prefix="p6c-zero-", dir=os.environ.get("VNX_ARCH_TMP")) as tmp:
            work = Path(tmp)
            prep = suite.prepare(work, m, w, seed, suite.trees(["v12"])["v12"], size)
            for r in range(reps + 1):                       # rep 0 = warm-up, discarded
                order = list(CONFIGS) if r % 2 else list(CONFIGS)[::-1]
                for c in order:
                    tree, sched = CONFIGS[c]
                    p = subprocess.run([sys.executable, "-c", ONE, str(work), str(w["workers"]), sched or "-"],
                                       env={**os.environ, "PYTHONPATH": str(tree / "src")}, capture_output=True, text=True)
                    if p.returncode != 0:
                        raise RuntimeError(p.stderr[-2000:])
                    row = json.loads(p.stdout.strip().splitlines()[-1])
                    assert row["vnxdna_file"].startswith(str(tree)), row["vnxdna_file"]
                    if r:
                        with open(out, "a") as f:
                            f.write(json.dumps({"label": "SIMULATED", "workload": name, "seed": seed, "size": size,
                                                "workers": w["workers"], "config": c, "rep": r,
                                                "archive_sha256": prep.get("archive_sha256"), **row}) + "\n")
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    for name, _, _ in CASES:
        for c in CONFIGS:
            rs = [r for r in rows if r["workload"] == name and r["config"] == c]
            print(f"{name:22s} {c:7s} {statistics.median(r['seconds'] for r in rs):6.3f}s "
                  f"{statistics.median(r['peak_rss_bytes'] for r in rs) / 2**20:7.1f} MiB "
                  f"(largest worker {statistics.median(r['children_peak_rss_bytes'] for r in rs) / 2**20:6.1f} MiB) "
                  f"rung={rs[0]['rung']} "
                  f"fallbacks={rs[0]['fallbacks']} statuses={sorted({r['status'] for r in rs})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
