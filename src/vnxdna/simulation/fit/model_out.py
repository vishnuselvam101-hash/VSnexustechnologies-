"""Assemble a ``vnx.channel-model/2`` document from a fit (V7 protocol 5.1 labels, 5.3 provenance)."""
from __future__ import annotations

import copy
from typing import Any

from vnxdna.simulation import model as cm
from vnxdna.simulation import model2
from vnxdna.simulation.fit import estimate as est

#: default basis label of each fitted parameter (protocol 5.1); callers may override paths
BASIS = {
    "sequencing.substitution.rate": "measured", "sequencing.substitution.matrix": "measured",
    "sequencing.substitution.from_multipliers": "measured", "sequencing.insertion.rate": "measured",
    "sequencing.insertion.base_weights": "measured", "sequencing.insertion.run_length.mean": "measured",
    "sequencing.deletion.rate": "measured", "sequencing.deletion.run_length.mean": "measured",
    # empirical run lengths (V7 7.2): observed histogram corrected by simulation calibration; absent paths are skipped
    "sequencing.insertion.run_length.pmf": "estimated", "sequencing.insertion.run_length.tail_mean": "estimated",
    "sequencing.deletion.run_length.pmf": "estimated", "sequencing.deletion.run_length.tail_mean": "estimated",
    "sequencing.homopolymer.min_run": "estimated", "sequencing.homopolymer.indel_multiplier": "estimated",
    "sequencing.homopolymer.substitution_multiplier": "estimated", "sequencing.homopolymer.indel_by_length": "estimated",
    "sequencing.position_profile.substitution": "estimated", "sequencing.position_profile.insertion": "estimated",
    "sequencing.position_profile.deletion": "estimated",
    "sequencing.context.substitution": "measured", "sequencing.context.insertion": "measured",
    "sequencing.context.deletion": "measured", "sequencing.read_heterogeneity.shape": "estimated",
    "sequencing.coverage.model": "estimated", "sequencing.coverage.mean": "estimated",
    "sequencing.coverage.dispersion": "estimated", "sequencing.coverage.sigma": "estimated",
    "synthesis.dropout_rate": "inferred", "sequencing.reverse_complement_rate": "measured",
    "sequencing.quality.correct": "assumed", "sequencing.quality.error": "assumed", "sequencing.quality.informative": "assumed",
    "sequencing.quality.sd": "assumed", "sequencing.quality.position_slope": "assumed",
}


