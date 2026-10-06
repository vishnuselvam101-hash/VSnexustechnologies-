"""V8.5 model-adequacy harness (docs/V8_PREREGISTRATION.md §3).

* ``m10_v8``: the pre-registered round-trip metric. ``replicates`` independent simulate + refit runs of the model. Each
  element must satisfy |refit_1 - target| <= 5 SE + 8 % |target| with SE^2 = SE_boot^2 + SD_rep^2, where refit_1 is the first
  replicate, SE_boot its bootstrap SE, and SD_rep the SD over the replicates.
  - Sparse elements are reported but not gated: run-length pmf bins with target < 1e-3, and a ``tail_mean`` with fewer than
    200 runs of >= 8 in the first replicate's tallies.
  - Under ``estimate.hp_sub_fixed`` the substitution matrix and context are gated through their identifiable product
    (per-3-mer, per-target-base substitution rate); the separate components are reported only.
  - Scalars must all pass; vectors need >= 99 % of their gated elements.
* ``metric_table``: one row per reported metric and stratum: observed, model, absolute and relative error, uncertainty,
  sample count, pass, identifiability. Strata (per kind, per run length, per band, per bin) are rows of their own, so an
  aggregate pass cannot hide a stratum failure.
"""
from __future__ import annotations

import numpy as np

from vnxdna.simulation import model as cm
from vnxdna.simulation.fit import estimate as est
from vnxdna.simulation.fit import fit as F
from vnxdna.simulation.fit import validate as V
from vnxdna.simulation.fit.pipeline import tally_matrix
from vnxdna.simulation.fit.simulate import simulate_clusters
from vnxdna.simulation.fit.tally import Layout

SPARSE_PMF = 1e-3
TAIL_MIN_RUNS = 200
REL_FLOOR = 0.08
N_SE = 5.0
VECTOR_SHARE = 0.99


def _flat(v) -> np.ndarray:
    return np.asarray(v, dtype=float).ravel()


def _se(ci, n: int) -> np.ndarray:
    if ci is None:
        return np.zeros(n)
    if isinstance(ci, dict):
        return (_flat(ci["hi"]) - _flat(ci["lo"])) / 3.92
    return np.full(n, (ci[1] - ci[0]) / 3.92)


