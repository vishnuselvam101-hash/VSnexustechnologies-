"""V7 item A unit tests: sketches, banded edit distance, union-find, clustering and the configuration
(V7_ARCHITECTURE §8 "Unit"). SYNTHETIC test data; the read channel is SIMULATED."""
from __future__ import annotations

import itertools

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.recovery.cluster import ClusterConfig, validate_mode
from vnxdna.recovery.cluster.editdist import banded_distance, full_distance
from vnxdna.recovery.cluster.graph import _UF, candidate_pairs, cluster_reads
from vnxdna.recovery.cluster.sketch import SEEDS, sketch_reads, splitmix64, splitmix64_int
from vnxdna.recovery.cluster.store import UnplacedStore, min_store_len, select_unplaced, store_width
from vnxdna.recovery.options import DecodeOptions

from .cluster_support import make_strands, noisy, revcomp

LAY = PROFILES["v4-balanced"][0]


def _pad(reads):
    lens = np.array([r.size for r in reads], dtype=np.int64)
    raw = np.full((len(reads), max(1, int(lens.max(initial=1)))), 4, dtype=np.uint8)
    for i, r in enumerate(reads):
        raw[i, : r.size] = r
    return raw, lens


def _sketch_reference(read: np.ndarray, k: int, s: int):
    """Pure-Python canonical k-mer MinHash (the specification of sketch.py)."""
    best = [None] * s
    for p in range(read.size - k + 1):
        w = read[p:p + k].tolist()
        if any(x > 3 for x in w):
            continue
        f = 0
        r = 0
        for q in range(k):
            f = (f << 2) | w[q]
            r = (r << 2) | (3 - w[k - 1 - q])
        canon, o = (r, 1) if r < f else (f, 0)
        for i in range(s):
            key = (splitmix64_int(canon ^ SEEDS[i]) & ~1) | o
            if best[i] is None or key < best[i]:
                best[i] = key
    return ([0xFFFFFFFF if b is None else b >> 32 for b in best], [2 if b is None else b & 1 for b in best])


# ------------------------------------------------------------------------------------------------------ sketch
def test_splitmix_vectorised_equals_reference():
    xs = np.array([0, 1, 2, 12345678901234, (1 << 64) - 1], dtype=np.uint64)
    assert splitmix64(xs).tolist() == [splitmix64_int(int(x)) for x in xs.tolist()]
    assert len(set(SEEDS)) == len(SEEDS)


def test_sketch_equals_pure_python_reference():
    rng = np.random.default_rng(11)
    reads = [rng.integers(0, 4, int(rng.integers(5, 60))).astype(np.uint8) for _ in range(12)]
    reads[3][7] = 4                                              # an N splits the read's k-mers
    reads.append(np.array([0, 1, 2], dtype=np.uint8))            # shorter than k: no k-mer
    raw, lens = _pad(reads)
    h, o = sketch_reads(raw, lens, 8, 6, chunk=5)
    for i, r in enumerate(reads):
        rh, ro = _sketch_reference(r, 8, 6)
        assert h[i].tolist() == rh and o[i].tolist() == ro


def test_sketch_of_reverse_complement_flips_orientation_only():
    rng = np.random.default_rng(12)
    reads = [rng.integers(0, 4, 300).astype(np.uint8) for _ in range(8)]
    raw, lens = _pad(reads)
    raw2, lens2 = _pad([revcomp(r) for r in reads])
    h1, o1 = sketch_reads(raw, lens, 12, 32)
    h2, o2 = sketch_reads(raw2, lens2, 12, 32)
    assert np.array_equal(h1, h2)
    # the orientation bit flips, except where the read holds the minimising canonical k-mer in both orientations
    # (then both reads report 0, the tie-break of the minimum); the pair's majority vote absorbs these
    same = o1 == o2
    assert (o1[same] == 0).all() and same.mean() < 0.02


# ------------------------------------------------------------------------------------------------------ edit distance
def test_banded_distance_equals_full_dp_when_the_band_is_wide():
    rng = np.random.default_rng(13)
    a, b = [], []
    for _ in range(150):
        x = rng.integers(0, 5 if rng.random() < 0.3 else 4, int(rng.integers(0, 30))).astype(np.uint8)
        a.append(x)
        b.append(noisy(rng, np.minimum(x, 3), 0.1, 0.1, 0.1))
    d = banded_distance(a, b, slack=64)
    assert d.tolist() == [full_distance(x, y) for x, y in zip(a, b)]


