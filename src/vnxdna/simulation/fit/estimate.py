"""Estimators of the V7 fitting plan 3.3, as functions of a total count vector (see :mod:`~vnxdna.simulation.fit.tally`).

``choose_design`` makes the data-dependent choices once on the full data (homopolymer ``min_run``, position-profile bin
width, whether insertion runs are geometric, which event kinds get a 3-mer context); ``estimate`` then maps any total (the
full data or a bootstrap resample) to parameter values under that fixed design, so confidence intervals are conditional on
the design. All rates are per reference site and are those the simulator's joint per-position draw takes as input:

* ``rate`` of each kind is the *baseline*: events / sum over sites of the product of the multipliers (position profile,
  homopolymer, base, context), so a model with multipliers reproduces the observed event totals. The raw observed per-base
  rates are returned separately in ``observed``.
* a maximal run of deleted sites is one deletion event; the start rate and run length are recovered from the observed run-start
  rate and deleted fraction by inverting the simulator's overlapping-geometric-run process (``deletion_from_observed``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from vnxdna.simulation.fit.tally import Layout

MIN_BIN_EVENTS = 200           # profiles are not attempted below this many events in total
MAX_BINS = 30                  # position profiles are smooth (<= 30 equal bins): finer ones are confounded with the context/homopolymer
                               # multipliers wherever the reference sequence is the same in every strand (D03 primers)
GEOMETRIC_MIN = 1.02           # mean insertion run length above which runs are modelled as geometric (alignment merges give ~1.01)
GEOMETRIC_MIN_DEL = 1.10       # same for deletions: adjacent independent deletions and rate heterogeneity already give ~1.03-1.05
CONTEXT_LR = 100.0             # 2 * log-likelihood gain (about chi-square 63 d.f. at p = 0.002) needed to include a context
CONTEXT_PRIOR = 10.0           # pseudo-events shrinking a 3-mer's multiplier towards 1
#: kinds that may get a 3-mer context. Insertions and deletions are excluded: after leftmost normalisation an indel inside a
#: repeat is attributed to the repeat's first site, so its site-level context is not identifiable (a fitted context would
#: concentrate simulated events on those sites and the simulated reads, normalised again, would overshoot). The homopolymer
#: multiplier, which is a run-level quantity, is unaffected. The schema allows indel contexts; this fitter does not fill them.
CONTEXT_KINDS = ("substitution",)
HETEROGENEITY_MIN = 0.01       # protocol 5.5 (A2.1): read_heterogeneity is used when the moment estimate of Var(m) exceeds this
HETEROGENEITY_FLOOR = 1e-6     # Var(m) is kept in [1e-6, 20]: shape in [0.05, 1e6], the schema range
HETEROGENEITY_CEIL = 20.0


@dataclass
class Design:
    min_run: int
    bins: dict = field(default_factory=dict)          # kind -> number of equal-width relative bins (None = no profile)
    ins_geometric: bool = False
    del_geometric: bool = False
    context: tuple = ()                                 # kinds ("substitution", "insertion", "deletion") with a 3-mer context
    use_hp: bool = True
    heterogeneity: bool = False                         # protocol 5.5 A2.1: per-read gamma rate multiplier


def hp_sub_fixed(d: Design) -> bool:
    """True if the homopolymer substitution multiplier is fixed at 1 under design ``d``. With a substitution 3-mer context
    and min_run <= 2, a site lies in a homopolymer exactly when a neighbour equals its base (edges aside), so the in-run
    flag is a function of the centred 3-mer: the multiplier and the context are confounded and only their product is
    identifiable. The context then carries the homopolymer effect."""
    return "substitution" in d.context and d.min_run <= 2


def _div(a, b):
    return a / b if b else 0.0


def deletion_survival(d: float, m: float, kmax: int = 6000) -> float:
    """P(a site is not deleted) = prod_{k>=0} (1 - d q^k), q = 1 - 1/m (starts k sites earlier whose run is longer than k)."""
    q = 1.0 - 1.0 / m
    k = np.arange(kmax)
    return float(np.exp(np.sum(np.log1p(-d * q ** k))))


def deletion_from_observed(r_start: float, f_del: float) -> tuple[float, float]:
    """(start rate d, mean run length m) of the simulator's deletion process from the observed rate of maximal deletion runs
    ``r_start`` and deleted fraction ``f_del``. Observed: f = 1 - A, r_start = d A with A = survival(d, m); hence
    d = r_start / (1 - f) and m is the root of survival(d, m) = 1 - f (survival decreases in m). m = 1 if f <= d."""
    if f_del <= 0 or r_start <= 0:
        return 0.0, 1.0
    f_del = min(f_del, 0.95)
    d = min(r_start / (1.0 - f_del), 0.999)
    target = 1.0 - f_del
    if deletion_survival(d, 1.0) <= target + 1e-12:
        return d, 1.0
    lo, hi = 1.0, 2.0
    while deletion_survival(d, hi) > target and hi < 5000:
        lo, hi = hi, hi * 2
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if deletion_survival(d, mid) > target:
            lo = mid
        else:
            hi = mid
    return d, 0.5 * (lo + hi)


def _profile_counts(counts: np.ndarray, B: int) -> np.ndarray:
    L = counts.size
    return counts.reshape(B, L // B).sum(axis=1)


def _divisors(L: int) -> list[int]:
    return [b for b in range(1, L + 1) if L % b == 0]


def _bins_for(counts: np.ndarray) -> int | None:
    """Number of equal-width relative bins (a divisor of L, 1 = no profile) minimising the Poisson BIC of per-bin rates;
    None if the flat profile wins. Replaces the plan's "merge into 5-nt bins below 200 events" with a significance rule."""
    L = counts.size
    total = float(counts.sum())
    if total < MIN_BIN_EVENTS:
        return None
    best, best_bic = 1, np.inf
    for B in (b for b in _divisors(L) if b <= MAX_BINS):
        c = _profile_counts(counts.astype(float), B)
        s = total / B                                   # expected events per bin under a flat profile (equal sites per bin)
        ok = c > 0
        ll = float(np.sum(c[ok] * np.log(c[ok] / s)))   # log-likelihood gain over the flat profile
        bic = -2.0 * ll + (B - 1) * np.log(total)
        if bic < best_bic - 1e-9:
            best, best_bic = B, bic
    return best if best > 1 else None


