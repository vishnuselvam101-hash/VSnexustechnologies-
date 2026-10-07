"""V9 G1 channel model: F1 + a discrete latent read state (docs/V9_PREREGISTRATION.md §4, amendment A2).

    PYTHONPATH=src python experiments/v9/d13/g1.py fit [--workers 3]        # per-read EM on FIT, K by BIC, calibration
    PYTHONPATH=src python experiments/v9/d13/g1.py precheck [--workers 3]   # V8 FIT-only pre-check of the G1 model

Reads only the FIT split (every access goes through the V8 guard and its ledger). DEV and held-out are not read here.

Fit (FIT only):

1. per-read sufficient statistics of the FIT segments of every ``ROW_STRIDE``-th table row: substitution events,
   insertion runs and deletion runs of the normalised unit-cost NW alignment (the V8 alignment), and the A1 selection
   (edit distance <= 0.30 L) applied as in the tables;
2. K in {1, 2, 3, 4}: EM for a mixture of K classes, each with three independent Poisson rates (sub, ins, del per read);
   K is chosen by BIC = -2 logL + (4K - 1) ln n; deterministic initialisation (classes seeded at total-count quantiles);
3. class multipliers = class rate / weighted mean rate, per type (so the mixture keeps the F1 mean per type); they replace
   F1's gamma multiplier (``read_heterogeneity`` -> ``latent-states``); every other F1 parameter is kept;
4. calibration: the three base rates are rescaled ``CAL_ITER`` times so that reads simulated on FIT references and passed
   through the A1 selection have the FIT per-base rates (the V8 calibration idea, applied to the three totals only).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
V8 = ROOT / "experiments" / "v8" / "d13"
sys.path.insert(0, str(V8))
import access as A                                                                      # noqa: E402
import fit as F8                                                                        # noqa: E402
import pipeline as PL                                                                   # noqa: E402
from vnxdna.simulation import model as cm                                               # noqa: E402
from vnxdna.simulation.fit import adequacy as AQ, precheck as PC, simulate as S        # noqa: E402
from vnxdna.simulation.fit import validate as V                                        # noqa: E402
from vnxdna.simulation.fit.align import align                                          # noqa: E402
from vnxdna.simulation.fit.pipeline import tally_matrix                                # noqa: E402

SEED_FIT, SEED_PRECHECK = 20261110, 20261112          # V9 seeds (the V8 ones + 100 in the month digit block)
ROW_STRIDE = 10
KS = (1, 2, 3, 4)
EM_ITERS, EM_TOL = 2000, 1e-10
CAL_REFS, CAL_COVERAGE, CAL_ITER = 2000, 10, 4
MAX_EDIT_FRAC = F8.TALLY_OPTS["max_edit_frac"]
MODEL = HERE / "models" / "d13-nanopore-g1.json"
RESULTS = HERE / "results"
PREREG = ROOT / "docs" / "V9_PREREGISTRATION.md"


def _git() -> dict:
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "src", "experiments/v9"], capture_output=True,
                                text=True).stdout.strip())
    return {"commit": sha, "dirty": dirty}


def per_read_counts(pairs, L: int) -> np.ndarray:
    """(n, 4) int array: substitution events, insertion runs, deletion runs, edit distance; A1 selection applied."""
    out = []
    for ref, seqs, _q in pairs:
        for read in seqs:
            res = align(read, ref, "NW")
            if res is None:
                continue
            ed, runs, _w = res
            if ed > MAX_EDIT_FRAC * L:
                continue
            s = sum(n for op, n in runs if op == "X")
            i = sum(1 for op, _ in runs if op == "I")
            d = sum(1 for op, _ in runs if op == "D")
            out.append((s, i, d, ed))
    return np.asarray(out, dtype=np.int64)


def em(X: np.ndarray, K: int) -> dict:
    """Mixture of K classes x 3 independent Poissons on X (n, 3). Deterministic: classes start at total-count quantiles."""
    n = X.shape[0]
    tot = X.sum(axis=1)
    edges = np.quantile(tot, np.linspace(0, 1, K + 1))
    z = np.clip(np.searchsorted(edges[1:-1], tot, side="right"), 0, K - 1)
    R = np.zeros((n, K))
    R[np.arange(n), z] = 1.0
    lg = np.vectorize(math.lgamma)(X + 1.0).sum(axis=1)
    prev = -np.inf
    for it in range(EM_ITERS):
        w = R.sum(axis=0) / n
        lam = (R.T @ X) / np.maximum(R.sum(axis=0)[:, None], 1e-300)
        lam = np.maximum(lam, 1e-9)
        ll_k = X @ np.log(lam).T - lam.sum(axis=1)[None, :] - lg[:, None] + np.log(np.maximum(w, 1e-300))[None, :]
        mx = ll_k.max(axis=1, keepdims=True)
        lse = mx[:, 0] + np.log(np.exp(ll_k - mx).sum(axis=1))
        ll = float(lse.sum())
        R = np.exp(ll_k - lse[:, None])
        if ll - prev < EM_TOL * abs(ll):
            break
        prev = ll
    order = np.argsort(lam.sum(axis=1))
    return {"K": K, "weights": w[order].tolist(), "rates": lam[order].tolist(), "loglik": ll, "iterations": it + 1,
            "bic": -2 * ll + (4 * K - 1) * math.log(n), "n": n}


def multipliers(sol: dict) -> list[dict]:
    w, lam = np.asarray(sol["weights"]), np.asarray(sol["rates"])
    mean = (w[:, None] * lam).sum(axis=0)
    m = lam / np.maximum(mean[None, :], 1e-300)
    return [{"weight": float(w[k]), "sub": float(m[k, 0]), "ins": float(m[k, 1]), "del": float(m[k, 2])} for k in range(len(w))]


def _sim_rates(model, lay, refs, seed: int, workers: int) -> dict:
    cl = S.simulate_clusters(model, refs, CAL_COVERAGE, seed)
    T, _ = tally_matrix(zip(refs, cl), lay, mode="NW", workers=workers, **F8.TALLY_OPTS)
    return V.rates(lay, T.sum(axis=0) if T.ndim == 2 else T)


def do_fit(workers: int) -> Path:
    guard = A.V8Guard("experiments/v9/d13/g1.py fit")
    for run in A.RUNS:
        guard.authorize(run, A.FIT, "V9 G1 fit (per-read EM + calibration) on FIT")
    t0 = time.time()
    lay, M, rs, runs, ref_rows = PL.load_tables(A.FIT)
    rows = set(range(0, M.shape[0], ROW_STRIDE))
    X4 = per_read_counts(F8._fit_pairs(rows), lay.L)
    X = X4[:, :3]
    sols = {K: em(X, K) for K in KS}
    K = min(KS, key=lambda k: sols[k]["bic"])
    states = multipliers(sols[K])
    f1 = json.loads((V8 / "models" / "d13-nanopore-f1.json").read_text())
    doc = copy.deepcopy(f1)
    seq = doc["stages"]["sequencing"]
    seq["read_heterogeneity"] = {"distribution": "latent-states", "states": states}
    doc["name"], doc["version"], doc["model_id"] = "d13-nanopore-g1", "1.0.0", "d13-nanopore-G1"
    doc["description"] = "V9 G1: V8 F1 (D13 FIT) with a discrete latent read state (K by BIC) replacing the gamma multiplier."
    real = V.rates(lay, M.sum(axis=0))
    refs = F8.fit_refs(ref_rows, runs)
    cal_refs = refs[:: max(1, len(refs) // CAL_REFS)][:CAL_REFS]
    cal = []
    for it in range(CAL_ITER):
        model, _ = cm.from_doc(doc)
        sim = _sim_rates(model, lay, cal_refs, SEED_FIT + it, workers)
        ratio = {k: real[k] / max(sim[k], 1e-12) for k in ("substitution", "insertion", "deletion")}
        cal.append({"iteration": it, "sim": {k: sim[k] for k in ratio}, "ratio": ratio})
        for k in ratio:
            seq[k]["rate"] = float(min(0.5, seq[k]["rate"] * ratio[k]))
    g = _git()
    prov = doc["provenance"]["fitting"]
    prov.update(method="V9 G1: per-read Poisson-mixture EM (K by BIC) on V8 F1 + 3-rate calibration (experiments/v9/d13/g1.py)",
                version="v9-g1/1", commit=g["commit"], dirty=g["dirty"], seed=SEED_FIT,
                timestamp_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    params = json.dumps(doc["stages"], sort_keys=True).encode()
    prov["parameter_sha256"] = hashlib.sha256(params).hexdigest()
    model, _ = cm.from_doc(doc)
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    MODEL.write_text(model.dumps())
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = {"experiment": "V9 G1 fit", "evidence_class": "PUBLIC-DATA-DERIVED parameters (D13 FIT split only)",
           "data_firewall": {"split_read": A.FIT, "dev_read": False, "heldout_read": False},
           "reads": int(X.shape[0]), "row_stride": ROW_STRIDE, "em": {str(k): v for k, v in sols.items()}, "K": K,
           "states": states, "real_rates": {k: real[k] for k in ("substitution", "insertion", "deletion")}, "calibration": cal,
           "per_read_means": {"sub": float(X[:, 0].mean()), "ins_runs": float(X[:, 1].mean()), "del_runs": float(X[:, 2].mean()),
                              "edit_distance": float(X4[:, 3].mean())},
           "model_sha256": model.sha256, "seed": SEED_FIT, "code": g, "seconds": round(time.time() - t0, 1), "workers": workers}
    p = RESULTS / "fit-g1.json"
    p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"K": K, "states": states, "bic": {k: round(v["bic"], 1) for k, v in sols.items()},
                      "model_sha256": model.sha256, "seconds": out["seconds"]}, indent=1))
    return p


def do_precheck(workers: int) -> Path:
    """The V8 FIT-only pre-check (experiments/v8/d13/fit.py do_precheck, A1 selection) run on the G1 model."""
    guard = A.V8Guard("experiments/v9/d13/g1.py precheck")
    for run in A.RUNS:
        guard.authorize(run, A.FIT, "V9 FIT-only pre-check of G1")
    t0 = time.time()
    model, _ = cm.from_doc(json.loads(MODEL.read_text()))
    lay, M, rs, runs, ref_rows = PL.load_tables(A.FIT)
    refs = F8.fit_refs(ref_rows, runs)
    clusters = [(r, s) for r, s, _q in F8._fit_pairs(set(range(0, M.shape[0], max(1, M.shape[0] // F8.M8_CLUSTERS))))][:F8.M8_CLUSTERS]
    full = F8._in_sample(model, lay, M, rs, clusters, refs, SEED_PRECHECK, workers)
    rt_refs = refs[:: max(1, len(refs) // 3000)][:3000]
    cal = dict(refs=refs[:: max(1, len(refs) // 2000)][:2000], coverage=F8.CAL_COVERAGE, iterations=F8.CAL_ITER, tally_opts=F8.TALLY_OPTS)
    full["M10"] = AQ.m10_v8(model, lay, rt_refs, coverage=8, seed=SEED_PRECHECK + 5, replicates=3, calibration=cal, workers=workers,
                            tally_opts=F8.TALLY_OPTS)
    halves = []
    for h, idx in enumerate(PC.halves(M.shape[0], SEED_PRECHECK)):
        rows = set(int(i) for i in idx)
        Mh, rsh = tally_matrix(F8._fit_pairs(rows), lay, mode="NW", workers=workers, **F8.TALLY_OPTS)
        hrefs = [refs[i] for i in sorted(rows)]
        hcl = [c for i, c in enumerate(clusters) if i % 2 == h]
        halves.append(F8._in_sample(model, lay, Mh, rsh, hcl, hrefs, SEED_PRECHECK + 10 + h, workers))
    stab = PC.stability(full, halves)
    decision = PC.decide(full)
    decision["model_sha256"] = model.sha256
    if decision["decision"] == PC.DECISION_BLOCKED:
        decision["decision"] = "DEV_LOOK_BLOCKED — FIT PRE-CHECK INADEQUATE"
    out = {"experiment": "V9 FIT-only pre-check of G1", "evidence_class":
           "SIMULATED reads vs PUBLIC-DATA-DERIVED FIT segments (IN SAMPLE: not validation)",
           "data_firewall": {"split_read": A.FIT, "dev_read": False, "heldout_read": False},
           "decision": decision, "stability": stab, "metric_table": AQ.metric_table(full),
           "metrics_full": full, "metrics_halves": halves, "model_sha256": model.sha256,
           "seed": SEED_PRECHECK, "code": _git(), "workers": workers, "seconds": round(time.time() - t0, 1),
           "amendments": ["V8 A1: matched read selection (max_edit_frac 0.30) on simulated reads", "V9 A2 (G1 definition)"]}
    p = RESULTS / "precheck-g1.json"
    p.write_text(json.dumps(F8.PC_jsonable(out), indent=1, sort_keys=True) + "\n")
    print(json.dumps({"decision": decision, "classes": {k: v["class"] for k, v in stab.items()}, "seconds": out["seconds"]}, indent=1))
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fit", "precheck"])
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    (do_fit if a.cmd == "fit" else do_precheck)(a.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
