"""Fit-quality metrics M1-M10 of the V7 fitting plan 3.4, computed on held-back (DEV) data against reads simulated from the
fitted model (SIMULATED). Real and simulated reads go through the *same* alignment and tally pipeline, so alignment
conventions cancel. Acceptance thresholds are the plan's; a model failing M2, M3 or M8 is INADEQUATE (protocol 5.4)."""
from __future__ import annotations

import math

import numpy as np

from vnxdna.simulation import model as cm
from vnxdna.simulation.errormodels import CoverageModel
from vnxdna.simulation.fit import estimate as est
from vnxdna.simulation.fit import fit as F
from vnxdna.simulation.fit.align import align
from vnxdna.simulation.fit.pipeline import tally_matrix
from vnxdna.simulation.fit.simulate import simulate_clusters
from vnxdna.simulation.fit.tally import DRIFT_BINS, DRIFT_OFF, EDIT_BINS, LUT, Layout

KS_MAX, TV_MAX = 0.03, 0.05
GATING = ("M2", "M3", "M8")
K_CURVE = (1, 2, 5, 10, 20)


def wilson(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - h) / d), min(1.0, (c + h) / d)


# ================================================================================================================ measures
def rates(layout: Layout, T: np.ndarray) -> dict:
    n = float(layout.get(T, "n_reads").sum())
    nb = n * layout.L
    return {"substitution": layout.get(T, "pos_sub").sum() / nb, "insertion": layout.get(T, "ins_base").sum() / nb,
            "deletion": layout.get(T, "pos_del").sum() / nb, "reads": n}


def _hist_stats(h: np.ndarray) -> dict:
    p = h / h.sum()
    c = np.cumsum(p)
    return {"p": p, "cdf": c, "p90": int(np.searchsorted(c, 0.90)), "p99": int(np.searchsorted(c, 0.99)),
            "mean": float((p * np.arange(h.size)).sum())}


def m1_rates(layout: Layout, real_M: np.ndarray, sim_T: np.ndarray, seed: int, B: int = 200) -> dict:
    """Per-base rates, simulated vs real: within 5 % relative or 3 bootstrap SE (of the real rate), whichever is larger."""
    R = real_M.shape[0]
    W = F.bootstrap_weights(R, B, seed)
    tot = F.bootstrap_totals(real_M, W)
    real = rates(layout, real_M.sum(axis=0).astype(float))
    sim = rates(layout, sim_T)
    reps = [rates(layout, tot[b]) for b in range(B)]
    out, ok = {}, True
    for k in ("substitution", "insertion", "deletion"):
        se = float(np.std([r[k] for r in reps], ddof=1))
        tol = max(0.05 * real[k], 3 * se)
        good = bool(abs(sim[k] - real[k]) <= tol)
        ok &= good
        out[k] = {"real": float(real[k]), "sim": float(sim[k]), "real_se": se, "tolerance": float(tol), "pass": good}
    return {"pass": bool(ok), "rates": out}


def m2_edit_distance(real_rs: np.ndarray, sim_rs: np.ndarray) -> dict:
    a, b = _hist_stats(real_rs[:EDIT_BINS].astype(float)), _hist_stats(sim_rs[:EDIT_BINS].astype(float))
    D = float(np.max(np.abs(a["cdf"] - b["cdf"])))
    tv = float(0.5 * np.abs(a["p"] - b["p"]).sum())
    rel = {q: abs(b[q] - a[q]) / max(a[q], 1) for q in ("p90", "p99")}
    ok = D <= KS_MAX and tv <= TV_MAX and all(v <= 0.10 for v in rel.values())
    return {"pass": bool(ok), "ks_D": D, "tv": tv, "real": {"mean": a["mean"], "p90": a["p90"], "p99": a["p99"]},
            "sim": {"mean": b["mean"], "p90": b["p90"], "p99": b["p99"]}, "p90_p99_rel_diff": rel,
            "thresholds": {"ks_D": KS_MAX, "tv": TV_MAX, "quantile_rel": 0.10}}