def _poisson_ll(E: np.ndarray, S: np.ndarray) -> float:
    E = np.asarray(E, dtype=float)
    S = np.asarray(S, dtype=float)
    ok = (E > 0) & (S > 0)
    return float(np.sum(E[ok] * (np.log(E[ok] / S[ok]) - 1.0)))


def _kind_arrays(layout: Layout, T: np.ndarray):
    return {"substitution": (layout.get(T, "pos_sub"), layout.ctx(T, "ctx_sub")),
            "insertion": (layout.get(T, "pos_ins"), layout.ctx(T, "ctx_ins")),
            "deletion": (layout.get(T, "pos_delstart"), layout.ctx(T, "ctx_del"))}


def hp_choice(layout: Layout, T: np.ndarray) -> int:
    """min_run in layout.minruns maximising the Poisson log-likelihood of in/out-of-run rates for indels and substitutions."""
    sites = layout.ctx(T, "ctx_sites").sum(axis=1)             # (M, 2)
    ev = {k: v[1].sum(axis=1) for k, v in _kind_arrays(layout, T).items()}
    indel = ev["insertion"] + ev["deletion"]
    best, best_ll = layout.minruns[1] if len(layout.minruns) > 1 else layout.minruns[0], -np.inf
    for mi, m in enumerate(layout.minruns):
        ll = _poisson_ll(indel[mi], sites[mi]) + _poisson_ll(ev["substitution"][mi], sites[mi])
        if ll > best_ll + 1e-9:
            best, best_ll = m, ll
    return best


