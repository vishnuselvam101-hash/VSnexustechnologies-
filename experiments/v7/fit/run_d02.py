"""D02 (DT4DDS Twist) fit and validation: read assignment, HW alignment of the design inside each read, quality calibration,
PhiX stage split. Called from run.py (``fit d02-twist`` / ``validate d02-twist``)."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path

import numpy as np

import d02 as D2
import fitlib as FL
import run as R
from fitlib import F, G, MO, P, V, cm
from vnxdna.simulation.fit import calibrate as CAL
from vnxdna.simulation.fit import simulate as S
from vnxdna.simulation.fit.tally import Layout

CYCLES = 150
GENOME = Path(FL.DATA_DIR) / "ref" / "NC_001422.1.fasta"
KINDS = (("substitution", "sub"), ("insertion", "ins"), ("deletion", "start"))


def layout_for(job: dict) -> Layout:
    return Layout(job["L"], quality=True, cycles=CYCLES)


def designs_in_split(guard: G.Guard, split: str) -> list:
    ids = guard.ids("dt4dds-twist", split)
    return [(i, s) for i, s in guard.references("dt4dds-twist") if i in ids]


def run_pairs(guard: G.Guard, assigner: D2.Assigner, run: str, split: str, designs: list, purpose: str):
    """Reads of ``run`` assigned to the designs of ``split``: [(design, oriented reads, [(qual, rev)])] in design order."""
    by_id: dict = {}
    for batch in guard.iter_fastq_batches(run, split, assigner, purpose, batch=50000):
        for rid, _name, seq, qual in batch:
            rev = assigner.orient[seq]
            by_id.setdefault(rid, []).append((FL.rc(seq) if rev else seq, qual, rev))
    pairs = []
    for rid, seq in designs:
        items = by_id.get(rid, [])
        pairs.append((seq, [x[0] for x in items], [(x[1], x[2]) for x in items]))
    return pairs


def tally_split(guard: G.Guard, job: dict, split: str, workers: int, purpose: str, keep_clusters: int = 0):
    lay = layout_for(job)
    all_designs = guard.references("dt4dds-twist")
    assigner = D2.Assigner(all_designs)
    designs = designs_in_split(guard, split)
    Ms, rss, stats, clusters = [], [], {}, []
    for run in job["runs"]:
        assigner.stats = {k: 0 for k in assigner.stats}
        pairs = run_pairs(guard, assigner, run, split, designs, purpose)
        stats[run] = dict(assigner.stats, dropped_not_in_split=guard.dropped.get(run, 0))
        if keep_clusters and not clusters:
            import edlib
            for ref, reads, _q in pairs:
                if reads and len(clusters) < keep_clusters:
                    win = []
                    for r in reads:
                        loc = edlib.align(ref, r, mode="HW", task="locations")["locations"]
                        if loc:
                            win.append(r[loc[0][0]:loc[0][1] + 1])
                    if win:
                        clusters.append((ref, win))
        M, rs = P.tally_matrix(pairs, lay, mode=job["mode"], workers=workers)
        Ms.append(M)
        rss.append(rs)
    return lay, Ms, rss, designs, stats, clusters


def phix_fit(guard: G.Guard, job: dict, bootstrap: int, seed: int) -> dict:
    genome = b"".join(ln.strip() for ln in GENOME.read_bytes().split(b"\n") if ln and not ln.startswith(b">")).upper()
    px = D2.PhiX(genome)
    for batch in guard.iter_fastq_control(job["control"], "PhiX sequencing-only control for the stage split (FIT)", batch=20000):
        for _name, seq, _q in batch:
            px.add(seq)
    out = px.fit(bootstrap, seed)
    out["genome_sha256"] = hashlib.sha256(GENOME.read_bytes()).hexdigest()
    return out


def _scaled_fit(fit: dict, scale: dict) -> dict:
    """The fit with the baseline rates of the sequencing stage scaled (kind -> factor); CIs scaled alike."""
    out = copy.copy(fit)
    out["values"] = dict(fit["values"])
    out["ci95"] = dict(fit["ci95"])
    for kind, f in scale.items():
        key = f"sequencing.{kind}.rate"
        out["values"][key] = fit["values"][key] * f
        c = fit["ci95"].get(key)
        if c:
            out["ci95"][key] = [c[0] * f, c[1] * f]
    return out


def synthesis_stage(fit: dict, S_rates: dict) -> dict:
    v, d = fit["values"], fit["design"]
    stage = {"substitution": {"rate": S_rates["substitution"], "matrix": v["sequencing.substitution.matrix"], "from_multipliers": None},
             "insertion": {"rate": S_rates["insertion"], "base_weights": v["sequencing.insertion.base_weights"]},
             "deletion": {"rate": S_rates["deletion"], "run_length": ({"distribution": "geometric", "mean": v["sequencing.deletion.run_length.mean"]}
                                                                      if d.del_geometric else {"distribution": "single", "mean": 1.0})},
             "molecules_per_strand": 64}
    prof = {k: v[f"sequencing.position_profile.{k}"] for k in ("substitution", "insertion", "deletion")}
    if any(x is not None for x in prof.values()):
        stage["position_profile"] = {"basis": "relative", **prof}
    return stage


def staged_model(fit: dict, phix: dict, S_rates: dict, scale: dict, *, job: dict, report: dict, seed: int, rc: float, datasets: list,
                 extra_ci: dict) -> dict:
    basis = {"synthesis.substitution.rate": "inferred", "synthesis.insertion.rate": "inferred", "synthesis.deletion.rate": "inferred",
             "synthesis.substitution.matrix": "inferred", "synthesis.insertion.base_weights": "inferred",
             "synthesis.deletion.run_length.mean": "inferred", "synthesis.molecules_per_strand": "assumed",
             "synthesis.position_profile.substitution": "inferred", "synthesis.position_profile.insertion": "inferred",
             "synthesis.position_profile.deletion": "inferred",
             "sequencing.substitution.rate": "measured", "sequencing.insertion.rate": "measured", "sequencing.deletion.rate": "measured"}
    return MO.build(
        _scaled_fit(fit, scale), name=job["name"], version=R.MODEL_VERSION, model_id=f"{job['name']}-F-{R.ROUND}",
        description=f"Model F fitted to the FIT split of {job['title']}.",
        note=("PUBLIC-DATA-DERIVED fit of other groups' sequencing data (FIT split only, protocol 4.1/5.2). Two stages: the sequencing "
              "stage carries the PhiX-run (sequencing-only) rate scale on the error structure fitted to the design reads; the synthesis "
              "stage is the residual (Aging_0 minus PhiX, floored at 0, INFERRED). Reads simulated from it are SIMULATED. The model "
              "describes the 108-nt design region at cycles 1-108 of an iSeq run; it is not a prediction for VNX strands."),
        datasets=datasets, split={"name": "FIT", "manifest_sha256": FL.SPLIT_SHA}, fitting=FL.fitting_block(seed),
        extra_sequencing={"reverse_complement_rate": rc}, synthesis=synthesis_stage(fit, S_rates), basis=basis, extra_ci=extra_ci,
        fit_report=report, references=[FL.MANIFEST["datasets"]["dt4dds-twist"]["publication"]])


def d02_datasets(phix_sha: str) -> list:
    d = FL.MANIFEST["datasets"]["dt4dds-twist"]
    files = [(f["path"], f["sha256"]) for f in d["files"]]
    entry = FL.dataset_entry("dt4dds-twist", files)
    return [entry, {"id": "PhiX174", "accession": "NC_001422.1", "url": "https://www.ncbi.nlm.nih.gov/nuccore/NC_001422.1",
                    "files": [{"name": "ref/NC_001422.1.fasta", "sha256": phix_sha}], "role": "PhiX reference genome (mapping of the control run)"}]


def do_fit(workers: int, bootstrap: int, seed: int = R.SEED) -> Path:
    job = R.JOBS["d02-twist"]
    guard = G.Guard(FL.DATA_DIR, script="experiments/v7/fit/run.py fit d02-twist")
    t0 = time.time()
    lay, Ms, rss, designs, astats, _ = tally_split(guard, job, "FIT", workers, "fit model F (D02, Twist) on FIT designs")
    t_tally = time.time() - t0
    M = Ms[0].astype(np.int64) + Ms[1]
    counts_a = FL.counts_of(lay, Ms[0])
    refs = [s for _, s in designs]
    cal_refs = refs[::max(1, len(refs) // R.CAL_REFS)][:R.CAL_REFS]
    t1 = time.time()
    fit = F.fit_tallies(M, lay, seed=seed, bootstrap=bootstrap, calibration=dict(refs=cal_refs, coverage=R.CAL_COVERAGE, iterations=R.CAL_ITER, workers=workers),
                        coverage_counts=counts_a)
    t_fit = time.time() - t1
    phix = phix_fit(guard, job, bootstrap, seed)
    pv = phix["values"]
    real_sum = CAL.summary(lay, fit["totals"], fit["design"])
    P_k = {"substitution": pv["substitution_rate"], "insertion": pv["insertion_rate"], "deletion": pv["deletion_rate"]}
    R_k = {kind: real_sum[key] for kind, key in KINDS}
    scale = {k: (min(1.0, P_k[k] / R_k[k]) if R_k[k] > 0 else 1.0) for k in P_k}
    S_rates = {k: max(0.0, R_k[k] - P_k[k]) for k in P_k}
    pc = phix["ci95"]
    tot_ci = fit["ci95"]["_observed"]
    se_tot = {"substitution": (tot_ci["substitution"][1] - tot_ci["substitution"][0]) / 3.92,
              "insertion": (tot_ci["insertion_events"][1] - tot_ci["insertion_events"][0]) / 3.92,
              "deletion": (tot_ci["deletion_runs"][1] - tot_ci["deletion_runs"][0]) / 3.92}
    se_px = {"substitution": (pc["substitution_rate"][1] - pc["substitution_rate"][0]) / 3.92,
             "insertion": (pc["insertion_rate"][1] - pc["insertion_rate"][0]) / 3.92,
             "deletion": (pc["deletion_rate"][1] - pc["deletion_rate"][0]) / 3.92}

    def synth_ci(rates):
        out = {}
        for k in rates:
            se = float(np.hypot(se_tot[k], se_px[k]))
            out[f"synthesis.{k}.rate"] = [max(0.0, rates[k] - 1.96 * se), rates[k] + 1.96 * se]
        return out
    rc = float(sum(s["reverse_strand"] for s in astats.values()) / max(1, sum(s["assigned"] for s in astats.values())))
    datasets = d02_datasets(phix["genome_sha256"])
    per_run = {run: R.group_rates(lay, m, None) for run, m in zip(job["runs"], Ms)}
    report = {"adequacy": "UNVALIDATED", "failed_metrics": [],
              "measured_statistics": {"observed_per_base_rates_total": fit["values"]["_observed"], "observed_ci95": fit["ci95"]["_observed"],
                                      "per_run": per_run, "assignment": astats, "reverse_strand_share": rc,
                                      "phix": {"values": pv, "ci95": pc, "mapped_reads": phix["mapped_reads"], "reference_bases": phix["reference_bases"],
                                               "stats": phix["stats"], "comparison": {
                                                   "substitution_published": R.PUBLISHED["d02"]["phix_substitution"], "substitution_published_sd": R.PUBLISHED["d02"]["phix_substitution_sd"],
                                                   "indel_published_upper_bound": R.PUBLISHED["d02"]["phix_indel_upper"],
                                                   "insertion_bases_rate": pv["insertion_bases_rate"], "deletion_bases_rate": pv["deletion_bases_rate"]}},
                                      "stage_split": {"total_events_per_site": R_k, "phix_events_per_site": P_k, "synthesis_residual": S_rates,
                                                      "sequencing_scale": scale},
                                      "error_correlation": {"p_event_given_event": fit["values"]["_stats"]["p_event_given_event"],
                                                            "p_event_given_no_event": fit["values"]["_stats"]["p_event_given_no_event"]},
                                      "coverage_aic_run_0a": fit["coverage"]["aic"], "coverage_choice": list(fit["coverage"]["choice"]),
                                      "design": {"min_run": fit["design"].min_run, "profile_bins": fit["design"].bins, "context_kinds": list(fit["design"].context)}},
              "misfit": {"notes": R.misfit_notes(fit)},
              "notes": ["PUBLIC-DATA-DERIVED parameters; reads simulated from this model are SIMULATED",
                        "the synthesis stage is INFERRED as Aging_0 minus PhiX (floor 0); PhiX events are counted at cycles 1-108 only",
                        "molecules_per_strand = 64 is ASSUMED (independent molecule variants per strand); molecule-shared errors were not identified",
                        "D02 qualities are binned to three values (11, 25, 37): the Gaussian quality model cannot represent the bins (see M9)",
                        "design reads start at design position 0; the 42 flanking cycles are not modelled"]}
    extra_ci = {"sequencing.reverse_complement_rate": None, **synth_ci(S_rates)}
    doc = staged_model(fit, phix, S_rates, scale, job=job, report=report, seed=seed, rc=rc, datasets=datasets, extra_ci=extra_ci)
    # two calibration passes of the synthesis rates (additive): simulated total events per site must match the FIT data
    refs_s = cal_refs
    for it in range(2):
        model = cm.from_doc(doc)[0]
        cl = S.simulate_clusters(model, refs_s, R.CAL_COVERAGE, seed * 10 + it)
        Msim, _ = P.tally_matrix(zip(refs_s, cl), Layout(lay.L), workers=workers)
        sim_sum = CAL.summary(Layout(lay.L), Msim.sum(axis=0).astype(float), fit["design"])
        for kind, key in KINDS:
            S_rates[kind] = max(0.0, S_rates[kind] + (real_sum[key] - sim_sum[key]))
        doc = staged_model(fit, phix, S_rates, scale, job=job, report=report, seed=seed, rc=rc, datasets=datasets, extra_ci={"sequencing.reverse_complement_rate": None, **synth_ci(S_rates)})
    d = R.d02_out_dir()
    (d / "models").mkdir(parents=True, exist_ok=True)
    (d / "results").mkdir(parents=True, exist_ok=True)
    mp = d / "models" / f"{job['name']}.json"
    mp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    summary = {"job": "d02-twist", "title": job["title"], "split": "FIT", "model_file": str(mp.relative_to(R.OUT)), "model_sha256": cm.from_doc(doc)[0].sha256,
               "designs": len(designs), "assigned_reads": fit["assigned_reads"], "assignment": astats, "phix": {k: phix[k] for k in ("values", "ci95", "mapped_reads", "reference_bases", "stats", "genome_sha256")},
               "stage_split": report["measured_statistics"]["stage_split"], "synthesis_after_calibration": S_rates,
               "seconds": {"assign_and_tally": round(t_tally, 1), "fit_bootstrap_calibration": round(t_fit, 1)}, "workers": workers,
               "peak_rss_mb_self_plus_children": round(R.mem_mb(), 1), "bootstrap": bootstrap, "seed": seed, "environment": R.environment_info(),
               "evidence_class": "PUBLIC-DATA-DERIVED"}
    (d / "results" / "d02-twist.fit.json").write_text(json.dumps(R._jsonable(summary), indent=1, sort_keys=True) + "\n")
    print(json.dumps(R._jsonable({"stage_split": report["measured_statistics"]["stage_split"], "phix_sub": pv["substitution_rate"], "assignment": astats,
                                  "seconds": summary["seconds"]}), indent=1))
    return mp


def do_validate(workers: int, seed: int = R.SEED + 1) -> Path:
    job = R.JOBS["d02-twist"]
    d = R.d02_out_dir()
    mp = d / "models" / f"{job['name']}.json"
    doc = json.loads(mp.read_text())
    model, _ = cm.from_doc(doc)
    guard = G.Guard(FL.DATA_DIR, script="experiments/v7/fit/run.py validate d02-twist")
    t0 = time.time()
    lay, Ms, rss, designs, astats, clusters = tally_split(guard, job, "DEV", workers, "validate model F (D02) against DEV designs", keep_clusters=1500)
    M = Ms[0].astype(np.int64) + Ms[1]          # both replicates, as in the fit (coverage M7: replicate a only, as in the fit)
    rs = rss[0] + rss[1]
    refs = [s for _, s in designs]
    rep = V.validate_model(model, lay, dev_refs=refs[::max(1, len(refs) // 3000)], dev_clusters=clusters, dev_M=M, dev_rs=rs, mode=job["mode"], seed=seed,
                           workers=workers, dev_counts=FL.counts_of(lay, Ms[0]))
    # M10: simulate from F, refit the *total* estimator, compare the design-independent observed rates with those of the FIT fit
    fit_obs = doc["fit_report"]["measured_statistics"]["observed_per_base_rates_total"]
    rt_refs = refs[::max(1, len(refs) // 3000)][:3000]
    q: list = []
    cl = S.simulate_clusters(model, rt_refs, 20, seed + 5, quals=q)
    Mrt, _ = P.tally_matrix(zip(rt_refs, cl, q), lay, workers=workers)
    rt = F.fit_tallies(Mrt, lay, seed=seed + 5, bootstrap=200, with_coverage=False)
    rows, ok = {}, True
    for k in ("substitution", "insertion_bases", "deletion_bases"):
        ci = rt["ci95"]["_observed"][k]
        se = (ci[1] - ci[0]) / 3.92
        sim_v, tgt = rt["values"]["_observed"][k], fit_obs[k]
        good = bool(abs(sim_v - tgt) <= 5 * se + 0.08 * tgt)
        ok &= good
        rows[k] = {"refit_of_simulated": sim_v, "fit_data": tgt, "se": se, "pass": good}
    rep["M10"] = {"pass": bool(ok), "observed_rates": rows, "rule": "|refit - FIT-data value| <= 5 SE + 8 % (observed per-base rates; the staged model is refit with the total estimator)"}
    adequacy, failed = V.adequacy(rep)
    fr = dict(doc["fit_report"])
    fr["adequacy"], fr["failed_metrics"] = adequacy, failed
    fr["validation"] = {"split": "DEV", "dev_references": int(M.shape[0]), "dev_reads_tallied_runs_0a_0b": int(lay.get(M, "n_reads").sum()),
                        "simulated_reads_note": "SIMULATED: 5 seeds x 6 reads per DEV design (<= 3,000 designs); real DEV statistics from runs 0a and 0b pooled (coverage: run 0a)",
                        "summary": {m: rep[m].get("pass") for m in sorted(rep)}}
    fr["metrics"] = R._jsonable(rep)
    fr["misfit"] = {"notes": list(fr["misfit"]["notes"]) + R.validation_notes(rep)}
    if adequacy == "INADEQUATE":
        fr["notes"] = list(fr["notes"]) + [f"INADEQUATE (protocol 5.4): fails {failed} on DEV; not used to choose decoder parameters; may be run as a labelled stress condition"]
    doc["fit_report"] = fr
    new = cm.from_doc(json.loads(json.dumps(doc)))[0]
    mp.write_text(json.dumps(new.doc, indent=1, sort_keys=True) + "\n")
    (d / "results" / "d02-twist.validation.json").write_text(json.dumps(
        {"job": "d02-twist", "model_sha256": new.sha256, "adequacy": adequacy, "failed_metrics": failed, "metrics": R._jsonable(rep), "assignment_dev": astats,
         "seconds": round(time.time() - t0, 1), "peak_rss_mb_self_plus_children": round(R.mem_mb(), 1), "workers": workers,
         "environment": R.environment_info(), "evidence_class": "SIMULATED reads vs PUBLIC-DATA-DERIVED DEV reads"}, indent=1, sort_keys=True) + "\n")
    print("d02-twist", adequacy, failed, {m: rep[m].get("pass") for m in sorted(rep)})
    return mp
