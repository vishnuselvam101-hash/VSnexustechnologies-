"""V6 redundancy profiles: named encoder presets built only from existing, tested layout and outer-code options.

A profile is a starting point, not a recommendation: which one fits depends on the channel (substitutions vs indels,
strand dropout, burst loss, coverage) and on the density the user can afford. None is "optimal"; compare them with
``experiments/v6/channel/evaluate.py`` on the error model that matters (all such results are SIMULATED).

    maximum-density   v4-dense layout (no sync markers, so indels erase their read), row code 64 + 16
    balanced          v4-balanced layout with sync markers, row code 64 + 16 — exactly the V5 default encoding
    maximum-recovery  v4-archival layout (sync markers, larger index), row code 32 + 32, plus V6 column parity (2 per
                      stripe of 8 data groups) and interleaved strand order against burst loss

Explicit options override a profile's fields (e.g. ``workers``). :func:`merge` is the single place where a profile and
explicit options are combined (V6 Phase 2.3; the CLI and :func:`vnxdna.pipeline.profiles.dna_options` both use it).
This module holds data only: building ``DNAOptions`` and describing a profile need the strand profiles (layer 3),
so they live in :mod:`vnxdna.pipeline.profiles`; the old path ``vnxdna.v6.profiles`` re-exports both.
"""
from __future__ import annotations

from vnxdna.core.errors import V6ConfigurationError

REDUNDANCY_PROFILES: dict[str, dict] = {
    "maximum-density": {"profile": "v4-dense"},
    "balanced": {"profile": "v4-balanced"},
    "maximum-recovery": {"profile": "v4-archival", "stripe_depth": 8, "column_parity": 2, "strand_order": "interleaved"},
}


def profile(name: str) -> dict:
    """The fields of a named redundancy profile (a copy)."""
    if name not in REDUNDANCY_PROFILES:
        raise V6ConfigurationError(f"unknown redundancy profile {name!r}; available: {sorted(REDUNDANCY_PROFILES)}")
    return dict(REDUNDANCY_PROFILES[name])


def merge(name: str | None, explicit: dict | None = None) -> dict:
    """``DNAOptions`` fields of profile ``name`` with ``explicit`` options on top (explicit always wins).

    Callers that treat ``None`` as "not given" (the CLI) drop those keys before calling; a ``None`` passed here is an
    explicit value. ``name=None`` returns ``explicit`` unchanged.
    """
    out = {} if name is None else profile(name)
    out.update(explicit or {})
    return out
