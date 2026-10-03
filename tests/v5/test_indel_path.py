"""Step 1 of Phase 3: the alignment path (read index of every template base), native vs reference, and its contract."""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.v4.sync import TemplateAligner
from vnxdna.v5 import native_alignment as na
from vnxdna.v5.indel.path import PathAligner, align_with_path

from .native_support import FIELDS, LAYOUTS, strand


def _reads(layout, rng, n=120):
    out = []
    for _ in range(n):
        s = strand(layout, rng)
        for _ in range(int(rng.integers(0, 6))):
            op = int(rng.integers(0, 3))
            if op == 0 and s.size:
                s = np.delete(s, int(rng.integers(0, s.size)))
            elif op == 1:
                s = np.insert(s, int(rng.integers(0, s.size + 1)), np.uint8(rng.integers(0, 4)))
            elif s.size:
                s = s.copy()
                s[int(rng.integers(0, s.size))] = np.uint8(rng.integers(0, 5))
        if rng.random() < 0.1:
            s = rng.integers(0, 4, max(0, s.size + int(rng.integers(-10, 11)))).astype(np.uint8)
        out.append(s.astype(np.uint8))
    return out


@pytest.mark.parametrize("name", ["default-313nt", "short-178nt", "round1-32/2", "dense-no-markers", "tiny-8/1"])
@pytest.mark.parametrize("band", [1, 6, 12])
def test_path_native_equals_reference(native_ready, name, band):
    layout = LAYOUTS[name]
    rng = np.random.default_rng(hash((name, band)) % (1 << 32))
    reads = _reads(layout, rng)
    quals = [rng.integers(0, 41, r.size).astype(np.uint8) for r in reads]
    v4 = TemplateAligner(layout, band, backend="reference").project(reads, quals, 13)
    al = TemplateAligner(layout, band, backend="native")
    pn, rn = align_with_path(al, reads, quals, 13)
    pr, rr = align_with_path(al, reads, quals, 13, backend="reference")
    for f in FIELDS:
        assert np.array_equal(getattr(v4, f), getattr(pn, f)), f
        assert np.array_equal(getattr(v4, f), getattr(pr, f)), f
    assert rn.dtype == np.int16 and np.array_equal(rn, rr)


def test_path_semantics(native_ready):
    layout = LAYOUTS["default-313nt"]
    s = strand(layout, np.random.default_rng(4))
    T = s.size
    al = TemplateAligner(layout, 6)
    _, rp = align_with_path(al, [s, np.delete(s, 100), np.insert(s, 200, np.uint8(1)), np.zeros(5, np.uint8)])
    assert np.array_equal(rp[0], np.arange(T))                  # clean: identity
    assert (rp[1] == -1).sum() == 1                             # one template base deleted
    assert (rp[2] >= 0).all() and len(set(range(T + 1)) - set(rp[2].tolist())) == 1   # one read base skipped
    assert (rp[3] == -1).all()                                  # unaligned read
    for row in rp[:3]:
        a = row[row >= 0]
        assert (np.diff(a) > 0).all()                           # the path is strictly increasing


def test_path_entry_point_rejects_missing_output(native_ready):
    lib = na._load()
    assert lib.vnx_align_abi_version() == 2 == na.ABI_VERSION
    al = TemplateAligner(LAYOUTS["default-313nt"], 6, backend="native")
    g = na.geometry(al)
    read = strand(al.layout, np.random.default_rng(1))
    outs = [np.zeros(al.layout.frame_nt, np.uint8), np.zeros(al.layout.frame_nt, np.uint8), np.zeros(1, np.uint8)] + \
           [np.zeros(1, np.int64) for _ in range(4)]
    p = na._ptr
    rc = lib.vnx_align_batch_path(1, p(read), p(np.array([0, read.size], np.int64)), read.size, None, None, al.T, p(g["tpl"]),
                                  p(g["seg_of"]), p(g["prev_seg"]), p(g["next_seg"]), al.n_segments, al.layout.frame_nt,
                                  p(g["frame_pos"]), p(g["seg_frame"]), 6, 4, 6, 6, 1, 0, 0, *(p(o) for o in outs), None)
    assert rc == -1


def test_reference_twin_is_reference_only():
    al = PathAligner(LAYOUTS["default-313nt"], 6)
    assert al.backend == "reference"


def test_readpos_with_timings_rejected(native_ready):
    al = TemplateAligner(LAYOUTS["default-313nt"], 6, backend="native")
    with pytest.raises(ValueError):
        na.align_usable(al, [strand(al.layout, np.random.default_rng(2))], None, 0, timings={}, readpos=True)