def m3_drift(real_rs: np.ndarray, sim_rs: np.ndarray) -> dict:
    out, ok = {}, True
    for band in (0, 3, 6):
        sl = slice(DRIFT_OFF + EDIT_BINS - band, DRIFT_OFF + EDIT_BINS + band + 1)
        r = real_rs[EDIT_BINS:].astype(float)
        s = sim_rs[EDIT_BINS:].astype(float)
        pr = r[DRIFT_OFF - band:DRIFT_OFF + band + 1].sum() / r.sum()
        ps = s[DRIFT_OFF - band:DRIFT_OFF + band + 1].sum() / s.sum()
        good = abs(pr - ps) <= 0.01
        ok &= bool(good)
        out[f"abs_drift_le_{band}"] = {"real": float(pr), "sim": float(ps), "diff_pp": float(100 * (ps - pr)), "pass": bool(good)}
        _ = sl
    return {"pass": bool(ok), "bands": out, "threshold_pp": 1.0}


def _bin_sums(a: np.ndarray, nb: int) -> np.ndarray:
    return np.array([c.sum() for c in np.array_split(a, nb)], dtype=float)


def m4_profile(layout: Layout, real_T: np.ndarray, sim_T: np.ndarray, bins: int = 11) -> dict:
    out, ok = {}, True
    for kind, name in (("substitution", "pos_sub"), ("insertion", "pos_ins"), ("deletion", "pos_delstart")):
        r, s = _bin_sums(layout.get(real_T, name), bins), _bin_sums(layout.get(sim_T, name), bins)
        nr, ns = layout.get(real_T, "n_reads").sum(), layout.get(sim_T, "n_reads").sum()
        rr, ss = r / nr, s / ns                              # events per read per bin
        ratio = np.where(rr > 0, ss / np.maximum(rr, 1e-300), np.nan)
        enough = r >= 100
        good = bool(np.all(np.abs(ratio[enough] - 1.0) <= 0.10)) if enough.any() else True
        exp = ss * nr                                         # expected real events if the simulated profile were right
        chi2 = float(np.sum((r[enough] - exp[enough]) ** 2 / np.maximum(exp[enough], 1)))
        ok &= good
        out[kind] = {"pass": good, "ratio_sim_over_real": [None if np.isnan(x) else round(float(x), 4) for x in ratio],
                     "chi2": chi2, "bins_tested": int(enough.sum())}
    return {"pass": bool(ok), "kinds": out, "threshold": "each 11-bin ratio within +-10% (bins with >= 100 real events)"}


def m5_delruns(layout: Layout, real_T: np.ndarray, sim_T: np.ndarray) -> dict:
    def hist8(T):
        h = layout.get(T, "del_runs").astype(float)
        v = np.concatenate([h[:7], [h[7:].sum()]])
        return v / v.sum() if v.sum() else v
    a, b = hist8(real_T), hist8(sim_T)
    tv = float(0.5 * np.abs(a - b).sum())
    return {"pass": bool(tv <= TV_MAX), "tv": tv, "real": a.tolist(), "sim": b.tolist(), "threshold": TV_MAX}


def m6_homopolymer(layout: Layout, real_T: np.ndarray, sim_T: np.ndarray, min_run: int) -> dict:
    mi = layout.minruns.index(min_run)

    def indel(T):
        s = layout.ctx(T, "ctx_sites")[mi].sum(axis=0)
        e = (layout.ctx(T, "ctx_ins", )[mi] + layout.ctx(T, "ctx_del")[mi]).sum(axis=0)
        return e / np.maximum(s, 1)
    a, b = indel(real_T), indel(sim_T)
    rel = np.abs(b - a) / np.maximum(a, 1e-12)
    return {"pass": bool(np.all(rel <= 0.10)), "min_run": min_run, "indel_rate_out_in_real": a.tolist(),
            "indel_rate_out_in_sim": b.tolist(), "rel_diff": rel.tolist(), "threshold": 0.10}


