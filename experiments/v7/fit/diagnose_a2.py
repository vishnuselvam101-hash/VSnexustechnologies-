#!/usr/bin/env python3
"""FIT-only diagnosis behind protocol amendment 2 (docs/V7_PROTOCOL.md 5.5, "Finding"): per-read edit-distance and
length-drift statistics of the FIT reads against reads simulated from the round-1 model F for FIT references, through the
same alignment pipeline. PUBLIC-DATA-DERIVED (FIT reads) and SIMULATED (simulated reads).

    PYTHONPATH=src python experiments/v7/fit/diagnose_a2.py [--workers 8] [jobs ...]   # default: d03-hac-fwd d03-fast-fwd cnr

Only FIT reads are read (through the split guard; every request is in the access ledger). Simulated reads: 6 per reference
for up to 3,000 FIT references (every k-th), seed 99, round-1 models in fit-<dataset>/models. As in the original
diagnosis (made before the A2.2 read-length window existed), no read-length selection is applied. Writes
experiments/v7/fit/results/a2-diagnosis.json. The result does not depend on the worker count.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fitlib as FL  # noqa: E402
import run as R  # noqa: E402
from fitlib import G, P, cm  # noqa: E402
from vnxdna.simulation.fit.simulate import simulate_clusters  # noqa: E402
from vnxdna.simulation.fit.tally import DRIFT_OFF, EDIT_BINS, Layout  # noqa: E402

SEED = 99
SIM_REFS, SIM_COVERAGE = 3000, 6


def stats(h: np.ndarray) -> dict:
    """Edit-distance and drift moments and band shares of a read-level histogram (EDIT_BINS edit bins, then drift bins)."""
    e = h[:EDIT_BINS].astype(float)
    x = np.arange(EDIT_BINS)
    p = e / e.sum()
    m = float((p * x).sum())
    v = float((p * (x - m) ** 2).sum())
    d = h[EDIT_BINS:].astype(float)
    d /= d.sum()
    dx = np.arange(d.size) - DRIFT_OFF
    dm = float((d * dx).sum())
    return {"reads": int(e.sum()), "edit_mean": m, "edit_var": v, "var_over_mean": v / m, "drift_mean": dm,
            "drift_var": float((d * (dx - dm) ** 2).sum()), "drift_le_0": float(d[DRIFT_OFF]),
            "drift_le_3": float(d[DRIFT_OFF - 3:DRIFT_OFF + 4].sum()), "drift_le_6": float(d[DRIFT_OFF - 6:DRIFT_OFF + 7].sum()),
            "drift_min": int(dx[d > 0].min()), "drift_max": int(dx[d > 0].max())}


def diagnose(job_id: str, workers: int) -> dict:
    job = R.JOBS[job_id]
    opts = {"aligner": job.get("aligner", "edlib"), "shift": "left", "length_window": None}
    guard = G.Guard(FL.DATA_DIR, script=f"experiments/v7/fit/diagnose_a2.py {job_id}")
    lay = Layout(job["L"])
    real = None
    refs: list = []
    for label, src in R.sources(guard, job, "FIT"):
        coll = FL.Collector(src, orient_backward=label.endswith("/backward"))
        _M, rs = P.tally_matrix(coll, lay, mode=job["mode"], workers=workers, **opts)
        real = rs if real is None else real + rs
        refs += coll.refs
    mp = R.OUT / Path(R.out_dir(job)).parent.name / "models" / f"{job['name']}.json"      # round 1
    model = cm.from_doc(json.loads(mp.read_text()))[0]
    sub = refs[::max(1, len(refs) // SIM_REFS)][:SIM_REFS]
    cl = simulate_clusters(model, sub, SIM_COVERAGE, SEED)
    _M, srs = P.tally_matrix(zip(sub, cl), lay, mode="NW", workers=workers, **opts)
    r, s = stats(real), stats(srs)
    s2 = (r["edit_var"] - s["edit_var"]) / r["edit_mean"] ** 2
    return {"model": str(mp.relative_to(FL.REPO)), "model_sha256": model.sha256, "fit_references": len(refs),
            "simulated_references": len(sub), "real_fit": r, "simulated": s,
            "implied_read_multiplier_variance": s2, "implied_gamma_shape": (1.0 / s2) if s2 > 0 else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="*", default=["d03-hac-fwd", "d03-fast-fwd", "cnr"])
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    out = {"evidence_class": "PUBLIC-DATA-DERIVED (FIT reads) vs SIMULATED (reads from round-1 model F)",
           "split": "FIT", "seed": SEED, "simulated_reads_per_reference": SIM_COVERAGE, "read_length_selection": None,
           "environment": R.environment_info(), "jobs": {}}
    for j in a.jobs:
        out["jobs"][j] = diagnose(j, a.workers)
        d = out["jobs"][j]
        print(j, "var/mean real", round(d["real_fit"]["var_over_mean"], 2), "simulated", round(d["simulated"]["var_over_mean"], 2))
    p = Path(__file__).resolve().parent / "results" / "a2-diagnosis.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
