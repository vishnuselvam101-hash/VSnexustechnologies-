"""V7 item A: the full-template consensus polish (``consensus_template="full"``, EXPERIMENTAL, opt-in). Exactness of the
single-edit costs against re-alignment of the edited template, the length-preserving shift, the opt-in switch, and no
wrong frame from SIMULATED noisy clusters. SYNTHETIC test data."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

import numpy as np
import pytest

from vnxdna.dnaenc.layout import PROFILES
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster.consensus import cluster_consensus, fb_calls_reference
from vnxdna.recovery.cluster.polish import edit_costs, segments, shift

from .cluster_support import frame_key, make_strands, noisy, revcomp, truth_frames

FULL = ClusterConfig(consensus_template="full")


def _opt(tpl, mc, reads, band, cin):
    n = len(reads)
    _, opt = fb_calls_reference(np.repeat(tpl[None, :], n, 0), np.repeat(mc[None, :], n, 0), reads,
                                np.full(n, band), cin)
    return opt


@pytest.mark.parametrize("seed", range(6))
def test_single_edit_costs_equal_realignment_of_the_edited_template(seed):
    rng = np.random.default_rng(seed)
    T, cin, csub, band = 14, 6, 3, 6
    tpl = rng.integers(0, 4, T).astype(np.int16)
    tpl[[4, 5, 10]] = [1, 2, 3]
    mc = np.full(T, csub, dtype=np.int32)
    mc[[4, 5, 10]] = 4                                   # marker-like positions keep their own mismatch cost
    reads = [noisy(rng, tpl.astype(np.uint8), 0.1, 0.1, 0.1) for _ in range(5)]
    n = len(reads)
    opt, sub, dele, ins = edit_costs(np.repeat(tpl[None, :], n, 0), np.repeat(mc[None, :], n, 0), reads,
                                     np.full(n, band), cin, csub)
    assert np.array_equal(opt, _opt(tpl, mc, reads, band, cin))
    for i in range(T):
        dt = np.delete(tpl, i)
        assert np.array_equal(dele[:, i], _opt(dt, np.delete(mc, i), reads, band, cin)), ("del", i)
        for b in range(4):
            st = tpl.copy()
            st[i] = b
            assert np.array_equal(sub[:, i, b], _opt(st, mc, reads, band, cin)), ("sub", i, b)
            it = np.insert(tpl, i, b)
            assert np.array_equal(ins[:, i, b], _opt(it, np.insert(mc, i, csub), reads, band, cin)), ("ins", i, b)


def test_segments_and_shift():
    lay = PROFILES["v4-balanced"][0]
    tpl0, fpos = lay.template()
    seg = segments(tpl0)
    assert (seg[fpos] >= 0).all() and (seg[tpl0 >= 0] == -1).all()
    assert np.array_equal(np.bincount(seg[fpos]), np.diff(np.r_[0, np.flatnonzero(np.diff(seg[fpos])) + 1, fpos.size]))
    t = np.arange(8, dtype=np.int16)
    assert shift(t, 2, 6, 9).tolist() == [0, 1, 3, 4, 5, 9, 6, 7]       # left shift: remove 2, insert before 6
    assert shift(t, 5, 1, 9).tolist() == [0, 9, 1, 2, 3, 4, 6, 7]       # right shift: insert before 1, remove 5


def test_default_is_the_reference_path_and_unknown_template_is_rejected():
    assert ClusterConfig().consensus_template == "wildcard"
    with pytest.raises(VNXConfigurationError):
        ClusterConfig(consensus_template="poa").validate()
    FULL.validate()


@pytest.fixture(scope="module")
def balanced(tmp_path_factory):
    lay = PROFILES["v4-balanced"][0]
    _, strands = make_strands(tmp_path_factory.mktemp("polish"), 1200)
    return lay, strands, truth_frames(lay, strands)


def test_full_template_decodes_true_frames_only_and_is_batch_independent(balanced):
    lay, strands, truth = balanced
    rng = np.random.default_rng(31)
    clusters = []
    for i in range(16):
        reads = [noisy(rng, strands[i]) for _ in range(4)]
        if i % 2:
            reads = [revcomp(r) for r in reads]
        clusters.append((i, reads))
    c_old, c_new = Counter(), Counter()
    old = cluster_consensus(lay, clusters, ClusterConfig(), counts=c_old)
    new = cluster_consensus(lay, clusters, FULL, counts=c_new)
    keys = [frame_key(f) for f in new]
    assert all(k in truth for k in keys) and all(frame_key(f) in truth for f in old)
    assert len(new) >= len(old) and c_new["cluster_polish_rounds"] >= 1 and "cluster_polish_rounds" not in c_old
    split = cluster_consensus(lay, clusters[:5], FULL) + cluster_consensus(lay, clusters[5:], FULL)
    assert [frame_key(f) for f in split] == keys


def test_full_template_on_markerless_layout_is_the_reference_path(tmp_path):
    lay = PROFILES["v4-dense"][0]
    _, strands = make_strands(tmp_path, 900, "v4-dense")
    rng = np.random.default_rng(25)
    clusters = [(i, [noisy(rng, strands[i], 0.02, 0.0, 0.0) for _ in range(5)]) for i in range(6)]
    a = cluster_consensus(lay, clusters, ClusterConfig())
    b = cluster_consensus(lay, clusters, replace(FULL))
    assert [frame_key(f) for f in a] == [frame_key(f) for f in b]
