"""Seeded input generators for native = reference checks of the V7 read-clustering kernels (SYNTHETIC data).

Shared by ``tests/v7/test_native_cluster.py``, ``benchmarks/v7/native_cluster/make_golden.py`` (golden hashes) and
``benchmarks/v7/native_cluster/stress_fuzz.py`` (large differential campaigns). Every generator is a pure function of
its ``numpy.random.Generator``; the reads are random sequences or i.i.d. substitution/insertion/deletion copies of them
(a test channel, not a model of any platform), with N and out-of-alphabet codes mixed in.
"""
from __future__ import annotations

import hashlib
from collections import Counter

import numpy as np

_RC = np.array([3, 2, 1, 0, 4, 5, 6, 7], dtype=np.uint8)


def mutate(rng: np.random.Generator, s: np.ndarray, rate: float) -> np.ndarray:
    """An i.i.d. edit copy of ``s``: substitutions, insertions and deletions, each about ``rate / 3`` per base."""
    if s.size == 0:
        return s.copy()
    u = rng.random(s.size)
    keep = u >= rate / 3
    sub = rng.random(s.size) < rate / 3
    base = np.where(sub, rng.integers(0, 4, s.size), s).astype(np.uint8)
    ins = rng.random(s.size) < rate / 3
    out = []
    for b, k, i in zip(base.tolist(), keep.tolist(), ins.tolist()):
        if i:
            out.append(int(rng.integers(4)))
        if k:
            out.append(b)
    return np.array(out, dtype=np.uint8)


def sprinkle(rng: np.random.Generator, r: np.ndarray, p_n: float, p_odd: float = 0.0) -> np.ndarray:
    """N (code 4) at rate ``p_n`` and other codes >= 4 (5, 6, 7) at rate ``p_odd``."""
    r = r.copy()
    if r.size:
        r[rng.random(r.size) < p_n] = 4
        odd = rng.random(r.size) < p_odd
        r[odd] = rng.integers(5, 8, int(odd.sum()))
    return r


def revcomp(r: np.ndarray) -> np.ndarray:
    return _RC[np.asarray(r)[::-1]]


def pad(reads: list, width: int | None = None, fill: int = 4) -> tuple[np.ndarray, np.ndarray]:
    lens = np.array([r.size for r in reads], dtype=np.int64)
    w = max(1, int(lens.max(initial=1))) if width is None else width
    raw = np.full((len(reads), w), fill, dtype=np.uint8)
    for i, r in enumerate(reads):
        raw[i, : min(r.size, w)] = r[:w]
    return raw, lens


# ------------------------------------------------------------------------------------------------ cases per kernel
def sketch_case(rng: np.random.Generator, n: int | None = None) -> dict:
    n = int(rng.integers(1, 200)) if n is None else n
    k = int(rng.choice([1, 2, 3, 5, 8, 12, 12, 12, 16, 21, 31]))
    s = int(rng.choice([1, 2, 7, 8, 32, 32, 64, 256]))
    lmax = int(rng.choice([0, 3, 20, 60, 160, 320]))
    reads = [sprinkle(rng, rng.integers(0, 4, int(rng.integers(0, lmax + 1))).astype(np.uint8),
                      float(rng.choice([0, 0.001, 0.05, 0.5])), float(rng.choice([0, 0.01]))) for _ in range(n)]
    width = None if rng.random() < 0.7 else int(rng.integers(1, lmax + 2))
    raw, lens = pad(reads, width, fill=int(rng.choice([0, 4, 7])))
    if rng.random() < 0.2:                       # lengths beyond the padded width, zero or negative
        lens = lens + rng.integers(-3, 4, lens.size)
    return {"raw": raw, "lengths": lens, "k": k, "s": s}


def candidates_case(rng: np.random.Generator, n: int | None = None) -> dict:
    n = int(rng.integers(0, 300)) if n is None else n
    s = int(rng.choice([1, 2, 4, 8, 32]))
    alphabet = int(rng.choice([1, 2, 5, 20, 200, 1 << 32]))
    hashes = rng.integers(0, alphabet, (n, s), dtype=np.uint64).astype(np.uint32)
    orient = rng.choice(np.array([0, 1, 2], dtype=np.uint8), (n, s), p=rng.dirichlet([1, 1, 0.3]))
    return {"hashes": hashes, "orient": orient, "bucket_cap": int(rng.choice([2, 3, 8, 50, 256, 100000])),
            "max_pairs": int(rng.choice([0, 10, 1000, 50_000_000])), "min_shared": int(rng.choice([1, 1, 2, 3]))}


