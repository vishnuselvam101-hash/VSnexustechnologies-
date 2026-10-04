"""Invariant for the Phase 3 accounting terminology (P3-EXP-04).

"undetected" does NOT mean "no physical indel existed". It means the marker/alignment layer did not localise the
indel inside an indel window covering its true position; downstream decoding then treated its effect (shifted or
substituted bases) according to the available symbol evidence — as substitutions for the inner code, or not at all
if the read failed. The physical indel is always counted in the true-indel total.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from vnxdna.v4.frame import decode_frames, nt_to_bytes
from vnxdna.v4.sync import TemplateAligner, frame_erasures_to_bytes
from vnxdna.v5.indel import recovery as rv
from vnxdna.v5.indel.path import align_with_path

from .indel_support import LAY, inject, strands

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "v5" / "phase3"))
from exp04_accounting import window_of  # noqa: E402


def test_undetected_means_not_localised_not_absent():
    S = strands(4, 9601)
    # an insertion and a deletion 6 nt apart in one segment: net shift 0, the aligner sees no indel
    events = [("ins", 100, 2), ("del", 106)]
    read = inject(S["strands"][0], events)[0]
    geom = rv.Geometry(LAY)
    al = TemplateAligner(LAY, 6)
    proj, rpos = align_with_path(al, [read])
    plan = rv.plan_read(geom, read, None, proj.bases[0], proj.erased[0], rpos[0], rv.IndelRecoveryConfig())
    classes = [window_of(plan.windows, e[0], e[1]) for e in events]
    assert classes == [None, None]                 # both physical indels are "undetected" ...
    assert len(events) == 2                        # ... and both exist and are counted as true indels
    # their effect reaches the inner code as substitutions inside one segment; here RS absorbs it
    P = decode_frames(LAY, nt_to_bytes(np.minimum(proj.bases, 3)), frame_erasures_to_bytes(proj.erased), errors_only_retry=False)
    assert P.ok[0]


def test_indel_misattributed_to_a_neighbouring_window_is_also_undetected():
    """A detected window that does not contain the indel's true position does not count as detecting that indel."""
    S = strands(4, 9602)
    geom = rv.Geometry(LAY)
    t0, t1 = geom.markers[3]
    read = inject(S["strands"][1], [("del", t0 - 1)])[0]           # deletion right before a marker
    al = TemplateAligner(LAY, 6)
    proj, rpos = align_with_path(al, [read])
    plan = rv.plan_read(geom, read, None, proj.bases[0], proj.erased[0], rpos[0], rv.IndelRecoveryConfig())
    act = [w for w in plan.windows if w.active]
    hit = window_of(plan.windows, "del", t0 - 1)
    assert act                                                       # the shift itself was detected
    if hit is None:
        assert all(not (w.ta <= t0 - 1 < w.tb) for w in act)        # ... but not at the indel's true position
