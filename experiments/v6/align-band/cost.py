"""AB-EXP-02: fresh-process decode cost of the retry band (--retry-band 16) versus the stock decoder (SIMULATED channel;
time and memory MEASURED on this machine under shared load). Adapted from experiments/v6/phase4/cost.py (P4-EXP-04).

    PYTHONPATH=src python experiments/v6/align-band/cost.py --out experiments/v6/align-band/AB-EXP-02-cost

Per (model, seed): encode a 1 MiB random payload once, simulate the model, then decode the same reads once per arm, each
in a FRESH child process (``python -m vnxdna.v4.cli decode``), so the child's ru_maxrss (``os.wait4``) is that decode's
peak RSS. Arms alternate order by seed to balance cache effects.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "experiments" / "v6" / "channel"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "phase4"))
import channel as chn  # noqa: E402
from phase4 import prepare  # noqa: E402

from vnxdna.core.provenance import environment  # noqa: E402

MODELS = ["mixed-mild", "illumina-like", "deletion-heavy", "nanopore-like"]
SEEDS = [81000, 81001, 81002]
ARMS = {"default": [], "retry16": ["--retry-band", "16"]}


# The child reports its own VmHWM (/proc/self/status: the high-water mark of the address space created by exec). The
# wait4 ru_maxrss of a posix_spawn child is NOT that decode's peak: Linux carries the spawning parent's high-water mark
# into the exec'd process (exec_mmap -> setmax_mm_hiwater_rss), so every decode smaller than this parent reported the
# parent's RSS. Both are recorded; the criterion uses VmHWM (deviation from PREREG §3, listed in the README).
CHILD = """
import runpy, sys
sys.argv = ["vnx"] + sys.argv[2:]
try:
    runpy.run_module("vnxdna.v4.cli", run_name="__main__")
finally:
    hwm = [l for l in open("/proc/self/status") if l.startswith("VmHWM:")][0].split()[1]
    open(HWM_PATH, "w").write(hwm)
"""


def run_child(args: list[str], hwm_path: Path) -> tuple[int, float, int, int]:
    t = time.perf_counter()
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    code = CHILD.replace("HWM_PATH", repr(str(hwm_path)))
    pid = os.posix_spawn(sys.executable, [sys.executable, "-c", code, "-", *args], env)
    _, status, ru = os.wait4(pid, 0)
    hwm = int(hwm_path.read_text()) * 1024
    hwm_path.unlink()
    return os.waitstatus_to_exitcode(status), time.perf_counter() - t, hwm, ru.ru_maxrss * 1024


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=1 << 20)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="vnx-ab-cost-"))
    rows = []
    try:
        info = prepare(tmp, {"size": a.size}, 6201)
        for model in MODELS:
            m = chn.load_model(model)
            for i, seed in enumerate(SEEDS):
                reads = tmp / f"{model}-{seed}.fastq"
                chn.simulate_with_sidecar(m, tmp / "strands.fasta", reads, seed)
                order = list(ARMS) if i % 2 == 0 else list(ARMS)[::-1]
                for arm in order:
                    o = tmp / f"o-{arm}.vnx"
                    code, secs, rss, ru_maxrss = run_child(["decode", str(reads), "-o", str(o), "--force", "--workers", "1",
                                                            "--no-input-hash", *ARMS[arm]], tmp / "hwm.txt")
                    ok = code == 0 and o.exists() and chn.sha256_file(o) == info["container_sha256"]
                    rows.append({"model": model, "seed": seed, "arm": arm, "exit": code, "exact": ok,
                                 "wall_seconds": round(secs, 3), "peak_rss_bytes": rss,
                                 "wait4_ru_maxrss_bytes": ru_maxrss})
                    if o.exists():
                        o.unlink()
                reads.unlink()
        ratios = []
        for model in MODELS:
            for seed in SEEDS:
                r = {x["arm"]: x for x in rows if x["model"] == model and x["seed"] == seed}
                ratios.append({"model": model, "seed": seed,
                               "rss_ratio": round(r["retry16"]["peak_rss_bytes"] / r["default"]["peak_rss_bytes"], 4),
                               "time_ratio": round(r["retry16"]["wall_seconds"] / r["default"]["wall_seconds"], 4),
                               "exact": {k: v["exact"] for k, v in r.items()}})
        doc = {"experiment": "AB-EXP-02-cost", "classification": "SIMULATED channel; time and peak RSS MEASURED",
               "statement": chn.STATEMENT, "size": a.size, "setup": info, "models": MODELS, "seeds": SEEDS,
               "git": chn.git_info(), "environment": environment(), "load_average": os.getloadavg(),
               "rows": rows, "pairs": ratios, "max_rss_ratio": max(x["rss_ratio"] for x in ratios),
               "median_time_ratio": sorted(x["time_ratio"] for x in ratios)[len(ratios) // 2],
               "false_success": sum(1 for x in rows if x["exit"] == 0 and not x["exact"])}
        (out / "results.json").write_text(json.dumps(doc, indent=1) + "\n")
        print(json.dumps({k: doc[k] for k in ("max_rss_ratio", "median_time_ratio", "false_success")}))
        return 2 if doc["false_success"] else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
