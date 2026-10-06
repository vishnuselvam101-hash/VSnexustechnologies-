"""V8 pre-registration §2: does the F2 (paired-indel) rule trigger? FIT only.

Rule (fixed before any data): F2 is fitted only if the F1 FIT pre-check fails M3 AND the FIT segments show co-located
insertion+deletion pairs (an insertion with a deletion within 2 reference bases) more than 1.5x as often as reads simulated
from F1 for the same references (with the amendment A1 selection: simulated reads with edit distance > 0.30 L removed).

    PYTHONPATH=src python experiments/v8/d13/f2_rule.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "experiments/v7/fit-nano/d03-a7c"))
import access as A                                          # noqa: E402
import fit as FT                                            # noqa: E402
from vnxdna.simulation import model as cm                   # noqa: E402
from vnxdna.simulation.fit.simulate import simulate_clusters  # noqa: E402

SEED = 20261014
REFS = 3000
RATIO = 1.5


def main() -> int:
    import diagnose as DG                                   # V7 per-read I/D/S statistics (edlib NW path)
    guard = A.V8Guard("experiments/v8/d13/f2_rule.py")
    for run in A.RUNS:
        guard.authorize(run, A.FIT, "V8 pre-registration §2: F2 rule (pair excess, FIT only)")
    pre = json.loads((FT.RESULTS / "precheck-f1-a1.json").read_text())
    m3_fails = pre["metrics_full"]["M3"]["pass"] is False
    model, _ = cm.from_doc(json.loads((FT.MODELS / "d13-nanopore-f1.json").read_text()))
    lay, M, _rs, runs, ref_rows = FT.PL.load_tables(A.FIT)
    step = max(1, M.shape[0] // REFS)
    rows = set(range(0, M.shape[0], step))
    real = [(r, s) for r, s, _q in FT._fit_pairs(rows)][:REFS]
    refs = [r for r, _ in real]
    cov = max(2, round(sum(len(s) for _, s in real) / len(real)))
    sim = simulate_clusters(model, refs, cov, SEED)
    lim = 0.30 * 150
    sim_sel = [(r, [x for x in reads if DG.read_ids(r, x)[0] + DG.read_ids(r, x)[1] + DG.read_ids(r, x)[2] <= lim])
               for r, reads in zip(refs, sim)]
    fr, sr = DG.per_read_stats(real, None), DG.per_read_stats(sim_sel, None)
    ratio = fr["insertions_with_deletion_within_2"] / max(sr["insertions_with_deletion_within_2"], 1e-12)
    out = {"rule": "F2 iff F1 pre-check fails M3 and pair ratio (FIT / F1-simulated) > 1.5", "m3_fails": m3_fails,
           "pair_share_fit": fr["insertions_with_deletion_within_2"], "pair_share_sim": sr["insertions_with_deletion_within_2"],
           "pair_ratio": ratio, "triggers": bool(m3_fails and ratio > RATIO), "references": len(refs), "sim_coverage": cov,
           "fit_reads": {k: v for k, v in fr.items() if k != "by_edit_distance"},
           "sim_reads": {k: v for k, v in sr.items() if k != "by_edit_distance"},
           "evidence_class": "PUBLIC-DATA-DERIVED FIT segments vs SIMULATED (F1); in sample", "seed": SEED,
           "model_sha256": model.sha256, "code": FT.PL.GIT_AT_START}
    (FT.RESULTS / "f2-rule.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: out[k] for k in ("m3_fails", "pair_share_fit", "pair_share_sim", "pair_ratio", "triggers")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
