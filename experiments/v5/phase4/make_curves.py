"""Recovery curves for the Phase 4 report, as CSV, generated from the stored results.json files (SIMULATED data).

  curves/read_recovery_substitutions.csv   P4-EXP-01: verified-correct reads vs substitution rate, per config and quality model
  curves/read_recovery_indels.csv          P4-EXP-03: the same vs indel + substitution rate
  curves/rs_boundary.csv                   P4-EXP-04: frames recovered vs 2e + f, summed over (e, f) splits
  curves/archive_coverage.csv              P4-EXP-08: archives verified vs coverage

usage: python experiments/v5/phase4/make_curves.py
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "curves"


def load(name: str) -> dict:
    return json.loads((HERE / name / "results.json").read_text())


def read_level(name: str, out: str) -> None:
    r = load(name)
    configs = list(r["config"]["configs"])
    with open(OUT / out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["channel", "qualities", *configs])
        for key, v in r["results"]["summary"].items():
            channel, quality = (s.strip() for s in key.split("|"))
            w.writerow([channel, quality, *(v[c]["verified_correct"] for c in configs)])


def rs_boundary() -> None:
    r = load("P4-EXP-04-rs-boundary")
    s, per_cell = r["results"]["summary"], r["config"]["frames_per_cell"]
    agg: dict = defaultdict(lambda: defaultdict(int))
    for key, v in s.items():
        d = agg[(v["2e+f"], key.split("|")[1].strip())]
        d["frames"] += per_cell
        for m in ("hard", "soft-erasure", "soft-chase", "soft-auto"):
            d[m] += v[m]["verified_correct"]
    with open(OUT / "rs_boundary.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["2e+f", "posteriors", "frames", "hard", "soft-erasure", "soft-chase", "soft-auto"])
        for (b, scen), d in sorted(agg.items()):
            w.writerow([b, scen, d["frames"], d["hard"], d["soft-erasure"], d["soft-chase"], d["soft-auto"]])


def archive_coverage() -> None:
    s = load("P4-EXP-08-coverage")["results"]["summary"]
    with open(OUT / "archive_coverage.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["channel", "coverage", "decoder", "verified_success", "trials", "false_success",
                    "groups_recovered_fraction_mean"])
        for key, v in s.items():
            channel, cov = key.rsplit(", cov ", 1)
            for d, x in v.items():
                g = [f for f in x["groups_recovered_fraction"] if f is not None]
                w.writerow([channel, int(cov), d, x["verified_success"], x["trials"], x["false_success"],
                            round(sum(g) / len(g), 4) if g else ""])


def main() -> None:
    OUT.mkdir(exist_ok=True)
    read_level("P4-EXP-01-substitution-sweep", "read_recovery_substitutions.csv")
    read_level("P4-EXP-03-indel-plus-substitution", "read_recovery_indels.csv")
    rs_boundary()
    archive_coverage()
    for f in sorted(OUT.glob("*.csv")):
        print(f.relative_to(HERE), sum(1 for _ in open(f)) - 1, "rows")


if __name__ == "__main__":
    main()
