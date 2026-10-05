"""P4-EXP-03: per-read IDS statistics of the Microsoft Clustered Nanopore Reads (CNR) dataset, read against the VNX-DNA
decoder's alignment assumptions. PUBLIC-DATA-DERIVED (and one clearly labelled i.i.d. extrapolation).

    python experiments/v6/phase4/cnr_ids.py --data <path to microsoft clustered-nanopore-reads-dataset> \
        --out experiments/v6/phase4/P4-EXP-03-cnr-ids

Dataset: Srinivasavaradhan, Gopi, Pfister, Yekhanin, "Trellis BMA", ISIT 2021 (arXiv:2107.06440); github.com/microsoft/
clustered-nanopore-reads-dataset, MIT licence. 10,000 centres of 110 nt (Twist synthesis, PCR, ONT MinION, clustered with
Rashtchian et al. 2017). Known caveat (dataset README, note of 2024-08-12): the centres were not generated uniformly at
random (long-range dependencies), so some clusters may be malformed. No quality scores are provided, so nothing about
quality-weighted consensus can be measured on it. VNX-DNA cannot decode these strands (they are not VNX frames); this
script only measures channel statistics that bear on the decoder's assumptions.

Per read: unit-cost global edit alignment to its cluster's centre (substitution = insertion = deletion = 1; ties broken
diagonal, then deletion, then insertion; an optimal alignment is not unique, so counts are of one optimal alignment).
Reads with edit distance > 30 % of 110 are counted as likely mis-clustered and excluded from the rate estimates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np

L = 110
MAX_LEN = 170
BANDS = (0, 3, 6, 10)
EXCLUDE_FRAC = 0.30
LUT = np.full(256, 4, dtype=np.uint8)
for _i, _c in enumerate(b"ACGT"):
    LUT[_c] = _i


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load(data: Path):
    centres = [ln.strip() for ln in (data / "Centers.txt").read_text().split("\n") if ln.strip()]
    clusters, cur = [], []
    started = False
    for ln in (data / "Clusters.txt").read_text().split("\n"):
        s = ln.strip()
        if s.startswith("="):
            if started:
                clusters.append(cur)
            cur, started = [], True
            continue
        if s:
            cur.append(s)
    if started:
        clusters.append(cur)
    return centres, clusters


def align_batch(cent: np.ndarray, reads: np.ndarray, lens: np.ndarray):
    """cent (B, L), reads (B, W) padded with 4, lens (B,) → per read (sub, ins, del, distance) and event positions."""
    B, W = reads.shape
    J = np.arange(W + 1)
    ptr = np.zeros((L + 1, B, W + 1), dtype=np.int8)            # 0 diag, 1 del (centre base missing), 2 ins
    prev = np.broadcast_to(J, (B, W + 1)).astype(np.int32).copy()
    ptr[0, :, 1:] = 2
    for i in range(1, L + 1):
        mism = (reads != cent[:, i - 1:i]).astype(np.int32)        # (B, W)
        diag = prev[:, :-1] + mism
        up = prev + 1
        cur = up.copy()
        p = np.ones((B, W + 1), dtype=np.int8)
        better = diag <= up[:, 1:]
        cur[:, 1:] = np.where(better, diag, up[:, 1:])
        p[:, 1:] = np.where(better, 0, 1)
        # insertions: cur'[j] = j + cummin_k<=j (cur[k] - k)
        ins = J + np.minimum.accumulate(cur - J, axis=1)
        take = ins < cur
        cur = np.where(take, ins, cur)
        p = np.where(take, 2, p)
        ptr[i] = p
        prev = cur
    dist = prev[np.arange(B), lens]
    # traceback, vectorised over reads
    i = np.full(B, L)
    j = lens.copy()
    sub = np.zeros(B, np.int64)
    nin = np.zeros(B, np.int64)
    nde = np.zeros(B, np.int64)
    pos_sub = np.zeros(L, np.int64)
    pos_ins = np.zeros(L + 1, np.int64)
    pos_del = np.zeros(L, np.int64)
    rows = np.arange(B)
    active = (i > 0) | (j > 0)
    while active.any():
        a = rows[active]
        op = ptr[i[a], a, j[a]]
        op = np.where(i[a] == 0, 2, np.where(j[a] == 0, 1, op))
        d = a[op == 0]
        if d.size:
            mm = reads[d, j[d] - 1] != cent[d, i[d] - 1]
            sub[d[mm]] += 1
            np.add.at(pos_sub, i[d[mm]] - 1, 1)
            i[d] -= 1
            j[d] -= 1
        de = a[op == 1]
        if de.size:
            nde[de] += 1
            np.add.at(pos_del, i[de] - 1, 1)
            i[de] -= 1
        n_ = a[op == 2]
        if n_.size:
            nin[n_] += 1
            np.add.at(pos_ins, i[n_], 1)
            j[n_] -= 1
        active = (i > 0) | (j > 0)
    assert (sub + nin + nde == dist).all()
    return sub, nin, nde, dist, pos_sub, pos_ins, pos_del


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=4096)
    a = ap.parse_args(argv)
    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    centres, clusters = load(data)
    assert len(centres) == 10000 and all(len(c) == L for c in centres)
    sizes = np.array([len(c) for c in clusters])
    cid = np.concatenate([np.full(len(c), k) for k, c in enumerate(clusters)]).astype(np.int64)
    seqs = [s for c in clusters for s in c]
    lens_all = np.array([len(s) for s in seqs], dtype=np.int64)
    usable = lens_all <= MAX_LEN
    cent_codes = np.stack([LUT[np.frombuffer(c.encode(), np.uint8)] for c in centres])
    res = {k: np.zeros(len(seqs), np.int64) for k in ("sub", "ins", "del", "dist")}
    pos = {"sub": np.zeros(L, np.int64), "ins": np.zeros(L + 1, np.int64), "del": np.zeros(L, np.int64)}
    idx = np.flatnonzero(usable)
    for s0 in range(0, idx.size, a.batch):
        sel = idx[s0:s0 + a.batch]
        W = int(lens_all[sel].max())
        R = np.full((sel.size, W), 4, dtype=np.uint8)
        for r, k in enumerate(sel.tolist()):
            R[r, : lens_all[k]] = LUT[np.frombuffer(seqs[k].encode(), np.uint8)]
        sub, nin, nde, dist, ps, pi, pd = align_batch(cent_codes[cid[sel]], R, lens_all[sel])
        res["sub"][sel], res["ins"][sel], res["del"][sel], res["dist"][sel] = sub, nin, nde, dist
    # positional profile only from included reads: recompute on them (cheap relative to clarity)
    incl = usable & (res["dist"] <= EXCLUDE_FRAC * L)
    sel_all = np.flatnonzero(incl)
    for s0 in range(0, sel_all.size, a.batch):
        sel = sel_all[s0:s0 + a.batch]
        W = int(lens_all[sel].max())
        R = np.full((sel.size, W), 4, dtype=np.uint8)
        for r, k in enumerate(sel.tolist()):
            R[r, : lens_all[k]] = LUT[np.frombuffer(seqs[k].encode(), np.uint8)]
        _, _, _, _, ps, pi, pd = align_batch(cent_codes[cid[sel]], R, lens_all[sel])
        pos["sub"] += ps
        pos["ins"] += pi
        pos["del"] += pd
    n_inc = int(incl.sum())
    bases = n_inc * L
    drift = lens_all[incl] - L
    per_read = res["dist"][incl] / L
    rates = {k: res[k][incl].sum() / bases for k in ("sub", "ins", "del")}
    # positional vote over exact-length reads per cluster (what a marker-free frame can use without position information)
    exact_ok = np.zeros(len(clusters), dtype=bool)
    has_exact = np.zeros(len(clusters), dtype=bool)
    pos_err = 0
    pos_n = 0
    start = np.concatenate([[0], np.cumsum(sizes)])
    for k in range(len(clusters)):
        rr = [LUT[np.frombuffer(s.encode(), np.uint8)] for s in clusters[k] if len(s) == L]
        if not rr:
            continue
        has_exact[k] = True
        M = np.stack(rr)
        votes = np.stack([(M == b).sum(axis=0) for b in range(4)], axis=1)
        best = votes.argmax(axis=1)
        err = int((best != cent_codes[k]).sum())
        exact_ok[k] = err == 0
        pos_err += err
        pos_n += L
    _ = start
    # labelled extrapolation: i.i.d. drift of a 313-nt strand under the measured rates
    rng = np.random.default_rng(20261005)
    T = 313
    sims = 200_000
    ins_n = rng.binomial(T, rates["ins"], sims)
    del_n = rng.binomial(T, rates["del"], sims)
    d313 = ins_n - del_n
    q = lambda x, p: float(np.percentile(x, p))  # noqa: E731
    bins = np.array_split(np.arange(L), 11)
    doc = {
        "experiment": "P4-EXP-03-cnr-ids", "classification": "PUBLIC-DATA-DERIVED",
        "dataset": {"name": "Clustered Nanopore Reads (CNR)", "source": "github.com/microsoft/clustered-nanopore-reads-dataset",
                    "licence": "MIT", "paper": "arXiv:2107.06440 (ISIT 2021)",
                    "caveat": "centres not uniformly random (dataset README, 2024-08-12); some clusters may be malformed",
                    "files": {"Centers.txt": sha256(data / "Centers.txt"), "Clusters.txt": sha256(data / "Clusters.txt")}},
        "method": {"alignment": "unit-cost global edit distance to the cluster centre, one optimal alignment (ties: diagonal, "
                                "deletion, insertion)", "excluded_if_distance_over": f"{EXCLUDE_FRAC} * {L}",
                   "max_read_length_aligned": MAX_LEN},
        "clusters": {"count": len(clusters), "empty": int((sizes == 0).sum()), "mean_size": round(float(sizes.mean()), 3),
                     "median_size": float(np.median(sizes)), "p10": q(sizes, 10), "p90": q(sizes, 90),
                     "max": int(sizes.max())},
        "reads": {"total": len(seqs), "longer_than_max": int((~usable).sum()), "likely_misclustered": int((usable & ~incl).sum()),
                  "included": n_inc},
        "per_base_rates": {k: round(float(v), 5) for k, v in rates.items()},
        "total_error_rate": round(float(sum(rates.values())), 5),
        "per_read_error": {"median": round(float(np.median(per_read)), 4), "p90": round(q(per_read, 90), 4),
                           "share_error_free": round(float((res["dist"][incl] == 0).mean()), 4),
                           "share_without_indel": round(float(((res["ins"] + res["del"])[incl] == 0).mean()), 4)},
        "length_drift_110nt": {"mean": round(float(drift.mean()), 3), "p05": q(drift, 5), "p95": q(drift, 95),
                               **{f"share_abs_le_{b}": round(float((np.abs(drift) <= b).mean()), 4) for b in BANDS}},
        "positional_profile_11_bins": {k: [int(pos[k][b].sum()) for b in bins] for k in ("sub", "del")}
                                      | {"ins": [int(pos["ins"][b].sum()) for b in np.array_split(np.arange(L + 1), 11)]},
        "exact_length_positional_vote": {
            "clusters_with_exact_length_read": int(has_exact.sum()),
            "clusters_recovered_exactly": int(exact_ok.sum()),
            "share_of_all_clusters": round(float(exact_ok.sum() / len(clusters)), 4),
            "per_base_error_of_vote": round(pos_err / max(1, pos_n), 5),
            "meaning": "a position-wise vote over the reads of exactly 110 nt, i.e. what a layout without markers can use "
                       "when it has no position information inside the frame (VNX s184-like); not a VNX decode"},
        "extrapolation_313nt_iid": {
            "label": "EXTRAPOLATION: i.i.d. insertions and deletions at the measured per-base rates; real errors are "
                     "position- and context-dependent (see positional_profile)",
            "strand_nt": T, "simulated_strands": sims,
            **{f"share_abs_drift_le_{b}": round(float((np.abs(d313) <= b).mean()), 4) for b in BANDS}},
        "seconds": round(time.perf_counter() - t0, 1),
    }
    (out / "results.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: doc[k] for k in ("per_base_rates", "length_drift_110nt", "extrapolation_313nt_iid")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