def substitution_product(values: dict) -> np.ndarray | None:
    """(64 * 4) per-3-mer, per-target-base substitution rate: rate x context[k] x from_multiplier[centre] x matrix[centre, b].
    Identifiable even where its factors are not (``estimate.hp_sub_fixed``)."""
    ctx = values.get("sequencing.context.substitution")
    if ctx is None:
        return None
    centre = (np.arange(64) // 4) % 4
    fm = _flat(values["sequencing.substitution.from_multipliers"])[centre]
    mat = np.asarray(values["sequencing.substitution.matrix"], dtype=float)[centre]          # (64, 4)
    return (values["sequencing.substitution.rate"] * _flat(ctx) * fm)[:, None] * mat


def _tail_runs(layout: Layout, M: np.ndarray, field: str) -> float:
    return float(layout.get(M.sum(axis=0), field)[7:].sum())


def m10_v8(model: cm.ChannelModel, layout: Layout, refs: list, *, coverage: int, seed: int, replicates: int = 3,
           bootstrap: int = 200, calibration: dict | None = None, tally_opts: dict | None = None, workers: int = 1) -> dict:
    target = {k: v["value"] for k, v in model.doc["parameters"].items()}
    design = V.design_from_model(model)
    fits, tallies = [], []
    for r in range(replicates):
        s = seed + 1000 * r
        M, _ = tally_matrix(zip(refs, simulate_clusters(model, refs, coverage, s)), layout, workers=workers, **(tally_opts or {}))
        cal = dict(calibration, workers=workers) if calibration else None
        fits.append(F.fit_tallies(M, layout, seed=s, bootstrap=bootstrap, with_coverage=False, design=design, calibration=cal))
        tallies.append(M)
    sparse_tail = {k: _tail_runs(layout, tallies[0], f) < TAIL_MIN_RUNS
                   for k, f in (("insertion", "ins_runs"), ("deletion", "del_runs"))}
    confounded = est.hp_sub_fixed(design)
    rows, ok = {}, True
    paths = [p for p in target if p in fits[0]["values"] and not p.startswith("_") and fits[0]["values"][p] is not None]
    items = [(p, _flat(target[p]), [_flat(f["values"][p]) for f in fits], _se(fits[0]["ci95"].get(p), _flat(target[p]).size))
             for p in paths if not isinstance(target[p], str)]
    if confounded:
        tp = substitution_product(target)
        prods = [substitution_product(f["values"]) for f in fits]
        reps_p = [x.ravel() for x in prods if x is not None]
        if tp is not None and len(reps_p) == len(prods):
            items.append(("identifiable:substitution_rate_by_3mer_and_base", tp.ravel(), reps_p, np.zeros(tp.size)))
    for path, t, reps, se_b in items:
        if any(x.size != t.size for x in reps):
            continue
        a = np.stack(reps)
        sd = a.std(axis=0, ddof=1) if len(reps) > 1 else np.zeros_like(t)
        se = np.sqrt(se_b ** 2 + sd ** 2)
        gated = np.ones(t.size, dtype=bool)
        note = ""
        if path.endswith("run_length.pmf"):
            gated = t >= SPARSE_PMF
            note = f"bins with target < {SPARSE_PMF} reported, not gated"
        kind = path.split(".")[1] if path.startswith("sequencing.") else ""
        if path.endswith("run_length.tail_mean") and sparse_tail.get(kind, False):
            gated[:] = False
            note = f"fewer than {TAIL_MIN_RUNS} runs >= 8: reported, not gated"
        if confounded and path in ("sequencing.substitution.matrix", "sequencing.context.substitution",
                                   "sequencing.substitution.rate", "sequencing.homopolymer.substitution_multiplier"):
            gated[:] = False
            note = "not identifiable separately (hp_sub_fixed): gated through the identifiable product"
        tol = N_SE * se + REL_FLOOR * np.abs(t) + 1e-12
        within = np.abs(a[0] - t) <= tol
        if gated.any():
            good = bool(within[gated].all()) if t.size == 1 else bool(within[gated].mean() >= VECTOR_SHARE)
        else:
            good = True
        ok &= good
        rows[path] = {"pass": good, "n": int(t.size), "gated": int(gated.sum()),
                      "worst_ratio": float(np.max((np.abs(a[0] - t) / tol)[gated])) if gated.any() else None,
                      "max_sd_rep_over_se_boot": float(np.max(np.where(se_b > 0, sd / np.maximum(se_b, 1e-300), 0.0))),
                      "note": note}
    return {"pass": bool(ok), "parameters": rows, "replicates": replicates, "seeds": [seed + 1000 * r for r in range(replicates)],
            "rule": f"|refit - target| <= {N_SE:g} sqrt(SE_boot^2 + SD_rep^2) + {REL_FLOOR} |target| (M10-V8)"}


def _row(observed, model, uncertainty=None, n=None, ok=None, identifiable=True, stratum=None, note=""):
    o = None if observed is None else float(observed)
    m = None if model is None else float(model)
    ae = None if o is None or m is None else abs(m - o)
    re_ = None if ae is None or not o else ae / abs(o)
    return {"stratum": stratum, "observed": o, "model": m, "abs_error": ae, "rel_error": re_,
            "uncertainty": None if uncertainty is None else float(uncertainty), "n": n, "pass": ok,
            "identifiability": "IDENTIFIABLE" if identifiable else "NOT IDENTIFIABLE FROM DATA", "note": note}


def metric_table(rep: dict, *, sample_counts: dict | None = None) -> list:
    """Normalised rows (``_row``) for every metric in a ``validate_model`` (+ M10) report; strata as separate rows."""
    sc = sample_counts or {}
    out = []

    def add(metric, gating, row):
        out.append({"metric": metric, "gating": gating, **row})
    g = set(V.GATING_7B)
    m = rep.get("M1")
    if isinstance(m, dict) and "rates" in m:
        for k, v in m["rates"].items():
            add("M1", True, _row(v["real"], v["sim"], v.get("real_se"), sc.get("bases"), v["pass"], stratum=k,
                                 note=f"tolerance {v['tolerance']:.3g}"))
    m = rep.get("M1a")
    if isinstance(m, dict) and "real" in m:
        add("M1a", True, _row(m["real"], m["sim"], m.get("real_se"), sc.get("bases"), m["pass"], stratum="total"))
    for name, key in (("M1c", "tv"), ("M5", "tv"), ("M5i", "tv")):
        m = rep.get(name)
        if isinstance(m, dict) and key in m:
            add(name, name in g, _row(0.0, m[key], None, None, m["pass"], stratum="total variation",
                                      note=f"threshold {m.get('threshold')}"))
            if "share_ge2" in m:
                add(name, name in g, _row(m["share_ge2"]["real"], m["share_ge2"]["sim"], None, None, m["pass"],
                                          stratum="share of runs >= 2", note=f"threshold {m.get('share_threshold_pp')} pp"))
    for name in ("M2", "M2b", "RL", "M7"):
        m = rep.get(name)
        if isinstance(m, dict) and "ks_D" in m:
            obs = m.get("real", {}).get("mean") if isinstance(m.get("real"), dict) else m.get("real_mean")
            mod = m.get("sim", {}).get("mean") if isinstance(m.get("sim"), dict) else m.get("sim_mean")
            add(name, name in g, _row(obs, mod, None, sc.get("reads"), m["pass"], stratum="mean",
                                      note=f"KS D {m['ks_D']:.4f}"))
    m = rep.get("M3")
    if isinstance(m, dict) and "bands" in m:
        for band, v in m["bands"].items():
            add("M3", True, _row(v["real"], v["sim"], None, sc.get("reads"), v["pass"], stratum=band, note="threshold 1 pp"))
    m = rep.get("M6r")
    if isinstance(m, dict) and "real" in m:
        se = m.get("real_se") or [None] * len(m["real"])
        for i, lab in enumerate(m["run_lengths"]):
            tested = m["tested"][i]
            ok = (abs(m["sim"][i] - m["real"][i]) <= m["tolerance"][i] + 1e-15) if tested else None
            add("M6r", True, _row(m["real"][i], m["sim"][i], se[i], int(m["real_sites"][i]), ok, stratum=f"run length {lab}",
                                  note="" if tested else f"sparse: < {m['min_sites']} sites, not tested"))
    m = rep.get("M8")
    if isinstance(m, dict) and "pass" in m:
        add("M8", True, _row(None, None, None, None, m["pass"], stratum="consensus curve", note=str(m.get("threshold"))))
    m = rep.get("M9")
    if isinstance(m, dict):
        add("M9", False, _row(None, None, None, None, m.get("pass"), identifiable=m.get("pass") is not None,
                              stratum="quality", note=m.get("note", "")))
    m = rep.get("M10")
    if isinstance(m, dict) and "parameters" in m:
        for p, v in m["parameters"].items():
            add("M10", True, _row(None, v.get("worst_ratio"), None, v.get("n"), v["pass"], stratum=p,
                                  note=v.get("note", "") + " (model column: worst |refit - target| / tolerance)"))
    return out
