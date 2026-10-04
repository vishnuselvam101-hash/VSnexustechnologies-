"""V6 redundancy profiles: named encoder presets built only from existing, tested layout and outer-code options.

A profile is a starting point, not a recommendation: which one fits depends on the channel (substitutions vs indels,
strand dropout, burst loss, coverage) and on the density the user can afford. None is "optimal"; compare them with
``experiments/v6/channel/evaluate.py`` on the error model that matters (all such results are SIMULATED).

    maximum-density   v4-dense layout (no sync markers, so indels erase their read), row code 64 + 16
    balanced          v4-balanced layout with sync markers, row code 64 + 16 — exactly the V5 default encoding
    maximum-recovery  v4-archival layout (sync markers, larger index), row code 32 + 32, plus V6 column parity (2 per
                      stripe of 8 data groups) and interleaved strand order against burst loss

Explicit keyword arguments override a profile's fields (e.g. ``workers``).
"""
from __future__ import annotations

from dataclasses import replace

from ..v4.encoder import DNAOptions
from ..v4.frame import PROFILES
from .errors import V6ConfigurationError

REDUNDANCY_PROFILES: dict[str, dict] = {
    "maximum-density": {"profile": "v4-dense"},
    "balanced": {"profile": "v4-balanced"},
    "maximum-recovery": {"profile": "v4-archival", "stripe_depth": 8, "column_parity": 2, "strand_order": "interleaved"},
}


def dna_options(name: str, **overrides) -> DNAOptions:
    """DNAOptions for a named redundancy profile (``overrides`` win)."""
    if name not in REDUNDANCY_PROFILES:
        raise V6ConfigurationError(f"unknown redundancy profile {name!r}; available: {sorted(REDUNDANCY_PROFILES)}")
    opts = DNAOptions(**REDUNDANCY_PROFILES[name])
    return replace(opts, **overrides) if overrides else opts


def describe(name: str) -> dict:
    """Nominal (analytic, before padding and superblock strands) density and redundancy of a profile.

    THEORETICAL: these follow from the layout and code parameters; measured strand counts and nt/byte for real inputs
    come from the encoder report and the benchmarks.
    """
    o = dna_options(name)
    lay, k, m = PROFILES[o.profile]
    stripe = o.stripe_depth or 0
    col = o.column_parity or 0
    row_redundancy = m / k
    col_factor = (stripe + col) / stripe if stripe and col else 1.0
    strands_per_data_strand = (k + m) / k * col_factor
    return {"profile": name, "layout_profile": o.profile, "strand_nt": lay.strand_nt, "payload_bytes": lay.payload_bytes,
            "row_code": [k, m], "stripe_depth": stripe, "column_parity": col, "strand_order": o.strand_order or "sequential",
            "sync_markers": bool(lay.markers), "nominal_row_redundancy": round(row_redundancy, 6),
            "nominal_strands_per_data_strand": round(strands_per_data_strand, 6),
            "nominal_nt_per_byte": round(lay.strand_nt / lay.payload_bytes * strands_per_data_strand, 6),
            "classification": "THEORETICAL"}
