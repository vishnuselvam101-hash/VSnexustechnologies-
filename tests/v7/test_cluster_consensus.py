"""V7 item A unit tests of the per-cluster consensus (V7_ARCHITECTURE §5.3, §8 "Unit"): forward-backward certainty
against brute-force enumeration of every optimal path, the vote, re-creation of the transmitted strand, and recovery of
verified frames from SIMULATED noisy clusters. SYNTHETIC test data."""
from __future__ import annotations

from collections import Counter

import numpy as np
import pytest

from vnxdna.dnaenc.layout import PROFILES
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster.consensus import cluster_consensus, fb_calls, transmitted_strand, vote

from .cluster_support import frame_key, make_strands, noisy, revcomp, truth_frames


def _all_paths(tpl, mc, read, cin):
    """Every global alignment of template and read as (cost, ops); ops: ("M", i, j), ("D", i), ("I", j)."""
    T, L = len(tpl), len(read)
    out = []

    def rec(i, j, cost, ops):
        if i == T and j == L:
            out.append((cost, list(ops)))
            return
        if i < T and j < L:
            c = 0 if tpl[i] < 0 or tpl[i] == read[j] else mc[i]
            ops.append(("M", i, j))
            rec(i + 1, j + 1, cost + c, ops)
            ops.pop()
        if i < T:
            ops.append(("D", i))
            rec(i + 1, j, cost + cin, ops)
            ops.pop()
        if j < L:
            ops.append(("I", j))
            rec(i, j + 1, cost + cin, ops)
            ops.pop()

    rec(0, 0, 0, [])
    return out


def _brute_calls(tpl, mc, read, cin):
    paths = _all_paths(tpl, mc, read, cin)
    opt = min(c for c, _ in paths)
    best = [ops for c, ops in paths if c == opt]
    calls = []
    for i in range(len(tpl)):
        deleted = any(op[0] == "D" and op[1] == i for ops in best for op in ops)
        bases = {int(read[op[2]]) for ops in best for op in ops if op[0] == "M" and op[1] == i}
        ok = not deleted and len(bases) == 1 and min(bases) < 4
        calls.append(min(bases) if ok else 4)
    return calls, opt


def test_certain_calls_equal_brute_force_enumeration():
    rng = np.random.default_rng(21)
    for _ in range(120):
        T = int(rng.integers(1, 6))
        L = int(rng.integers(max(0, T - 2), T + 3))
        tpl = rng.integers(-1, 4, T).astype(np.int16)
        mc = rng.choice([3, 4], T).astype(np.int32)
        read = rng.integers(0, 4, L).astype(np.uint8)
        if rng.random() < 0.2 and L:
            read[int(rng.integers(L))] = 4
        cin = int(rng.choice([2, 6]))
        calls, opt = fb_calls(tpl[None, :], mc[None, :], [read], np.array([T + L + 1]), cin)
        bc, bopt = _brute_calls(tpl.tolist(), mc.tolist(), read.tolist(), cin)
        assert int(opt[0]) == bopt
        assert calls[0].tolist() == bc, (tpl, mc, read, cin)


def test_band_excludes_reads_that_do_not_fit():
    tpl = np.full((1, 10), -1, dtype=np.int16)
    calls, opt = fb_calls(tpl, np.zeros((1, 10), np.int32), [np.zeros(16, np.uint8)], np.array([3]), 6)
    assert int(opt[0]) >= 1 << 28 and (calls == 4).all()


def test_homopolymer_deletion_stays_certain_and_other_indels_erase_little():
    tpl = np.array([0, 1, 2, 2, 2, 2, 3, 0, 1, 3, 0, 2], dtype=np.int16)
    mc = np.full(tpl.size, 3, dtype=np.int32)
    read = np.delete(tpl, 3).astype(np.uint8)              # one G of the GGGG run deleted
    calls, _ = fb_calls(tpl[None, :], mc[None, :], [read], np.array([4]), 6)
    # every placement of the deletion inside the run gives the same bases: only the run is uncertain about which G
    # is missing, and a deleted position is never certain, so the run is erased while all other positions are certain
    c = calls[0].tolist()
    assert c[:2] == [0, 1] and c[6:] == [3, 0, 1, 3, 0, 2]


def test_wildcard_template_with_one_indel_erases_only_ambiguous_positions():
    rng = np.random.default_rng(22)
    s = rng.integers(0, 4, 24).astype(np.uint8)
    tpl = np.full((1, 24), -1, dtype=np.int16)
    tpl[0, 0] = s[0]
    tpl[0, -1] = s[-1]
    read = np.delete(s, 12)
    calls, _ = fb_calls(tpl, np.full((1, 24), 4, np.int32), [read], np.array([4]), 6)
    c = calls[0]
    assert all(c[i] in (4, s[i]) for i in range(24))      # never a wrong certain call at this position set


