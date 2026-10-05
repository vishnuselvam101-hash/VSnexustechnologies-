"""Coverage models, bootstrap over references and the fit driver (V7 fitting plan 3.3, protocol 5).

Everything here is a deterministic function of the per-reference count matrix ``M`` (R x K), the layout and the seed: the
bootstrap resamples references (never reads), replicate b uses ``default_rng([seed, b])``, and replicate totals are exact
integer sums, so the output is identical for any number of workers (the workers only compute the rows of ``M``).
"""
from __future__ import annotations

import math

import numpy as np

from vnxdna.simulation.fit import estimate as est
from vnxdna.simulation.fit.tally import Layout

BOOTSTRAP = 200
SHAPE_MAX = 1e6          # NB shape beyond this is Poisson for all purposes


# ================================================================================================================ numerics
def nelder_mead(f, x0, step=0.5, iters=400, tol=1e-9):
    """A small deterministic Nelder-Mead minimiser (no SciPy)."""
    x0 = np.asarray(x0, dtype=float)
    n = x0.size
    pts = [x0] + [x0 + step * np.eye(n)[i] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol * (1 + abs(vals[0])):
            break
        c = np.mean(pts[:-1], axis=0)
        xr = c + (c - pts[-1])
        fr = f(xr)
        if fr < vals[0]:
            xe = c + 2 * (c - pts[-1])
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = c + 0.5 * ((xr if fr < vals[-1] else pts[-1]) - c)
            fc = f(xc)
            if fc < min(fr, vals[-1]):
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0]] + [pts[0] + 0.5 * (p - pts[0]) for p in pts[1:]]
                vals = [vals[0]] + [f(p) for p in pts[1:]]
    i = int(np.argmin(vals))
    return pts[i], vals[i]


_GH_X, _GH_W = np.polynomial.hermite.hermgauss(40)


def _lgamma(n: np.ndarray) -> np.ndarray:
    return np.array([math.lgamma(k + 1.0) for k in n])


def _logpmf(family: str, n: np.ndarray, mu: float, shape: float) -> np.ndarray:
    lg = _lgamma(n)
    if family == "poisson":
        return n * math.log(mu) - mu - lg
    if family == "negative-binomial":
        k = shape
        return (np.array([math.lgamma(x + k) for x in n]) - math.lgamma(k) - lg + k * math.log(k / (k + mu))
                + n * math.log(mu / (k + mu)))
    if family == "lognormal":
        lam = mu * np.exp(shape * math.sqrt(2.0) * _GH_X - shape * shape / 2.0)            # (40,)
        lp = n[None, :] * np.log(lam)[:, None] - lam[:, None] - lg[None, :]
        w = np.log(_GH_W / math.sqrt(math.pi))[:, None]
        mx = (lp + w).max(axis=0)
        return mx + np.log(np.exp(lp + w - mx).sum(axis=0))
    raise ValueError(family)


def _ll(family: str, zi: bool, hist: np.ndarray, x: np.ndarray) -> float:
    n = np.arange(hist.size, dtype=float)
    mu = math.exp(x[0])
    shape = min(math.exp(x[1]), SHAPE_MAX) if family != "poisson" else 0.0
    pi = 1.0 / (1.0 + math.exp(-x[-1])) if zi else 0.0
    lp = _logpmf(family, n, mu, shape)
    p = np.exp(lp)
    if zi:
        p = (1.0 - pi) * p
        p[0] += pi
    with np.errstate(divide="ignore"):
        return float(np.sum(hist * np.log(np.maximum(p, 1e-300))))


def fit_family(hist: np.ndarray, family: str, zi: bool, start: dict | None = None) -> dict:
    """ML fit of one coverage family (with or without a zero-inflation share) to a histogram of reads per reference."""
    n = np.arange(hist.size)
    total = hist.sum()
    mean = float((hist * n).sum() / total)
    var = float((hist * (n - mean) ** 2).sum() / total)
    x0 = [math.log(max(mean, 1e-3))]
    if family == "negative-binomial":
        x0.append(math.log(max(mean * mean / max(var - mean, 1e-3), 0.05)))
    elif family == "lognormal":
        x0.append(math.log(max(math.sqrt(max(math.log1p(max(var - mean, 0.0) / max(mean * mean, 1e-9)), 1e-4)), 0.02)))
    if zi:
        x0.append(-3.0)
    if start:
        x0 = list(start["x"])
    if family == "poisson" and not zi:
        mu = mean
        ll = _ll(family, zi, hist, np.array([math.log(mu)]))
        return {"family": family, "zi": zi, "mean": mu, "shape": None, "pi": 0.0, "ll": ll, "k": 1, "x": [math.log(mu)]}
    best_x, best_v = np.asarray(x0, dtype=float), np.inf
    for step in (0.5, 0.1):
        x, v = nelder_mead(lambda p: -_ll(family, zi, hist, p), best_x, step=step, iters=300)
        if v < best_v:
            best_x, best_v = x, v
    shape = min(math.exp(best_x[1]), SHAPE_MAX) if family != "poisson" else None
    pi = 1.0 / (1.0 + math.exp(-best_x[-1])) if zi else 0.0
    return {"family": family, "zi": zi, "mean": math.exp(best_x[0]), "shape": shape, "pi": pi, "ll": -best_v,
            "k": len(best_x), "x": [float(v) for v in best_x]}


FAMILIES = ("poisson", "negative-binomial", "lognormal")


