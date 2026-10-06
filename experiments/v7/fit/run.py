#!/usr/bin/env python3
"""V7 fitting experiments. SIMULATED reads / PUBLIC-DATA-DERIVED fits (docs/V7_PROTOCOL.md section 5).

    python experiments/v7/fit/run.py fit <job> [--workers 8]        # fit model F on the FIT split
    python experiments/v7/fit/run.py validate <job> [--workers 8]   # validate F against DEV (M1-M10), mark INADEQUATE
    python experiments/v7/fit/run.py list

Reads are obtained only through experiments/v7/split/guard.py (FIT and DEV; held-out data is unreachable without a PREREG SHA).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fitlib as FL  # noqa: E402
from fitlib import F, G, MO, P, V, cm  # noqa: E402
from vnxdna.simulation.fit.tally import Layout  # noqa: E402

D03_FILES = (0, 2)          # non-held-out D03 files (file-1 is the held-out run)
#: protocol 5.5 (A2.2): read-length selection (read length - L), the closed range observed over all FIT reads of the dataset;
#: do_fit checks that the FIT reads span exactly this range and refuses to continue otherwise
LENGTH_WINDOW = {"cnr": (-4, 5), "d03-nanopore": (-15, 15)}
PUBLISHED = {
    "d02": {"source": "Gimpel et al. 2023 (Nat Commun 14:6026): 40 datasets, deletions 6.7 +- 6.9, substitutions 7.9 +- 2.0, insertions < 0.3 +- 0.2 "
                      "per 1000 nt; iSeq PhiX substitutions 1.8 +- 0.8e-3, indels < 1e-4 (quoted from docs/DNA_STORAGE_DATASET_REGISTRY.md)",
            "phix_substitution": 0.0018, "phix_substitution_sd": 0.0008, "phix_indel_upper": 1e-4},
    "cnr-ont": {"source": "P4-EXP-03 (this repository, all 10,000 clusters) and Srinivasavaradhan et al. 2021 (clusters 1-2000)",
                "substitution": 0.0216, "insertion": 0.0166, "deletion": 0.0195,
                "paper": {"substitution": 0.022, "insertion": 0.017, "deletion": 0.020}},
    "d03-hac": {"source": "Welter et al. Table II, file 3, accurate basecaller, pass (arXiv:2406.12955)", "insertion": 0.009,
                "deletion": 0.014, "substitution": 0.020},
    "d03-fast": {"source": "Welter et al. Table II, file 3, fast basecaller, pass", "insertion": 0.014, "deletion": 0.038,
                 "substitution": 0.043},
}


def d03_groups(acc: bool, direction: str) -> list[str]:
    return [f"file-{f}_acc-{'true' if acc else 'false'}_passQ-true/{direction}" for f in D03_FILES]


JOBS = {
    "cnr-p4tie": {"dataset": "cnr", "L": 110, "mode": "NW", "name": "cnr-ont-p4tie-fit", "pub": "cnr-ont", "aligner": "dp-diag",
                  "title": "CNR (D04), ONT, with the P4-EXP-03 alignment tie-break (diagonal, deletion, insertion)"},
    "cnr": {"dataset": "cnr", "L": 110, "mode": "NW", "name": "cnr-ont-fit", "pub": "cnr-ont", "title": "CNR (D04), ONT, basecaller not stated"},
    "d03-hac-fwd": {"dataset": "d03-nanopore", "L": 150, "mode": "NW", "groups": d03_groups(True, "forward"), "back": False,
                    "name": "ont-guppy-hac-pass-fwd-fit", "pub": "d03-hac", "title": "D03 guppy accurate (HAC), pass, forward"},
    "d03-hac-bwd": {"dataset": "d03-nanopore", "L": 150, "mode": "NW", "groups": d03_groups(True, "backward"), "back": True,
                    "name": "ont-guppy-hac-pass-bwd-fit", "pub": "d03-hac", "title": "D03 guppy accurate (HAC), pass, backward (reverse-complemented to the reference frame)"},
    "d03-fast-fwd": {"dataset": "d03-nanopore", "L": 150, "mode": "NW", "groups": d03_groups(False, "forward"), "back": False,
                     "name": "ont-guppy-fast-pass-fwd-fit", "pub": "d03-fast", "title": "D03 guppy fast, pass, forward"},
    "d03-fast-bwd": {"dataset": "d03-nanopore", "L": 150, "mode": "NW", "groups": d03_groups(False, "backward"), "back": True,
                     "name": "ont-guppy-fast-pass-bwd-fit", "pub": "d03-fast", "title": "D03 guppy fast, pass, backward (reverse-complemented to the reference frame)"},
}
JOBS["d03-hac-merged"] = {"dataset": "d03-nanopore", "L": 150, "mode": "NW", "groups": d03_groups(True, "forward") + d03_groups(True, "backward"),
                          "name": "ont-guppy-hac-pass-fit", "pub": "d03-hac", "merged": True,
                          "title": "D03 guppy accurate (HAC), pass, forward and backward merged in the reference frame"}
JOBS["d03-fast-merged"] = {"dataset": "d03-nanopore", "L": 150, "mode": "NW", "groups": d03_groups(False, "forward") + d03_groups(False, "backward"),
                           "name": "ont-guppy-fast-pass-fit", "pub": "d03-fast", "merged": True,
                           "title": "D03 guppy fast, pass, forward and backward merged in the reference frame"}
JOBS["d02-twist"] = {"dataset": "dt4dds-twist", "L": 108, "mode": "HW", "runs": ["ERR12033806", "ERR12033810"], "control": "ERR12033850",
                     "name": "illumina-iseq-twist-fit", "pub": "d02", "title": "DT4DDS Twist_GCfix Aging_0a/0b (R1) with PhiX stage split"}
OUT = Path(os.environ.get("VNX_FIT_OUT", Path(__file__).resolve().parents[1]))   # override for dry runs only
CAL_REFS, CAL_COVERAGE, CAL_ITER = 4000, 10, 5
#: round of model F: results of round 1 (before amendment 2, commit 1c0b889) stay in fit-<dataset>/{models,results};
#: this code writes round 2 (protocol 5.5) to fit-<dataset>/a2/{models,results}
ROUND = "a2"
MODEL_VERSION = "2.0.0"
#: round a7b (V7 step 7.4): fitted-nanopore pre-registration 7B + amendment A1 (experiments/v7/fit-nano/PREREGISTRATION*.md).
#: Empirical insertion/deletion run lengths, layout with homopolymer masks 2..6 and the per-read edit-rate field, gating
#: GATING_7B; outputs under experiments/v7/fit-nano/<dataset>/. Selected with --round a7b.
ROUNDS = {"a2": {"version": "2.0.0"}, "a7b": {"version": "3.0.0"}}
MINRUNS_7B = (2, 3, 4, 5, 6)


def is_7b() -> bool:
    return ROUND == "a7b"


def layout_for(job: dict) -> Layout:
    return Layout(job["L"], minruns=MINRUNS_7B, read_rate=True) if is_7b() else Layout(job["L"])
SEED = 20261005


def d02_out_dir() -> Path:
    return OUT / "fit-d02" / ROUND


def out_dir(job: dict) -> Path:
    if is_7b():
        return OUT / "fit-nano" / ("d03" if job["dataset"] == "d03-nanopore" else job["dataset"])
    return OUT / ("fit-cnr" if job["dataset"] == "cnr" else "fit-d03" if job["dataset"] == "d03-nanopore" else "fit-d02") / ROUND


def sources(guard: G.Guard, job: dict, split: str):
    """[(label, iterator of (id, ref, reads))] for the job's runs/groups on a split."""
    purpose = f"fit model F ({job['name']}) / validation on DEV"
    if job["dataset"] == "cnr":
        return [("cnr", guard.iter_cnr(split, purpose))]
    return [(g, guard.iter_d03(g, split, purpose)) for g in job["groups"]]