def _tables(layout: Layout, T: np.ndarray, mi: int, d_eff_over_obs: float = 1.0) -> tuple[np.ndarray, dict]:
    """(S, O): sites (64, 2) and event counts per kind (64, 2) at min_run index mi; (3-mer, in-run flag). Deletion events are
    the run starts, rescaled so that their total is the effective start count of the simulator's process."""
    S = layout.ctx(T, "ctx_sites")[mi].astype(float)
    O = {"substitution": layout.ctx(T, "ctx_sub")[mi].astype(float), "insertion": layout.ctx(T, "ctx_ins")[mi].astype(float),
         "deletion": layout.ctx(T, "ctx_del")[mi].astype(float) * d_eff_over_obs}
    return S, O


def _ipf(S: np.ndarray, O: dict, fm: dict, ctx_on: set, use_hp: bool, iters: int = 200,
         fixed_hp: frozenset = frozenset()) -> dict:
    """Joint Poisson maximum likelihood of  mu[kind][k, h] = r_kind * fm_kind[k] * ctx_kind[k] * hp_group^h * S[k, h]  by
    iterative proportional fitting: baselines r_kind, per-3-mer multipliers ctx_kind (shrunk by CONTEXT_PRIOR pseudo-events,
    only for kinds in ``ctx_on``) and one in-homopolymer multiplier per group (insertion and deletion share the indel
    multiplier). The fixed point reproduces the observed in-run and out-of-run totals of every group. Groups in ``fixed_hp``
    keep the multiplier 1 (see :func:`hp_sub_fixed`)."""
    kinds = list(O)
    group = {"substitution": "substitution", "insertion": "indel", "deletion": "indel"}
    ctx = {k: np.ones(64) for k in kinds}
    hp = {"substitution": 1.0, "indel": 1.0}
    r = {k: _div(O[k].sum(), (S.sum(axis=1) * fm[k]).sum()) for k in kinds}
    for _ in range(iters):
        old = (dict(r), {k: v.copy() for k, v in ctx.items()}, dict(hp))
        for k in kinds:
            h = np.array([1.0, hp[group[k]]])
            r[k] = _div(O[k].sum(), float((S * fm[k][:, None] * ctx[k][:, None] * h[None, :]).sum()))
            if k in ctx_on:
                h = np.array([1.0, hp[group[k]]])
                ek1 = r[k] * (S * fm[k][:, None] * h[None, :]).sum(axis=1)
                ctx[k] = (O[k].sum(axis=1) + CONTEXT_PRIOR) / (ek1 + CONTEXT_PRIOR)
        if use_hp:
            for g in ("substitution", "indel"):
                if g in fixed_hp:
                    continue
                num = sum(O[k][:, 1].sum() for k in kinds if group[k] == g)
                den = sum(r[k] * (S[:, 1] * fm[k] * ctx[k]).sum() for k in kinds if group[k] == g)
                hp[g] = _div(num, den) if num > 0 and den > 0 else 1.0
        delta = max(abs(r[k] - old[0][k]) / max(r[k], 1e-12) for k in kinds) + max(abs(hp[g] - old[2][g]) for g in hp) \
            + max(float(np.max(np.abs(ctx[k] - old[1][k]))) for k in kinds)
        if delta < 1e-10:
            break
    # scale: the site-weighted mean of the context multipliers is 1 and r is the baseline of the unit-multiplier site
    for k in kinds:
        if k in ctx_on:
            h = np.array([1.0, hp[group[k]]])
            w = (S * fm[k][:, None] * h[None, :]).sum(axis=1)
            c = float((w * ctx[k]).sum() / w.sum()) if w.sum() else 1.0
            ctx[k] = ctx[k] / c
            r[k] = r[k] * c
    mu = {}
    for k in kinds:
        h = np.array([1.0, hp[group[k]]])
        mu[k] = r[k] * S * fm[k][:, None] * ctx[k][:, None] * h[None, :]
    return {"r": r, "ctx": ctx, "hp": hp, "mu": mu}


def _loglik(O: np.ndarray, mu: np.ndarray) -> float:
    ok = (O > 0) & (mu > 0)
    return float(np.sum(O[ok] * np.log(mu[ok])) - mu.sum())