def fit_coverage(hist: np.ndarray, choice: tuple | None = None) -> dict:
    """Fit Poisson, NB and Poisson-lognormal, each with and without zero inflation, and pick by AIC (or refit ``choice`` =
    (family, zi)). Returns the /2 coverage fields and the AIC table."""
    hist = np.asarray(hist, dtype=float)
    if choice is not None:
        fits = [fit_family(hist, choice[0], choice[1])]
    else:
        fits = [fit_family(hist, f, zi) for f in FAMILIES for zi in (False, True)]
    for f in fits:
        f["aic"] = 2 * f["k"] - 2 * f["ll"]
    best = min(fits, key=lambda f: f["aic"])
    cov = {"model": best["family"], "mean": best["mean"], "dispersion": 5.0, "sigma": 0.0}
    if best["family"] == "negative-binomial":
        cov["dispersion"] = best["shape"]
    elif best["family"] == "lognormal":
        cov["sigma"] = best["shape"]
    return {"coverage": cov, "dropout": best["pi"], "choice": (best["family"], best["zi"]),
            "aic": {f"{f['family']}{'+zi' if f['zi'] else ''}": round(f["aic"], 3) for f in fits}}


def counts_hist(counts: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    nmax = int(counts.max()) if counts.size else 0
    return np.bincount(counts, weights=weights, minlength=nmax + 1).astype(float)


# ================================================================================================================ bootstrap
def bootstrap_weights(R: int, B: int, seed: int) -> np.ndarray:
    """(B, R) resample multiplicities over references; replicate b uses default_rng([seed, b])."""
    W = np.zeros((B, R), dtype=np.int32)
    for b in range(B):
        W[b] = np.bincount(np.random.default_rng([seed, b]).integers(0, R, R), minlength=R)
    return W


def bootstrap_totals(M: np.ndarray, W: np.ndarray, chunk: int = 2048) -> np.ndarray:
    """(B, K) replicate totals W @ M, exact (integer-valued float64 sums)."""
    out = np.zeros((W.shape[0], M.shape[1]), dtype=np.float64)
    for a in range(0, M.shape[0], chunk):
        out += W[:, a:a + chunk].astype(np.float64) @ M[a:a + chunk].astype(np.float64)
    return out


def _flat(v):
    return np.asarray(v, dtype=float).ravel()


def percentile_ci(values: list) -> list | dict | None:
    """95% percentile interval of replicate values: [lo, hi] for scalars, {'lo': [...], 'hi': [...]} for vectors."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    arr = np.asarray(vals, dtype=float)
    lo, hi = np.percentile(arr, 2.5, axis=0), np.percentile(arr, 97.5, axis=0)
    if arr.ndim == 1:
        return [float(lo), float(hi)]
    return {"lo": lo.tolist(), "hi": hi.tolist()}


def fit_tallies(M: np.ndarray, layout: Layout, *, seed: int, bootstrap: int = BOOTSTRAP, with_coverage: bool = True,
                design: est.Design | None = None, calibration: dict | None = None,
                coverage_counts: np.ndarray | None = None) -> dict:
    """Estimate every sequencing-stage parameter, coverage and dropout from the reference-by-count matrix ``M``, with
    bootstrap-over-references confidence intervals. Returns ``{'values', 'ci95', 'design', 'coverage', 'observed', 'stats'}``."""
    R = M.shape[0]
    T = M.sum(axis=0).astype(np.float64)
    design = design if design is not None else est.choose_design(layout, T)
    point = est.estimate(layout, T, design)
    assigned = layout.get(M, "n_reads")[:, 0] + layout.get(M, "excluded")[:, 0]
    if coverage_counts is not None:
        assigned = np.asarray(coverage_counts, dtype=np.int64)
    cov_point: dict | None = fit_coverage(counts_hist(assigned)) if with_coverage else None
    ci: dict = {}
    if bootstrap:
        W = bootstrap_weights(R, bootstrap, seed)
        totals = bootstrap_totals(M, W)
        reps = [est.estimate(layout, totals[b], design) for b in range(bootstrap)]
        for key in point:
            if key.startswith("_") or point[key] is None:
                continue
            ci[key] = percentile_ci([r[key] for r in reps])
        ci["_observed"] = {k: percentile_ci([r["_observed"][k] for r in reps]) for k in point["_observed"]}
        ci["_stats"] = {k: percentile_ci([r["_stats"][k] for r in reps]) for k in ("p_event_given_event", "p_event_given_no_event",
                                                                              "ins_run_share_gt1")}
        if with_coverage:
            assert cov_point is not None
            creps = []
            for b in range(bootstrap):
                h = counts_hist(assigned, W[b].astype(float))
                creps.append(fit_coverage(h, cov_point["choice"]))
            ci["coverage.mean"] = percentile_ci([c["coverage"]["mean"] for c in creps])
            ci["coverage.dispersion"] = percentile_ci([c["coverage"]["dispersion"] for c in creps])
            ci["coverage.sigma"] = percentile_ci([c["coverage"]["sigma"] for c in creps])
            ci["dropout"] = percentile_ci([c["dropout"] for c in creps])
    out = {"values": point, "ci95": ci, "design": design, "coverage": cov_point, "totals": T, "references": R,
           "assigned_reads": int(assigned.sum())}
    if calibration:
        from vnxdna.simulation.fit.calibrate import calibrate
        cal = calibrate(out, layout, real_T=T, seed=seed, **calibration)
        out["calibration"] = {k: cal[k] for k in ("factors", "trace", "raw_values", "target", "final_residual_ratios")}
        out["values"], out["ci95"] = cal["values"], cal["ci95"]
    return out
