"""Where a file's bytes live in DNA: chunk ranges (archive layer) mapped to strand groups and strand-file records.

Formerly ``vnxdna.v4.archive.locate(profile=...)``/``dna_location`` (V6 Phase 2): the mapping needs the superblock and
the strand profiles, so it lives above the archive layer.
"""
from __future__ import annotations

import os

from vnxdna.archive import operations as ar
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.dnaenc.superblock import Superblock


def dna_location(ranges: list[list[int]], profile: str, container_size: int) -> dict:
    """Strand groups and strand-file record ranges holding container byte ranges (no index file needed: group g covers
    container bytes [g·K·P, (g+1)·K·P); strand records are the superblock strands, then each group's K+M symbols)."""
    lay, k, m = PROFILES[profile]
    span = k * lay.payload_bytes
    groups = sorted({g for off, n in ranges for g in range(off // span, (off + n - 1) // span + 1)})
    ks, ms = Superblock.symbols(lay.payload_bytes)
    total_groups = -(-container_size // span)
    return {"profile": profile, "groups": groups, "groups_total": total_groups,
            "strand_records": [[ks + ms + g * (k + m), k + m] for g in groups], "superblock_records": [0, ks + ms],
            "note": "records are 0-based positions in the strand file written by `vnx encode` with this profile"}


def locate(path: str | os.PathLike, name: str, *, profile: str | None = None, dna=None, key: bytes | None = None,
           passphrase: str | None = None, allow_unencrypted: bool = False) -> dict:
    """:func:`vnxdna.archive.operations.locate` plus, with a strand ``profile``, the strand groups and records."""
    out = ar.locate(path, name, key=key, passphrase=passphrase, allow_unencrypted=allow_unencrypted)
    if profile:
        out["dna"] = dna_location(out["container_ranges"], profile, out["container_bytes"])
    return out
