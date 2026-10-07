#!/usr/bin/env python3
"""Sampling and Monte Carlo standard errors of the gating metrics (M2, M3, M8) of every DEV validation, computed only from
the committed validation results and model files (no read data is opened; no new look at DEV).

    python experiments/v7/fit/gating_se.py          # writes experiments/v7/fit/results/gating-se.json

What can be computed from the committed outputs:

* M3 (band shares): binomial SE of the real share (n = DEV reads tallied), of the simulated share (n = nominal simulated
  reads: 5 seeds x 6 reads x simulated references, before the read-length window and exclusions) and of their difference.
  Reads of one reference share its rates (read heterogeneity), so these are lower bounds of the true SEs.
* M2 KS D: the two-sample 5 % critical value 1.36 sqrt((n + m) / (n m)), the D two equal distributions reach by chance.
* M8: SE of each consensus error rate from its committed Wilson 95 % interval (half-width / 1.96).

Not computable without the read-level histograms, which are not committed (re-deriving them would be a new look at DEV):
the SE of M2's TV distance and of its p90/p99 quantile comparison.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SEEDS, READS = 5, 6
#: references simulated per job: dev_refs = refs[::max(1, R // step)] capped at 4,000 (run.py; run_d02.py uses 3,000 as step)
STEP = {"d02-twist": 3000}


def sim_refs(job: str, R: int) -> int:
    k = max(1, R // STEP.get(job, 4000))
    return min(len(range(0, R, k)), 4000)


def model_of(results: Path, job: str) -> dict:
    d = results.parent.parent
    name = {"cnr": "cnr-ont-fit", "cnr-p4tie": "cnr-ont-p4tie-fit", "d02-twist": "illumina-iseq-twist-fit",
            "d03-hac-fwd": "ont-guppy-hac-pass-fwd-fit", "d03-hac-bwd": "ont-guppy-hac-pass-bwd-fit",
            "d03-fast-fwd": "ont-guppy-fast-pass-fwd-fit", "d03-fast-bwd": "ont-guppy-fast-pass-bwd-fit"}[job]
    return json.loads((d / "models" / f"{name}.json").read_text())


def binom_se(p: float, n: float) -> float:
    return math.sqrt(max(p * (1 - p), 0.0) / n) if n > 0 else float("nan")


def one(path: Path) -> dict:
    v = json.loads(path.read_text())
    job = v["job"]
    val = model_of(path, job)["fit_report"]["validation"]
    n_real = val.get("dev_reads_tallied", val.get("dev_reads_tallied_runs_0a_0b"))
    n_sim = SEEDS * READS * sim_refs(job, val["dev_references"])
    m = v["metrics"]
    out: dict = {"job": job, "round": 2 if "/a2/" in str(path) else 1, "source": str(path.relative_to(ROOT)), "n_real_reads": n_real,
                 "n_simulated_reads_nominal": n_sim, "M3": {}, "M2": {}, "M8": {}}
    for band, b in m["M3"]["bands"].items():
        sr, ss = binom_se(b["real"], n_real), binom_se(b["sim"], n_sim)
        sd = math.hypot(sr, ss)
        margin = 1.0 - abs(b["diff_pp"])
        out["M3"][band] = {"diff_pp": b["diff_pp"], "pass": b["pass"], "se_real_pp": 100 * sr, "se_sim_mc_pp": 100 * ss,
                           "se_diff_pp": 100 * sd, "margin_to_1pp": margin, "margin_in_se": margin / (100 * sd) if sd > 0 else None}
    crit = 1.36 * math.sqrt((n_real + n_sim) / (n_real * n_sim))
    out["M2"] = {"ks_D": m["M2"]["ks_D"], "ks_threshold": m["M2"]["thresholds"]["ks_D"], "ks_D_5pct_null_critical": crit,
                 "tv": m["M2"]["tv"], "tv_se": None, "quantile_se": None,
                 "note": "TV and quantile SEs need the read-level histograms (not committed; not re-derived: no new DEV look)"}
    for k, c in m["M8"]["curve"].items():
        se_r = (c["real_wilson95"][1] - c["real_wilson95"][0]) / (2 * 1.96)
        se_s = (c["sim_wilson95"][1] - c["sim_wilson95"][0]) / (2 * 1.96)
        out["M8"][k] = {"real": c["real"], "sim": c["sim"], "diff_pp": 100 * (c["sim"] - c["real"]), "se_real_pp": 100 * se_r,
                        "se_sim_pp": 100 * se_s, "se_diff_pp": 100 * math.hypot(se_r, se_s), "pass": c["pass"]}
    return out


def main() -> int:
    files = sorted(ROOT.glob("experiments/v7/fit-*/results/*.validation.json")) + \
        sorted(ROOT.glob("experiments/v7/fit-*/a2/results/*.validation.json"))
    res = {"note": __doc__.strip().split("\n\n")[0], "assumption": "binomial SEs treat reads as independent (lower bounds)",
           "validations": [one(p) for p in files]}
    p = HERE / "results" / "gating-se.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    for r in res["validations"]:
        b = r["M3"]
        print(f"{r['job']:13s} r{r['round']}", " ".join(f"{k[-1]}:{v['diff_pp']:+.4f}+-{v['se_diff_pp']:.3f}" for k, v in b.items()),
              f"KS {r['M2']['ks_D']:.4f} (null 5% {r['M2']['ks_D_5pct_null_critical']:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