def pairs_case(rng: np.random.Generator, p: int | None = None, lmax: int | None = None) -> dict:
    p = int(rng.integers(1, 120)) if p is None else p
    lmax = int(rng.choice([0, 1, 5, 40, 64, 65, 130, 320])) if lmax is None else lmax
    a, b = [], []
    for _ in range(p):
        x = rng.integers(0, 4, int(rng.integers(0, lmax + 1))).astype(np.uint8)
        u = rng.random()
        if u < 0.6:
            y = mutate(rng, x, float(rng.choice([0, 0.02, 0.1, 0.3, 0.6])))
        elif u < 0.7:
            y = x[: int(rng.integers(0, x.size + 1))].copy()          # prefix: large length difference
        else:
            y = rng.integers(0, 4, int(rng.integers(0, lmax + 1))).astype(np.uint8)
        pn = float(rng.choice([0, 0, 0.02, 0.3]))
        a.append(sprinkle(rng, x, pn, 0.01 if rng.random() < 0.2 else 0.0))
        b.append(sprinkle(rng, y, pn))
    return {"a": a, "b": b, "slack": int(rng.choice([0, 1, 2, 5, 32, 32, 100]))}


def pool_case(rng: np.random.Generator, strands: int | None = None, per_strand: int | None = None) -> tuple[list, dict]:
    """Reads of a few random strands (both orientations, edits, N) plus unrelated reads; a ClusterConfig override."""
    strands = int(rng.integers(1, 25)) if strands is None else strands
    L = int(rng.choice([40, 120, 313]))
    rate = float(rng.choice([0.0, 0.03, 0.08, 0.15]))
    reads = []
    for _ in range(strands):
        s = rng.integers(0, 4, L).astype(np.uint8)
        for _ in range(int(rng.integers(1, 12)) if per_strand is None else per_strand):
            r = sprinkle(rng, mutate(rng, s, rate), 0.002)
            reads.append(revcomp(r) if rng.random() < 0.5 else r)
    for _ in range(int(rng.integers(0, 10))):
        reads.append(rng.integers(0, 4, int(rng.integers(0, L + 20))).astype(np.uint8))
    order = rng.permutation(len(reads))
    reads = [reads[i] for i in order]
    cfg = {"k": int(rng.choice([8, 10, 12])), "sketch_size": int(rng.choice([8, 16, 32])),
           "bucket_cap": int(rng.choice([4, 64, 256])), "min_shared_slots": int(rng.choice([1, 2, 3])),
           "max_candidate_pairs": int(rng.choice([50, 50_000_000])), "theta": float(rng.choice([0.1, 0.3, 0.5])),
           "band_slack": int(rng.choice([0, 3, 32])), "max_cluster_reads": int(rng.choice([2, 4, 8, 64])),
           "max_refine_pairs": int(rng.choice([20, 2_000_000])), "medoid_members": int(rng.choice([2, 16]))}
    return reads, cfg


def fb_case(rng: np.random.Generator, n: int | None = None, T: int | None = None) -> dict:
    n = int(rng.integers(1, 70)) if n is None else n
    T = int(rng.choice([0, 1, 2, 7, 24, 40, 100])) if T is None else T
    B = int(rng.choice([0, 1, 2, 4, 8, 16, 32, 40]))
    truth = rng.integers(0, 4, T).astype(np.uint8)
    reads = []
    for _ in range(n):
        u = rng.random()
        if u < 0.75:
            r = mutate(rng, truth, float(rng.choice([0, 0.03, 0.1, 0.25])))
        elif u < 0.85:
            r = rng.integers(0, 4, int(rng.integers(0, T + B + 8))).astype(np.uint8)
        else:
            r = truth[: int(rng.integers(0, T + 1))].copy()
        reads.append(sprinkle(rng, r, float(rng.choice([0, 0, 0.05])), float(rng.choice([0, 0.02]))))
    wild = rng.random((n, T)) < float(rng.choice([0, 0.3, 0.8, 1.0]))
    tpl = np.where(wild, np.int16(-1), np.broadcast_to(truth.astype(np.int16), (n, T))).astype(np.int16)
    if rng.random() < 0.2:
        tpl[rng.random((n, T)) < 0.05] = rng.integers(4, 8)           # template codes no read base equals
    mode = rng.random()
    if mode < 0.5:
        mc = np.where(rng.random((n, T)) < 0.1, 4, 3).astype(np.int32)      # the stage's costs (marker 4, c_sub 3)
        c_indel = 6
    elif mode < 0.9:
        mc = rng.integers(0, 40, (n, T)).astype(np.int32)
        c_indel = int(rng.integers(0, 40))
    else:                                         # large costs: the int32 lanes
        mc = rng.integers(0, 1025, (n, T)).astype(np.int32)
        c_indel = int(rng.integers(0, 1025))
    band = rng.integers(0, B + 1, n).astype(np.int64)
    if rng.random() < 0.5 and n:
        band[int(rng.integers(n))] = B
    return {"tpl": tpl, "mc": mc, "reads": reads, "band": band, "c_indel": c_indel,
            "slack": int(rng.choice([0, 0, 1, 3, 10]))}


