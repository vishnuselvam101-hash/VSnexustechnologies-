"""Simulation-based calibration of a fit (V7). The count estimators of :mod:`estimate` are unbiased for the event rates they
count, but the simulator's per-site probabilities and the *measured* events differ: unit-cost alignment merges adjacent events
and saturates inside homopolymers. So after the first estimate, reads are simulated from the fitted model for a subset of the
FIT references, measured with the same alignment pipeline, and every parameter is scaled by (target statistic / simulated
statistic), a few times. The targets are always statistics measured on the FIT data; no DEV or held-out data is used.
The confidence intervals of the raw estimator are scaled by the same factors.

With ``design.heterogeneity`` (protocol 5.5, A2.1) the variance of the per-read edit distance is matched as well: Var(m) of
the per-read gamma multiplier is moved by (var_real - var_sim) / mean_real^2 (damped), since a mixed read-level rate adds
about mean^2 Var(m) to the variance of the per-read error count."""
from __future__ import annotations

import copy

import numpy as np

from vnxdna.simulation import model as cm
from vnxdna.simulation.fit import estimate as est
from vnxdna.simulation.fit import model_out as MO
from vnxdna.simulation.fit.pipeline import tally_matrix
from vnxdna.simulation.fit.simulate import simulate_clusters
from vnxdna.simulation.fit.tally import Layout

CTX_PRIOR = 10.0
STEP = 0.7            # damping exponent of every multiplicative update (ratio ** STEP)
CLIP = (0.4, 2.5)     # a single update never changes a parameter by more than this factor


def summary(layout: Layout, T: np.ndarray, design: est.Design) -> dict:
    """Measured statistics that calibration matches (design-dependent selection)."""
    mi = layout.minruns.index(design.min_run)
    n, nb, O, f_del, d_eff, m_del, _scale = est._basics(layout, T, mi)
    S = layout.ctx(T, "ctx_sites")[mi].astype(float)
    ev = {"substitution": layout.ctx(T, "ctx_sub")[mi].astype(float), "insertion": layout.ctx(T, "ctx_ins")[mi].astype(float),
          "deletion": layout.ctx(T, "ctx_del")[mi].astype(float)}
    indel = ev["insertion"] + ev["deletion"]

    def ratio(e):
        s_in, s_out = S[:, 1].sum(), S[:, 0].sum()
        e_in, e_out = e[:, 1].sum(), e[:, 0].sum()
        return (e_in / s_in) / (e_out / s_out) if min(s_in, s_out, e_in, e_out) > 0 else 1.0
    ins_runs = layout.get(T, "ins_runs").astype(float)
    out = {"sub": O["substitution"] / nb, "ins": O["insertion"] / nb, "start": O["deletion"] / nb, "f_del": f_del,
           "del_mean": f_del / (O["deletion"] / nb) if O["deletion"] else 1.0,
           "ins_mean": est._mean_run(ins_runs), "hp_indel": ratio(indel), "hp_sub": ratio(ev["substitution"]),
           "ctx_sub": ev["substitution"].sum(axis=1) / np.maximum(S.sum(axis=1), 1), "ctx_sub_events": ev["substitution"].sum(axis=1),
           "profile": {}}
    out["ed_mean"], out["ed_var"] = est.edit_moments(layout, T)
    for kind, (pos, _c) in est._kind_arrays(layout, T).items():
        B = design.bins.get(kind)
        out["profile"][kind] = (est._profile_counts(pos.astype(float), B), B) if B else None
    return out


def _sim_model(values: dict, design: est.Design, fit_like: dict) -> cm.ChannelModel:
    fit = dict(fit_like, values=values, design=design, coverage=None, ci95={})
    doc = {"schema": cm.SCHEMA_V2, "name": "calibration", "version": "0.0.1", "data_source": "SIMULATED", "evidence_class": "SIMULATED",
           "stages": {"sequencing": MO.sequencing_stage(fit)}}
    return cm.from_doc(doc)[0]


