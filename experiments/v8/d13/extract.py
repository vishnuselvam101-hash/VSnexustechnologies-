"""V8.2 public-data error extraction: aggregate channel statistics of the D13 FIT split (PUBLIC-DATA-DERIVED).

    PYTHONPATH=src python experiments/v8/d13/extract.py

Reads the FIT tables (hash-checked) and the FIT read-level aggregates of pipeline-<run>.json. Writes
results/extraction.json: per-base rates with bootstrap intervals (over references), per run; substitution spectrum;
position dependence (deciles of the 150-nt reference); quality dependence (error probability by Phred); insertion and
deletion run-length histograms (1..32+); indel rate by homopolymer run length; 3-mer context spread; coverage per reference;
references without segments; read-level structure (segments per read, concatemer length, mean quality); drift distribution.
Every statistic carries its sample count. Statistics the data cannot support are marked NOT IDENTIFIABLE FROM DATA.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import access as A                                                       # noqa: E402
import pipeline as PL                                                    # noqa: E402
from vnxdna.simulation.fit import fit as F, precheck as PC, validate as V  # noqa: E402
from vnxdna.simulation.fit.tally import DRIFT_OFF, EDIT_BINS              # noqa: E402

SEED = 20261013
B = 200
MIN_EVENTS_CONTEXT = 200


def _rates_ci(lay, M, seed) -> dict:
    tot = F.bootstrap_totals(M, F.bootstrap_weights(M.shape[0], B, seed))
    reps = [V.rates(lay, tot[b]) for b in range(B)]
    point = V.rates(lay, M.sum(axis=0).astype(float))
    out = {}
    for k in ("substitution", "insertion", "deletion"):
        arr = np.array([r[k] for r in reps])
        out[k] = {"value": float(point[k]), "se": float(arr.std(ddof=1)),
                  "ci95": [float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))]}
    out["bases"] = float(point["reads"]) * lay.L
    return out


def _deciles(v: np.ndarray, nb: float) -> list:
    return [float(c.sum() / nb * 10) for c in np.array_split(v.astype(float), 10)]


def main() -> int:
    guard = A.V8Guard("experiments/v8/d13/extract.py")
    for run in A.RUNS:
        guard.authorize(run, A.FIT, "V8.2 aggregate error extraction (FIT)")
    t0 = time.time()
    lay, M, rs, runs, _refs = PL.load_tables(A.FIT)
    T = M.sum(axis=0).astype(float)
    n_reads = float(lay.get(T, "n_reads").sum())
    nb = n_reads * lay.L
    out: dict = {"experiment": "V8.2 D13 error extraction", "split": A.FIT,
                 "evidence_class": "PUBLIC-DATA-DERIVED (aggregate statistics of D13 FIT segments)",
                 "tables_sha256": json.loads((PL.RESULTS / "tables.json").read_text())["splits"][A.FIT]["sha256"],
                 "segments_tallied": int(n_reads), "references": int(M.shape[0]), "excluded_segments": int(lay.get(T, "excluded").sum())}
    out["per_base_rates"] = _rates_ci(lay, M, SEED)
    out["per_run"] = {str(r): _rates_ci(lay, M[runs == r], SEED + i + 1) for i, r in enumerate(sorted(set(runs.tolist())))}
    mat = lay.get(T, "sub_matrix").reshape(4, 4)
    out["substitution_spectrum"] = {"counts_from_to_ACGT": mat.astype(int).tolist(),
                                    "row_normalised": (mat / np.maximum(mat.sum(axis=1, keepdims=True), 1)).tolist()}
    out["position_deciles_events_per_base"] = {k: _deciles(lay.get(T, f), nb) for k, f in
                                               (("substitution", "pos_sub"), ("insertion", "pos_ins"), ("deletion_start", "pos_delstart"))}
    qc, qe = lay.get(T, "q_correct").astype(float), lay.get(T, "q_error").astype(float)
    q = []
    for lo in range(0, 60, 5):
        c, e = qc[lo:lo + 5].sum(), qe[lo:lo + 5].sum()
        if c + e >= 1000:
            q.append({"phred": f"{lo}-{lo + 4}", "bases": int(c + e), "error_probability": float(e / (c + e)),
                      "phred_implied": float(10 ** (-(lo + 2) / 10))})
    out["quality_dependence"] = q
    for kind, f in (("insertion", "ins_runs"), ("deletion", "del_runs")):
        h = lay.get(T, f).astype(float)
        out[f"{kind}_run_length"] = {"histogram_1_to_32plus": h.astype(int).tolist(), "runs": int(h.sum()),
                                     "share_ge2": float(h[1:].sum() / max(h.sum(), 1)),
                                     "mean": float((h * np.arange(1, h.size + 1)).sum() / max(h.sum(), 1)),
                                     "runs_in_last_bin_censored": int(h[-1])}
    out["homopolymer_run_length"] = PC.run_length_table(lay, M, seed=SEED, B=B)
    ctx = {}
    sites = lay.ctx(T, "ctx_sites")[0].sum(axis=1)
    for kind, f in (("substitution", "ctx_sub"), ("insertion", "ctx_ins"), ("deletion", "ctx_del")):
        ev = lay.ctx(T, f)[0].sum(axis=1)
        ok = ev >= MIN_EVENTS_CONTEXT
        r = np.where(sites > 0, ev / np.maximum(sites, 1), 0.0)
        mean = ev.sum() / max(sites.sum(), 1)
        rel = r[ok] / mean if mean else r[ok]
        ctx[kind] = {"three_mers_with_ge_200_events": int(ok.sum()), "rate_over_mean_min": float(rel.min()) if ok.any() else None,
                     "rate_over_mean_median": float(np.median(rel)) if ok.any() else None,
                     "rate_over_mean_max": float(rel.max()) if ok.any() else None}
    out["context_3mer"] = ctx
    cov = lay.get(M, "n_reads")[:, 0] + lay.get(M, "excluded")[:, 0]
    out["coverage_per_reference_with_segment"] = {"references": int(cov.size), "mean": float(cov.mean()),
                                                  "median": float(np.median(cov)), "p10": float(np.percentile(cov, 10)),
                                                  "p90": float(np.percentile(cov, 90)), "cv": float(cov.std() / cov.mean())}
    drift = rs[EDIT_BINS:].astype(float)
    out["drift"] = {"segments": int(drift.sum()), "share_zero": float(drift[DRIFT_OFF] / drift.sum()),
                    "share_abs_le_3": float(drift[DRIFT_OFF - 3:DRIFT_OFF + 4].sum() / drift.sum())}
    missing, readlevel = {}, {}
    for run in A.RUNS:
        rec = json.loads((PL.RESULTS / f"pipeline-{run}.json").read_text())
        o = rec["outputs"][A.FIT]
        missing[run] = {"references_in_split": o["references_in_split"], "references_with_segment": o["references_with_segment"],
                        "share_without_segment": 1 - o["references_with_segment"] / o["references_in_split"]}
        rl = rec["read_level"][A.FIT]
        lens = np.array(rl["length_hist_100nt_bins"], dtype=float)
        segs = np.array(rl["segments_per_read_hist"], dtype=float)
        readlevel[run] = {"reads": rl["reads"], "concatemer_length_median_100nt_bin": int(np.searchsorted(np.cumsum(lens) / lens.sum(), 0.5)),
                          "segments_per_read_mean": float((segs * np.arange(segs.size)).sum() / segs.sum()),
                          "reads_without_segment_share": float(segs[0] / segs.sum())}
    out["references_without_segment_upper_bound_on_dropout"] = missing
    out["read_level"] = readlevel
    out["identifiability"] = {
        "substitution rate / spectrum / position / quality": "IDENTIFIABLE",
        "insertion and deletion rate / run length / homopolymer / position / context": "IDENTIFIABLE",
        "read-level (whole read) length": "IDENTIFIABLE as concatemer length; NOT a single-oligo read length",
        "dropout (molecule loss)": "NOT IDENTIFIABLE FROM DATA (upper bound only: assembly, PCR and segmentation losses mixed)",
        "synthesis vs sequencing errors": "NOT IDENTIFIABLE FROM DATA (no sequencing-only control)",
        "basecaller / chemistry dependence": "NOT IDENTIFIABLE FROM DATA (one condition, basecaller not stated)"}
    out["seed"], out["bootstrap"] = SEED, B
    out["code"], out["seconds"] = PL.GIT_AT_START, round(time.time() - t0, 1)
    p = PL.RESULTS / "extraction.json"
    p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: out[k] for k in ("per_base_rates", "drift")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
