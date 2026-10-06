"""V8 amendment A1: ``max_edit_frac`` applies the D13 segmentation selection (edit distance <= frac x L) to simulated reads:
removed reads count in ``window_out`` only (not in the read-level histograms); default None leaves the V7 tally unchanged."""
from __future__ import annotations

import random

import numpy as np
import pytest

pytest.importorskip("edlib")
from vnxdna.simulation.fit import pipeline as P, tally as T     # noqa: E402


def test_max_edit_frac_removes_reads_beyond_the_cut_from_everything():
    rnd = random.Random(4)
    ref = bytes(rnd.choice(b"ACGT") for _ in range(100))
    good = bytearray(ref)
    good[10] = ord("A") if good[10] != ord("A") else ord("C")                    # 1 edit
    bad = bytes(rnd.choice(b"ACGT") for _ in range(100))                         # ~50+ edits
    lay = T.Layout(100, read_rate=True)
    M0, rs0 = P.tally_matrix([(ref, [bytes(good), bad])], lay)
    M1, rs1 = P.tally_matrix([(ref, [bytes(good), bad])], lay, max_edit_frac=0.30)
    assert rs0[:T.EDIT_BINS].sum() == 2 and rs1[:T.EDIT_BINS].sum() == 1                 # edit histogram
    assert lay.get(M0[0], "read_rate").sum() == 2 and lay.get(M1[0], "read_rate").sum() == 1
    assert lay.get(M1[0], "window_out")[0] == 1 and lay.get(M0[0], "window_out")[0] == 0
    assert lay.get(M0[0], "excluded")[0] == 1 and lay.get(M1[0], "excluded")[0] == 0
    np.testing.assert_array_equal(lay.get(M0[0], "pos_sub"), lay.get(M1[0], "pos_sub"))   # event tallies identical
