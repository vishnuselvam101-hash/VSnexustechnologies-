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

from .channel import ChannelConfig
from .constraints import ConstraintConfig
from .errors import VNXConfigurationError
from .frame import Layout

PERFORMANCE_PROFILES = {
    # name: workers (None = all logical CPUs), decode batch, encode groups/task, verify-after-encode
    "safe": {"workers": 1, "batch_reads": 4096, "groups_per_task": 32, "verify_after_encode": True},
    "balanced": {"workers": max(1, min(4, (os.cpu_count() or 1))), "batch_reads": 8192, "groups_per_task": 64,
                 "verify_after_encode": False},
    "maximum-throughput": {"workers": os.cpu_count() or 1, "batch_reads": 16384, "groups_per_task": 128,
                           "verify_after_encode": False},
}
SECTIONS = {"archive", "dna", "constraints", "channel", "decode", "performance"}
DNA_KEYS = {"profile", "outer_code", "data_symbols", "parity_symbols", "layout", "lt_seed", "lt_distribution", "experimental"}
DECODE_KEYS = {"profile", "band", "min_quality", "reverse_complement", "consensus_threshold", "max_pending_per_address",
               "archive_tag", "batch_reads", "workers", "indel_recovery"}
ARCHIVE_KEYS = {"chunk_size", "compression", "level", "workers", "preserve_metadata", "dedup"}


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


def performance(name: str | None) -> dict:
    return dict(PERFORMANCE_PROFILES[name or "balanced"])


def dna_options(cfg: dict, **overrides):
    from .encoder import DNAOptions
    sec = dict(cfg.get("dna", {}))
    sec.update({k: v for k, v in overrides.items() if v is not None})
    lay = sec.pop("layout", None)
    cons = ConstraintConfig.from_dict(cfg["constraints"]) if "constraints" in cfg else ConstraintConfig()
    opt = DNAOptions(**{k: v for k, v in sec.items() if k in DNA_KEYS | {"workers", "groups_per_task", "fmt"}}, constraints=cons)
    if lay is not None:
        opt.layout = Layout(**lay) if isinstance(lay, dict) else lay
    return opt


def decode_options(cfg: dict, **overrides):
    from .decoder import DecodeOptions
    sec = dict(cfg.get("decode", {}))
    sec.update({k: v for k, v in overrides.items() if v is not None})
    lay = cfg.get("dna", {}).get("layout")
    opt = DecodeOptions(**{k: v for k, v in sec.items() if k in DECODE_KEYS})
    if lay is not None:
        opt.layout = Layout(**lay)
    return opt


def resolved(obj) -> dict:
    """Plain-dict view of an options dataclass (for reports)."""
    out = {}
    for f in fields(obj):
        v = getattr(obj, f.name)
        if f.name in ("key", "passphrase"):
            out[f.name] = None if v is None else "<redacted>"
        elif hasattr(v, "__dataclass_fields__"):
            out[f.name] = asdict(v)
        else:
            out[f.name] = v
    return out
