"""Table for docs/V5_PHASE3_OPTIMIZATION.md from P3O-EXP-02 (SIMULATED): saved work vs better load balance.

usage: python experiments/v5/phase3opt/summarize_control.py [results.json]
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "P3O-EXP-02-parallelism-control" / "results.json"
    R = json.loads(path.read_text())["results"]
    names = ["V5-hard eager", "V5-hard eager batch256", "V5-hard deferred"]
    print("| channel | cov | " + " | ".join(f"{n}: wall s / CPU s / SUCCESS" for n in names) + " | same outcome (all three) |")
    print("|---|---|" + "---|" * len(names) + "---|")
    tot = {n: [0.0, 0.0, 0] for n in names}
    for key, cell in R["summary"].items():
        ch, cov = key.split(" | cov ")
        out = []
        for n in names:
            x = cell[n]
            w, c = st.mean(x["seconds"]), st.mean(x["cpu_seconds"])
            tot[n][0] += sum(x["seconds"])
            tot[n][1] += sum(x["cpu_seconds"])
            tot[n][2] += x["verified_success"]
            out.append(f"{w:.1f} / {c:.0f} / {x['verified_success']}")
        same = all(cell[n]["status"] == cell[names[0]]["status"] and cell[n]["output_sha256"] == cell[names[0]]["output_sha256"]
                   and cell[n]["groups_recovered_fraction"] == cell[names[0]]["groups_recovered_fraction"] for n in names)
        print(f"| {ch} | {cov} | " + " | ".join(out) + f" | {'yes' if same else '**no**'} |")
    print("| **total** | | " + " | ".join(f"**{t[0]:.0f} / {t[1]:.0f} / {t[2]}**" for t in tot.values()) + " | |")


if __name__ == "__main__":
    main()