def test_banded_distance_is_symmetric_and_an_upper_bound():
    rng = np.random.default_rng(14)
    a = [rng.integers(0, 4, int(rng.integers(20, 50))).astype(np.uint8) for _ in range(60)]
    b = [noisy(rng, x, 0.05, 0.1, 0.1) for x in a]
    d1 = banded_distance(a, b, slack=2)
    d2 = banded_distance(b, a, slack=2)
    d3 = banded_distance([revcomp(x) for x in a], [revcomp(y) for y in b], slack=2)
    full = np.array([full_distance(x, y) for x, y in zip(a, b)])
    assert np.array_equal(d1, d2) and np.array_equal(d1, d3)
    assert (d1 >= full).all()
    assert banded_distance([], [], 4).size == 0


def test_n_matches_nothing():
    n = np.array([4, 4, 4], dtype=np.uint8)
    assert banded_distance([n], [n.copy()], 4).tolist() == [3]


# ------------------------------------------------------------------------------------------------------ union-find
def test_union_find_partition_does_not_depend_on_edge_order():
    rng = np.random.default_rng(15)
    n = 40
    edges = [(int(a), int(b), int(rng.integers(2))) for a, b in rng.integers(0, n, (50, 2)) if a != b]
    parts = set()
    for perm in itertools.islice(itertools.permutations(range(len(edges))), 0, 2000, 397):
        uf = _UF(n)
        for e in perm:
            uf.union(*edges[e])
        roots = [uf.find(x)[0] for x in range(n)]
        parts.add(tuple(roots))
    assert len(parts) == 1
    roots = parts.pop()
    assert all(r <= x for x, r in enumerate(roots))           # the root is the smallest index of its set


def test_union_find_parity_is_consistent():
    uf = _UF(4)
    assert uf.union(0, 1, 1) and uf.union(1, 2, 1) and uf.union(2, 3, 0)
    assert [uf.find(x)[1] for x in range(4)] == [0, 1, 0, 0]
    assert not uf.union(0, 3, 1)                               # an odd cycle is reported, not applied


# ------------------------------------------------------------------------------------------------------ clustering
@pytest.fixture(scope="module")
def strands(tmp_path_factory):
    _, s = make_strands(tmp_path_factory.mktemp("cl_unit"), 1200)
    return s


def test_clusters_are_pure_and_orientation_is_recovered(strands):
    rng = np.random.default_rng(16)
    reads, src, flip = [], [], []
    for i, s in enumerate(strands[:25]):
        for _ in range(6):
            r = noisy(rng, s)
            f = int(rng.integers(2))
            reads.append(revcomp(r) if f else r)
            src.append(i)
            flip.append(f)
    order = rng.permutation(len(reads))
    reads = [reads[i] for i in order]
    src = np.array(src)[order]
    flip = np.array(flip)[order]
    raw, lens = _pad(reads)
    cfg = ClusterConfig()
    h, o = sketch_reads(raw, lens, cfg.k, cfg.sketch_size)
    cl = cluster_reads(reads, h, o, cfg)
    assert cl.counts["clustered_reads"] + cl.counts["unassigned"] == len(reads)
    for cid in np.unique(cl.labels[cl.labels >= 0]):
        mem = np.flatnonzero(cl.labels == cid)
        assert len(set(src[mem].tolist())) == 1, "a cluster mixes strands"
        rel = cl.orient[mem] ^ flip[mem]
        assert len(set(rel.tolist())) == 1, "relative orientation inconsistent with the truth"
        assert cid == mem.min()
    a = cl.labels >= 0
    assert np.all((cl.score[a] >= 1 - cfg.theta) & (cl.score[a] <= 1))
    assert np.isnan(cl.score[~a]).all()
    assert a.mean() > 0.9