def test_vote_rules():
    calls = np.array([[0, 1, 2, 4], [0, 1, 3, 4], [0, 2, 3, 4]], dtype=np.uint8)
    best, dec, margin, total = vote(calls, 0.6, 2)
    assert best[:2].tolist() == [0, 1] and dec.tolist() == [True, True, True, False]
    assert margin.tolist() == [3, 1, 1, 0] and total.tolist() == [3, 3, 3, 0]
    best, dec, _, _ = vote(calls[:1], 0.6, 2)              # one read never decides a base
    assert not dec.any()


@pytest.mark.parametrize("profile", ["v4-balanced", "v4-indel", "v4-archival", "v4-dense"])
def test_transmitted_strand_is_recreated_bit_exactly(profile, tmp_path):
    lay = PROFILES[profile][0]
    _, strands = make_strands(tmp_path, 900, profile)
    from vnxdna.dnaenc.frame4 import decode_frames
    from vnxdna.dnaenc.mapping import nt_to_bytes
    from vnxdna.sync.template import strip_markers_exact
    fb, _ = strip_markers_exact(lay, np.stack(strands))
    frames = nt_to_bytes(fb)
    P = decode_frames(lay, frames)
    noisy_frames = frames.copy()
    noisy_frames[:, 5] ^= 0x5A                               # the received bytes need not be exact
    for i in range(0, len(strands), 7):
        got = transmitted_strand(lay, int(P.kind[i]), int(P.tag[i]), int(P.group[i]), int(P.symbol[i]), P.payload[i],
                                 noisy_frames[i])
        assert np.array_equal(got, strands[i])


@pytest.fixture(scope="module")
def balanced(tmp_path_factory):
    lay = PROFILES["v4-balanced"][0]
    _, strands = make_strands(tmp_path_factory.mktemp("cons"), 1200)
    return lay, strands, truth_frames(lay, strands)


def test_identical_reads_give_the_read(balanced):
    lay, strands, truth = balanced
    tpl0, fpos = lay.template()
    reads = [strands[3].copy() for _ in range(3)]
    n = len(reads)
    mc = np.where(tpl0 >= 0, 4, 0).astype(np.int32)
    calls, opt = fb_calls(np.repeat(tpl0[None, :].astype(np.int16), n, 0), np.repeat(mc[None, :], n, 0), reads,
                          np.full(n, 4), 6)
    assert (opt == 0).all() and np.array_equal(calls[0], strands[3])
    best, dec, _, _ = vote(calls[:, fpos], 0.6, 2)
    assert dec.all() and np.array_equal(best, strands[3][fpos])


def test_exact_duplicates_vote_once(balanced):
    lay, strands, truth = balanced
    c = Counter()
    fr = cluster_consensus(lay, [(0, [strands[3].copy(), strands[3].copy(), strands[3].copy()])], ClusterConfig(),
                           counts=c)
    assert fr == [] and c["cluster_duplicate_reads"] == 2 and c["cluster_insufficient_reads"] == 1


def test_noisy_clusters_decode_to_their_true_frames(balanced):
    lay, strands, truth = balanced
    rng = np.random.default_rng(23)
    clusters = []
    for i in range(20):
        reads = [noisy(rng, strands[i]) for _ in range(10)]
        if i % 2:
            reads = [revcomp(r) for r in reads]           # the cluster orientation is decided by the marker cost
        clusters.append((i, reads))
    c = Counter()
    fr = cluster_consensus(lay, clusters, ClusterConfig(), counts=c)
    keys = [frame_key(f) for f in fr]
    assert all(k in truth for k in keys), "a verified cluster frame differs from every true frame"
    assert len(fr) >= 18 and c["cluster_orientation_flipped"] >= 8
    # batching does not change the result
    fr2 = cluster_consensus(lay, clusters[:7], ClusterConfig()) + cluster_consensus(lay, clusters[7:], ClusterConfig())
    assert [frame_key(f) for f in fr2] == keys


def test_mixed_cluster_yields_no_frame_or_a_constituent_and_peels(balanced):
    lay, strands, truth = balanced
    rng = np.random.default_rng(24)
    reads = [noisy(rng, strands[5], 0.01, 0.005, 0.01) for _ in range(8)]
    reads += [noisy(rng, strands[9], 0.01, 0.005, 0.01) for _ in range(8)]
    order = rng.permutation(len(reads))
    c = Counter()
    fr = cluster_consensus(lay, [(0, [reads[i] for i in order])], ClusterConfig(), counts=c)
    want = truth_frames(lay, [strands[5], strands[9]])
    assert all(frame_key(f) in want for f in fr)
    assert len({frame_key(f) for f in fr}) == len(fr)


def test_markerless_layout_uses_the_medoid_seed(tmp_path):
    lay = PROFILES["v4-dense"][0]
    _, strands = make_strands(tmp_path, 900, "v4-dense")
    truth = truth_frames(lay, strands)
    rng = np.random.default_rng(25)
    clusters = [(i, [noisy(rng, strands[i], 0.02, 0.0, 0.0) for _ in range(5)]) for i in range(10)]
    clusters[1] = (1, [revcomp(r) for r in clusters[1][1]])
    fr = cluster_consensus(lay, clusters, ClusterConfig())
    assert len(fr) == 10 and all(frame_key(f) in truth for f in fr)
