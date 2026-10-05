"""Named channel models shipped with the package (``vnxdna/simulation/models/*.json``, all ``vnx.channel-model/1``).

A model is referred to as ``NAME`` (the only shipped version of that name, or the highest if several), ``NAME@VERSION``
(exactly that version) or a path to a JSON file in any readable schema (/1, /0, channel-config). The 14 shipped models
are the V6 Phase 1 models of ``experiments/v6/channel/models`` converted to /1; ``provenance.converted_from`` records
the SHA-256 of each /0 source file.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.simulation.model import SCHEMA_V1, ChannelModel, from_doc, read_file, sha256_bytes

MODEL_DIR = Path(__file__).with_name("models")
_REF = re.compile(r"^([a-z0-9][a-z0-9._+-]{0,63})(?:@([0-9A-Za-z.+-]+))?$")


def _semver_key(v: str) -> tuple:
    core = v.split("-", 1)[0].split("+", 1)[0]
    return tuple(int(x) for x in core.split(".")) + ((1,) if "-" not in v else (0,))


@lru_cache(maxsize=1)
def _catalogue() -> dict[tuple[str, str], Path]:
    out: dict[tuple[str, str], Path] = {}
    for p in sorted(MODEL_DIR.glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        key = (doc["name"], doc["version"])
        if key in out:
            raise VNXConfigurationError(f"duplicate shipped channel model {key[0]}@{key[1]}")
        out[key] = p
    return out


def available() -> list[tuple[str, str]]:
    """(name, version) of every shipped model, sorted."""
    return sorted(_catalogue(), key=lambda k: (k[0], _semver_key(k[1])))


def model_names() -> list[str]:
    return sorted({n for n, _ in _catalogue()})


@lru_cache(maxsize=64)
def _load_shipped(name: str, version: str) -> ChannelModel:
    p = _catalogue()[(name, version)]
    raw = p.read_bytes()
    doc = json.loads(raw)
    if doc.get("schema") != SCHEMA_V1:
        raise VNXConfigurationError(f"shipped model {p.name} is not {SCHEMA_V1}")
    model, _ = from_doc(doc, source={"file": p.name, "sha256": sha256_bytes(raw), "schema": SCHEMA_V1, "shipped": True})
    return model


def resolve(ref: str | Path) -> tuple[ChannelModel, int | None]:
    """(model, seed stored in the source or None) for a name, ``name@version`` or file path."""
    text = str(ref)
    m = _REF.match(text)
    if m and not Path(text).is_file():
        name, version = m.group(1), m.group(2)
        versions = sorted((v for n, v in _catalogue() if n == name), key=_semver_key)
        if not versions:
            raise VNXConfigurationError(f"unknown channel model {name!r}; named models: {model_names()}",
                                        details={"model": text})
        if version is None:
            version = versions[-1]
        elif version not in versions:
            raise VNXConfigurationError(f"channel model {name!r} has no version {version!r} (shipped: {versions})",
                                        details={"model": text})
        return _load_shipped(name, version), None
    p = Path(text)
    if not p.is_file():
        raise VNXConfigurationError(f"unknown channel model {text!r}: not a shipped name and not a file; named models: "
                                    f"{model_names()}", details={"model": text})
    return read_file(p)


def load_model(ref: str | Path) -> ChannelModel:
    return resolve(ref)[0]


__all__ = ["MODEL_DIR", "available", "model_names", "resolve", "load_model"]
