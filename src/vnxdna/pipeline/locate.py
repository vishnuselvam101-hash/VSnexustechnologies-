"""Where a file's bytes live in DNA: chunk ranges (archive layer) mapped to strand groups and strand-file records.

Formerly ``vnxdna.v4.archive.locate(profile=...)``/``dna_location`` (V6 Phase 2): the mapping needs the superblock and
the strand profiles, so it lives above the archive layer.
"""
from __future__ import annotations

import os

from vnxdna.archive import operations as ar
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.dnaenc.superblock import Superblock


def _options(profile: str | None, dna=None):
    """DNAOptions for a strand or redundancy profile (or the given options), validated (CONFIGURATION_ERROR)."""
    from vnxdna.codec.profiles import REDUNDANCY_PROFILES, merge
    from vnxdna.core.errors import VNXConfigurationError
    from vnxdna.pipeline.encode import DNAOptions
    if dna is None:
        if profile in REDUNDANCY_PROFILES:
            dna = DNAOptions(**merge(profile, {}))
        elif profile in PROFILES:
            dna = DNAOptions(profile=profile)
        else:
            raise VNXConfigurationError(f"unknown DNA profile {profile!r}; strand profiles: {sorted(PROFILES)}, "
                                        f"redundancy profiles: {sorted(REDUNDANCY_PROFILES)}")
    dna.resolve()
    return dna


def _runs(idx) -> list[list[int]]:
    """Sorted unique record indices as [first, count] runs."""
    out: list[list[int]] = []
    for i in sorted(set(int(x) for x in idx)):
        if out and out[-1][0] + out[-1][1] == i:
            out[-1][1] += 1
        else:
            out.append([i, 1])
    return out


def dna_location(ranges: list[list[int]], profile: str | None, container_size: int, dna=None) -> dict:
    """Strand groups and strand-file record ranges holding container byte ranges, for the strand file that
    `vnx encode` writes with these options (no index file needed: data group g covers container bytes
    [g·K·P, (g+1)·K·P) in both layouts).

    V4/V5 layout (superblock version 1, sequential order): superblock strands, then each group's symbols in order;
    one [first, count] record run per group (output unchanged since V4).

    V6 outer code (superblock version 2: stripes with column-parity groups, sequential or interleaved order, adaptive
    plan): the records follow the encoder's stripe order (:meth:`vnxdna.codec.outer.Geometry.data_index`) with the
    superblock strands at their slots, so a group's strands are not contiguous in general; all record lists are
    merged [first, count] runs (``superblock_records`` too)."""
    dna = _options(profile, dna)
    if dna.v6:
        return _dna_location_v6(ranges, profile, container_size, dna)
    from vnxdna.codec.codecs import make_outer
    from vnxdna.dnaenc.superblock import group_k
    lay, k, m = dna.resolve()
    codec = make_outer(dna.outer_code, k, m, dna.lt_seed, dna.lt_distribution)
    P = lay.payload_bytes
    span = k * P
    groups = sorted({g for off, n in ranges for g in range(off // span, (off + n - 1) // span + 1)})
    ks, ms = Superblock.symbols(P)
    total_groups = -(-container_size // span)
    full = codec.symbols_for(k)
    records = [[ks + ms + g * full, codec.symbols_for(group_k(container_size, k, P, g))] for g in groups]
    return {"profile": profile or dna.profile, "data_symbols": k, "parity_symbols": m, "groups": groups,
            "groups_total": total_groups, "strand_records": records, "superblock_records": [0, ks + ms],
            "superblock_version": 1,
            "note": "records are 0-based positions in the strand file written by `vnx encode` with these options"}


def _dna_location_v6(ranges: list[list[int]], profile: str | None, container_size: int, dna) -> dict:
    import numpy as np

    from vnxdna.dnaenc.superblock import SB_VERSION_V6
    from vnxdna.pipeline.encode_v6 import resolve_geometry
    lay, k, m = dna.resolve()
    geo, plan_info = resolve_geometry(dna, lay, k, m, container_size)
    P, span = geo.P, geo.K * geo.P
    ks, ms = Superblock.symbols(P)
    n_super = ks + ms
    groups = sorted({g for off, n in ranges for g in range(off // span, (off + n - 1) // span + 1)})
    held: dict[int, set[int]] = {}
    for off, n in ranges:
        for g in range(off // span, (off + n - 1) // span + 1):
            lo, hi = max(off, g * span) - g * span, min(off + n, (g + 1) * span) - g * span
            held.setdefault(g, set()).update(range(lo // P, (hi - 1) // P + 1))
    stripes = sorted({geo.stripe_of(g) for g in groups})
    parity = [g for s in stripes for g in geo.stripe_rows(s)[1]]

    def records(gs) -> np.ndarray:
        idx = [geo.data_index(g, np.arange(geo.symbols_of(g))) for g in gs]
        return geo.file_index(np.concatenate(idx), n_super) if idx else np.zeros(0, dtype=np.int64)

    data_idx = [geo.data_index(g, np.array(sorted(held[g]))) for g in groups]
    return {"profile": profile or dna.profile, "data_symbols": geo.K, "parity_symbols": geo.M,
            "stripe_depth": geo.D, "column_parity": geo.Mc, "strand_order": geo.order, "groups": groups,
            "groups_total": geo.G, "stripes": stripes, "stripes_total": geo.stripes,
            "strand_records": _runs(records(groups)),
            "data_strand_records": _runs(geo.file_index(np.concatenate(data_idx), n_super) if data_idx else []),
            "column_parity_groups": parity, "column_parity_records": _runs(records(parity)),
            "superblock_records": _runs(geo.superblock_index(n_super)), "superblock_version": SB_VERSION_V6,
            "strands_total": geo.strands() + n_super, "plan": plan_info,
            "note": "records are 0-based positions in the strand file written by `vnx encode` with these options, as "
                    "merged [first, count] runs: strand_records = every strand of the groups, data_strand_records = "
                    "the data strands holding the bytes, column_parity_records = the column-parity groups of their "
                    "stripes"}


def locate(path: str | os.PathLike, name: str, *, profile: str | None = None, dna=None, key: bytes | None = None,
           passphrase: str | None = None, allow_unencrypted: bool = False) -> dict:
    """:func:`vnxdna.archive.operations.locate` plus, with a DNA ``profile`` (strand or redundancy profile) or
    ``dna`` options, the strand groups and records (V4/V5 and V6 layouts; see :func:`dna_location`)."""
    if profile is not None or dna is not None:
        dna = _options(profile, dna)               # refuse unknown or invalid options before opening the archive
    out = ar.locate(path, name, key=key, passphrase=passphrase, allow_unencrypted=allow_unencrypted)
    if profile is not None or dna is not None:
        out["dna"] = dna_location(out["container_ranges"], profile, out["container_bytes"], dna)
    return out
