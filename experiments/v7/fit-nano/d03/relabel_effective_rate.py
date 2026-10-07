"""Add the effective per-base substitution rate to the committed a7b D03 models whose substitution rate is confounded
(homopolymer min_run 2 + substitution context; ``estimate.hp_sub_fixed``), as the V7 review did for the a2 models (7c00d42).

The fit driver did not yet write ``fit_report.measured_statistics.substitution_rate_effective_per_base`` (fixed in
experiments/v7/fit/run.py with ``model_out.confounded_substitution``). No refit: the value is the FIT observed per-base
substitution rate already in the file, the stages are unchanged and one simulated batch is byte-identical before and after.
The canonical SHA-256 changes; before/after hashes go to RELABEL-2026-10-07.json. results/*.validation.json keep the SHA-256
of the model as validated (sha256_before). Reads only the model files (PUBLIC-DATA-DERIVED parameters; no data split).

    PYTHONPATH=src python experiments/v7/fit-nano/d03/relabel_effective_rate.py
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

import numpy as np

from vnxdna.simulation import model as cm
from vnxdna.simulation.engine import Simulator
from vnxdna.simulation.fit import estimate as est, model_out as MO, validate as V

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def sim_digest(model: cm.ChannelModel) -> str:
    rnd = random.Random(17)
    codes = np.array([[rnd.randrange(4) for _ in range(150)] for _ in range(256)], dtype=np.uint8)
    res = Simulator(model.stages).simulate_batch(codes, 17, 0)
    h = hashlib.sha256()
    for k in sorted(k for k, v in res.items() if isinstance(v, np.ndarray)):
        h.update(k.encode() + res[k].tobytes())
    return h.hexdigest()


def main() -> int:
    out = {"what": "fit_report.measured_statistics.substitution_rate_effective_per_base and the confounded-rate note added "
                   "(no refit; stages unchanged)",
           "why": "the fit driver did not write them for confounded designs (est.hp_sub_fixed); "
                  "test_fit_identifiability requires them on every committed confounded model",
           "simulation_check": "one batch (256 random 150-nt strands, seed 17) simulated before and after: identical SHA-256 "
                               "of every output array; the stages are equal",
           "validation_results_note": "results/*.validation.json keep the SHA-256 of the model as validated (sha256_before)",
           "models": {}}
    for p in sorted((HERE / "models").glob("*.json")):
        doc = json.loads(p.read_text())
        before, _ = cm.from_doc(doc)
        if not est.hp_sub_fixed(V.design_from_model(before)):
            continue
        ms = doc["fit_report"]["measured_statistics"]
        if "substitution_rate_effective_per_base" in ms:
            print(f"{p.name}: already relabelled", file=sys.stderr)
            continue
        ms["substitution_rate_effective_per_base"] = {"value": ms["observed_per_base_rates"]["substitution"],
                                                      "ci95": ms["observed_per_base_rates_ci95"]["substitution"]}
        doc["fit_report"]["notes"] = list(doc["fit_report"]["notes"]) + [MO.CONFOUNDED_SUBSTITUTION_NOTE]
        after, _ = cm.from_doc(doc)
        assert after.stages == before.stages
        d0, d1 = sim_digest(before), sim_digest(after)
        assert d0 == d1, p.name
        assert after.doc["parameters"]["sequencing.substitution.rate"]["basis"] == "estimated"
        p.write_text(json.dumps(after.doc, indent=1, sort_keys=True) + "\n")      # the format run.py writes
        out["models"][str(p.relative_to(ROOT))] = {"sha256_before": before.sha256, "sha256_after": after.sha256,
                                                    "effective": ms["substitution_rate_effective_per_base"]["value"],
                                                    "rate": after.stages["sequencing"]["substitution"]["rate"],
                                                    "sim_digest_identical": True}
    (HERE / "RELABEL-2026-10-07.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out["models"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