def m7_coverage(real_counts: np.ndarray, model: cm.ChannelModel, seed: int, B: int = 200) -> dict:
    cov = CoverageModel.from_json(model.stages["sequencing"]["coverage"])
    rng = np.random.default_rng([seed, 7])
    n = 200_000
    drop = rng.random(n) < model.stages["synthesis"]["dropout_rate"]
    sim = np.where(drop, 0, cov.sample(np.full(n, float(cov.mean)), rng, weighted=False)).astype(np.int64)
    a = np.sort(real_counts)
    grid = np.arange(0, max(int(a.max()), int(sim.max())) + 1)
    D = float(np.max(np.abs(np.searchsorted(a, grid, side="right") / a.size - np.searchsorted(np.sort(sim), grid, side="right") / sim.size)))
    zr = float((real_counts == 0).mean())
    zs = float((sim == 0).mean())
    W = F.bootstrap_weights(real_counts.size, B, seed + 1)
    zboot = (W * (real_counts == 0)[None, :]).sum(axis=1) / real_counts.size
    lo, hi = np.percentile(zboot, [2.5, 97.5])
    q = {p: (float(np.percentile(real_counts, p)), float(np.percentile(sim, p))) for p in (10, 90)}
    qok = all(abs(s - r) <= 0.10 * max(r, 1.0) for r, s in q.values())
    ok = D <= 0.05 and lo <= zs <= hi and qok
    return {"pass": bool(ok), "ks_D": D, "zero_fraction": {"real": zr, "sim": zs, "real_ci95": [float(lo), float(hi)]},
            "p10_p90": {str(p): {"real": r, "sim": s} for p, (r, s) in q.items()},
            "thresholds": {"ks_D": 0.05, "zero_fraction": "simulated inside real bootstrap 95% CI", "quantile_rel": 0.10}}


def m9_quality(layout: Layout, real_T: np.ndarray, sim_T: np.ndarray, min_bases: int = 1000) -> dict:
    """Quality calibration (FASTQ data): for each Phred value occupied in the real reads, the empirical error probability of
    bases at that value, in Phred units (-10 log10 p), real vs simulated; every occupied bin within 1 Phred unit. Simulated
    values are assigned to the nearest real value (the real instrument bins its qualities)."""
    rc_, re_ = layout.get(real_T, "q_correct").astype(float), layout.get(real_T, "q_error").astype(float)
    sc, se = layout.get(sim_T, "q_correct").astype(float), layout.get(sim_T, "q_error").astype(float)
    occ = np.flatnonzero(rc_ + re_ >= min_bases)
    if occ.size == 0:
        return {"pass": None, "note": "no occupied quality values"}
    sim_c, sim_e = np.zeros(occ.size), np.zeros(occ.size)
    for q in np.flatnonzero(sc + se > 0):
        j = int(np.argmin(np.abs(occ - q)))
        sim_c[j] += sc[q]
        sim_e[j] += se[q]

    def phred(e, n):
        p = (e + 0.5) / (n + 1.0)
        return -10.0 * np.log10(p)
    rows, ok = {}, True
    for j, q in enumerate(occ):
        pr, ps = phred(re_[q], rc_[q] + re_[q]), phred(sim_e[j], sim_c[j] + sim_e[j])
        good = bool(abs(pr - ps) <= 1.0)
        ok &= good
        rows[int(q)] = {"real_error_phred": float(pr), "sim_error_phred": float(ps), "real_bases": float(rc_[q] + re_[q]),
                        "sim_bases": float(sim_c[j] + sim_e[j]), "pass": good}
    return {"pass": bool(ok), "bins": rows, "threshold_phred_units": 1.0}


