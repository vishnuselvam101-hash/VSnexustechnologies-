"""Where a file's bytes live in DNA: chunk ranges (archive layer) mapped to strand groups and strand-file records.

Formerly ``vnxdna.v4.archive.locate(profile=...)``/``dna_location`` (V6 Phase 2): the mapping needs the superblock and
the strand profiles, so it lives above the archive layer.
"""
from __future__ import annotations

import os

from vnxdna.archive import operations as ar
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.dnaenc.superblock import Superblock


def dna_location(ranges: list[list[int]], profile: str | None, container_size: int, dna=None) -> dict:
    """Strand groups and strand-file record ranges holding container byte ranges (no index file needed: group g covers
    container bytes [g·K·P, (g+1)·K·P); strand records are the superblock strands, then each group's symbols in order).

    Only the V4/V5 strand layout (superblock version 1, sequential order) has this mapping. Options that select the V6
    outer code (column parity, interleaved order, adaptive plan; superblock version 2) are refused with
    CONFIGURATION_ERROR: their strand order differs and these ranges would be wrong (audit §5.5)."""
    from vnxdna.codec.codecs import make_outer
    from vnxdna.codec.profiles import REDUNDANCY_PROFILES, merge
    from vnxdna.core.errors import VNXConfigurationError
    from vnxdna.dnaenc.superblock import group_k
    from vnxdna.pipeline.encode import DNAOptions
    if dna is None:
        if profile in REDUNDANCY_PROFILES:
            dna = DNAOptions(**merge(profile, {}))
        elif profile in PROFILES:
            dna = DNAOptions(profile=profile)
        else:
            raise VNXConfigurationError(f"unknown DNA profile {profile!r}; strand profiles: {sorted(PROFILES)}, "
                                        f"redundancy profiles: {sorted(REDUNDANCY_PROFILES)}")
    if dna.v6:
        raise VNXConfigurationError("locate maps container ranges to strands only for the V4/V5 layout (superblock version "
                                    "1, sequential strand order); these options select the V6 outer code (superblock "
                                    "version 2), whose strand order differs. Use `vnx decode --select` for random access",
                                    details={"stripe_depth": dna.stripe_depth, "column_parity": dna.column_parity,
                                             "strand_order": dna.strand_order, "outer_plan": dna.outer_plan})
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


def locate(path: str | os.PathLike, name: str, *, profile: str | None = None, dna=None, key: bytes | None = None,
           passphrase: str | None = None, allow_unencrypted: bool = False) -> dict:
    """:func:`vnxdna.archive.operations.locate` plus, with a DNA ``profile`` (strand or redundancy profile) or
    ``dna`` options, the strand groups and records (V4/V5 layouts only; see :func:`dna_location`)."""
    if profile is not None or dna is not None:
        dna_location([], profile, 0, dna)          # refuse unknown or V6 options before opening the archive
    out = ar.locate(path, name, key=key, passphrase=passphrase, allow_unencrypted=allow_unencrypted)
    if profile is not None or dna is not None:
        out["dna"] = dna_location(out["container_ranges"], profile, out["container_bytes"], dna)
    return out