def _basics(layout: Layout, T: np.ndarray, mi: int):
    n = float(layout.get(T, "n_reads")[..., 0])
    nb = n * layout.L
    kinds = _kind_arrays(layout, T)
    O = {k: float(v[0].sum()) for k, v in kinds.items()}
    f_del = _div(float(layout.get(T, "pos_del").sum()), nb)
    d_eff, m_del = deletion_from_observed(_div(O["deletion"], nb), f_del)
    scale = _div(d_eff * nb, O["deletion"])
    return n, nb, O, f_del, d_eff, m_del, scale


def _from_mult(layout: Layout, T: np.ndarray, mi: int, nb: float, O_sub: float):
    mat = layout.get(T, "sub_matrix").reshape(4, 4).astype(float)
    sites_b = layout.ctx(T, "ctx_sites")[mi].reshape(4, 16, 2).sum(axis=(1, 2))
    row = mat.sum(axis=1)
    r_sub = _div(O_sub, nb)
    return np.array([(_div(row[i], sites_b[i]) / r_sub) if r_sub and sites_b[i] else 1.0 for i in range(4)]), mat, row


def _fm(from_mult: np.ndarray, with_ctx: bool) -> dict:
    centre = (np.arange(64) // 4) % 4
    return {"substitution": np.ones(64) if with_ctx else from_mult[centre], "insertion": np.ones(64), "deletion": np.ones(64)}


def choose_design(layout: Layout, T: np.ndarray) -> Design:
    kinds = _kind_arrays(layout, T)
    d = Design(min_run=hp_choice(layout, T))
    for kind, (pos, _ctx) in kinds.items():
        d.bins[kind] = _bins_for(pos)
    runs = layout.get(T, "ins_runs")
    d.ins_geometric = _mean_run(runs) > GEOMETRIC_MIN
    mi = layout.minruns.index(d.min_run)
    n, nb, O, f_del, d_eff, m_del, scale = _basics(layout, T, mi)
    d.del_geometric = m_del > GEOMETRIC_MIN_DEL
    S, Ot = _tables(layout, T, mi, scale)
    fmv, _mat, _row = _from_mult(layout, T, mi, nb, O["substitution"])
    base = _ipf(S, Ot, _fm(fmv, False), set(), True)
    full = _ipf(S, Ot, _fm(fmv, True), set(CONTEXT_KINDS), True,
                fixed_hp=frozenset({"substitution"}) if d.min_run <= 2 else frozenset())
    ctx = []
    for kind in CONTEXT_KINDS:
        lr = 2.0 * (_loglik(Ot[kind], full["mu"][kind]) - _loglik(Ot[kind], base["mu"][kind]))
        if lr >= CONTEXT_LR and Ot[kind].sum() >= 500:
            ctx.append(kind)
    d.context = tuple(ctx)
    d.heterogeneity = heterogeneity_moment(layout, T) > HETEROGENEITY_MIN
    return d


def edit_moments(layout: Layout, T: np.ndarray) -> tuple[float, float]:
    """(mean, variance) of the per-read unit-cost edit distance over the tallied reads of the totals ``T``."""
    n = float(layout.get(T, "ed_n")[0])
    if n <= 1:
        return 0.0, 0.0
    m = float(layout.get(T, "ed_sum")[0]) / n
    return m, max(0.0, float(layout.get(T, "ed_sq")[0]) / n - m * m)


def independent_edit_variance(layout: Layout, T: np.ndarray) -> float:
    """Per-read edit-distance variance if every read had the same rates: the compound-Poisson variance of the observed
    events (substituted sites, insertion runs and deletion runs, each contributing its length squared) per read."""
    n = float(layout.get(T, "ed_n")[0])
    if n <= 0:
        return 0.0
    lens2 = np.arange(1, layout.max_run + 1, dtype=float) ** 2
    s2 = (float(layout.get(T, "pos_sub").sum()) + float(layout.get(T, "ins_runs") @ lens2)
          + float(layout.get(T, "del_runs") @ lens2))
    return s2 / n


def heterogeneity_moment(layout: Layout, T: np.ndarray) -> float:
    """Moment estimator of Var(m) of the per-read rate multiplier (protocol 5.5, A2.1): max(0, var - var0) / mean^2 with var0
    the variance without heterogeneity (:func:`independent_edit_variance`); the simulation calibration refines it."""
    m, v = edit_moments(layout, T)
    return max(0.0, v - independent_edit_variance(layout, T)) / (m * m) if m > 0 else 0.0


def _mean_run(hist: np.ndarray) -> float:
    n = hist.sum()
    return float((hist * np.arange(1, hist.size + 1)).sum() / n) if n else 1.0


def estimate(layout: Layout, T: np.ndarray, d: Design) -> dict:
    """Parameter values (dotted /2 paths) of the sequencing stage from the totals ``T`` under design ``d``."""
    mi = layout.minruns.index(d.min_run)
    n, nb, O, f_del, d_eff, m_del, scale = _basics(layout, T, mi)
    kinds = _kind_arrays(layout, T)
    out: dict = {}
    r_ins_bases = _div(float(layout.get(T, "ins_base").sum()), nb)
    observed = {"substitution": _div(O["substitution"], nb), "insertion_events": _div(O["insertion"], nb),
                "insertion_bases": r_ins_bases, "deletion_runs": _div(O["deletion"], nb), "deletion_bases": f_del}
    fmv, mat, row = _from_mult(layout, T, mi, nb, O["substitution"])
    matrix = np.zeros((4, 4))
    for i in range(4):
        matrix[i] = mat[i] / row[i] if row[i] > 0 else np.where(np.arange(4) == i, 0.0, 1.0 / 3.0)
    S, Ot = _tables(layout, T, mi, scale)
    fit = _ipf(S, Ot, _fm(fmv, "substitution" in d.context), set(d.context), d.use_hp,
               fixed_hp=frozenset({"substitution"}) if hp_sub_fixed(d) else frozenset())
    ins_mean = _mean_run(layout.get(T, "ins_runs"))
    for kind, (pos, _ctx) in kinds.items():
        B = d.bins.get(kind)
        if B:
            c = _profile_counts(pos.astype(float), B)
            out[f"sequencing.position_profile.{kind}"] = (c / c.mean()).tolist() if c.mean() > 0 else None
        else:
            out[f"sequencing.position_profile.{kind}"] = None
    out["sequencing.substitution.rate"] = fit["r"]["substitution"]
    out["sequencing.substitution.matrix"] = matrix.tolist()
    out["sequencing.substitution.from_multipliers"] = (np.ones(4) if "substitution" in d.context else fmv).tolist()
    out["sequencing.insertion.rate"] = fit["r"]["insertion"]
    ib = layout.get(T, "ins_base").astype(float)
    out["sequencing.insertion.base_weights"] = (ib / ib.sum()).tolist() if ib.sum() > 0 else None
    out["sequencing.insertion.run_length.mean"] = ins_mean if d.ins_geometric else 1.0
    out["sequencing.deletion.rate"] = fit["r"]["deletion"]
    out["sequencing.deletion.run_length.mean"] = m_del if d.del_geometric else 1.0
    out["sequencing.homopolymer.min_run"] = d.min_run
    out["sequencing.homopolymer.indel_multiplier"] = fit["hp"]["indel"]
    out["sequencing.homopolymer.substitution_multiplier"] = fit["hp"]["substitution"]
    for kind in ("substitution", "insertion", "deletion"):
        out[f"sequencing.context.{kind}"] = fit["ctx"][kind].tolist() if kind in d.context else None
    out["sequencing.read_heterogeneity.shape"] = (1.0 / min(max(heterogeneity_moment(layout, T), HETEROGENEITY_FLOOR), HETEROGENEITY_CEIL)
                                                  if d.heterogeneity else None)
    n00, n01, n10, n11 = layout.get(T, "corr").astype(float)
    if layout.quality:
        out.update(estimate_quality(layout, T))
    out["_observed"] = observed
    out["_stats"] = {"reads": n, "sites": nb, "end_insertion_events": float(layout.get(T, "end_ins_events").sum()),
                     "deletion_runs_hist": layout.get(T, "del_runs").tolist(),
                     "insertion_runs_hist": layout.get(T, "ins_runs").tolist(),
                     "ins_run_share_gt1": _div(float(layout.get(T, "ins_runs")[1:].sum()), float(layout.get(T, "ins_runs").sum())),
                     "deletion_run_mean_observed": _div(float(layout.get(T, "pos_del").sum()), O["deletion"]),
                     "deletion_run_mean_geometric_fit": m_del,
                     "edit_distance_mean_var": list(edit_moments(layout, T)),
                     "p_event_given_event": _div(n11, n11 + n10), "p_event_given_no_event": _div(n01, n01 + n00),
                     "excluded_reads": float(layout.get(T, "excluded").sum())}
    return out


def estimate_quality(layout: Layout, T: np.ndarray) -> dict:
    """Quality calibration of the simulator's model from FASTQ tallies (plan 3.3): ``correct`` / ``error`` = median Phred
    on correct / erroneous bases, ``informative`` = max(0, P(Q <= tau | error) - P(Q <= tau | correct)) with tau the midpoint,
    ``sd`` = SD of correct bases net of the cycle slope, ``position_slope`` = Phred units lost per base: minus the least-squares slope of the mean
    Phred of correct bases on the read cycle (cycles 0..L-1)."""
    qc = layout.get(T, "q_correct").astype(float)
    qe = layout.get(T, "q_error").astype(float)
    v = np.arange(qc.size, dtype=float)

    def median(h):
        c = np.cumsum(h)
        return float(np.searchsorted(c, 0.5 * c[-1])) if c[-1] > 0 else 0.0
    correct, error0 = median(qc), median(qe)
    tau = int((correct + error0) // 2)
    low = qe[:tau + 1]
    # refinement of the plan's definition: the simulator draws `error` only for the informative share of errors, so it is
    # the median over the erroneous bases at or below the midpoint (the plan's plain median mixes in uninformative errors)
    error = median(low) if low.sum() > 0 else error0
    pc = float(qc[:tau + 1].sum() / qc.sum()) if qc.sum() else 0.0
    pe = float(qe[:tau + 1].sum() / qe.sum()) if qe.sum() else 0.0
    mc = float((qc * v).sum() / qc.sum()) if qc.sum() else 0.0
    var_c = float((qc * (v - mc) ** 2).sum() / qc.sum()) if qc.sum() else 0.0
    qs, qn = layout.get(T, "q_cycle_sum").astype(float), layout.get(T, "q_cycle_n").astype(float)
    cyc = np.arange(qs.size)[:layout.L]
    w = qn[:layout.L]
    ok = w > 0
    slope, xbar = 0.0, 0.0
    if ok.sum() > 2:
        y = qs[:layout.L][ok] / w[ok]
        x = cyc[ok].astype(float)
        xm, ym = np.average(x, weights=w[ok]), np.average(y, weights=w[ok])
        xbar = float(xm)
        slope = float(np.sum(w[ok] * (x - xm) * (y - ym)) / max(np.sum(w[ok] * (x - xm) ** 2), 1e-12))
    # sd: spread of correct bases around their mean, minus the part explained by the cycle slope (the simulator adds the slope
    # and the Gaussian noise separately)
    sd = float(np.sqrt(max(0.0, var_c - slope ** 2 * (layout.L ** 2 - 1) / 12.0)))
    shift = -slope * xbar                  # slope applies from the first cycle: report first-cycle values
    correct, error = float(np.clip(round(correct + shift), 0, 93)), float(np.clip(round(error + shift), 0, 93))
    return {"sequencing.quality.correct": int(correct), "sequencing.quality.error": int(error),
            "sequencing.quality.informative": max(0.0, pe - pc), "sequencing.quality.sd": sd,
            "sequencing.quality.position_slope": -slope}
