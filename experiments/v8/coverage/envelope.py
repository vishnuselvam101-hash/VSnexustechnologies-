"""V8.11 coverage envelope (SIMULATED; analytic + empirical).

Analytic part. For an outer row of n data symbols (strands) with parity M, a row decodes if at most M strands are lost
(erasures; an errored strand counted as lost, conservatively). With per-strand loss probability q, P(row) =
P(Binomial(n, q) <= M) and P(archive) = prod over rows. q = q_struct + (1 - q_struct) q_cons:
* q_struct = P(reads < 2) under the coverage law (no consensus from fewer than 2 reads; negative binomial with the matrix
  dispersion 4);
* q_cons = the empirical share of strands with >= 2 reads lost later (clustering, alignment, consensus, RS), from the matrix
  rows of the same channel, coverage and profile (cluster-stage decodes only).

Empirical part: EXACT rate of the matrix cell with its Wilson 95 % interval (10 seeds).

Classification, fixed before the matrix ran:
* SUPPORTED: analytic P(archive) >= 0.95 and the empirical Wilson lower bound >= 0.70 (at 10/10, Wilson lower is 0.72);
* NOT SUPPORTED: analytic P(archive) < 0.50 or the empirical Wilson upper bound < 0.50;
* MARGINALLY SUPPORTED: otherwise.
One successful seed never makes a coverage level supported.

Post-hoc correction (disclosed; the pre-specified result is kept next to it): the q_struct assumption (a strand needs >= 2
reads) does not hold where the decoder succeeds in pass 1 from single reads (the cluster stage does not run). For cells
where the cluster stage ran in fewer than half of the decodes, ``envelope`` uses q_struct = P(reads < 1). Those cells are
marked ``post_hoc: true`` and ``envelope_pre_specified`` keeps the classification of the original rule.

    PYTHONPATH=src python experiments/v8/coverage/envelope.py      # reads experiments/v8/matrix/results/matrix.jsonl
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MATRIX = ROOT / "experiments/v8/matrix/results/matrix.jsonl"
DISPERSION = 4.0
SIZE_ROWS = {"v4-balanced": None, "v7-lowcov": None}       # filled from the archive geometry


def nb_pmf(k: int, mean: float, disp: float) -> float:
    """Negative binomial P(X = k) with mean ``mean`` and size ``disp`` (variance mean + mean^2 / disp)."""
    p = disp / (disp + mean)
    return math.exp(math.lgamma(k + disp) - math.lgamma(disp) - math.lgamma(k + 1) + disp * math.log(p) + k * math.log1p(-p))


def q_struct(mean: float, disp: float = DISPERSION) -> dict:
    p0, p1 = nb_pmf(0, mean, disp), nb_pmf(1, mean, disp)
    return {"p_below_1": p0, "p_below_2": p0 + p1}


def binom_cdf(m: int, n: int, q: float) -> float:
    return float(sum(math.comb(n, k) * q ** k * (1 - q) ** (n - k) for k in range(m + 1)))


def row_sizes(profile: str) -> tuple[list, int]:
    """Data strands per outer row of the 20,000-byte matrix archive, and the parity M."""
    sys.path.insert(0, str(ROOT / "tests" / "nanopore"))
    import tempfile

    import nanofunnel as nf
    from collections import Counter
    from vnxdna.dnaenc.layout import KIND_DATA, PROFILES
    with tempfile.TemporaryDirectory() as tmp:
        arc = nf.build_archive(Path(tmp), 20_000, 83000, profile)
    rows = Counter(g for k, _t, g, _s in arc["keys"] if k == KIND_DATA)
    return [rows[g] for g in sorted(rows)], PROFILES[profile][2]


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    z = 1.959964
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def classify(p_analytic: float, lo: float, hi: float) -> str:
    if p_analytic >= 0.95 and lo >= 0.70:
        return "SUPPORTED"
    if p_analytic < 0.50 or hi < 0.50:
        return "NOT SUPPORTED"
    return "MARGINALLY SUPPORTED"


def main() -> int:
    rows = [json.loads(x) for x in MATRIX.read_text().splitlines()]
    geom = {p: row_sizes(p) for p in ("v4-balanced", "v7-lowcov")}
    cells: dict = {}
    for r in rows:
        cells.setdefault((r["channel"], r["coverage"], r["profile"]), []).append(r)
    out = {"label": "SIMULATED (analytic model + simulated decodes); no DNA was synthesised, stored or sequenced",
           "dispersion": DISPERSION, "geometry": {p: {"rows": len(g[0]), "data_strands_per_row": sorted(set(g[0])), "M": g[1]}
                                                  for p, g in geom.items()}, "cells": []}
    for (ch, cov, prof), rs in sorted(cells.items()):
        sizes, M = geom[prof]
        qs = q_struct(float(cov))
        cl = [r for r in rs if r.get("cluster_stage_ran") and r["taxonomy"].get("lost_by_category") is not None]
        late = ["clustering", "alignment", "consensus", "indel_placement", "substitution_correction"]
        if cl:
            lost_late = np.mean([sum(r["taxonomy"]["lost_by_category"].get(k, 0) for k in late) for r in cl])
            ge2 = np.mean([sum(sizes) - r["strands_below_2_reads"] * sum(sizes) / r["strands"] for r in cl])
            q_cons = float(lost_late / max(ge2, 1.0))
        else:
            q_cons = 0.0
        q = qs["p_below_2"] + (1 - qs["p_below_2"]) * q_cons
        p_arch = float(np.prod([binom_cdf(M, n, q) for n in sizes]))
        k = sum(r["outcome"] == "EXACT" for r in rs)
        lo, hi = wilson(k, len(rs))
        cluster_share = sum(bool(r.get("cluster_stage_ran")) for r in rs) / len(rs)
        pre = classify(p_arch, lo, hi)
        post_hoc = cluster_share < 0.5
        if post_hoc:
            q1 = qs["p_below_1"] + (1 - qs["p_below_1"]) * q_cons
            p_arch1 = float(np.prod([binom_cdf(M, n, q1) for n in sizes]))
        else:
            p_arch1 = p_arch
        out["cells"].append({"channel": ch, "coverage": cov, "profile": prof, "q_struct": qs, "q_consensus": round(q_cons, 5),
                             "q_total": round(q, 5), "p_archive_analytic": round(p_arch, 4), "exact": k, "n": len(rs),
                             "wilson95": [round(lo, 4), round(hi, 4)], "envelope_pre_specified": pre,
                             "cluster_stage_share": round(cluster_share, 2), "post_hoc": post_hoc,
                             "p_archive_analytic_single_read": round(p_arch1, 4) if post_hoc else None,
                             "envelope": classify(p_arch1, lo, hi),
                             "expected_reads_per_strand": float(cov)})
    p = HERE / "results" / "envelope.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    for c in out["cells"]:
        print(c["channel"], c["coverage"], c["profile"], c["envelope"], "(pre", c["envelope_pre_specified"] + ")" if c["post_hoc"] else "",
              c["p_archive_analytic"], f"{c['exact']}/{c['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
