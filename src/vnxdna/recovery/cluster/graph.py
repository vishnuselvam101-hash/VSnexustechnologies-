"""Candidate pairs, verification, components and refinement (V7_ARCHITECTURE §5.2 steps 2-5; NumPy reference).

* Candidates: reads are bucketed by (slot i, h_i); buckets holding more than ``bucket_cap`` reads are ignored (k-mers
  shared by many strands: markers, common header bytes, primers, low-entropy payloads). A candidate pair shares at
  least ``min_shared_slots`` retained buckets (default 2; the architecture states 1 — see below); its relative
  orientation is the majority XOR of the orientation bits over the shared slots (ties: same orientation).
* Verification: banded edit distance of the oriented pair; the edge is accepted iff distance ≤ θ · max(La, Lb).
  Pairs are processed by shared slots (descending), then (min index, max index); a pair whose reads are already in one
  component is skipped (it cannot change the components, so the components do not depend on this order).

``min_shared_slots = 2`` (deviation from V7_ARCHITECTURE §5.2 step 2, "at least one"): on a small nanopore-like pool
(EXPERIMENTAL, SIMULATED; 2,133 reads) 76 % of the candidate pairs shared exactly one slot and 2 % of those verified,
so verification time was dominated by them. THEORETICAL: two reads of one strand share Bin(32, 0.13) slots at ε ≈ 0.06
(architecture §5.2), P(≥ 2) ≈ 0.93, so a read of a strand with c reads finds no partner with probability ≈ 0.067^(c−1)
(0.0045 at c = 3).
* Components: union-find with orientation parity; the root is the smallest read index. Components are a function of
  the set of accepted edges only, so they do not depend on read order.
* Refinement: a component larger than ``max_cluster_reads`` is split by deterministic nearest-representative
  assignment (representatives chosen greedily in read-index order among reads farther than θ from every existing
  representative; each read joins its nearest representative, ties to the lower index); a second pass replaces each
  representative by its group's medoid and reassigns. A read farther than θ from every representative is left
  UNASSIGNED.

Every read gets an explicit assignment score 1 − d / max(La, Lb) of the edge (or representative distance) that placed
it; reads that no accepted edge reaches stay UNASSIGNED (label −1, score NaN) and are counted — a read is never put into
the nearest cluster without passing the threshold (directive §9, protocol §8.2).

Native kernels (``vnxdna.native.cluster``, selected by ``VNXDNA_CLUSTER_BACKEND``) run the candidate step and the
verification loop (same chunks of ``VERIFY_CHUNK`` pairs, same union-find) when available; the results, including
which counters exist, are identical to the reference functions ``candidate_pairs_reference`` and the reference loop.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from vnxdna.native import cluster as _nc
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster.editdist import banded_distance, revcomp

VERIFY_CHUNK = 512


@dataclass
class Clustering:
    labels: np.ndarray            # (n,) cluster ID (smallest read index of the cluster) or −1 = unassigned
    orient: np.ndarray            # (n,) 1 = reverse complement relative to the cluster's reference read
    score: np.ndarray             # (n,) float64 assignment score, NaN = unassigned
    counts: Counter = field(default_factory=Counter)
    budget: dict | None = None    # the work budget that stopped the stage, if any


def candidate_pairs(hashes: np.ndarray, orient: np.ndarray, bucket_cap: int, max_pairs: int, counts: Counter,
                    min_shared: int = 1) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict | None]:
    """Native or reference :func:`candidate_pairs_reference` (identical results and counters)."""
    if hashes.ndim == 2 and hashes.shape[1] >= 1 and hashes.shape[0] <= (1 << 31) and _nc.resolve_backend() == "native":
        a, b, rel, st = _nc.candidates(hashes, orient, bucket_cap, max_pairs, min_shared)
        if st[5]:
            counts["buckets_over_cap"] += int(st[0])
        if st[2]:
            counts["candidate_pairs_slots"] = int(st[1])
            return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.uint8), {
                "limit": "max_candidate_pairs", "allowed": max_pairs, "used": int(st[1]),
                "effect": "clustering stage stopped; no cluster frame produced"}
        if not st[6]:
            return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.uint8), None
        counts["candidate_pairs"] += int(st[3])
        counts["candidate_pairs_below_min_shared"] += int(st[4])
        return a, b, rel, None
    return candidate_pairs_reference(hashes, orient, bucket_cap, max_pairs, counts, min_shared)


def candidate_pairs_reference(hashes: np.ndarray, orient: np.ndarray, bucket_cap: int, max_pairs: int, counts: Counter,
                              min_shared: int = 1) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict | None]:
    """(a, b, relative orientation) with a < b for pairs sharing at least ``min_shared`` retained buckets, sorted by
    shared slots (descending), then (a, b); and the budget record if ``max_pairs`` was exceeded."""
    n, s = hashes.shape
    pa: list = []
    pb: list = []
    px: list = []
    total = 0
    for i in range(s):
        h = hashes[:, i]
        valid = orient[:, i] < 2
        idx = np.flatnonzero(valid)
        if idx.size < 2:
            continue
        order = idx[np.argsort(h[idx], kind="stable")]
        hv = h[order]
        cut = np.flatnonzero(hv[1:] != hv[:-1]) + 1
        starts = np.concatenate([[0], cut])
        ends = np.concatenate([cut, [order.size]])
        size = ends - starts
        counts["buckets_over_cap"] += int((size > bucket_cap).sum())
        for st, en in zip(starts[(size >= 2) & (size <= bucket_cap)].tolist(), ends[(size >= 2) & (size <= bucket_cap)].tolist()):
            mem = np.sort(order[st:en])
            m = mem.size
            iu, ju = np.triu_indices(m, 1)
            total += iu.size
            if total > max_pairs:
                counts["candidate_pairs_slots"] = total
                return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.uint8), {
                    "limit": "max_candidate_pairs", "allowed": max_pairs, "used": total,
                    "effect": "clustering stage stopped; no cluster frame produced"}
            pa.append(mem[iu])
            pb.append(mem[ju])
            px.append(orient[mem[iu], i] ^ orient[mem[ju], i])
    if not pa:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.uint8), None
    a = np.concatenate(pa).astype(np.int64)
    b = np.concatenate(pb).astype(np.int64)
    x = np.concatenate(px).astype(np.int64)
    key = a * n + b
    uk, inv = np.unique(key, return_inverse=True)
    shared = np.bincount(inv, minlength=uk.size)
    xs = np.bincount(inv, weights=x, minlength=uk.size)
    rel = (2 * xs > shared).astype(np.uint8)
    keep = shared >= min_shared
    counts["candidate_pairs"] += int(keep.sum())
    counts["candidate_pairs_below_min_shared"] += int((~keep).sum())
    uk, shared, rel = uk[keep], shared[keep], rel[keep]
    # verification order: most shared slots first, then (a, b); the components do not depend on it (a pair whose reads
    # are already connected cannot change them), it only lets more pairs be skipped
    order = np.lexsort((uk, -shared))
    uk, rel = uk[order], rel[order]
    return (uk // n).astype(np.int64), (uk % n).astype(np.int64), rel, None


class _UF:
    """Union-find with orientation parity; roots are the smallest index of their set."""

    def __init__(self, n: int):
        self.parent = np.arange(n, dtype=np.int64)
        self.par = np.zeros(n, dtype=np.uint8)

    def find(self, x: int) -> tuple[int, int]:
        path = []
        p = 0
        while self.parent[x] != x:
            path.append(x)
            x = int(self.parent[x])
        root = x
        # compress: parity of each node relative to the root
        acc = 0
        for node in reversed(path):
            acc ^= int(self.par[node])
            self.par[node] = acc
            self.parent[node] = root
        if path:
            p = int(self.par[path[0]])
        return root, p

    def union(self, a: int, b: int, rel: int) -> bool:
        ra, pa = self.find(a)
        rb, pb = self.find(b)
        if ra == rb:
            return (pa ^ pb) == rel
        lo, hi = (ra, rb) if ra < rb else (rb, ra)
        self.parent[hi] = lo
        self.par[hi] = pa ^ pb ^ rel
        return True


def _oriented(reads: list, flip) -> list:
    return [revcomp(r) if f else r for r, f in zip(reads, flip)]


def cluster_reads(reads: list, hashes: np.ndarray, orient_bits: np.ndarray, cfg: ClusterConfig) -> Clustering:
    n = len(reads)
    counts: Counter = Counter()
    counts["reads"] = n
    labels = np.full(n, -1, dtype=np.int64)
    orient = np.zeros(n, dtype=np.uint8)
    score = np.full(n, np.nan)
    if n == 0:
        return Clustering(labels, orient, score, counts)
    lens = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    counts["reads_without_sketch"] = int((orient_bits[:, 0] == 2).sum()) if orient_bits.size else n
    a, b, rel, budget = candidate_pairs(hashes, orient_bits, cfg.bucket_cap, cfg.max_candidate_pairs, counts,
                                        cfg.min_shared_slots)
    if budget is not None:
        counts["unassigned"] = n
        return Clustering(labels, orient, score, counts, budget)
    maxlen = np.maximum(lens[a], lens[b]) if a.size else np.zeros(0, np.int64)
    # a length difference beyond the threshold already rejects the pair (the distance is at least |dL|)
    feasible = np.abs(lens[a] - lens[b]) <= cfg.theta * maxlen
    counts["pairs_rejected_length"] = int((~feasible).sum())
    a, b, rel, maxlen = a[feasible], b[feasible], rel[feasible], maxlen[feasible]
    native = _verify_native(reads, a, b, rel, maxlen, cfg, counts)
    if native is not None:
        root, orient, best = native
    else:
        root, orient, best = _verify_reference(reads, a, b, rel, maxlen, cfg, counts)
    size = np.bincount(root, minlength=n)
    member = size[root] >= 2
    labels[member] = root[member]
    score[member] = best[member]
    counts["components"] = int((size >= 2).sum())
    refine_left = [cfg.max_refine_pairs]
    for c in np.flatnonzero(size > cfg.max_cluster_reads).tolist():
        counts["components_refined"] += 1
        mem = np.flatnonzero(root == c)
        _refine(reads, mem, orient, labels, score, cfg, counts, refine_left)
    if refine_left[0] < 0:
        budget = {"limit": "max_refine_pairs", "allowed": cfg.max_refine_pairs,
                  "effect": "members of oversized components beyond the budget left unassigned"}
    final = np.bincount(labels[labels >= 0], minlength=n)
    single = (labels >= 0) & (final[np.maximum(labels, 0)] < 2)
    labels[single] = -1
    score[single] = np.nan
    counts["clusters"] = int((final >= 2).sum())
    counts["clustered_reads"] = int((labels >= 0).sum())
    counts["unassigned"] = int((labels < 0).sum())
    return Clustering(labels, orient, score, counts, budget)


def _verify_native(reads: list, a: np.ndarray, b: np.ndarray, rel: np.ndarray, maxlen: np.ndarray, cfg: ClusterConfig,
                   counts: Counter):
    """The verification loop in the native kernel; None when the reference must run (backend, domain)."""
    if not 0 <= cfg.band_slack <= _nc.MAX_SLACK or _nc.resolve_backend() != "native":
        return None
    if maxlen.size and int(maxlen.min()) < 1:
        return None
    buf, off, lens = _nc.pack(reads)
    if not _nc.codes_in_domain(buf) or not _nc.lengths_in_domain(lens):
        return None
    root, orient, best, c = _nc.verify(buf, off, lens, a, b, rel, maxlen, cfg.theta, cfg.band_slack, VERIFY_CHUNK)
    skipped, verified, accepted, rejected, conflicts = (int(x) for x in c)
    # the counters the reference loop creates (a Counter keeps keys incremented by 0, and reports list them)
    if a.size:
        counts["pairs_skipped_same_component"] += skipped
    if verified:
        counts["pairs_verified"] += verified
        counts["edges_accepted"] += accepted
        counts["edges_rejected"] += rejected
    if conflicts:
        counts["orientation_conflicts"] += conflicts
    return root, orient, best


def _verify_reference(reads: list, a: np.ndarray, b: np.ndarray, rel: np.ndarray, maxlen: np.ndarray,
                      cfg: ClusterConfig, counts: Counter):
    """Verify candidate pairs in chunks, unite accepted edges; (root, orientation parity, best score) per read."""
    n = len(reads)
    uf = _UF(n)
    best = np.full(n, -np.inf)          # best assignment score of each read over its accepted edges
    for c0 in range(0, a.size, VERIFY_CHUNK):
        ca, cb, cr, cm = a[c0:c0 + VERIFY_CHUNK], b[c0:c0 + VERIFY_CHUNK], rel[c0:c0 + VERIFY_CHUNK], maxlen[c0:c0 + VERIFY_CHUNK]
        roots = np.array([uf.find(int(x))[0] for x in np.concatenate([ca, cb]).tolist()], dtype=np.int64)
        todo = np.flatnonzero(roots[: ca.size] != roots[ca.size:])
        counts["pairs_skipped_same_component"] += int(ca.size - todo.size)
        if not todo.size:
            continue
        d = banded_distance([reads[i] for i in ca[todo].tolist()],
                            [revcomp(reads[j]) if r else reads[j] for j, r in zip(cb[todo].tolist(), cr[todo].tolist())],
                            cfg.band_slack)
        counts["pairs_verified"] += int(todo.size)
        ok = d <= cfg.theta * cm[todo]
        counts["edges_accepted"] += int(ok.sum())
        counts["edges_rejected"] += int((~ok).sum())
        for t in np.flatnonzero(ok).tolist():
            i, j, r = int(ca[todo[t]]), int(cb[todo[t]]), int(cr[todo[t]])
            sc = 1.0 - float(d[t]) / float(cm[todo[t]])
            best[i] = max(best[i], sc)
            best[j] = max(best[j], sc)
            if not uf.union(i, j, r):
                counts["orientation_conflicts"] += 1
    root = np.empty(n, dtype=np.int64)
    orient = np.zeros(n, dtype=np.uint8)
    for x in range(n):
        root[x], orient[x] = uf.find(x)
    return root, orient, best


def _dist_matrix(xs: list, ys: list, slack: int) -> np.ndarray:
    if not xs or not ys:
        return np.zeros((len(xs), len(ys)), dtype=np.int64)
    ia, ib = np.meshgrid(np.arange(len(xs)), np.arange(len(ys)), indexing="ij")
    d = banded_distance([xs[i] for i in ia.ravel().tolist()], [ys[j] for j in ib.ravel().tolist()], slack)
    return d.reshape(len(xs), len(ys))


def _refine(reads: list, mem: np.ndarray, orient: np.ndarray, labels: np.ndarray, score: np.ndarray,
            cfg: ClusterConfig, counts: Counter, left: list) -> None:
    """Split one oversized component (members in index order, oriented by their parity)."""
    ori = _oriented([reads[i] for i in mem.tolist()], orient[mem].tolist())
    lens = np.fromiter((r.size for r in ori), dtype=np.int64, count=len(ori))
    m = len(ori)
    thr = lambda d, la, lb: d <= cfg.theta * np.maximum(la, lb)  # noqa: E731
    reps: list[int] = []
    # pass 1: greedy representatives in index order (block-wise; identical to the sequential rule)
    block = 256
    for b0 in range(0, m, block):
        blk = list(range(b0, min(m, b0 + block)))
        left[0] -= len(blk) * len(reps)
        if left[0] < 0:
            labels[mem[b0:]] = -1
            score[mem[b0:]] = np.nan
            counts["refine_budget_unassigned"] += m - b0
            break
        near = np.zeros(len(blk), dtype=bool)
        if reps:
            d = _dist_matrix([ori[i] for i in blk], [ori[r] for r in reps], cfg.band_slack)
            near = thr(d, lens[blk][:, None], lens[reps][None, :]).any(axis=1)
        rest = [blk[t] for t in np.flatnonzero(~near).tolist()]
        # the first remaining read is a representative; drop the reads within θ of it; repeat (equal to the
        # sequential rule: a read becomes a representative iff no earlier representative is within θ)
        while rest:
            r0 = rest[0]
            reps.append(r0)
            rest = rest[1:]
            if not rest:
                break
            left[0] -= len(rest)
            d = _dist_matrix([ori[r0]], [ori[i] for i in rest], cfg.band_slack)[0]
            close = thr(d, lens[r0], lens[rest])
            rest = [i for i, c in zip(rest, close.tolist()) if not c]
    keep = np.flatnonzero(labels[mem] >= 0)
    if not reps or not keep.size:
        return

    def assign(rep_idx: list[int]) -> tuple[np.ndarray, np.ndarray]:
        left[0] -= keep.size * len(rep_idx)
        d = _dist_matrix([ori[i] for i in keep.tolist()], [ori[r] for r in rep_idx], cfg.band_slack)
        ok = thr(d, lens[keep][:, None], lens[rep_idx][None, :])
        dm = np.where(ok, d, np.iinfo(np.int64).max)
        near = dm.argmin(axis=1)          # first minimum = lower representative index
        hit = ok.any(axis=1)
        mx = np.maximum(lens[keep], lens[np.asarray(rep_idx)[near]])
        sc = 1.0 - d[np.arange(keep.size), near] / mx
        return np.where(hit, near, -1), np.where(hit, sc, np.nan)

    grp, sc = assign(reps)
    # pass 2: each group's medoid (over its first ``medoid_members`` members) becomes the representative
    reps2: list[int] = []
    for g in range(len(reps)):
        mm = keep[grp == g][: cfg.medoid_members].tolist()
        if not mm:
            continue
        d = _dist_matrix([ori[i] for i in mm], [ori[i] for i in mm], cfg.band_slack)
        reps2.append(mm[int(d.sum(axis=1).argmin())])
    reps2 = sorted(set(reps2))
    if left[0] >= keep.size * len(reps2):
        grp, sc = assign(reps2)
        rep_of = np.asarray(reps2)
    else:
        rep_of = np.asarray(reps)
        counts["refine_second_pass_skipped"] += 1
    hit = grp >= 0
    new_label = np.full(keep.size, -1, dtype=np.int64)
    # a sub-cluster's ID is the smallest read index among its members
    for g in np.unique(grp[hit]).tolist():
        sel = keep[grp == g]
        new_label[grp == g] = int(mem[sel].min())
    labels[mem[keep]] = new_label
    score[mem[keep]] = np.where(hit, sc, np.nan)
    counts["refine_unassigned"] += int((~hit).sum())
    counts["refine_representatives"] += int(rep_of.size)
