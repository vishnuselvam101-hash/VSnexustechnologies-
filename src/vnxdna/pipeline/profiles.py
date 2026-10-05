"""Redundancy profiles as ``DNAOptions`` (V6): the profile data and the merge rule are in :mod:`vnxdna.codec.profiles`."""
from __future__ import annotations

from vnxdna.codec.profiles import REDUNDANCY_PROFILES, merge
from vnxdna.v4.encoder import DNAOptions
from vnxdna.v4.frame import PROFILES

__all__ = ["REDUNDANCY_PROFILES", "dna_options", "describe"]


def dna_options(name: str, **overrides) -> DNAOptions:
    """DNAOptions for a named redundancy profile (``overrides`` win)."""
    return DNAOptions(**merge(name, overrides))


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
