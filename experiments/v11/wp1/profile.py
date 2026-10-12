"""WP1 §9 cProfile before/after: one 4 MiB indel decode, 1 worker, v10.0.0 vs V11. SIMULATED channel data only.

Same channel and size as the ``indel-w1`` benchmark scenario (experiments/v11/bench/bench.py). One worker keeps the
whole decode in the profiled process. Both trees decode the same prepared reads.

    V10_TREE=/root/vnx-dna-lab/v10-rel PYTHONPATH=src python experiments/v11/wp1/profile.py

Output (next to this file): profile-v10.prof, profile-v11.prof (pstats), profile-summary.json, profile-top.txt.
"""
from __future__ import annotations

import io
import json
import os
import pstats
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MIB = 1 << 20
SEED = 95500
CHANNEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002, "coverage": 5}
TOP = 25


def _load(name: str, rel: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _profile(label: str, tree: Path, work: Path) -> dict:
    prof = HERE / f"profile-{label}.prof"
    env = {**os.environ, "PYTHONPATH": str(tree / "src")}
    p = subprocess.run([sys.executable, "-m", "cProfile", "-o", str(prof), str(ROOT / "experiments/arch/compare.py"), "one",
                        "--step", "decode", "--work", str(work), "--label", label, "--workers", "1"],
                       env=env, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"{label} failed: {p.stderr[-2000:]}")
    row = json.loads(p.stdout.strip().splitlines()[-1])
    if not row["vnxdna_file"].startswith(str(tree)):
        raise SystemExit(f"{label} imported {row['vnxdna_file']}, not its tree {tree}")
    stats = pstats.Stats(str(prof))
    total = stats.total_tt
    align = {}
    for (file, line, func), (_cc, nc, tt, ct, _callers) in stats.stats.items():
        if file.endswith(("native/align.py", "v5/indel/align.py")) or "align" in func:
            align[f"{Path(file).name}:{line}:{func}"] = {"calls": nc, "tottime_s": round(tt, 4), "cumtime_s": round(ct, 4)}
    align = dict(sorted(align.items(), key=lambda kv: -kv[1]["cumtime_s"])[:10])
    buf = io.StringIO()
    pstats.Stats(str(prof), stream=buf).sort_stats("tottime").print_stats(TOP)
    return {"label": label, "tree": str(tree), "outcome": row["outcome"], "matches_input": row["matches_input"],
            "decode_seconds_profiled": round(row["seconds"], 3), "profile_total_s": round(total, 3),
            "native": row["native"], "align_functions_by_cumtime": align, "_top": buf.getvalue()}


def main() -> int:
    v10 = Path(os.environ.get("V10_TREE", "/root/vnx-dna-lab/v10-rel"))
    compare = _load("arch_compare", "experiments/arch/compare.py")
    with tempfile.TemporaryDirectory(prefix="v11-wp1-prof-") as tmp:
        work = Path(tmp)
        compare._prepare(work, v10, "decode", 4 * MIB, CHANNEL, SEED)
        rows = [_profile("v10", v10, work), _profile("v11", ROOT, work)]
    with open(HERE / "profile-top.txt", "w") as f:
        for r in rows:
            f.write(f"===== {r['label']} ({r['tree']}) top {TOP} by tottime =====\n{r.pop('_top')}\n")
    summary = {"scenario": {"kind": "decode", "bytes": 4 * MIB, "workers": 1, "seed": SEED, "channel": CHANNEL},
               "evidence": "SIMULATED", "load1_end": os.getloadavg()[0], "runs": rows,
               "profiled_decode_ratio_v10_over_v11": round(rows[0]["decode_seconds_profiled"] / rows[1]["decode_seconds_profiled"], 3)}
    (HERE / "profile-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("profiled_decode_ratio_v10_over_v11", "load1_end")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
