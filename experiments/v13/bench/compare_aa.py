"""Compare a V13 A/B e2e-summary.json against an A/A noise band scenario by scenario (default: this directory's
baseline-aa; an optional second argument names another A/A summary, e.g. ../v12/bench/baseline-aa/e2e-summary.json).

    PYTHONPATH=src python experiments/v13/bench/compare_aa.py experiments/v13/bench/ab-<name>/e2e-summary.json [aa-summary.json]

A difference is called "beyond noise" only when the A/B 95 % CI (min-max with n = 5) does not overlap the A/A CI of
the same scenario. Everything here is SIMULATED (simulated channels, this host).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    ab = json.loads(Path(argv[1]).read_text())
    aa = json.loads(Path(argv[2] if len(argv) > 2 else HERE / "baseline-aa" / "e2e-summary.json").read_text())
    aa_key = next(k for k in next(iter(aa["scenarios"].values())) if k.startswith("ratio_"))
    print(f"{'scenario':18s} {'A/B median':>10s} {'A/B ci95':>16s} {'A/A median':>10s} {'A/A ci95':>16s}  verdict")
    for name, s in ab["scenarios"].items():
        r, n = s["ratio_v12_over_v13"], aa["scenarios"].get(name, {}).get(aa_key)
        lo, hi = r["ci95"]
        if n is None:
            verdict = "no A/A band"
        elif lo > n["ci95"][1]:
            verdict = "FASTER beyond noise"
        elif hi < n["ci95"][0]:
            verdict = "SLOWER beyond noise"
        else:
            verdict = "within noise"
        ident = "" if s["identical_outcome_and_output"] else "  OUTPUT DIFFERS"
        nm = f"{n['median']:10.3f} [{n['ci95'][0]:.3f}, {n['ci95'][1]:.3f}]" if n else f"{'':10s} {'':16s}"
        print(f"{name:18s} {r['median']:10.3f} [{lo:.3f}, {hi:.3f}] {nm}  {verdict}{ident}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