def job_files(job: dict) -> list[dict]:
    """Provenance dataset entries (every file used, with SHA-256 from the dataset manifest)."""
    if job["dataset"] == "cnr":
        used = [(f["path"], f["sha256"]) for f in FL.MANIFEST["datasets"]["cnr"]["files"] if f["path"].endswith(".txt")]
        return [FL.dataset_entry("cnr", used)]
    d = FL.MANIFEST["datasets"]["d03-nanopore"]
    files = [("d03/oligos.fasta", FL.manifest_sha("d03-nanopore", "d03/oligos.fasta")),
             ("d03/clustered_read_segments.tar.gz", FL.manifest_sha("d03-nanopore", "d03/clustered_read_segments.tar.gz"))]
    for g in job["groups"]:
        gd = d["groups"][g]
        files.append((f"{g}/TX", gd["TX"]["sha256"]))
        files.append((f"{g}/RX", gd["RX"]["sha256"]))
    return [FL.dataset_entry("d03-nanopore", files)]


def topts(job: dict) -> dict:
    return {"aligner": job.get("aligner", "edlib"), "shift": "left", "length_window": LENGTH_WINDOW.get(job["dataset"])}


def observed_window(rs: np.ndarray) -> tuple[int, int]:
    """(min, max) of read length - L over the reads of a read-level histogram (NW alignment: the whole read is aligned)."""
    from vnxdna.simulation.fit.tally import DRIFT_OFF, EDIT_BINS
    nz = np.flatnonzero(rs[EDIT_BINS:])
    return int(nz.min()) - DRIFT_OFF, int(nz.max()) - DRIFT_OFF