def test_reads_with_a_destroyed_header_still_cluster(strands):
    """Header independence: the first 48 nt (the frame header and more) replaced by random bases."""
    rng = np.random.default_rng(17)
    reads = []
    for s in strands[:10]:
        for _ in range(5):
            r = noisy(rng, s, 0.01, 0.005, 0.01)
            r[:48] = rng.integers(0, 4, 48)
            reads.append(r)
    raw, lens = _pad(reads)
    cfg = ClusterConfig()
    cl = cluster_reads(reads, *sketch_reads(raw, lens, cfg.k, cfg.sketch_size), cfg)
    for i in range(10):
        labs = set(cl.labels[5 * i: 5 * i + 5].tolist())
        assert len(labs) == 1 and -1 not in labs


def test_unrelated_random_reads_stay_unassigned():
    rng = np.random.default_rng(18)
    reads = [rng.integers(0, 4, 313).astype(np.uint8) for _ in range(200)]
    raw, lens = _pad(reads)
    cfg = ClusterConfig()
    cl = cluster_reads(reads, *sketch_reads(raw, lens, cfg.k, cfg.sketch_size), cfg)
    assert (cl.labels == -1).all() and cl.counts["unassigned"] == 200 and cl.counts["clusters"] == 0


def test_oversized_component_is_refined(strands):
    rng = np.random.default_rng(19)
    reads = [noisy(rng, strands[0], 0.01, 0.005, 0.01) for _ in range(30)]
    reads += [noisy(rng, strands[1], 0.01, 0.005, 0.01) for _ in range(30)]
    cfg = ClusterConfig(max_cluster_reads=20)
    raw, lens = _pad(reads)
    cl = cluster_reads(reads, *sketch_reads(raw, lens, cfg.k, cfg.sketch_size), cfg)
    assert cl.counts["components_refined"] == 2
    assert set(cl.labels[:30].tolist()) == {0} and set(cl.labels[30:].tolist()) == {30}


def test_candidate_pair_budget_stops_and_is_named():
    hashes = np.zeros((40, 4), dtype=np.uint32)
    orient = np.zeros((40, 4), dtype=np.uint8)
    from collections import Counter
    a, b, rel, budget = candidate_pairs(hashes, orient, 256, 100, Counter())
    assert budget["limit"] == "max_candidate_pairs" and a.size == 0
    a, b, rel, budget = candidate_pairs(hashes, orient, 10, 100, Counter())   # every bucket over the cap
    assert budget is None and a.size == 0


# ------------------------------------------------------------------------------------------------------ store
def test_store_selects_unverified_reads_raw_and_counts_lengths(tmp_path):
    T = LAY.strand_nt
    W = store_width(T)
    lens = np.array([T, T - 5, min_store_len(T) - 1, W + 1, W, T], dtype=np.int64)
    codes = np.concatenate([np.full(n, i % 4, dtype=np.uint8) for i, n in enumerate(lens)])
    acc = np.array([True, False, False, False, False, False])
    rec = select_unplaced(codes, lens, None, acc, T)
    assert rec["too_short"] == 1 and rec["too_long"] == 1
    assert rec["len"].tolist() == [T - 5, W, T]
    assert (rec["raw"][0, : T - 5] == 1).all() and (rec["raw"][2, :T] == 1).all()
    st = UnplacedStore(tmp_path, T, max_reads=2)
    st.write(rec)
    rows = st.load()
    assert st.exceeded and len(rows) == 2 and st.counts["unplaced_over_budget"] == 1
    assert rows["len"].tolist() == [T - 5, W]


# ------------------------------------------------------------------------------------------------------ configuration
def test_options_and_config_validation():
    assert DecodeOptions().read_clustering == "off"
    o = DecodeOptions(read_clustering="fallback")
    o.validate()
    assert isinstance(o.cluster_config, ClusterConfig) and o.cluster_config.max_unplaced_reads == 4_000_000
    with pytest.raises(VNXConfigurationError):
        DecodeOptions(read_clustering="on").validate()
    with pytest.raises(VNXConfigurationError):
        DecodeOptions(read_clustering="fallback", cluster_config={"k": 12}).validate()
    for bad in (dict(k=40), dict(theta=0.0), dict(theta=1.5), dict(max_unplaced_reads=-1), dict(vote_share=0.2),
                dict(consensus_band=100), dict(min_votes=True)):
        with pytest.raises(VNXConfigurationError):
            ClusterConfig(**bad).validate()
    assert validate_mode("off", None) is None
