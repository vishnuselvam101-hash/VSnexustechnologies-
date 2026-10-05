"""The ``vnx.result/1`` envelope, provenance and the ``vnx.error/1`` object (V6_ARCHITECTURE §4.2, §4.4).

Every SDK function returns a :class:`Result`. ``to_json()`` is the envelope; ``to_cli()`` is what ``vnx`` prints: the
envelope plus the command's 5.x top-level fields mirrored next to it (where both define a key the 5.x value is kept), so
5.x consumers keep working through 6.x.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from vnxdna._version import __version__
from vnxdna.core.errors import error_json  # noqa: F401 - re-exported as part of the SDK
from vnxdna.core.provenance import git_state
from vnxdna.core.util import peak_rss_bytes
from vnxdna.core.version import SPEC_VERSION

RESULT_SCHEMA = "vnx.result/1"
SOFTWARE = {"name": "vnxdna", "version": __version__}
#: envelope keys a mirrored body never replaces (no 5.x command output used them; ``status`` and ``warnings`` are mirrored)
ENVELOPE_ONLY = frozenset({"schema", "kind", "software", "spec", "provenance", "inputs", "outputs", "formats", "timings",
                           "resources", "result", "error"})


# ------------------------------------------------------------------------------------------------------------- provenance
@lru_cache(maxsize=1)
def _git() -> dict:
    """Commit and dirty flag only when the package runs from a source checkout (``src/vnxdna`` inside a repository)."""
    here = Path(__file__).resolve().parents[1]
    if here.parent.name != "src":
        return {"commit": None, "dirty": None}
    return git_state(here)


@lru_cache(maxsize=16)
def _file_sha256(path: str, mtime_ns: int, size: int) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest()
    except OSError:
        return None


def backends() -> dict:
    """Active backend, ABI, SIMD level and library SHA-256 of every native kernel (never raises)."""
    from vnxdna.native import native_status
    out = {}
    for k, v in native_status()["kernels"].items():
        lib = v.get("library")
        sha = None
        if lib and v.get("backend") == "native":
            try:
                st = os.stat(lib)
                sha = _file_sha256(lib, st.st_mtime_ns, st.st_size)
            except OSError:
                sha = None
        rec = {"active": v.get("backend"), "abi": v.get("abi_version"), "lib_sha256": sha, "origin": v.get("library_origin")}
        if k == "rs":
            rec["level"] = v.get("simd_level")
        if v.get("error"):
            rec["error"] = v["error"]
        out[k] = rec
    return out


def config_sha256(config: Any) -> str | None:
    if config is None:
        return None
    return hashlib.sha256(json.dumps(config, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def provenance(config: Any = None, seeds: dict | None = None) -> dict:
    g = _git()
    return {"git_commit": g.get("commit"), "git_dirty": g.get("dirty"), "python": platform.python_version(),
            "platform": f"{sys.platform}-{platform.machine()}", "backends": backends(), "config_sha256": config_sha256(config),
            "seeds": dict(seeds or {})}


# ------------------------------------------------------------------------------------------------------------- files
def file_ref(role: str, path: str | os.PathLike | None, sha256: str | None = None, *, hash_file: bool = False) -> dict:
    p = None if path is None else Path(path)
    size = None
    if p is not None:
        try:
            size = p.stat().st_size if p.is_file() else None
        except OSError:
            size = None
    if sha256 is None and hash_file and size is not None:
        sha256 = _hash_now(p)
    return {"role": role, "path": "" if p is None else str(p), "bytes": size, "sha256": sha256}


def _hash_now(p: Path) -> str | None:
    try:
        with open(p, "rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest()
    except OSError:
        return None


class InputHasher:
    """SHA-256 of input files computed in a helper thread while the operation runs (hashlib releases the GIL), so
    hashing adds no serial pass; ``refs()`` joins it. Disabled with ``enabled=False`` (``--no-input-hash``)."""

    def __init__(self, role: str, paths, enabled: bool = True):
        self.role = role
        self.paths = [Path(p) for p in paths]
        self.digests: dict = {}
        self._t = None
        if enabled:
            self._t = threading.Thread(target=self._run, name="vnx-input-hash", daemon=True)
            self._t.start()

    def _run(self) -> None:
        for p in self.paths:
            self.digests[p] = _hash_now(p) if p.is_file() else None

    def refs(self) -> list[dict]:
        if self._t is not None:
            self._t.join()
        return [file_ref(self.role, p, self.digests.get(p)) for p in self.paths]


# ------------------------------------------------------------------------------------------------------------- errors
# ------------------------------------------------------------------------------------------------------------- results
@dataclass(frozen=True)
class Result:
    """One command's outcome. ``body`` is the kind-specific ``result`` (for decode: the decode report)."""

    kind: str
    status: str
    body: dict
    inputs: tuple = ()
    outputs: tuple = ()
    formats: dict = field(default_factory=dict)
    seconds: float | None = None
    stage_seconds: dict | None = None
    workers: int | None = None
    warnings: tuple = ()
    error: dict | None = None
    config: Any = None
    seeds: dict | None = None
    peak_rss_bytes: int = field(default_factory=peak_rss_bytes)

    def to_json(self, schema: str = RESULT_SCHEMA) -> dict:
        timings = {"seconds": self.seconds}
        if self.stage_seconds is not None:
            timings["stage_seconds"] = self.stage_seconds
        return {"schema": schema, "kind": self.kind, "status": self.status, "software": dict(SOFTWARE), "spec": SPEC_VERSION,
                "provenance": provenance(self.config, self.seeds), "inputs": list(self.inputs), "outputs": list(self.outputs),
                "formats": dict(self.formats), "timings": timings,
                "resources": {"peak_rss_bytes": self.peak_rss_bytes, "peak_rss_scope": "parent", "workers": self.workers},
                "result": self.body, "warnings": list(self.warnings), "error": self.error}

    def to_cli(self, schema: str = RESULT_SCHEMA) -> dict:
        """The envelope plus the 5.x top-level fields of this command (for ``status`` and ``warnings`` the 5.x value wins)."""
        out = self.to_json(schema)
        out.update({k: v for k, v in self.body.items() if k not in ENVELOPE_ONLY})
        return out


class EncodeResult(Result):
    pass


class DecodeResult(Result):
    pass


class ArchiveResult(Result):
    pass


class InspectResult(Result):
    pass


class VerifyResult(Result):
    pass


class ExtractResult(Result):
    pass


class ListResult(Result):
    pass


class SimulateResult(Result):
    pass


class BenchmarkResult(Result):
    pass


class ConformanceResult(Result):
    pass
