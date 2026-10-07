"""V8.4 D13 fitter and FIT-only pre-check (docs/V8_PREREGISTRATION.md §2-4).

    PYTHONPATH=src python experiments/v8/d13/fit.py fit F1 [--workers 4]        # fit on the pooled FIT tables
    PYTHONPATH=src python experiments/v8/d13/fit.py precheck F1 [--workers 4]   # in-sample 7B + M10-V8 on FIT, halves

Reads only FIT tables and FIT caches produced by pipeline.py (hash-checked); DEV and held-out are not read here.
Outputs: models/d13-nanopore-<family>.json (parameters only; PUBLIC-DATA-DERIVED), results/fit-<family>.json,
results/precheck-<family>.json.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import access as A                                                                  # noqa: E402
import pipeline as PL                                                               # noqa: E402
from vnxdna import _version                                                         # noqa: E402
from vnxdna.simulation import model as cm, model2                                   # noqa: E402
from vnxdna.simulation.fit import adequacy as AQ, estimate as est, fit as F         # noqa: E402
from vnxdna.simulation.fit import model_out as MO, precheck as PC, validate as V    # noqa: E402
from vnxdna.simulation.fit.pipeline import tally_matrix                             # noqa: E402

SEED_FIT, SEED_PRECHECK = 20261010, 20261012                  # V8_MANIFEST seeds
CAL_REFS, CAL_COVERAGE, CAL_ITER = 4000, 10, 5                # as V7 run.py
BOOTSTRAP = 200
SIM_REFS = 4000
M8_CLUSTERS = 6000
PREREG = HERE.parents[2] / "docs" / "V8_PREREGISTRATION.md"
MODELS, RESULTS = HERE / "models", HERE / "results"
METHOD = ("vnx-channel-fit/1 (V7 fitter: edlib unit-cost global alignment, leftmost indel normalisation, count estimators, "
          "bootstrap over references, simulation calibration) on D13 segments (V8)")
#: amendment A1 (docs/V8_PREREGISTRATION-A1.md): the D13 segmentation selection applied to simulated reads in every comparison
TALLY_OPTS = {"max_edit_frac": 0.30}
FAMILIES = ("F1",)                                            # F2 only if the pre-registered rule triggers (§2)
#: V7 basis labels → V8 vocabulary (model2.V8_BASIS)
V8_LABELS = {k: model2.V8_BASIS[v] for k, v in MO.BASIS.items()}


def fit_refs(ref_rows: np.ndarray, runs: np.ndarray) -> list[bytes]:
    """Reference sequences of the FIT table rows (row order)."""
    by_run = {r: PL.run_refs(r) for r in A.RUNS}
    return [by_run[str(run)][int(i)] for run, i in zip(runs, ref_rows)]


def design_for(family: str, lay, T: np.ndarray) -> est.Design:
    d = est.choose_design(lay, T)
    if family == "F1":
        return dataclasses.replace(d, ins_geometric=False, del_geometric=False, ins_empirical=True, del_empirical=True,
                                   hp_by_length=True)
    raise SystemExit(f"family {family} is not implemented (pre-registration §2: F2 only after its rule triggers)")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def dataset_entry() -> dict:
    man = json.loads(PL.MANIFEST.read_text())["datasets"]["d13-lopez-nanopore"]
    files = [f for f in man["files"] if not f["path"].endswith("run13.fastq.gz") and "space_shuttle" not in f["path"]]
    return {"id": "D13", "accession": f"github:uwmisl/data-ncomms19-nanopore@{man['source_commit']['sha'][:12]}",
            "url": man["url"], "files": [{"name": f["path"], "sha256": f["sha256"]} for f in files]}


def do_fit(family: str, workers: int) -> Path:
    guard = A.V8Guard(f"experiments/v8/d13/fit.py fit {family}")
    for run in A.RUNS:
        guard.authorize(run, A.FIT, f"V8.4 fit {family} on pooled FIT tables")
    t0 = time.time()
    lay, M, rs, runs, ref_rows = PL.load_tables(A.FIT)
    T = M.sum(axis=0).astype(float)
    refs = fit_refs(ref_rows, runs)
    design = design_for(family, lay, T)
    cal_refs = refs[:: max(1, len(refs) // CAL_REFS)][:CAL_REFS]
    fit = F.fit_tallies(M, lay, seed=SEED_FIT, bootstrap=BOOTSTRAP, design=design,
                        calibration=dict(refs=cal_refs, coverage=CAL_COVERAGE, iterations=CAL_ITER, workers=workers))
    config = {"family": family, "design": dataclasses.asdict(design), "layout": json.loads((RESULTS / "tables.json").read_text())["layout"],
              "bootstrap": BOOTSTRAP, "calibration": {"refs": len(cal_refs), "coverage": CAL_COVERAGE, "iterations": CAL_ITER},
              "seed": SEED_FIT, "prereg_sha256": sha256_bytes(PREREG.read_bytes()),
              "tables_sha256": json.loads((RESULTS / "tables.json").read_text())["splits"][A.FIT]["sha256"]}
    cfg_sha = sha256_bytes(json.dumps(config, sort_keys=True, default=str).encode())
    obs = fit["values"]["_observed"]
    report = {"adequacy": "UNVALIDATED", "failed_metrics": [],
              "measured_statistics": {"observed_per_base_rates": obs, "observed_per_base_rates_ci95": fit["ci95"]["_observed"],
                                      "deletion_run_histogram_1_to_32plus": fit["values"]["_stats"]["deletion_runs_hist"],
                                      "insertion_run_histogram_1_to_32plus": fit["values"]["_stats"]["insertion_runs_hist"],
                                      "design": PC_jsonable(config["design"])},
              "notes": ["PUBLIC-DATA-DERIVED parameters (D13, Lopez et al. 2019; FIT split only); reads simulated from this "
                        "model are SIMULATED. No DNA was synthesised, stored or sequenced by VNX-DNA",
                        "pooled over D13 runs 15, 16, 18 and 20 (one ONT MinION R9.4 1D^2 condition; basecaller not stated)",
                        "segments of concatemer reads: whole-read length and read-end effects are not part of this model",
                        "dropout is not identifiable from these data (assembly, PCR and segmentation losses are mixed)"]}
    eff = MO.confounded_substitution(fit)
    if eff is not None:
        report["measured_statistics"]["substitution_rate_effective_per_base"] = eff
        report["notes"].append(MO.CONFOUNDED_SUBSTITUTION_NOTE)
    git = PL.GIT_AT_START
    fitting = {"method": METHOD, "version": _version.__version__, "commit": git["commit"], "dirty": git["dirty_tracked"],
               "seed": SEED_FIT, "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "software": {"python": sys.version.split()[0], "numpy": np.__version__, "vnxdna": _version.__version__},
               "configuration_sha256": cfg_sha}
    labels = dict(V8_LABELS)
    if design.context and "substitution" in design.context:
        labels["sequencing.substitution.rate"] = "fitted"
        if est.hp_sub_fixed(design):
            labels["sequencing.homopolymer.substitution_multiplier"] = "assumed"
    labels["synthesis.dropout_rate"] = "derived"
    doc = MO.build(fit, name=f"d13-nanopore-{family.lower()}", version="8.0.0", model_id=f"d13-nanopore-{family.lower()}@8.0.0",
                   description=f"V8 {family} channel model fitted to the D13 FIT split (Lopez et al. 2019, ONT R9.4 1D^2).",
                   note="PUBLIC-DATA-DERIVED fit; adequacy is decided by the V8 pre-registration, not by this file.",
                   datasets=[dataset_entry()], split={"name": "FIT", "manifest_sha256": config["prereg_sha256"]},
                   fitting=fitting, basis=labels, fit_report=report)
    doc["provenance"]["fitting"]["parameter_sha256"] = model2.parameter_sha256(doc["stages"])
    model, _ = cm.from_doc(doc)
    MODELS.mkdir(parents=True, exist_ok=True)
    mp = MODELS / f"d13-nanopore-{family.lower()}.json"
    mp.write_text(json.dumps(model.doc, indent=1, sort_keys=True) + "\n")
    summary = {"family": family, "model_file": str(mp.relative_to(HERE.parents[2])), "model_sha256": model.sha256,
               "parameter_sha256": doc["provenance"]["fitting"]["parameter_sha256"], "configuration": config,
               "configuration_sha256": cfg_sha, "references": int(M.shape[0]), "reads_tallied": int(lay.get(M, "n_reads").sum()),
               "calibration": {k: fit["calibration"][k] for k in ("final_residual_ratios", "trace") if k in fit["calibration"]},
               "evidence_class": "PUBLIC-DATA-DERIVED", "code": git, "environment": PL.environment(), "workers": workers,
               "seconds": round(time.time() - t0, 1)}
    out = RESULTS / f"fit-{family.lower()}.json"
    out.write_text(json.dumps(PC_jsonable(summary), indent=1, sort_keys=True) + "\n")
    print(json.dumps({"family": family, "model_sha256": model.sha256, "seconds": summary["seconds"]}))
    return out


def PC_jsonable(o):          # noqa: N802
    if isinstance(o, dict):
        return {str(k): PC_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [PC_jsonable(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return PC_jsonable(dataclasses.asdict(o))
    return o


def _fit_pairs(rows: set | None = None):
    """(reference, segments, qualities) of the FIT caches, optionally only table rows in ``rows`` (row numbering of tables)."""
    k = 0
    for run in A.RUNS:
        refs = PL.run_refs(run)
        for item in PL.split_pairs(run, A.FIT, refs):
            if rows is None or k in rows:
                yield item
            k += 1


def _in_sample(model, lay, M, rs, clusters, refs, seed, workers) -> dict:
    return V.validate_model(model, lay, dev_refs=refs[:: max(1, len(refs) // SIM_REFS)], dev_clusters=clusters, dev_M=M,
                            dev_rs=rs, mode="NW", seed=seed, workers=workers, prereg="7B", tally_opts=TALLY_OPTS)


def do_precheck(family: str, workers: int) -> Path:
    guard = A.V8Guard(f"experiments/v8/d13/fit.py precheck {family}")
    for run in A.RUNS:
        guard.authorize(run, A.FIT, f"V8.4 FIT-only pre-check of {family}")
    t0 = time.time()
    mp = MODELS / f"d13-nanopore-{family.lower()}.json"
    model, _ = cm.from_doc(json.loads(mp.read_text()))
    lay, M, rs, runs, ref_rows = PL.load_tables(A.FIT)
    refs = fit_refs(ref_rows, runs)
    clusters = [(r, s) for r, s, _q in _fit_pairs(set(range(0, M.shape[0], max(1, M.shape[0] // M8_CLUSTERS))))][:M8_CLUSTERS]
    full = _in_sample(model, lay, M, rs, clusters, refs, SEED_PRECHECK, workers)
    rt_refs = refs[:: max(1, len(refs) // 3000)][:3000]
    cal = dict(refs=refs[:: max(1, len(refs) // 2000)][:2000], coverage=CAL_COVERAGE, iterations=CAL_ITER, tally_opts=TALLY_OPTS)
    full["M10"] = AQ.m10_v8(model, lay, rt_refs, coverage=8, seed=SEED_PRECHECK + 5, replicates=3, calibration=cal, workers=workers,
                            tally_opts=TALLY_OPTS)
    halves = []
    for h, idx in enumerate(PC.halves(M.shape[0], SEED_PRECHECK)):
        rows = set(int(i) for i in idx)
        Mh, rsh = tally_matrix(_fit_pairs(rows), lay, mode="NW", workers=workers, **TALLY_OPTS)
        hrefs = [refs[i] for i in sorted(rows)]
        hcl = [c for i, c in enumerate(clusters) if i % 2 == h]
        halves.append(_in_sample(model, lay, Mh, rsh, hcl, hrefs, SEED_PRECHECK + 10 + h, workers))
    stab = PC.stability(full, halves)
    decision = PC.decide(full)
    decision["model_sha256"] = model.sha256
    if decision["decision"] == PC.DECISION_BLOCKED:
        decision["decision"] = "DEV_LOOK_BLOCKED — FIT PRE-CHECK INADEQUATE"
    out = {"experiment": f"V8 FIT-only pre-check of {family}", "evidence_class":
           "SIMULATED reads vs PUBLIC-DATA-DERIVED FIT segments (IN SAMPLE: not validation)",
           "data_firewall": {"split_read": A.FIT, "dev_read": False, "heldout_read": False},
           "decision": decision, "stability": stab, "metric_table": AQ.metric_table(full),
           "run_length_table": PC.run_length_table(lay, M, seed=SEED_PRECHECK, B=200),
           "metrics_full": full, "metrics_halves": halves,
           "model_sha256": model.sha256, "parameter_sha256": model.doc["provenance"]["fitting"].get("parameter_sha256"),
           "tables_sha256": json.loads((RESULTS / "tables.json").read_text())["splits"][A.FIT]["sha256"],
           "seed": SEED_PRECHECK, "code": PL.GIT_AT_START, "environment": PL.environment(), "workers": workers,
           "seconds": round(time.time() - t0, 1)}
    out["amendments"] = ["A1: matched read selection (max_edit_frac 0.30) on simulated reads"]
    p = RESULTS / f"precheck-{family.lower()}-a1.json"
    p.write_text(json.dumps(PC_jsonable(out), indent=1, sort_keys=True) + "\n")
    print(json.dumps({"family": family, "decision": decision, "classes": {k: v["class"] for k, v in stab.items()},
                      "seconds": out["seconds"]}, indent=1))
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fit", "precheck"])
    ap.add_argument("family", choices=FAMILIES)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    (do_fit if a.cmd == "fit" else do_precheck)(a.family, a.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
