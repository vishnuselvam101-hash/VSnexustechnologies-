"""Decode result cache (V13, docs/V13_ENGINE_ARCHITECTURE.md §5): verified containers of earlier decodes, keyed by the
identity of the input and of the decoder, so a repeated decode of the same read file with the same options is a
copy instead of a decode.

Off by default; on when ``VNXDNA_CACHE_DIR`` is set or ``sdk.decode(cache_dir=...)`` is given. Never used by the
benchmarks (they call the pipeline directly). The key is the SHA-256 of a canonical record of: the software version and
git commit, the engine library's SHA-256 and the native backends, the input file's SHA-256 and size, the decode options
that can change the output (worker counts and engine threads are excluded: output never depends on them), and whether
a key was given. Any component that changes invalidates the entry. Entries hold the container and the decode report;
the store is bounded in bytes (``VNXDNA_CACHE_BYTES``, default 1 GiB; the oldest entries go first). A cached container
is re-verified by SHA-256 before it is published.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

from vnxdna._version import __version__
from vnxdna.core.util import atomic_output, sha256_file

ENV_DIR, ENV_BYTES = "VNXDNA_CACHE_DIR", "VNXDNA_CACHE_BYTES"
DEFAULT_BYTES = 1 << 30
#: decode options that never change the decoded container (parallelism and batching only)
_VOLATILE = ("workers", "batch_reads", "engine_threads")


def configured_dir(explicit: str | os.PathLike | None = None) -> Path | None:
    d = explicit if explicit is not None else os.environ.get(ENV_DIR)
    return Path(d) if d else None


def limit_bytes() -> int:
    try:
        return max(0, int(os.environ.get(ENV_BYTES) or DEFAULT_BYTES))
    except ValueError:
        return DEFAULT_BYTES


def _plain(obj):
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return hashlib.sha256(bytes(obj)).hexdigest()
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)


def key_fingerprint(key: bytes | None, passphrase: str | None) -> str | None:
    """A one-way fingerprint of the decode's key material (never the material itself), so that a cached result is
    served only to a caller holding the same key or passphrase; None without key material."""
    if key is None and passphrase is None:
        return None
    material = b"key:" + bytes(key) if key is not None else b"passphrase:" + str(passphrase).encode("utf-8")
    return hashlib.sha256(b"vnx-dna result cache key fingerprint\0" + material).hexdigest()


def key_for(reads: str | os.PathLike, options, *, has_key: bool, key_material: str | None = None,
            allow_unencrypted: bool = False) -> str:
    """The cache key (hex) of a decode of ``reads`` with ``options``: computed before the decode (one pass over the
    read file for its SHA-256). ``key_material`` (:func:`key_fingerprint`) and ``allow_unencrypted`` are part of the
    key: a hit never bypasses the key or the V6-SEC-02 check the uncached decode would apply."""
    from vnxdna.sdk.envelope import backends, provenance
    opts = _plain(options)
    for k in _VOLATILE:
        opts.pop(k, None)
    p = Path(reads)
    st = p.stat()
    record = {"software": __version__, "git": provenance().get("git_commit"), "backends": backends(),
              "input_sha256": sha256_file(p), "input_bytes": st.st_size, "options": opts, "has_key": bool(has_key),
              "key_material": key_material, "allow_unencrypted": bool(allow_unencrypted)}
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class ResultCache:
    def __init__(self, root: str | os.PathLike, limit: int | None = None):
        self.root = Path(root)
        self.limit = limit_bytes() if limit is None else limit

    def _entry(self, key: str) -> Path:
        return self.root / key[:2] / key

    def lookup(self, key: str) -> tuple[Path, dict] | None:
        """(container path, report) of a verified entry, or None (a damaged entry is removed)."""
        e = self._entry(key)
        meta, container = e / "meta.json", e / "container.vnx"
        if not (meta.is_file() and container.is_file()):
            return None
        try:
            m = json.loads(meta.read_text())
            if m.get("container_sha256") != sha256_file(container):
                raise ValueError("container does not hash as recorded")
            report = json.loads((e / "report.json").read_text())
        except (OSError, ValueError):
            shutil.rmtree(e, ignore_errors=True)
            return None
        os.utime(e, None)
        return container, report

    def store(self, key: str, container: str | os.PathLike, report: dict) -> dict:
        import tempfile
        e = self._entry(key)
        e.mkdir(parents=True, exist_ok=True)
        sha = sha256_file(container)                     # the source's hash: a torn or corrupted copy never matches it
        fd, tmp_name = tempfile.mkstemp(prefix=".container-", suffix=".tmp", dir=e)   # private to this store
        os.close(fd)
        try:
            shutil.copyfile(container, tmp_name)
            if sha256_file(tmp_name) != sha:
                raise OSError(f"result cache: the copied container does not hash as its source ({tmp_name})")
            os.replace(tmp_name, e / "container.vnx")
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        with atomic_output(e / "report.json", overwrite=True) as tmp:
            tmp.write_text(json.dumps(report, sort_keys=True, default=str))
        meta = {"key": key, "container_sha256": sha, "bytes": (e / "container.vnx").stat().st_size,
                "stored": int(time.time()), "software": __version__}
        with atomic_output(e / "meta.json", overwrite=True) as tmp:
            tmp.write_text(json.dumps(meta, sort_keys=True))
        self.evict()
        return meta

    def entries(self) -> list[dict]:
        out: list[dict] = []
        if not self.root.is_dir():
            return out
        for meta in sorted(self.root.glob("*/*/meta.json")):
            try:
                m = json.loads(meta.read_text())
                m["mtime"] = meta.parent.stat().st_mtime
                m["path"] = str(meta.parent)
                out.append(m)
            except (OSError, ValueError):
                continue
        return out

    def evict(self) -> int:
        """Remove the least recently used entries until the store is within its byte limit."""
        es = sorted(self.entries(), key=lambda m: m["mtime"])
        total = sum(int(m.get("bytes", 0)) for m in es)
        removed = 0
        while es and total > self.limit:
            m = es.pop(0)
            shutil.rmtree(m["path"], ignore_errors=True)
            total -= int(m.get("bytes", 0))
            removed += 1
        return removed

    def stats(self) -> dict:
        es = self.entries()
        return {"dir": str(self.root), "entries": len(es), "bytes": sum(int(m.get("bytes", 0)) for m in es),
                "limit_bytes": self.limit}

    def clear(self) -> dict:
        n = len(self.entries())
        if self.root.is_dir():
            for d in self.root.glob("*/"):
                shutil.rmtree(d, ignore_errors=True)
        return {"dir": str(self.root), "removed": n}


__all__ = ["ENV_DIR", "ENV_BYTES", "DEFAULT_BYTES", "ResultCache", "configured_dir", "key_for", "limit_bytes"]