def _py(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _py(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_py(x) for x in v]
    if hasattr(v, "tolist"):
        return _py(v.tolist())
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15 and False:
        return int(v)
    return v.item() if hasattr(v, "item") else v


def _run_length(v: dict, kind: str, geometric: bool, empirical: bool) -> dict:
    if empirical:
        return model2.empirical_run_length(v[f"sequencing.{kind}.run_length.pmf"], v[f"sequencing.{kind}.run_length.tail_mean"])
    if geometric:
        return {"distribution": "geometric", "mean": v[f"sequencing.{kind}.run_length.mean"]}
    return {"distribution": "single", "mean": 1.0}


#: fit_report note of a model whose substitution rate is confounded (est.hp_sub_fixed); the V7 review wording (7c00d42)
CONFOUNDED_SUBSTITUTION_NOTE = (
    "sequencing.substitution.rate is 'estimated': with homopolymer min_run 2 the in-homopolymer flag is a function of the "
    "centred 3-mer, so the homopolymer substitution multiplier and the substitution context are confounded and the rate is "
    "not identifiable separately from them. Only the product rate x context x multiplier is determined. The effective "
    "per-base substitution rate (observed on FIT) is measured_statistics.substitution_rate_effective_per_base")


def confounded_substitution(fit: dict) -> dict | None:
    """``{"value", "ci95"}`` of the effective per-base substitution rate observed on FIT when the design makes the fitted rate
    unidentifiable (:func:`estimate.hp_sub_fixed`), else None. Recorded in ``fit_report.measured_statistics``."""
    d = fit.get("design")
    if d is None or not est.hp_sub_fixed(d):
        return None
    return {"value": fit["values"]["_observed"]["substitution"], "ci95": _py(fit["ci95"]["_observed"]["substitution"])}


def sequencing_stage(fit: dict, extra: dict | None = None) -> dict:
    """The /2 ``sequencing`` stage from the fitted values (extra fields such as quality or reverse_complement_rate merged in)."""
    v = fit["values"]
    d = fit["design"]
    seq: dict = {
        "substitution": {"rate": v["sequencing.substitution.rate"], "matrix": v["sequencing.substitution.matrix"],
                         "from_multipliers": v["sequencing.substitution.from_multipliers"]},
        "insertion": {"rate": v["sequencing.insertion.rate"], "base_weights": v["sequencing.insertion.base_weights"],
                      "run_length": _run_length(v, "insertion", d.ins_geometric, getattr(d, "ins_empirical", False))},
        "deletion": {"rate": v["sequencing.deletion.rate"],
                     "run_length": _run_length(v, "deletion", d.del_geometric, getattr(d, "del_empirical", False))},
        "homopolymer": {"min_run": d.min_run, "indel_multiplier": v["sequencing.homopolymer.indel_multiplier"],
                        "substitution_multiplier": v["sequencing.homopolymer.substitution_multiplier"]},
    }
    if getattr(d, "hp_by_length", False):
        seq["homopolymer"]["indel_by_length"] = v["sequencing.homopolymer.indel_by_length"]
    prof = {k: v[f"sequencing.position_profile.{k}"] for k in ("substitution", "insertion", "deletion")}
    if any(x is not None for x in prof.values()):
        seq["position_profile"] = {"basis": "relative", **prof}
    if getattr(d, "heterogeneity", False) and v.get("sequencing.read_heterogeneity.shape") is not None:
        seq["read_heterogeneity"] = {"distribution": "gamma", "shape": v["sequencing.read_heterogeneity.shape"]}
    if d.context:
        seq["context"] = {"k": 3, **{k: v[f"sequencing.context.{k}"] for k in d.context}}
    cov = fit.get("coverage")
    if cov:
        seq["coverage"] = cov["coverage"]
    if "sequencing.quality.correct" in v:
        seq["quality"] = {k: v[f"sequencing.quality.{k}"] for k in ("correct", "error", "informative", "sd", "position_slope")}
    if extra:
        for k, x in extra.items():
            seq[k] = x
    return _py(seq)


def build(fit: dict, *, name: str, version: str, model_id: str, description: str, note: str, datasets: list,
          split: dict, fitting: dict, extra_sequencing: dict | None = None, synthesis: dict | None = None,
          basis: dict | None = None, extra_ci: dict | None = None, fit_report: dict | None = None,
          references: list | None = None) -> dict:
    """The canonical /2 document (validated). ``datasets`` entries need ``id``, ``accession``, ``url``, ``files`` (name,
    sha256); the digest is added. ``fitting`` needs method, version, commit, dirty, seed, timestamp_utc, software."""
    ds = []
    for d in datasets:
        d = copy.deepcopy(d)
        d["sha256"] = model2.dataset_digest(d["files"])
        ds.append(d)
    stages = {"sequencing": sequencing_stage(fit, extra_sequencing)}
    cov = fit.get("coverage")
    syn = dict(synthesis or {})
    if cov and "dropout_rate" not in syn:
        syn["dropout_rate"] = cov["dropout"]
    stages["synthesis"] = _py(syn)
    doc = {"schema": cm.SCHEMA_V2, "name": name, "version": version, "model_id": model_id, "description": description,
           "note": note, "data_source": "LABORATORY", "evidence_class": "PUBLIC-DATA-DERIVED",
           "provenance": {"datasets": ds, "split": split, "fitting": fitting, "references": references or []},
           "stages": stages, "fit_report": fit_report}
    norm = model2.normalize_v2(doc)
    labels = dict(BASIS)
    design = fit.get("design")
    if design is not None and "substitution" in design.context:
        # the rate is the baseline of a site whose context multiplier is the site-weighted mean (1), not a counted rate
        labels["sequencing.substitution.rate"] = "estimated"
        if est.hp_sub_fixed(design):          # confounded with the context: fixed at 1, not fitted
            labels["sequencing.homopolymer.substitution_multiplier"] = "assumed"
    labels.update(basis or {})
    if "sequencing.quality.correct" in fit["values"]:      # FASTQ data: the quality parameters are measured, not assumed
        labels.update({f"sequencing.quality.{k}": "measured" for k in ("correct", "error", "informative", "sd", "position_slope")})
    ci = fit["ci95"]
    cmap = {"sequencing.coverage.mean": ci.get("coverage.mean"), "sequencing.coverage.dispersion": ci.get("coverage.dispersion"),
            "sequencing.coverage.sigma": ci.get("coverage.sigma"), "synthesis.dropout_rate": ci.get("dropout")}
    cmap.update(extra_ci or {})
    params: dict = {}
    for path in sorted(set(labels)):
        try:
            value = model2._lookup(norm["stages"], path)
        except cm.VNXConfigurationError:
            continue
        if value is None or isinstance(value, str) or (path.startswith("sequencing.context") and value is None):
            continue
        interval = ci.get(path, cmap.get(path))
        params[path] = {"value": value, "ci95": _py(interval), "basis": labels[path]}
    norm["parameters"] = model2._normalize_parameters(params, norm["stages"])
    return model2.normalize_v2(norm)
