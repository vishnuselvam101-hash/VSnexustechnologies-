"""The JSON Schema documents of VNX-DNA outputs (spec §4.5), shipped as package data in ``vnxdna/core/schemas``.

``load("vnx.result/1")`` returns the schema; :func:`validate` checks an object against it with the optional
``jsonschema`` package (a development dependency). Schemas reference each other by ``$id`` (``urn:vnx:vnx.error/1``).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).with_name("schemas")
IDS = {"vnx.result/1": "result", "vnx.error/1": "error", "vnx.decode-report/1": "decode-report", "vnx.event/1": "event",
       "vnx.version/1": "version", "vnx.probe/1": "probe", "vnx.export-package/1": "export-package",
       "vnx.import-package/1": "import-package", "vnx.channel-model/1": "channel-model",
       "vnx.simulation-metadata/1": "simulation-metadata"}


@lru_cache(maxsize=None)
def load(schema_id: str) -> dict:
    if schema_id not in IDS:
        from vnxdna.core.errors import VNXConfigurationError
        raise VNXConfigurationError(f"unknown schema {schema_id!r}; known: {sorted(IDS)}", code="SCHEMA_UNSUPPORTED")
    return json.loads((SCHEMA_DIR / f"{IDS[schema_id]}.schema.json").read_text())


def validate(obj: dict, schema_id: str | None = None) -> None:
    """Raise ``jsonschema.ValidationError`` if ``obj`` does not match (default: the schema named by ``obj["schema"]``)."""
    import jsonschema
    from referencing import Registry, Resource
    sid = schema_id or obj.get("schema")
    registry = Registry().with_resources((f"urn:vnx:{i}", Resource.from_contents(load(i))) for i in IDS)
    jsonschema.Draft202012Validator(load(sid), registry=registry).validate(obj)