# ------------------------------------------------------------------------------------------------ digests
def digest(*arrays) -> str:
    h = hashlib.sha256()
    for a in arrays:
        if isinstance(a, (dict, Counter)):
            h.update(repr(sorted(dict(a).items())).encode())
        elif a is None:
            h.update(b"None")
        else:
            a = np.ascontiguousarray(a)
            h.update(f"{a.dtype.str}{a.shape}".encode())
            h.update(a.tobytes())
    return h.hexdigest()


def clustering_digest(cl) -> str:
    return digest(cl.labels, cl.orient, cl.score, dict(cl.counts), np.frombuffer(repr(cl.budget).encode(), np.uint8))


# ------------------------------------------------------------------------------------------------ backends
class backend:
    """Context manager: ``VNXDNA_CLUSTER_BACKEND`` set to ``name`` (restored on exit)."""

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        import os
        self.old = os.environ.get("VNXDNA_CLUSTER_BACKEND")
        os.environ["VNXDNA_CLUSTER_BACKEND"] = self.name
        return self

    def __exit__(self, *exc):
        import os
        if self.old is None:
            os.environ.pop("VNXDNA_CLUSTER_BACKEND", None)
        else:
            os.environ["VNXDNA_CLUSTER_BACKEND"] = self.old
        return False


def run_kernel(kind: str, case, name: str):
    """Output of one case through the public dispatcher of ``kind`` with backend ``name`` ('native' or 'reference')."""
    from vnxdna.recovery.cluster import ClusterConfig
    from vnxdna.recovery.cluster import consensus, editdist, graph, sketch
    with backend(name):
        if kind == "sketch":
            return sketch.sketch_reads(case["raw"], case["lengths"], case["k"], case["s"])
        if kind == "candidates":
            counts: Counter = Counter()
            a, b, rel, budget = graph.candidate_pairs(case["hashes"], case["orient"], case["bucket_cap"],
                                                      case["max_pairs"], counts, case["min_shared"])
            return a, b, rel, dict(counts), np.frombuffer(repr(budget).encode(), np.uint8)
        if kind == "banded":
            return (editdist.banded_distance(case["a"], case["b"], case["slack"]),)
        if kind == "pool":
            reads, cfg = case
            c = ClusterConfig(**cfg)
            raw, lens = pad(reads)
            h, o = sketch.sketch_reads(raw, lens, c.k, c.sketch_size)
            cl = graph.cluster_reads(reads, h, o, c)
            return h, o, cl.labels, cl.orient, cl.score, dict(cl.counts), np.frombuffer(repr(cl.budget).encode(),
                                                                                         np.uint8)
        if kind == "fb":
            return consensus.fb_calls(case["tpl"], case["mc"], case["reads"], case["band"], case["c_indel"],
                                      case["slack"])
    raise ValueError(kind)


CASES = {"sketch": sketch_case, "candidates": candidates_case, "banded": pairs_case, "pool": pool_case, "fb": fb_case}


def case_for(kind: str, seed: int):
    return CASES[kind](np.random.default_rng([0x56584E37, seed, list(CASES).index(kind)]))


def same(x, y) -> bool:
    """Field-by-field identity (dtype, shape, values; NaN equals NaN)."""
    if len(x) != len(y):
        return False
    for u, v in zip(x, y):
        if isinstance(u, dict):
            if u != v:
                return False
            continue
        u, v = np.asarray(u), np.asarray(v)
        if u.dtype != v.dtype or u.shape != v.shape:
            return False
        if u.dtype.kind == "f":
            if not np.array_equal(u, v, equal_nan=True):
                return False
        elif not np.array_equal(u, v):
            return False
    return True
