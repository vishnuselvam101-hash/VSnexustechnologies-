"""Storage profiles and the complete option set of a format-5 archive.

A profile is a named, documented starting point, not a claim that it is
optimal. ``docs/BENCHMARKS.md`` reports the measured cost of each one (density,
speed, and recovery under the simulated channel). Every field can be
overridden individually. The resulting values are recorded in the manifest,
so decoding never depends on the profile table.

| profile   | chunk   | outer code (K+M) | payload / inner parity | guaranteed loss per ECC group | constraints                         |
|-----------|---------|------------------|------------------------|-------------------------------|-------------------------------------|
| compact   | 4 MiB   | 128+12 (9.4 %)   | 48 B / 6 B  (276 nt)   | any 12 of 140 strands         | GC 35–65 %, homopolymer ≤ 5         |
| balanced  | 1 MiB   | 64+16 (25 %)     | 40 B / 8 B  (252 nt)   | any 16 of 80 strands          | GC 40–60 %, homopolymer ≤ 4         |
| resilient | 1 MiB   | 48+24 (50 %)     | 36 B / 12 B (252 nt)   | any 24 of 72 strands          | GC 40–60 %, homopolymer ≤ 4         |
| archival  | 256 KiB | 32+32 (100 %)    | 32 B / 16 B (252 nt)   | any 32 of 64 strands          | GC 45–55 %, homopolymer ≤ 4, tandem ≤ 12 |
"""
from __future__ import annotations

import re

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from ..container import compression
from ..ecc.cauchy import CauchyErasureCode
from ..errors import ConfigurationError
from .constraints import ConstraintSpecV2
from .frame import FrameGeometry
from .manifest import MAX_CHUNK_SIZE


_PROFILE_NAME = re.compile(r"[a-z0-9_-]{1,32}")


@dataclass(frozen=True)
class StoreOptionsV2:
    """Every parameter of a new format-5 archive."""

    profile: str = "balanced"
    chunk_size: int = 1024 * 1024
    compression: str = "zstd"
    compression_level: int = 3
    data_shards: int = 64
    parity_shards: int = 16
    mapping: str = "2bit"
    payload_bytes: int = 40
    inner_parity_bytes: int = 8
    constraints: ConstraintSpecV2 = field(default_factory=ConstraintSpecV2)
    store_name: bool = True
    timestamp: str | None = None  # recorded verbatim; None keeps unencrypted output byte-deterministic

    def validate(self) -> FrameGeometry:
        if not isinstance(self.chunk_size, int) or isinstance(self.chunk_size, bool) or not 1 <= self.chunk_size <= MAX_CHUNK_SIZE:
            raise ConfigurationError(f"chunk_size must be an integer in 1 .. {MAX_CHUNK_SIZE}")
        compression.validate(self.compression, self.compression_level)
        CauchyErasureCode(self.data_shards, self.parity_shards)
        if not isinstance(self.profile, str) or not _PROFILE_NAME.fullmatch(self.profile):
            # the same rule as the manifest schema, so a bad name fails here and not after the whole file was stored
            raise ConfigurationError("profile must match [a-z0-9_-]{1,32}")
        if self.timestamp is not None and (not isinstance(self.timestamp, str) or len(self.timestamp) > 64):
            raise ConfigurationError("timestamp must be a string of at most 64 characters")
        if not isinstance(self.constraints, ConstraintSpecV2):
            raise ConfigurationError("constraints must be a ConstraintSpecV2")
        return FrameGeometry(self.mapping, self.payload_bytes, self.inner_parity_bytes)

    def public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["constraints"] = self.constraints.to_dict()
        return d

    def overhead(self) -> dict[str, Any]:
        """Expected overhead of this configuration, excluding compression (computed, not measured)."""
        g = self.validate()
        k, m = self.data_shards, self.parity_shards
        return {"outer_parity_percent": 100.0 * m / k, "strand_nt": g.strand_nt,
                "guaranteed_erasures_per_group": m, "group_strands": k + m,
                "inner_correctable_byte_errors": self.inner_parity_bytes // 2,
                "bases_per_stored_byte_asymptotic": g.strand_nt * (k + m) / (k * self.payload_bytes)}


PROFILES: dict[str, StoreOptionsV2] = {
    "compact": StoreOptionsV2(profile="compact", chunk_size=4 * 1024 * 1024, data_shards=128, parity_shards=12,
                              payload_bytes=48, inner_parity_bytes=6,
                              constraints=ConstraintSpecV2(gc_min_percent=35, gc_max_percent=65, max_homopolymer=5)),
    "balanced": StoreOptionsV2(),
    "resilient": StoreOptionsV2(profile="resilient", data_shards=48, parity_shards=24, payload_bytes=36, inner_parity_bytes=12),
    "archival": StoreOptionsV2(profile="archival", chunk_size=256 * 1024, compression_level=9, data_shards=32, parity_shards=32,
                               payload_bytes=32, inner_parity_bytes=16,
                               constraints=ConstraintSpecV2(gc_min_percent=45, gc_max_percent=55, max_homopolymer=4,
                                                            max_tandem_repeat_nt=12)),
}


def options_for(profile: str = "balanced", **overrides: Any) -> StoreOptionsV2:
    """Profile defaults with explicit overrides (``None`` values are ignored).

    A customised profile is recorded as ``<profile>-custom`` unless ``profile_name`` names it explicitly.
    """
    name = overrides.pop("profile_name", None)
    if name is not None:
        overrides["profile"] = name
    if profile not in PROFILES:
        raise ConfigurationError(f"unknown profile {profile!r}; available: {sorted(PROFILES)}")
    base = PROFILES[profile]
    changes = {k: v for k, v in overrides.items() if v is not None}
    constraint_fields = {f for f in ConstraintSpecV2.__dataclass_fields__}
    constraint_changes = {k: changes.pop(k) for k in list(changes) if k in constraint_fields}
    if constraint_changes:
        changes["constraints"] = replace(base.constraints, **constraint_changes)
    customised = bool(changes)
    options = replace(base, **changes)
    if customised and "profile" not in overrides:
        options = replace(options, profile=f"{profile}-custom" if len(profile) < 26 else "custom")
    options.validate()
    return options