# ================================================================================================================ consensus (M8)
def center_star(reads: list) -> bytes:
    """One-pass consensus: the median-length read is the pivot; every other read is aligned to it (edlib, global) and each
    pivot position takes the majority over {A, C, G, T, deleted} (the pivot's own base breaks ties). Insertions are ignored."""
    if len(reads) == 1:
        return reads[0]
    order = sorted(range(len(reads)), key=lambda i: (len(reads[i]), i))
    pivot = reads[order[len(order) // 2]]
    P = len(pivot)
    votes = np.zeros((P, 5), dtype=np.int32)                 # A C G T gap
    pc = LUT[np.frombuffer(pivot, dtype=np.uint8)]
    votes[np.arange(P), np.minimum(pc, 3)] += 1
    for i, r in enumerate(reads):
        if i == order[len(order) // 2]:
            continue
        res = align(r, pivot, "NW", normalise=False)
        if res is None:
            continue
        _d, runs, _w = res
        pi = ri = 0
        for op, n in runs:
            if op in "=X":
                for j in range(n):
                    c = LUT[r[ri + j]]
                    if c < 4:
                        votes[pi + j, c] += 1
                pi += n
                ri += n
            elif op == "D":
                votes[pi:pi + n, 4] += 1
                pi += n
            else:
                ri += n
    best = votes.argmax(axis=1)
    tie = (votes == votes.max(axis=1, keepdims=True)).sum(axis=1) > 1
    best = np.where(tie, np.minimum(pc, 3), best)
    out = bytes(b"ACGT"[b] for b in best if b < 4)
    return out


def consensus_error(reads: list, ref: bytes) -> float:
    c = center_star(reads)
    res = align(c, ref, "NW", normalise=False)
    return (res[0] if res else len(ref)) / len(ref)


def exact_length_vote(reads: list, ref: bytes) -> tuple[bool, float] | None:
    """P4-EXP-03 metric: position-wise majority over the reads of exactly len(ref) bases."""
    L = len(ref)
    ex = [r for r in reads if len(r) == L]
    if not ex:
        return None
    arr = np.stack([np.minimum(LUT[np.frombuffer(r, dtype=np.uint8)], 3) for r in ex])
    cnt = np.stack([(arr == b).sum(axis=0) for b in range(4)])
    vote = cnt.argmax(axis=0)
    rc = np.minimum(LUT[np.frombuffer(ref, dtype=np.uint8)], 3)
    err = int((vote != rc).sum())
    return err == 0, err / L


def _curve(clusters: list, ref_len: int) -> dict:
    out = {}
    for k in K_CURVE:
        errs = [consensus_error(reads[:k], ref) for ref, reads in clusters if len(reads) >= k]
        out[k] = {"clusters": len(errs), "per_base_error": float(np.mean(errs)) if errs else None,
                  "errors": float(np.sum(errs) * ref_len), "bases": float(len(errs) * ref_len)}
    return out


def m8_functional(real_clusters: list, model: cm.ChannelModel, seed: int, *, max_clusters: int = 1500, ref_len: int | None = None) -> dict:
    """Consensus per-base error vs cluster size k and the exact-length positional vote, real vs simulated clusters.
    ``real_clusters``: list of (ref, reads); the simulated clusters use the same references (first ``max_clusters``)."""
    ref_len = ref_len or len(real_clusters[0][0])
    out: dict = {"curve": {}}
    ok = True
    for k in K_CURVE:
        pool = [(r, rd) for r, rd in real_clusters if len(rd) >= k][:max_clusters]
        if len(pool) < 100:
            out["curve"][str(k)] = {"clusters": len(pool), "pass": None, "note": "fewer than 100 real clusters with >= k reads"}
            continue
        real_e = float(np.mean([consensus_error(rd[:k], r) for r, rd in pool]))
        sims = simulate_clusters(model, [r for r, _ in pool], k, seed + k)
        sim_e = float(np.mean([consensus_error(rd, r) for (r, _), rd in zip(pool, sims)]))
        n = len(pool) * ref_len
        rlo, rhi = wilson(real_e * n, n)
        slo, shi = wilson(sim_e * n, n)
        good = abs(real_e - sim_e) <= 0.02 or (rlo <= sim_e <= rhi and slo <= real_e <= shi)
        ok &= bool(good)
        out["curve"][str(k)] = {"clusters": len(pool), "real": real_e, "sim": sim_e, "real_wilson95": [rlo, rhi],
                                "sim_wilson95": [slo, shi], "pass": bool(good)}
    # exact-length vote (all reads of each cluster)
    pool = real_clusters[:max_clusters]
    real_v = [v for v in (exact_length_vote(rd, r) for r, rd in pool) if v is not None]
    simc = simulate_clusters(model, [r for r, _ in pool], 10, seed + 99)
    sim_v = [v for v in (exact_length_vote(rd, r) for (r, _), rd in zip(pool, simc)) if v is not None]
    out["exact_length_vote"] = {
        "note": "real: all reads of the cluster; simulated: 10 reads per cluster",
        "real": {"clusters_with_exact_length_read": len(real_v), "exact_share": float(np.mean([v[0] for v in real_v])) if real_v else None,
                 "per_base_error": float(np.mean([v[1] for v in real_v])) if real_v else None},
        "sim": {"clusters_with_exact_length_read": len(sim_v), "exact_share": float(np.mean([v[0] for v in sim_v])) if sim_v else None,
                "per_base_error": float(np.mean([v[1] for v in sim_v])) if sim_v else None}}
    out["pass"] = bool(ok) if out["curve"] and any(c.get("pass") is not None for c in out["curve"].values()) else None
    out["consensus"] = "one-pass center-star (median-length pivot, edlib global alignment, majority vote); not the VNX decoder"
    out["threshold"] = "each k: within 2 percentage points or inside each other's Wilson 95% CI"
    return out


# ================================================================================================================ driver
def validate_model(model: cm.ChannelModel, layout: Layout, *, dev_refs: list, dev_clusters: list, dev_M: np.ndarray,
                   dev_rs: np.ndarray, mode: str = "NW", seed: int = 7, sim_seeds: int = 5, sim_coverage: int = 6,
                   workers: int = 1, sim_refs_cap: int = 4000, with_functional: bool = True,
                   tally_opts: dict | None = None, dev_counts: np.ndarray | None = None) -> dict:
    """M1-M8 for ``model`` against DEV data (``dev_M``/``dev_rs`` from the same pipeline; ``dev_clusters`` = (ref, reads)).
    M9 (quality) and M10 (round trip) are separate (see ``quality_calibration`` in the experiment code and ``round_trip``)."""
    refs = dev_refs[:sim_refs_cap]
    simM = np.zeros(layout.size, dtype=np.int64)
    simrs = np.zeros(EDIT_BINS + DRIFT_BINS, dtype=np.int64)
    raw_drift = np.zeros(EDIT_BINS + DRIFT_BINS, dtype=np.int64)    # simulated read lengths before any read selection
    for s in range(sim_seeds):
        sq: list | None = [] if layout.quality else None
        cl = simulate_clusters(model, refs, sim_coverage, seed * 1000 + s, quals=sq)
        for reads in cl:
            for r in reads:
                raw_drift[EDIT_BINS + max(0, min(DRIFT_BINS - 1, len(r) - layout.L + DRIFT_OFF))] += 1
        pairs = zip(refs, cl, sq) if sq is not None else zip(refs, cl)
        M, rs = tally_matrix(pairs, layout, mode="NW" if mode == "HW" else mode, workers=workers, **(tally_opts or {}))
        simM += M.sum(axis=0, dtype=np.int64)
        simrs += rs
    sim_T = simM.astype(float)
    real_T = dev_M.sum(axis=0).astype(float)
    min_run = model.stages["sequencing"]["homopolymer"]["min_run"]
    rep = {
        "M1": m1_rates(layout, dev_M, sim_T, seed),
        "M2": m2_edit_distance(dev_rs, simrs), "M3": m3_drift(dev_rs, simrs),
        "M3_unselected": dict(m3_drift(dev_rs, raw_drift), gating=False,
                              note="simulated reads without the dataset's read selection (protocol 5.5, A2.2); not gating"),
        "M4": m4_profile(layout, real_T, sim_T), "M5": m5_delruns(layout, real_T, sim_T),
        "M6": m6_homopolymer(layout, real_T, sim_T, min_run),
        "M7": m7_coverage(dev_counts if dev_counts is not None else
                          (layout.get(dev_M, "n_reads")[:, 0] + layout.get(dev_M, "excluded")[:, 0]).astype(np.int64), model, seed),
    }
    if layout.quality:
        rep["M9"] = m9_quality(layout, real_T, sim_T)
    if with_functional:
        rep["M8"] = m8_functional(dev_clusters, model, seed * 1000 + 500, ref_len=layout.L)
    return rep


def adequacy(report: dict) -> tuple[str, list]:
    failed = [m for m in GATING if report.get(m, {}).get("pass") is False]
    unknown = [m for m in GATING if report.get(m, {}).get("pass") is None]
    if failed:
        return "INADEQUATE", failed
    return ("UNVALIDATED", []) if unknown else ("ADEQUATE", [])


def design_from_model(model: cm.ChannelModel) -> "est.Design":
    """The fitting design a fitted model implies (so a refit estimates the same parameters)."""
    seq = model.stages["sequencing"]
    pp = seq.get("position_profile") or {}
    bins = {k: (len(pp[k]) if pp.get(k) else None) for k in ("substitution", "insertion", "deletion")}
    ctx = seq.get("context") or {}
    return est.Design(min_run=seq["homopolymer"]["min_run"], bins=bins,
                      ins_geometric=seq["insertion"].get("run_length", {}).get("distribution") == "geometric",
                      del_geometric=seq["deletion"]["run_length"]["distribution"] == "geometric",
                      context=tuple(k for k in ("substitution", "insertion", "deletion") if ctx.get(k) is not None),
                      heterogeneity=seq.get("read_heterogeneity") is not None)


def compare_params(target: dict, refit: dict, ci: dict, rel_floor: float = 0.08, abs_floor: float = 0.02) -> dict:
    """Each parameter of the refit within 5 bootstrap SE (of the refit) or ``rel_floor`` relative of the target. Scalars must
    all pass; for vectors at least 99 % of the elements must."""
    rows, ok = {}, True
    for path, tv in target.items():
        rv = refit.get(path)
        if rv is None or tv is None:
            continue
        t, r = np.asarray(tv, dtype=float), np.asarray(rv, dtype=float)
        c = ci.get(path)
        if c is None:
            se = np.zeros_like(t)
        elif isinstance(c, dict):
            se = (np.asarray(c["hi"], dtype=float) - np.asarray(c["lo"], dtype=float)) / 3.92
        else:
            se = np.full_like(t, (c[1] - c[0]) / 3.92)
        within = np.abs(r - t) <= 5 * se + rel_floor * np.abs(t) + 1e-12
        good = bool(within.all()) if t.ndim == 0 else bool(within.mean() >= 0.99)
        ok &= good
        rows[path] = {"pass": good, "n": int(t.size), "worst_ratio": float(np.max(np.abs(r - t) / (5 * se + rel_floor * np.abs(t) + 1e-12)))}
    return {"pass": bool(ok), "parameters": rows, "rule": f"|refit - target| <= 5 SE + {rel_floor} * |target| + {abs_floor}"}


def round_trip(model: cm.ChannelModel, layout: Layout, refs: list, *, coverage: int, seed: int, bootstrap: int = 200,
               workers: int = 1, rel_floor: float = 0.08, calibration: dict | None = None, tally_opts: dict | None = None) -> dict:
    """M10: simulate ``refs`` from the fitted model, refit with the same pipeline and design, and compare every sequencing
    parameter with the model's own value (5 bootstrap SE of the refit, plus an alignment-ambiguity floor)."""
    cl = simulate_clusters(model, refs, coverage, seed)
    M, _rs = tally_matrix(zip(refs, cl), layout, workers=workers, **(tally_opts or {}))
    cal = dict(calibration) if calibration else None
    if cal is not None:
        cal.setdefault("workers", workers)
    fit = F.fit_tallies(M, layout, seed=seed, bootstrap=bootstrap, with_coverage=False, design=design_from_model(model),
                        calibration=cal)
    target = {k: v["value"] for k, v in model.doc["parameters"].items() if k in fit["values"] and not k.startswith("_")}
    return compare_params(target, {k: v for k, v in fit["values"].items() if not k.startswith("_")}, fit["ci95"], rel_floor)
