"""V9 model comparison on D13 DEV (docs/V9_PREREGISTRATION.md §4.3 item 3): one evaluation, after D's verdict is fixed.

The V8.6 harness (experiments/v8/d13/compare.py) with the V9 model set. Models: A = V6 shipped ``nanopore-like``;
B = V7 a7b ``d03-hac-fwd``; C = V8 F1 (D13 FIT); D = V9 G1 (D13 FIT, latent read classes; verdict INADEQUATE from the
FIT pre-check). Every model goes
through the same harness: ``validate_model`` (7B metrics) against the D13 DEV tables, simulated reads under the
amendment A1 selection, the same seeds. Per-run M1 strata are reported. Nothing is selected or tuned; the comparison does
not count as a DEV look for D.

    PYTHONPATH=src python experiments/v9/d13/compare.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "v8" / "d13"))
import access as A                                                     # noqa: E402
import fit as FT                                                       # noqa: E402
from vnxdna.simulation import model as cm, registry                    # noqa: E402
from vnxdna.simulation.fit import adequacy as AQ, validate as V        # noqa: E402

SEED = 20261111                                                        # V9 comparison seed
RESULTS = HERE / "results"
WORKERS = 2
MODELS = {"A": ("V6 shipped nanopore-like", None),
          "B": ("V7 a7b d03-hac-fwd", ROOT / "experiments/v7/fit-nano/d03/models/ont-guppy-hac-pass-fwd-fit.json"),
          "C": ("V8 F1 (D13 FIT)", ROOT / "experiments/v8/d13/models/d13-nanopore-f1.json"),
          "D": ("V9 G1 (D13 FIT, latent read classes)", HERE / "models" / "d13-nanopore-g1.json")}


def load(key: str):
    path = MODELS[key][1]
    return registry.load_model("nanopore-like") if path is None else cm.from_doc(json.loads(path.read_text()))[0]


def main() -> int:
    verdict = json.loads((RESULTS / "VERDICT.json").read_text())
    if verdict.get("verdict") not in ("ADEQUATE", "INADEQUATE"):
        raise SystemExit("the D verdict must be fixed before the comparison (pre-registration §4.4)")
    if (RESULTS / "comparison.json").exists():
        raise SystemExit("the comparison runs once (results/comparison.json exists)")
    guard = A.V8Guard("experiments/v9/d13/compare.py")
    for run in A.RUNS:
        guard.authorize(run, A.DEV, "V9 one comparison evaluation of A-D on DEV (selects nothing; not a DEV look for G1)")
    t0 = time.time()
    lay, M, rs, runs, ref_rows = FT.PL.load_tables(A.DEV)
    refs = FT.fit_refs(ref_rows, runs)
    dev_rows = set(range(0, M.shape[0], max(1, M.shape[0] // FT.M8_CLUSTERS)))
    clusters = []
    k = 0
    for run in A.RUNS:
        rr = FT.PL.run_refs(run)
        for ref, seqs, _q in FT.PL.split_pairs(run, A.DEV, rr):
            if k in dev_rows:
                clusters.append((ref, seqs))
            k += 1
    clusters = clusters[:FT.M8_CLUSTERS]
    out: dict = {"experiment": "V9 model comparison on D13 DEV", "evidence_class":
                 "SIMULATED reads (each model) vs PUBLIC-DATA-DERIVED D13 DEV segments", "seed": SEED,
                 "d_verdict": verdict["verdict"], "dev_tables_sha256": json.loads((FT.RESULTS / "tables.json").read_text())["splits"][A.DEV]["sha256"],
                 "dev_segments": int(lay.get(M, "n_reads").sum()), "dev_references": int(M.shape[0]), "models": {}}
    for key in MODELS:
        m = load(key)
        t = time.time()
        rep = V.validate_model(m, lay, dev_refs=refs[:: max(1, len(refs) // FT.SIM_REFS)], dev_clusters=clusters, dev_M=M,
                               dev_rs=rs, mode="NW", seed=SEED, workers=WORKERS, prereg="7B", tally_opts=FT.TALLY_OPTS)
        sim_rates = {k: v["sim"] for k, v in rep["M1"]["rates"].items()}
        per_run = {}
        for r in sorted(set(runs.tolist())):
            real = V.rates(lay, M[runs == r].sum(axis=0).astype(float))
            per_run[str(r)] = {k: {"real": float(real[k]), "model": sim_rates[k],
                                   "rel_error": abs(sim_rates[k] - real[k]) / real[k] if real[k] else None,
                                   "within_5pct": bool(abs(sim_rates[k] - real[k]) <= 0.05 * real[k])}
                               for k in ("substitution", "insertion", "deletion")}
        gate = [g for g in V.GATING_7B if g != "M10"]
        failed = [g for g in gate if rep.get(g, {}).get("pass") is False]
        out["models"][key] = {"name": MODELS[key][0], "model_sha256": m.sha256, "schema": m.doc["schema"],
                              "evidence_class": m.doc["evidence_class"], "failed_7b_metrics_excluding_M10": failed,
                              "passed": [g for g in gate if rep.get(g, {}).get("pass") is True],
                              "metric_table": AQ.metric_table(rep), "m1_by_run_not_gating": per_run,
                              "seconds": round(time.time() - t, 1)}
        print(key, MODELS[key][0], "fails", failed, flush=True)
    out["note"] = ("M10 (round trip) is a property of the model alone and is not part of this DEV comparison; see the V7 and V8 "
                   "pre-check records. No model was selected or tuned on these numbers.")
    out["code"], out["seconds"] = FT.PL.GIT_AT_START, round(time.time() - t0, 1)
    (RESULTS / "comparison.json").write_text(json.dumps(FT.PC_jsonable(out), indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
