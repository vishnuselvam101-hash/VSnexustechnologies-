"""Sync markers of the VNX4 strand frame: the rotating marker tables and their insertion (VNX4 §10).

Markers carry no data; they let the decoder re-synchronise after insertions/deletions and turn an indel into a bounded
run of erasures for the inner code (:mod:`vnxdna.sync.template`). Split from ``vnxdna.v4.frame`` (V6 Phase 2, M3).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from vnxdna.dnaenc.layout import Layout

# Marker tables: no internal homopolymer, balanced GC; rotating identities make a one-period slip detectable.
MARKER_TABLES = {
    0: [],
    1: ["A", "C", "G", "T"],
    2: ["AC", "GT", "CA", "TG"],
    3: ["ACG", "TGC", "CAT", "GTA"],
    4: ["ACGT", "TGCA", "CATG", "GTAC"],
    5: ["ACGTC", "TGCAG", "CATGA", "GTACT"],
    6: ["ACGTCA", "TGCAGT", "CATGAC", "GTACTG"],
}


def insert_markers(layout: Layout, frame_nt: np.ndarray) -> np.ndarray:
    tpl, pos = layout.template()
    out = np.empty((frame_nt.shape[0], layout.strand_nt), dtype=np.uint8)
    marker_pos = tpl >= 0
    out[:, marker_pos] = tpl[marker_pos].astype(np.uint8)
    out[:, pos] = frame_nt
    return out
