"""Shared helpers for the V5 native-aligner tests (the ``native_ready`` fixture is in conftest.py).

``native_ready`` makes sure the native library is loaded. If the package was installed without it, the fixture builds
it into a temporary directory (a C compiler is needed); if that is impossible, native tests are skipped with the reason.
The reference (V4 NumPy) aligner needs nothing.
"""
from __future__ import annotations

import numpy as np

from vnxdna.v4.frame import PROFILES, Layout
from vnxdna.v4.sync import SyncCosts, TemplateAligner

FIELDS = ("bases", "erased", "ok", "insertions", "deletions", "marker_mismatches", "cost")

LAYOUTS = {
    "default-313nt": PROFILES["v4-balanced"][0],
    "short-178nt": Layout(10, 16, 24, 3),
    "long-494nt": Layout(80, 16, 24, 3),
    "dense-no-markers": PROFILES["v4-dense"][0],
    "indel-profile": PROFILES["v4-indel"][0],
    "round1-32/2": Layout(40, 16, 32, 2),
    "tiny-8/1": Layout(1, 0, 8, 1),
    "dense-markers-8/6": Layout(5, 2, 8, 6),
    "wide-period-256/4": Layout(200, 16, 256, 4),
}


def strand(layout: Layout, rng: np.random.Generator) -> np.ndarray:
    """A strand with the layout's markers and random frame bases (frame content is a wildcard for the aligner)."""
    tpl, _ = layout.template()
    s = rng.integers(0, 4, tpl.size).astype(np.uint8)
    s[tpl >= 0] = tpl[tpl >= 0].astype(np.uint8)
    return s


def project_both(layout: Layout, reads, quals=None, min_quality: int = 0, band: int = 6, costs: SyncCosts | None = None):
    ref = TemplateAligner(layout, band, costs, backend="reference").project(reads, quals, min_quality)
    nat_al = TemplateAligner(layout, band, costs, backend="native")
    assert nat_al.backend == "native"
    nat = nat_al.project(reads, quals, min_quality)
    return ref, nat


def assert_identical(ref, nat, context: str = "") -> None:
    for f in FIELDS:
        a, b = getattr(ref, f), getattr(nat, f)
        assert a.dtype == b.dtype and a.shape == b.shape, f"{context} {f}: {a.dtype}{a.shape} vs {b.dtype}{b.shape}"
        if not np.array_equal(a, b):
            bad = np.flatnonzero((a != b).reshape(a.shape[0], -1).any(axis=1)) if a.ndim else []
            raise AssertionError(f"{context} field {f} differs for reads {list(bad)[:10]}")
