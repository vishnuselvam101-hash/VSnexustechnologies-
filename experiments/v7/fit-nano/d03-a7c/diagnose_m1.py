"""V7 7.4 step 3: M1 (per-base insertion rate) diagnosis of the a7c candidates on FIT data only.

M1 counts every inserted base (tally field ``ins_base``), including insertions after the last reference base
(``end_ins_bases``); the calibration matches insertion *events* inside the reference (``pos_ins``) and the run-length pmf.
This splits the inserted bases of FIT reads and of reads simulated from the candidate (validation settings: 6 reads per
reference, 5 seeds, the same read-length window) into interior and read-end insertions, and the interior ones by relative
position (10 bins), to tell an aggregate calibration gap from a stratified (end-of-read) one.

Data firewall: FIT split only (split guard); no DEV, held-out or validation artefact is read.
    PYTHONPATH=src python experiments/v7/fit-nano/d03-a7c/diagnose_m1.py <job> [--workers 4]
Evidence class: PUBLIC-DATA-DERIVED (FIT reads) vs SIMULATED. In sample; not validation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "fit"))
import fitlib as FL                                                     # noqa: E402
import run as R                                                         # noqa: E402
from fitlib import G, P, V, cm                                          # noqa: E402
from vnxdna.simulation.fit.simulate import simulate_clusters            # noqa: E402

SPLIT = "FIT"
SEED = 20261008


def split_counts(lay, T) -> dict:
    n = float(lay.get(T, "n_reads").sum())
    nb = n * lay.L
    total = float(lay.get(T, "ins_base").sum())
    end = float(lay.get(T, "end_ins_bases").sum())
    pos = lay.get(T, "pos_ins").astype(float)
    prof = np.array([c.sum() for c in np.array_split(pos, 10)]) / nb
    return {"reads": n, "insertion_bases_per_base": total / nb, "end_insertion_bases_per_base": end / nb,
            "interior_insertion_bases_per_base": (total - end) / nb, "end_share": end / total if total else 0.0,
            "end_insertion_events_per_read": float(lay.get(T, "end_ins_events").sum()) / n,
            "interior_insertion_events_per_base_by_decile": prof.tolist(),
            "insertion_events_per_base": float(pos.sum()) / nb}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    R.ROUND, R.MODEL_VERSION = "a7c", R.ROUNDS["a7c"]["version"]
    job = R.JOBS[a.job]
    d = R.out_dir(job)
    model, _ = cm.from_doc(json.loads((d / "models" / f"{job['name']}.json").read_text()))
    guard = G.Guard(FL.DATA_DIR, script=f"experiments/v7/fit-nano/d03-a7c/diagnose_m1.py {a.job}")
    lay, Ms, rss, colls, labels = R.tally_split(guard, job, SPLIT, a.workers)
    M = np.concatenate(Ms)
    refs = [r for c in colls for r in c.refs]
    refs = refs[::max(1, len(refs) // 4000)][:4000]
    sim = np.zeros(lay.size)
    for s in range(5):
        Ms_, _ = P.tally_matrix(zip(refs, simulate_clusters(model, refs, 6, SEED * 1000 + s)), lay, workers=a.workers, **R.topts(job))
        sim += Ms_.sum(axis=0)
    real = split_counts(lay, M.sum(axis=0).astype(float))
    simc = split_counts(lay, sim)
    ratio = {k: (simc[k] / real[k] if isinstance(real[k], float) and real[k] else None) for k in real if not isinstance(real[k], list)}
    out = {"experiment": "V7 7.4 step 3 M1 diagnosis (insertion bases: interior vs read end)", "job": a.job, "split_read": SPLIT,
           "evidence_class": "PUBLIC-DATA-DERIVED FIT reads vs SIMULATED reads (in sample; not validation)",
           "model_sha256": model.sha256, "fit": real, "simulated": simc, "sim_over_fit": ratio,
           "decile_sim_over_fit": (np.asarray(simc["interior_insertion_events_per_base_by_decile"])
                                   / np.maximum(np.asarray(real["interior_insertion_events_per_base_by_decile"]), 1e-300)).tolist(),
           "m1_check": V.rates(lay, M.sum(axis=0).astype(float)), "seeds": [SEED * 1000 + s for s in range(5)],
           "code": FL.git_state(FL.REPO), "split_manifest_sha256": FL.SPLIT_SHA}
    p = d / "diagnosis" / f"{a.job}.m1.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(R._jsonable(out), indent=1, sort_keys=True) + "\n")
    print(json.dumps({"job": a.job, "sim_over_fit": ratio, "deciles": [round(x, 3) for x in out["decile_sim_over_fit"]]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