def calibrate(fit: dict, layout: Layout, refs: list, *, real_T: np.ndarray, coverage: int = 10, iterations: int = 3, seed: int,
              workers: int = 1, tol: float = 0.01, tally_opts: dict | None = None) -> dict:
    """Scale the fitted values (and their CIs) so that reads simulated for ``refs`` measure like the real FIT data (``real_T``).
    Returns a dict with ``values``, ``ci95`` (scaled), ``factors`` (final multiplicative change per parameter), ``trace``."""
    design = fit["design"]
    values = copy.deepcopy(fit["values"])
    raw = copy.deepcopy(fit["values"])
    target = summary(layout, real_T, design)
    trace: list[dict] = []
    for it in range(iterations):
        model = _sim_model(values, design, fit)
        cl = simulate_clusters(model, refs, coverage, seed * 100 + it)
        M, _ = tally_matrix(zip(refs, cl), layout, workers=workers, **(tally_opts or {}))
        sim = summary(layout, M.sum(axis=0).astype(float), design)
        ratios = {k: (target[k] / sim[k] if sim[k] else 1.0) for k in ("sub", "ins", "start", "hp_indel", "hp_sub")}
        ratios["del_mean"] = target["del_mean"] / sim["del_mean"] if sim["del_mean"] else 1.0
        ratios["ins_mean"] = (target["ins_mean"] - 1.0 + 1e-6) / (sim["ins_mean"] - 1.0 + 1e-6)
        if design.heterogeneity and values.get("sequencing.read_heterogeneity.shape") is not None and target["ed_mean"] > 0:
            ratios["ed_var"] = target["ed_var"] / sim["ed_var"] if sim["ed_var"] else 1.0
            s2 = 1.0 / values["sequencing.read_heterogeneity.shape"]
            s2 += STEP * (target["ed_var"] - sim["ed_var"]) / target["ed_mean"] ** 2
            s2 = min(max(s2, est.HETEROGENEITY_FLOOR), est.HETEROGENEITY_CEIL)
            values["sequencing.read_heterogeneity.shape"] = 1.0 / s2
        trace.append({"iteration": it, "ratios": {k: float(v) for k, v in ratios.items()}})
        ratios.pop("ed_var", None)
        ratios = {k: float(np.clip(v, *CLIP) ** STEP) for k, v in ratios.items()}
        values["sequencing.substitution.rate"] *= ratios["sub"]
        values["sequencing.insertion.rate"] *= ratios["ins"]
        values["sequencing.deletion.rate"] *= ratios["start"]
        if design.del_geometric:
            values["sequencing.deletion.run_length.mean"] = max(1.0, values["sequencing.deletion.run_length.mean"] * ratios["del_mean"])
        if design.ins_geometric:
            values["sequencing.insertion.run_length.mean"] = max(1.0, 1.0 + (values["sequencing.insertion.run_length.mean"] - 1.0) * ratios["ins_mean"])
        if design.use_hp:
            values["sequencing.homopolymer.indel_multiplier"] *= ratios["hp_indel"]
            values["sequencing.homopolymer.substitution_multiplier"] *= ratios["hp_sub"]
        if "substitution" in design.context and values["sequencing.context.substitution"] is not None:
            tr, sr = target["ctx_sub"], sim["ctx_sub"]
            te, se = target["ctx_sub_events"], sim["ctx_sub_events"]
            # per-3-mer rate ratio, shrunk towards 1 by the smaller of the two event counts
            w = STEP * np.minimum(te, se * (te.sum() / max(se.sum(), 1))) / (np.minimum(te, se * (te.sum() / max(se.sum(), 1))) + CTX_PRIOR)
            raw_ratio = np.where(sr > 0, tr / np.maximum(sr, 1e-300), 1.0)
            raw_ratio = raw_ratio / (np.sum(raw_ratio * sr) / max(np.sum(sr), 1e-300) if sr.sum() else 1.0) * 1.0
            ctx = np.asarray(values["sequencing.context.substitution"]) * (1.0 + w * (raw_ratio - 1.0))
            values["sequencing.context.substitution"] = (ctx / np.mean(ctx)).tolist()
        for kind in ("substitution", "insertion", "deletion"):
            key = f"sequencing.position_profile.{kind}"
            if target["profile"][kind] is not None and values.get(key) is not None:
                tc, B = target["profile"][kind]
                sc, _ = sim["profile"][kind]
                tn, sn = tc / tc.sum() * B, sc / max(sc.sum(), 1) * B
                prof = np.asarray(values[key]) * np.where(sn > 0, np.clip(tn / np.maximum(sn, 1e-300), *CLIP), 1.0) ** STEP
                values[key] = (prof / prof.mean()).tolist()
        if max(abs(r - 1.0) for r in trace[-1]["ratios"].values()) < tol:
            break
    # verification pass at the final values (not applied): how far the simulated statistics are from the targets
    model = _sim_model(values, design, fit)
    cl = simulate_clusters(model, refs, coverage, seed * 100 + 99)
    M, _ = tally_matrix(zip(refs, cl), layout, workers=workers, **(tally_opts or {}))
    sim = summary(layout, M.sum(axis=0).astype(float), design)
    residual = {k: float(target[k] / sim[k]) if sim[k] else 1.0 for k in ("sub", "ins", "start", "hp_indel", "hp_sub")}
    if design.heterogeneity:
        residual["ed_var"] = float(target["ed_var"] / sim["ed_var"]) if sim["ed_var"] else 1.0
    factors = {}
    ci = copy.deepcopy(fit["ci95"])
    for key, v in values.items():
        if key.startswith("_") or v is None or raw.get(key) is None:
            continue
        a, b = np.asarray(v, dtype=float), np.asarray(raw[key], dtype=float)
        f = np.where(b != 0, a / np.where(b != 0, b, 1.0), 1.0)
        factors[key] = f.tolist() if f.ndim else float(f)
        c = ci.get(key)
        if c is None:
            continue
        if isinstance(c, dict):
            lo, hi = np.asarray(c["lo"]) * f, np.asarray(c["hi"]) * f
            ci[key] = {"lo": np.minimum(lo, hi).tolist(), "hi": np.maximum(lo, hi).tolist()}
        else:
            ci[key] = [float(c[0] * f), float(c[1] * f)]
    return {"values": values, "ci95": ci, "factors": factors, "trace": trace, "final_residual_ratios": residual, "raw_values": raw, "target": {k: v for k, v in target.items() if k in ("sub", "ins", "start", "f_del", "del_mean", "ins_mean", "hp_indel", "hp_sub", "ed_mean", "ed_var")}}
