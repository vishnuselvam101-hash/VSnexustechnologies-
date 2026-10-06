"""V7 7.4 step 3: diagnosis of the remaining in-sample failures M3 and M10 of the a7c candidates (FIT data only).

* M3 (zero-drift reads): per read, the inserted (I), deleted (D) and substituted (S) bases of the optimal alignment (edlib NW
  path, the tally aligner) on FIT reads and on reads simulated from the candidate for the same references, after the same read
  length window. Reports P(I = D = 0), P(I = D > 0), P(drift = 0 | edit distance), corr(I, D), dispersion of I and D, and the
  share of insertions with a deletion within 2 reference bases (co-located indel pairs).
* M10 (round trip): K independent simulate + refit replicates of the candidate (SIMULATED; FIT reference sequences only),
  per failing parameter: bias of the refits against the target in units of their seed-to-seed SD, and the ratio of that SD to
  the bootstrap SE the M10 rule uses. Bias >> 0 = estimator bias; SD >> SE = the bootstrap SE understates the refit
  variability (metric statistics); neither = the failure was a draw from the tail.

Data firewall: the only data request is the split guard's FIT split; no DEV, held-out or validation artefact is read.

    PYTHONPATH=src python experiments/v7/fit-nano/d03-a7c/diagnose.py <job> [--workers 4] [--replicates 6]
Evidence class: PUBLIC-DATA-DERIVED (FIT reads) vs SIMULATED (reads from the candidate). In sample; not validation.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import edlib
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "fit"))
import fitlib as FL                                                           # noqa: E402
import run as R                                                               # noqa: E402
from fitlib import F, G, P, V, cm                                             # noqa: E402
from vnxdna.simulation.fit.simulate import simulate_clusters                  # noqa: E402

SPLIT = "FIT"
SEED = 20261007
READS_PER_GROUP = 4000          # FIT clusters per group used for the per-read statistics


def read_ids(ref: bytes, read: bytes) -> tuple[int, int, int, list, list]:
    """(I, D, S, insertion ref positions, deletion ref positions) of the optimal global alignment of read to ref."""
    a = edlib.align(read, ref, mode="NW", task="path")
    ins_pos, del_pos = [], []
    i = d = s = 0
    pos = 0
    num = ""
    for ch in a["cigar"] or "":
        if ch.isdigit():
            num += ch
            continue
        n = int(num)
        num = ""
        if ch == "I":           # extra bases in the read (query) = insertion
            i += n
            ins_pos.append(pos)
        elif ch == "D":         # reference bases missing from the read = deletion
            d += n
            del_pos.append(pos)
            pos += n
        else:
            if ch == "X":
                s += n
            pos += n
    return i, d, s, ins_pos, del_pos


def per_read_stats(pairs, window) -> dict:
    rows, paired, ins_total = [], 0, 0
    for ref, reads in pairs:
        for r in reads:
            drift = len(r) - len(ref)
            if window is not None and not (window[0] <= drift <= window[1]):
                continue
            i, d, s, ip, dp = read_ids(ref, r)
            rows.append((i, d, s))
            dset = np.asarray(dp)
            for p in ip:
                ins_total += 1
                if dset.size and np.min(np.abs(dset - p)) <= 2:
                    paired += 1
    a = np.asarray(rows, dtype=float)
    I, D, S = a[:, 0], a[:, 1], a[:, 2]
    ed = I + D + S
    out = {"reads": int(a.shape[0]), "p_no_indel": float(np.mean((I == 0) & (D == 0))),
           "p_balanced_indels": float(np.mean((I == D) & (I > 0))), "p_drift0": float(np.mean(I == D)),
           "mean_I": float(I.mean()), "mean_D": float(D.mean()), "mean_S": float(S.mean()),
           "var_over_mean_I": float(I.var() / I.mean()) if I.mean() else None,
           "var_over_mean_D": float(D.var() / D.mean()) if D.mean() else None,
           "corr_I_D": float(np.corrcoef(I, D)[0, 1]), "insertions_with_deletion_within_2": paired / max(ins_total, 1)}
    by_ed = {}
    for lo, hi in ((0, 2), (3, 5), (6, 9), (10, 14), (15, 1 << 30)):
        m = (ed >= lo) & (ed <= hi)
        if m.sum() >= 200:
            by_ed[f"{lo}-{hi if hi < 1 << 30 else 'inf'}"] = {"reads": int(m.sum()), "p_drift0": float(np.mean(I[m] == D[m])),
                                                              "share_indel_bases": float((I[m] + D[m]).sum() / max(ed[m].sum(), 1))}
    out["by_edit_distance"] = by_ed
    return out


def m3_diagnosis(job, model, colls, workers) -> dict:
    window = R.topts(job).get("length_window")
    real_pairs = [x for c in colls for x in c.clusters[:READS_PER_GROUP]]
    refs = [p[0] for p in real_pairs]
    cov = int(round(np.mean([len(p[1]) for p in real_pairs])))
    sim = simulate_clusters(model, refs, max(cov, 2), SEED)
    return {"fit_reads": per_read_stats(real_pairs, window), "simulated_reads": per_read_stats(zip(refs, sim), window),
            "length_window": window, "references": len(refs), "simulated_coverage": max(cov, 2)}


def _flat(v):
    return np.asarray(v, dtype=float).ravel()


def m10_diagnosis(job, model, refs, workers, K, failing) -> dict:
    lay = R.layout_for(job)
    rt_refs = refs[::max(1, len(refs) // 3000)][:3000]
    cal = dict(refs=refs[::max(1, len(refs) // 2000)][:2000], coverage=R.CAL_COVERAGE, iterations=R.CAL_ITER,
               tally_opts=R.topts(job), workers=workers)
    target = {k: v["value"] for k, v in model.doc["parameters"].items()}
    vals: dict = {k: [] for k in failing}
    ses: dict = {k: [] for k in failing}
    passes = []
    for k in range(K):
        seed = SEED + 1000 + k
        cl = simulate_clusters(model, rt_refs, 8, seed)
        M, _ = P.tally_matrix(zip(rt_refs, cl), lay, workers=workers, **R.topts(job))
        fit = F.fit_tallies(M, lay, seed=seed, bootstrap=200, with_coverage=False, design=V.design_from_model(model), calibration=cal)
        cmp_ = V.compare_params({p: target[p] for p in target if p in fit["values"]},
                                {p: v for p, v in fit["values"].items() if not p.startswith("_")}, fit["ci95"])
        passes.append(cmp_["pass"])
        for p in failing:
            vals[p].append(_flat(fit["values"][p]))
            c = fit["ci95"].get(p)
            se = (_flat(c["hi"]) - _flat(c["lo"])) / 3.92 if isinstance(c, dict) else np.full(_flat(target[p]).size, (c[1] - c[0]) / 3.92)
            ses[p].append(se)
    out = {"replicates": K, "m10_pass_per_replicate": passes, "parameters": {}}
    for p in failing:
        t = _flat(target[p])
        a, s = np.asarray(vals[p]), np.asarray(ses[p]).mean(axis=0)
        sd = a.std(axis=0, ddof=1)
        bias = a.mean(axis=0) - t
        z = np.where(sd > 0, bias / (sd / np.sqrt(K)), 0.0)
        out["parameters"][p] = {"n": int(t.size), "target_small_elements": int(np.sum(np.abs(t) < 0.01)),
                                "max_abs_bias_rel": float(np.max(np.abs(bias) / np.maximum(np.abs(t), 1e-12))),
                                "max_bias_z": float(np.max(np.abs(z))), "elements_bias_z_gt_3": int(np.sum(np.abs(z) > 3)),
                                "median_sd_over_bootstrap_se": float(np.median(np.where(s > 0, sd / s, np.nan))),
                                "max_sd_over_bootstrap_se": float(np.nanmax(np.where(s > 0, sd / s, np.nan))),
                                "worst_elements": [{"i": int(i), "target": float(t[i]), "refit_mean": float(a[:, i].mean()),
                                                    "seed_sd": float(sd[i]), "bootstrap_se": float(s[i]), "bias_z": float(z[i])}
                                                   for i in np.argsort(-np.abs(z))[:4]]}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--replicates", type=int, default=6)
    a = ap.parse_args(argv)
    R.ROUND, R.MODEL_VERSION = "a7c", R.ROUNDS["a7c"]["version"]
    job = R.JOBS[a.job]
    d = R.out_dir(job)
    mp = d / "models" / f"{job['name']}.json"
    model, _ = cm.from_doc(json.loads(mp.read_text()))
    pre = json.loads((d / "precheck" / f"{a.job}.precheck.json").read_text())       # FIT-only pre-check output
    failing = sorted(p for p, v in pre["metrics_full"]["M10"]["parameters"].items() if not v["pass"])
    t0 = time.time()
    guard = G.Guard(FL.DATA_DIR, script=f"experiments/v7/fit-nano/d03-a7c/diagnose.py {a.job}")
    lay, Ms, rss, colls, labels = R.tally_split(guard, job, SPLIT, a.workers, keep_clusters=READS_PER_GROUP)
    refs = [r for c in colls for r in c.refs]
    out = {"experiment": "V7 7.4 step 3 diagnosis (M3, M10) of the a7c candidate", "job": a.job, "split_read": SPLIT,
           "evidence_class": "PUBLIC-DATA-DERIVED FIT reads vs SIMULATED reads (in sample; not validation)",
           "model_sha256": model.sha256, "precheck_failing_m10_parameters": failing,
           "m3": m3_diagnosis(job, model, colls, a.workers),
           "m10": m10_diagnosis(job, model, refs, a.workers, a.replicates, failing),
           "seeds": {"m3_simulation": SEED, "m10_replicates": [SEED + 1000 + k for k in range(a.replicates)]},
           "code": FL.git_state(FL.REPO), "code_at_start": FL.GIT_AT_START, "split_manifest_sha256": FL.SPLIT_SHA}
    out["seconds"] = round(time.time() - t0, 1)
    p = d / "diagnosis" / f"{a.job}.diagnosis.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(R._jsonable(out), indent=1, sort_keys=True) + "\n")
    print(json.dumps({"job": a.job, "m3_fit": {k: v for k, v in out["m3"]["fit_reads"].items() if k != "by_edit_distance"},
                      "m3_sim": {k: v for k, v in out["m3"]["simulated_reads"].items() if k != "by_edit_distance"},
                      "m10": {p: {k: v for k, v in r.items() if k != "worst_elements"} for p, r in out["m10"]["parameters"].items()},
                      "m10_pass": out["m10"]["m10_pass_per_replicate"], "seconds": out["seconds"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