def check_window(job: dict, rs: np.ndarray, M: np.ndarray, lay) -> dict:
    """A2.2: the declared window must equal the range of the FIT reads, so it removes no real read."""
    declared = LENGTH_WINDOW.get(job["dataset"])
    if declared is None:
        return {"declared": None}
    seen = observed_window(rs)
    removed = int(lay.get(M, "window_out").sum())
    if tuple(declared) != seen or removed:
        raise SystemExit(f"read-length window {declared} differs from the FIT range {seen} or removes {removed} real reads")
    return {"declared": list(declared), "observed_fit_range": list(seen), "real_reads_removed": removed}


def tally_split(guard: G.Guard, job: dict, split: str, workers: int, keep_clusters: int = 0):
    lay = layout_for(job)
    Ms, rss, colls, labels = [], [], [], []
    for label, src in sources(guard, job, split):
        coll = FL.Collector(src, orient_backward=label.endswith("/backward"), keep_clusters=keep_clusters)
        M, rs = P.tally_matrix(coll, lay, mode=job["mode"], workers=workers, **topts(job))
        Ms.append(M)
        rss.append(rs)
        colls.append(coll)
        labels.append(label)
    return lay, Ms, rss, colls, labels


def mem_mb() -> float:
    return (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss + resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) / 1024.0


def group_rates(lay: Layout, M: np.ndarray, coll) -> dict:
    T = M.sum(axis=0).astype(float)
    r = V.rates(lay, T)
    counts = FL.counts_of(lay, M)
    return {"references": int(M.shape[0]), "reads_tallied": int(r["reads"]), "excluded_reads": int(lay.get(M, "excluded").sum()),
            "empty_references": int((counts == 0).sum()), "reads_per_reference": {"mean": float(counts.mean()), "median": float(np.median(counts)),
                                                                                    "p10": float(np.percentile(counts, 10)), "p90": float(np.percentile(counts, 90))},
            "per_base_rates": {k: float(r[k]) for k in ("substitution", "insertion", "deletion")}}


def misfit_notes(fit: dict) -> list[str]:
    st = fit["values"]["_stats"]
    notes = []
    pe, pn = st["p_event_given_event"], st["p_event_given_no_event"]
    if pn > 0 and not (0.87 <= pe / pn <= 1.15):
        notes.append(f"error correlation: P(event at i+1 | event at i) = {pe:.4f} vs {pn:.4f} after a non-event (ratio {pe / pn:.2f}); "
                     "the model draws sites independently, so this is not represented")
    h = np.asarray(st["deletion_runs_hist"], dtype=float)
    if h.sum() > 0:
        m = float(h @ np.arange(1, h.size + 1) / h.sum())
        q = 1.0 - 1.0 / max(m, 1.0000001)
        exp_tail = q ** 8
        obs_tail = float(h[8:].sum() / h.sum())
        if obs_tail > 2 * exp_tail + 1e-4:
            notes.append(f"deletion runs have a heavier tail than a geometric run length (P(run > 8) {obs_tail:.4f} observed vs "
                         f"{exp_tail:.4f} geometric with the same mean); read-level bursts are not fitted")
    if st["ins_run_share_gt1"] > 0.05 and not fit["design"].ins_geometric:
        notes.append(f"{100 * st['ins_run_share_gt1']:.1f} % of insertion runs are longer than 1 but are modelled as single")
    if st["end_insertion_events"] > 0:
        notes.append(f"{int(st['end_insertion_events'])} insertion events after the last reference base are not representable "
                     "(the model inserts before a reference base) and are not in the fitted insertion rate")
    notes.append("insertion and deletion contexts (k = 3) are not fitted: after leftmost normalisation an indel in a repeat is "
                 "attributed to its first site, so a site-level indel context is not identifiable (see vnxdna.simulation.fit.estimate)")
    return notes


