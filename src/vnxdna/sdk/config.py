"""Structured configuration: explicit defaults, JSON files, CLI overrides, schema validation, performance profiles.

A configuration file is a JSON object with optional sections::

    {"archive": {...ArchiveOptions fields...},
     "dna": {"profile": "v4-balanced", "outer_code": "cauchy-rs", "data_symbols": 64, "parity_symbols": 16,
             "layout": {"payload_bytes": 40, "inner_parity": 16, "marker_period": 32, "marker_len": 2}},
     "constraints": {...ConstraintConfig fields...},
     "channel": {...ChannelConfig fields...},
     "decode": {...DecodeOptions fields (band, min_quality, consensus_threshold, ...)...},
     "performance": "safe" | "balanced" | "maximum-throughput"}

Unknown keys are errors (typos must not silently fall back to defaults).
Every benchmark and experiment records the *resolved* configuration.

Performance profiles change only parallelism, batching and optional extra
checks. No profile disables integrity verification: decoding always ends
with the container SHA-256 + Merkle validation, extraction always checks
every chunk ID and file SHA-256.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path

from vnxdna.simulation.channel import ChannelConfig
from vnxdna.dnaenc.constraints import ConstraintConfig
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.layout import Layout
from vnxdna.pipeline.performance import PERFORMANCE_PROFILES, performance  # noqa: F401 - part of this module's API

SECTIONS = {"archive", "dna", "constraints", "channel", "decode", "performance"}
DNA_KEYS = {"profile", "outer_code", "data_symbols", "parity_symbols", "layout", "lt_seed", "lt_distribution", "experimental",
            "stripe_depth", "column_parity", "strand_order", "outer_plan", "redundancy_budget"}
DECODE_KEYS = {"profile", "band", "retry_band", "min_quality", "reverse_complement", "consensus_threshold", "max_pending_per_address",
               "archive_tag", "batch_reads", "workers", "indel_recovery", "soft_decoding",
               "recovery_schedule", "max_container_bytes", "consensus_weighting",
               "expect_archive_id", "expect_sha256"}
ARCHIVE_KEYS = {"chunk_size", "compression", "level", "workers", "preserve_metadata", "dedup", "archive_id"}


def load_config(path: str | os.PathLike | None) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise VNXConfigurationError(f"cannot read configuration {path}: {error}") from None
    return validate_config(data)


def validate_config(data: dict) -> dict:
    if not isinstance(data, dict):
        raise VNXConfigurationError("configuration must be a JSON object")
    unknown = set(data) - SECTIONS
    if unknown:
        raise VNXConfigurationError(f"unknown configuration sections: {sorted(unknown)}")
    for name, allowed in (("dna", DNA_KEYS), ("decode", DECODE_KEYS), ("archive", ARCHIVE_KEYS)):
        sec = data.get(name, {})
        if not isinstance(sec, dict):
            raise VNXConfigurationError(f"section {name!r} must be an object")
        bad = set(sec) - allowed
        if bad:
            raise VNXConfigurationError(f"unknown keys in {name!r}: {sorted(bad)}")
    for name in ("constraints", "channel"):
        if name in data and not isinstance(data[name], dict):
            raise VNXConfigurationError(f"section {name!r} must be an object")
    if "constraints" in data:
        ConstraintConfig.from_dict(data["constraints"])
    if "channel" in data:
        ChannelConfig.from_dict(data["channel"])
    if "layout" in data.get("dna", {}):
        lay = data["dna"]["layout"]
        if not isinstance(lay, dict) or set(lay) - {f.name for f in fields(Layout)}:
            raise VNXConfigurationError("dna.layout must contain only payload_bytes, inner_parity, marker_period, marker_len")
        Layout(**lay).validate()
    perf = data.get("performance")
    if perf is not None and (not isinstance(perf, str) or perf not in PERFORMANCE_PROFILES):
        raise VNXConfigurationError(f"performance must be one of {sorted(PERFORMANCE_PROFILES)}")
    return data


def dna_options(cfg: dict, **overrides):
    from vnxdna.pipeline.encode import DNAOptions
    sec = dict(cfg.get("dna", {}))
    sec.update({k: v for k, v in overrides.items() if v is not None})
    lay = sec.pop("layout", None)
    cons = ConstraintConfig.from_dict(cfg["constraints"]) if "constraints" in cfg else ConstraintConfig()
    opt = DNAOptions(**{k: v for k, v in sec.items() if k in DNA_KEYS | {"workers", "groups_per_task", "fmt"}}, constraints=cons)
    if lay is not None:
        opt.layout = Layout(**lay) if isinstance(lay, dict) else lay
    return opt


def decode_options(cfg: dict, **overrides):
    from vnxdna.recovery.options import DecodeOptions
    sec = dict(cfg.get("decode", {}))
    sec.update({k: v for k, v in overrides.items() if v is not None})
    lay = cfg.get("dna", {}).get("layout")
    opt = DecodeOptions(**{k: v for k, v in sec.items() if k in DECODE_KEYS})
    if lay is not None:
        opt.layout = Layout(**lay)
    return opt


def resolved(obj) -> dict:
    """Plain-dict view of an options dataclass (for reports)."""
    out: dict = {}
    for f in fields(obj):
        v = getattr(obj, f.name)
        if f.name in ("key", "passphrase"):
            out[f.name] = None if v is None else "<redacted>"
        elif hasattr(v, "__dataclass_fields__"):
            out[f.name] = asdict(v)
        else:
            out[f.name] = v
    return out


# ---------------------------------------------------------------------------------------------- command-line option assembly
def encode_options(config_path=None, performance_name: str | None = None, *, redundancy_profile: str | None = None,
                   workers: int = 0, experimental: bool | None = None, **explicit):
    """(DNAOptions, performance profile) from a config file, a performance profile, an optional redundancy profile and
    explicit options (``None`` = not given). Precedence: explicit > redundancy profile > config file > defaults; the
    redundancy-profile merge is :func:`vnxdna.codec.profiles.merge` (the only one)."""
    from vnxdna.codec.profiles import merge
    cfg = load_config(config_path)
    p = performance(performance_name or cfg.get("performance"))
    if redundancy_profile is not None:
        explicit = merge(redundancy_profile, {k: v for k, v in explicit.items() if v is not None})
    w = workers if workers else p["workers"]
    return dna_options(cfg, workers=w, groups_per_task=p["groups_per_task"], experimental=experimental, **explicit), p


def decode_options_for(config_path=None, performance_name: str | None = None, *, workers: int = 0,
                       archive_tag: str | None = None, budget: dict | None = None, **explicit):
    """DecodeOptions from a config file, a performance profile and explicit options (``None`` = not given)."""
    from vnxdna.recovery.planner import RecoveryBudget
    cfg = load_config(config_path)
    p = performance(performance_name or cfg.get("performance"))
    opts = decode_options(cfg, workers=workers or p["workers"], batch_reads=p["batch_reads"],
                          archive_tag=int(archive_tag, 16) if archive_tag else None, **explicit)
    if budget:
        opts.recovery_budget = RecoveryBudget(**budget)
    return opts


def archive_options_for(config_path=None, performance_name: str | None = None, *, key=None, passphrase=None,
                        chunk_size: int | None = None, workers: int | None = None, **explicit):
    """ArchiveOptions from the config file's ``archive`` section, a performance profile and explicit options."""
    from vnxdna.archive.operations import ArchiveOptions
    cfg = load_config(config_path).get("archive", {})
    w = performance(performance_name)["workers"] if performance_name else workers
    fields_ = {**cfg, **{k: v for k, v in explicit.items() if v is not None}}
    fields_["chunk_size"] = chunk_size if chunk_size is not None else cfg.get("chunk_size", 1 << 20)
    if w is not None:
        fields_["workers"] = w
    return ArchiveOptions(**fields_, key=key, passphrase=passphrase)