def do_fit(job_id: str, workers: int, bootstrap: int, seed: int = SEED) -> Path:
    job = JOBS[job_id]
    guard = G.Guard(FL.DATA_DIR, script=f"experiments/v7/fit/run.py fit {job_id}")
    t0 = time.time()
    lay, Ms, rss, colls, labels = tally_split(guard, job, "FIT", workers)
    t_tally = time.time() - t0
    M = np.concatenate(Ms)
    window = check_window(job, sum(rss), M, lay)
    refs = [r for c in colls for r in c.refs]
    step = max(1, len(refs) // CAL_REFS)
    cal_refs = refs[::step][:CAL_REFS]
    t1 = time.time()
    design = None
    if is_7b():     # FIT-chosen design (min_run, profiles, context, heterogeneity) with empirical run lengths (7A §3)
        import dataclasses
        from vnxdna.simulation.fit import estimate as est
        design = dataclasses.replace(est.choose_design(lay, M.sum(axis=0).astype(np.float64)), ins_geometric=False,
                                     del_geometric=False, ins_empirical=True, del_empirical=True)
    fit = F.fit_tallies(M, lay, seed=seed, bootstrap=bootstrap, design=design, calibration=dict(refs=cal_refs, coverage=CAL_COVERAGE, iterations=CAL_ITER, workers=workers, tally_opts=topts(job)))
    t_fit = time.time() - t1
    v, ci = fit["values"], fit["ci95"]
    per_group = {lab: group_rates(lay, m, c) for lab, m, c in zip(labels, Ms, colls)}
    cov_stats = group_rates(lay, M, None)
    obs = v["_observed"]
    pub = PUBLISHED[job["pub"]]
    comparison = {}
    for k_obs, k_pub in (("substitution", "substitution"), ("insertion_bases", "insertion"), ("deletion_bases", "deletion")):
        interval = ci["_observed"][k_obs]
        comparison[k_pub] = {"fitted_observed": obs[k_obs], "ci95": interval, "published": pub[k_pub],
                             "published_inside_ci": bool(interval[0] <= pub[k_pub] <= interval[1])}
    report = {"adequacy": "UNVALIDATED", "failed_metrics": [],
              "measured_statistics": {"observed_per_base_rates": obs, "observed_per_base_rates_ci95": ci["_observed"],
                                      "error_correlation": {"p_event_given_event": v["_stats"]["p_event_given_event"],
                                                            "p_event_given_no_event": v["_stats"]["p_event_given_no_event"],
                                                            "ci95_p_event_given_event": ci["_stats"]["p_event_given_event"]},
                                      "deletion_run_histogram_1_to_16plus": v["_stats"]["deletion_runs_hist"],
                                      "insertion_run_histogram_1_to_16plus": v["_stats"]["insertion_runs_hist"],
                                      "per_group": per_group, "pooled": cov_stats, "read_length_window": window,
                                      "edit_distance_mean_var": v["_stats"]["edit_distance_mean_var"],
                                      "coverage_aic": fit["coverage"]["aic"], "coverage_choice": list(fit["coverage"]["choice"]),
                                      "design": {"min_run": fit["design"].min_run, "profile_bins": fit["design"].bins,
                                                 "context_kinds": list(fit["design"].context),
                                                 "insertion_runs_geometric": fit["design"].ins_geometric,
                                                 "read_heterogeneity": fit["design"].heterogeneity,
                                                 "deletion_runs_geometric": fit["design"].del_geometric,
                                                 "insertion_runs_empirical": fit["design"].ins_empirical,
                                                 "deletion_runs_empirical": fit["design"].del_empirical}},
              "misfit": {"notes": misfit_notes(fit)},
              "notes": ["PUBLIC-DATA-DERIVED parameters; reads simulated from this model are SIMULATED",
                        "dropout is an upper bound: empty clusters mix molecular loss with clustering/segmentation loss",
                        "nanopore quality is assumed (no qualities in the dataset): sequencing.quality is the schema default",
                        "applying the model to 313-nt VNX strands extrapolates the relative position profile",
                        "protocol 5.5 (amendment 2): read_heterogeneity (per-read gamma rate multiplier) and the read-length window "
                        f"{window.get('declared')} applied to simulated reads in calibration and validation; the model describes "
                        "length-selected reads"]}
    report["misfit"] = {"notes": report["misfit"]["notes"]}
    doc = MO.build(
        fit, name=job["name"], version=MODEL_VERSION, model_id=f"{job['name']}-F-{ROUND}",
        description=f"Model F fitted to the FIT split of {job['title']}.",
        note=("PUBLIC-DATA-DERIVED fit of other groups' sequencing data (FIT split only, protocol 4.1/5.2). Reads simulated from it are "
              "SIMULATED. Not a prediction for VNX strands or any wet-lab round."),
        datasets=job_files(job), split={"name": "FIT", "manifest_sha256": FL.SPLIT_SHA}, fitting=FL.fitting_block(seed),
        fit_report=report, references=[FL.MANIFEST["datasets"][job["dataset"]]["publication"]])
    d = out_dir(job)
    (d / "models").mkdir(parents=True, exist_ok=True)
    (d / "results").mkdir(parents=True, exist_ok=True)
    mp = d / "models" / f"{job['name']}.json"
    mp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    summary = {"job": job_id, "title": job["title"], "split": "FIT", "model_file": str(mp.relative_to(OUT)),
               "model_sha256": cm.from_doc(doc)[0].sha256, "references": fit["references"], "assigned_reads": fit["assigned_reads"],
               "comparison_with_published": comparison, "published_source": pub["source"],
               "calibration": {"refs": len(cal_refs), "coverage": CAL_COVERAGE, "iterations": CAL_ITER,
                               "trace": fit["calibration"]["trace"], "final_residual_ratios": fit["calibration"]["final_residual_ratios"], "factors_scalar": {k: x for k, x in fit["calibration"]["factors"].items() if not isinstance(x, list)}},
               "seconds": {"tally": round(t_tally, 1), "fit_bootstrap_calibration": round(t_fit, 1)}, "workers": workers,
               "peak_rss_mb_self_plus_children": round(mem_mb(), 1), "bootstrap": bootstrap, "seed": seed,
               "environment": environment_info(), "evidence_class": "PUBLIC-DATA-DERIVED"}
    (d / "results" / f"{job_id}.fit.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"job": job_id, "comparison": comparison, "seconds": summary["seconds"], "rss_mb": summary["peak_rss_mb_self_plus_children"]}, indent=1))
    return mp


def validation_notes(rep: dict) -> list[str]:
    """Plain statements about failed metrics (what was measured), with the usual structural reasons stated as consistent-with."""
    notes = []
    m2, m3, m5, m8 = rep.get("M2", {}), rep.get("M3", {}), rep.get("M5", {}), rep.get("M8", {})
    if m2.get("pass") is False:
        notes.append(f"M2 fails: per-read edit-distance KS D = {m2['ks_D']:.3f} (limit 0.03), TV = {m2['tv']:.3f} (limit 0.05); "
                     f"real p90/p99 = {m2['real']['p90']}/{m2['real']['p99']}, simulated {m2['sim']['p90']}/{m2['sim']['p99']}. Consistent with read-to-read "
                     "heterogeneity of the error rate, which independent per-site draws do not produce")
    if m3.get("pass") is False:
        worst = max(m3["bands"].items(), key=lambda kv: abs(kv[1]["diff_pp"]))
        notes.append(f"M3 fails: P({worst[0].replace('abs_drift_le_', '|drift| <= ')}) real {worst[1]['real']:.3f} vs simulated {worst[1]['sim']:.3f} "
                     f"({worst[1]['diff_pp']:+.1f} pp, limit 1 pp). Consistent with insertions and deletions that are not independent along a read")
    if m5.get("pass") is False:
        notes.append(f"M5 fails: deletion run-length TV = {m5['tv']:.3f} (limit 0.05): the run-length tail is heavier or lighter than geometric")
    if m8.get("pass") is False:
        bad = [k for k, v in m8["curve"].items() if v.get("pass") is False]
        notes.append(f"M8 fails at k = {bad}")
    if is_7b():
        for m in V.GATING_7B:
            if m not in ("M2", "M3", "M5", "M8") and rep.get(m, {}).get("pass") is False:
                notes.append(f"{m} fails (gating under pre-registration 7B; see metrics)")
        for m in ("M4", "M6", "M9"):
            if rep.get(m, {}).get("pass") is False:
                notes.append(f"{m} fails (not gating under 7B; see metrics)")
        return notes
    for m in ("M1", "M4", "M6", "M9"):
        if rep.get(m, {}).get("pass") is False:
            notes.append(f"{m} fails (not gating; see metrics)")
    return notes


def do_validate(job_id: str, workers: int, seed: int = SEED + 1) -> Path:
    job = JOBS[job_id]
    d = out_dir(job)
    mp = d / "models" / f"{job['name']}.json"
    doc = json.loads(mp.read_text())
    model, _ = cm.from_doc(doc)
    guard = G.Guard(FL.DATA_DIR, script=f"experiments/v7/fit/run.py validate {job_id}")
    t0 = time.time()
    lay, Ms, rss, colls, labels = tally_split(guard, job, "DEV", workers, keep_clusters=1500)
    M = np.concatenate(Ms)
    rs = sum(rss)
    refs = [r for c in colls for r in c.refs]
    clusters = [x for c in colls for x in c.clusters][:6000]
    rep = V.validate_model(model, lay, dev_refs=refs[::max(1, len(refs) // 4000)], dev_clusters=clusters, dev_M=M, dev_rs=rs,
                           mode=job["mode"], seed=seed, workers=workers, tally_opts=topts(job),
                           prereg="7B" if is_7b() else None)
    cal = dict(refs=refs[::max(1, len(refs) // 2000)][:2000], coverage=CAL_COVERAGE, iterations=CAL_ITER, tally_opts=topts(job))
    rt_refs = refs[::max(1, len(refs) // 3000)][:3000]
    rep["M9"] = {"pass": None, "note": "no qualities in this dataset (nanopore quality is assumed)"}
    rep["M10"] = V.round_trip(model, lay, rt_refs, coverage=8, seed=seed + 5, bootstrap=200, workers=workers, calibration=cal, tally_opts=topts(job))
    adequacy, failed = V.adequacy(rep, V.GATING_7B if is_7b() else V.GATING)
    fr = dict(doc["fit_report"])
    fr["adequacy"], fr["failed_metrics"] = adequacy, failed
    fr["validation"] = {"split": "DEV", "dev_references": int(M.shape[0]), "dev_reads_tallied": int(lay.get(M, "n_reads").sum()),
                        "simulated_reads_note": "SIMULATED: 5 seeds x 6 reads per DEV reference (<= 4,000 references)",
                        "summary": {m: rep[m].get("pass") for m in sorted(rep)}}
    fr["metrics"] = _jsonable(rep)
    fr["misfit"] = {"notes": list(fr["misfit"]["notes"]) + validation_notes(rep)}
    if adequacy == "INADEQUATE":
        rule = "pre-registration 7B + A1" if is_7b() else "protocol 5.4"
        fr["notes"] = list(fr["notes"]) + [f"INADEQUATE ({rule}): fails {failed} on DEV; not used to choose decoder parameters; may be run as a labelled stress condition"]
    doc["fit_report"] = fr
    new = cm.from_doc(json.loads(json.dumps(doc)))[0]
    mp.write_text(json.dumps(new.doc, indent=1, sort_keys=True) + "\n")
    (d / "results" / f"{job_id}.validation.json").write_text(json.dumps(
        {"job": job_id, "model_sha256": new.sha256, "adequacy": adequacy, "failed_metrics": failed, "metrics": _jsonable(rep),
         "seconds": round(time.time() - t0, 1), "peak_rss_mb_self_plus_children": round(mem_mb(), 1), "workers": workers,
         "environment": environment_info(), "evidence_class": "SIMULATED reads vs PUBLIC-DATA-DERIVED DEV reads"}, indent=1, sort_keys=True) + "\n")
    print(job_id, adequacy, failed, {m: rep[m].get("pass") for m in sorted(rep)})
    return mp


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        o = o.item()
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def do_smoke(workers: int, bootstrap: int, seed: int = SEED) -> Path:
    """Day-0 smoke test: CNR FIT split under three alignment conventions; the P4-EXP-03 convention is `dp-diag`."""
    job = JOBS["cnr"]
    guard = G.Guard(FL.DATA_DIR, script="experiments/v7/fit/run.py smoke")
    pub = PUBLISHED["cnr-ont"]
    rows = {}
    for label, opts in (("edlib+leftmost (main)", {"aligner": "edlib", "shift": "left"}),
                        ("edlib+rightmost", {"aligner": "edlib", "shift": "right"}),
                        ("dp-diag (P4-EXP-03 tie-break)", {"aligner": "dp-diag", "shift": "left"})):
        t0 = time.time()
        coll = FL.Collector(guard.iter_cnr("FIT", "smoke test: CNR FIT split, alignment conventions"))
        lay = Layout(job["L"])
        M, rs = P.tally_matrix(coll, lay, mode="NW", workers=workers, **opts)
        refs = coll.refs
        cal_refs = refs[::max(1, len(refs) // CAL_REFS)][:CAL_REFS]
        fit = F.fit_tallies(M, lay, seed=seed, bootstrap=bootstrap, with_coverage=False,
                            calibration=dict(refs=cal_refs, coverage=CAL_COVERAGE, iterations=CAL_ITER, workers=workers, tally_opts=opts))
        obs, ci = fit["values"]["_observed"], fit["ci95"]["_observed"]
        v = fit["values"]
        rows[label] = {
            "observed_per_base_rates": {k: obs[k] for k in ("substitution", "insertion_bases", "deletion_bases")},
            "ci95": {k: ci[k] for k in ("substitution", "insertion_bases", "deletion_bases")},
            "total_error_rate": obs["substitution"] + obs["insertion_bases"] + obs["deletion_bases"],
            "p4_exp_03_inside_ci": {k: bool(ci[kk][0] <= pub[k] <= ci[kk][1]) for k, kk in
                                     (("substitution", "substitution"), ("insertion", "insertion_bases"), ("deletion", "deletion_bases"))},
            "calibrated_model_parameters": {k.split("sequencing.")[1]: v[k] for k in
                                             ("sequencing.substitution.rate", "sequencing.insertion.rate", "sequencing.deletion.rate",
                                              "sequencing.homopolymer.indel_multiplier", "sequencing.homopolymer.substitution_multiplier")},
            "design": {"min_run": fit["design"].min_run, "context": list(fit["design"].context)},
            "seconds": round(time.time() - t0, 1)}
    base = rows["edlib+leftmost (main)"]["calibrated_model_parameters"]
    for label, r in rows.items():
        r["calibrated_relative_change_vs_main"] = {k: (r["calibrated_model_parameters"][k] / base[k] - 1.0) for k in base}
    doc = {"experiment": "v7 CNR smoke test (fitter end to end)", "evidence_class": "PUBLIC-DATA-DERIVED", "split": "FIT",
           "references": len(refs), "p4_exp_03": {k: pub[k] for k in ("substitution", "insertion", "deletion")},
           "p4_exp_03_note": "P4-EXP-03 used all 10,000 clusters; this run uses the 6,017 FIT clusters only", "conventions": rows,
           "bootstrap": bootstrap, "seed": seed, "workers": workers, "peak_rss_mb_self_plus_children": round(mem_mb(), 1),
           "environment": environment_info()}
    d = out_dir(job)
    (d / "results").mkdir(parents=True, exist_ok=True)
    p = d / "results" / "smoke.json"
    p.write_text(json.dumps(_jsonable(doc), indent=1, sort_keys=True) + "\n")
    for label, r in rows.items():
        print(label, {k: round(100 * x, 3) for k, x in r["observed_per_base_rates"].items()}, r["p4_exp_03_inside_ci"],
              {k: round(100 * x, 1) for k, x in r["calibrated_relative_change_vs_main"].items()})
    return p


def do_orientation(base: str, workers: int, bootstrap: int) -> Path:
    """Protocol 5 / plan 3.2: forward and backward fitted separately, then compared; merged only if the substitution matrices
    agree within their confidence intervals (every off-diagonal element's 95 % intervals overlap)."""
    jf, jb = JOBS[f"d03-{base}-fwd"], JOBS[f"d03-{base}-bwd"]
    d = out_dir(jf)
    mf = cm.from_doc(json.loads((d / "models" / f"{jf['name']}.json").read_text()))[0]
    mb = cm.from_doc(json.loads((d / "models" / f"{jb['name']}.json").read_text()))[0]
    cf, cb = mf.doc["parameters"]["sequencing.substitution.matrix"]["ci95"], mb.doc["parameters"]["sequencing.substitution.matrix"]["ci95"]
    lo_f, hi_f, lo_b, hi_b = (np.asarray(x) for x in (cf["lo"], cf["hi"], cb["lo"], cb["hi"]))
    off = ~np.eye(4, dtype=bool)
    overlap = (lo_f <= hi_b) & (lo_b <= hi_f)
    agree = bool(overlap[off].all())
    vf, vb = np.asarray(mf.stages["sequencing"]["substitution"]["matrix"]), np.asarray(mb.stages["sequencing"]["substitution"]["matrix"])
    # the same comparison in the backward reads' native (basecalled) frame: complement the base labels of the backward matrix
    comp = np.ix_([3, 2, 1, 0], [3, 2, 1, 0])
    overlap_n = (lo_f <= hi_b[comp]) & (lo_b[comp] <= hi_f)
    native = {"max_abs_difference_off_diagonal": float(np.abs(vf - vb[comp])[off].max()), "off_diagonal_intervals_overlap": overlap_n.tolist(),
              "agree": bool(overlap_n[off].all()),
              "note": "forward and backward reads compared as basecalled (backward matrix with complemented base labels); informational, the merge rule uses the reference frame"}
    rf, rb = mf.doc["fit_report"]["measured_statistics"], mb.doc["fit_report"]["measured_statistics"]
    rates = {}
    for k in ("substitution", "insertion_bases", "deletion_bases"):
        a, b = rf["observed_per_base_rates_ci95"][k], rb["observed_per_base_rates_ci95"][k]
        rates[k] = {"forward": rf["observed_per_base_rates"][k], "forward_ci95": a, "backward": rb["observed_per_base_rates"][k],
                    "backward_ci95": b, "intervals_overlap": bool(a[0] <= b[1] and b[0] <= a[1])}
    doc = {"basecaller": base, "forward_model": jf["name"], "backward_model": jb["name"],
           "substitution_matrix_forward": vf.tolist(), "substitution_matrix_backward": vb.tolist(),
           "max_abs_difference_off_diagonal": float(np.abs(vf - vb)[off].max()), "off_diagonal_intervals_overlap": overlap.tolist(),
           "matrices_agree_within_ci": agree, "native_frame": native, "observed_rates": rates,
           "decision": "merge forward and backward (protocol 5 / plan 3.2)" if agree else
           "keep separate: the substitution matrices differ beyond their 95 % intervals (strand asymmetry); the /2 `asymmetry` effect is not honoured "
           "by the simulator, so no merged model is produced",
           "model_sha256": {"forward": mf.sha256, "backward": mb.sha256}, "environment": environment_info(),
           "evidence_class": "PUBLIC-DATA-DERIVED"}
    p = d / "results" / f"orientation-{base}.json"
    p.write_text(json.dumps(_jsonable(doc), indent=1, sort_keys=True) + "\n")
    print(base, "agree" if agree else "differ", "max |diff| off-diagonal", round(doc["max_abs_difference_off_diagonal"], 4))
    if agree:
        do_fit(f"d03-{base}-merged", workers, bootstrap)
        do_validate(f"d03-{base}-merged", workers)
    return p


def write_configs() -> None:
    """experiments/v7/fit-<dataset>/config.json: the complete definition of every job (protocol 7, vnx-dna-experiment)."""
    for ds, name in (("cnr", "fit-cnr"), ("d03-nanopore", "fit-d03"), ("dt4dds-twist", "fit-d02")):
        jobs = {k: v for k, v in JOBS.items() if v["dataset"] == ds}
        cfg = {"experiment": name, "evidence_class": "PUBLIC-DATA-DERIVED (fits); SIMULATED (simulated reads)", "split": "FIT (fit), DEV (validation)",
               "split_manifest_sha256": FL.SPLIT_SHA, "jobs": jobs, "seed_fit": SEED, "seed_validation": SEED + 1,
               "bootstrap_over_references": 200, "calibration": {"references": CAL_REFS, "coverage": CAL_COVERAGE, "iterations": CAL_ITER},
               "validation": {"simulated_seeds": 5, "simulated_coverage": 6, "simulated_references_cap": 4000, "metrics": "M1-M10 (docs/research/V7_CHANNEL_FITTING_PLAN.md 3.4)",
                              "gating": ["M2", "M3", "M8"]},
               "alignment": "edlib unit-cost global (NW) / infix (HW), leftmost indel normalisation; 'dp-diag' = P4-EXP-03 tie-break",
               "workers": "results do not depend on the worker count",
               "environment": environment_info()}
        cfg["round"] = {"name": ROUND, "protocol": "docs/V7_PROTOCOL.md 5.5 (amendment 2)",
                        "read_heterogeneity": "per-read gamma rate multiplier, estimated on FIT (A2.1)",
                        "length_window": LENGTH_WINDOW.get(ds), "model_version": MODEL_VERSION}
        (OUT / name / ROUND).mkdir(parents=True, exist_ok=True)
        (OUT / name / ROUND / "config.json").write_text(json.dumps(cfg, indent=1, sort_keys=True) + "\n")


def environment_info() -> dict:
    from vnxdna.core.provenance import environment
    env = environment()
    env["software"] = FL.software()
    return env


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fit", "validate", "smoke", "orientation", "config", "list"])
    ap.add_argument("job", nargs="?")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--bootstrap", type=int, default=200)
    ap.add_argument("--round", choices=sorted(ROUNDS), default="a2")
    a = ap.parse_args(argv)
    global ROUND, MODEL_VERSION
    ROUND, MODEL_VERSION = a.round, ROUNDS[a.round]["version"]
    if a.cmd == "list":
        print("\n".join(JOBS))
        return 0
    if a.cmd == "config":
        write_configs()
        return 0
    if a.cmd == "orientation":
        do_orientation(a.job, a.workers, a.bootstrap)
        return 0
    if a.cmd == "smoke":
        do_smoke(a.workers, a.bootstrap)
        return 0
    if a.job not in JOBS:
        ap.error(f"job must be one of {list(JOBS)}")
    if a.job == "d02-twist":
        import run_d02
        (run_d02.do_fit(a.workers, a.bootstrap) if a.cmd == "fit" else run_d02.do_validate(a.workers))
        return 0
    (do_fit(a.job, a.workers, a.bootstrap) if a.cmd == "fit" else do_validate(a.job, a.workers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
