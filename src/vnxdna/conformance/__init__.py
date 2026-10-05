"""Conformance vector runner (spec §6; layer 7). The package ships a small subset of vectors (``vectors/``); the full set
of spec §6.2 is V6 Phase 8 work and uses the same layout and runner.

A vector (``vnx.conformance-vector/1``) names an *operation* over byte strings, its parameters and inputs, and either
the expected outputs (positive) or the expected error (negative: code, category, exit code, retryable). An independent
implementation needs only the vector files and the specification.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from vnxdna._version import __version__
from vnxdna.core.version import SPEC_VERSION

VECTORS = Path(__file__).with_name("vectors")
BACKEND_ENV = ("VNXDNA_RS_BACKEND", "VNXDNA_READS_BACKEND", "VNXDNA_ALIGN_BACKEND")


@contextmanager
def _backend(backend: str):
    """``reference`` forces every kernel's NumPy reference for the duration of the run; ``native`` requires them."""
    if backend == "auto":
        yield
        return
    if backend not in ("native", "reference"):
        from vnxdna.core.errors import VNXConfigurationError
        raise VNXConfigurationError("backend must be auto, native or reference")
    old = {k: os.environ.get(k) for k in BACKEND_ENV}
    try:
        for k in BACKEND_ENV:
            os.environ[k] = backend
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


from vnxdna.conformance.operations import OPERATIONS, read_input as _read  # noqa: E402,F401


def _error_of(exc: BaseException) -> dict:
    from vnxdna.core.errors import error_json
    e = error_json(exc)
    return {k: e.get(k) for k in ("code", "category", "exit_code", "retryable")}


def run_vector(vdir: Path, entry: dict | None = None) -> dict:
    """Run one vector directory. ``entry`` (the index record) is cross-checked: a vector whose own ``id``, ``stage`` or
    ``kind`` differs from the index is a FAIL, not a silent rename."""
    vec = json.loads((vdir / "vector.json").read_text())
    t0 = time.perf_counter()
    rec = {"id": vec["id"], "stage": vec["stage"], "kind": vec["kind"], "expected": vec["expected"]}
    op = OPERATIONS.get(vec["operation"])
    if entry is not None and any(entry.get(k) != vec.get(k) for k in ("id", "stage", "kind")):
        rec.update(status="FAIL", observed={"reason": "vector.json disagrees with the index entry", "index": entry})
    elif vec.get("schema") != "vnx.conformance-vector/1":
        rec.update(status="FAIL", observed={"reason": f"unknown vector schema {vec.get('schema')!r}"})
    elif op is None:
        rec.update(status="SKIP", observed={"reason": f"operation {vec['operation']!r} not implemented by this runner"})
    else:
        try:
            observed = {"outputs": op(vec, vdir)}
        except Exception as exc:  # noqa: BLE001 - a negative vector expects one; a bug shows up as INTERNAL_ERROR
            observed = {"error": _error_of(exc)}
        rec["observed"] = observed
        rec["status"] = "PASS" if observed == vec["expected"] else "FAIL"
    rec["seconds"] = round(time.perf_counter() - t0, 6)
    return rec


def _backend_record(backend: str, summary: dict) -> dict | None:
    """A forced backend must be the one that ran: a kernel that fell back (library not built) makes the run a SKIP with
    the missing component named, which is NONCONFORMANT (spec §6.3). Not a vector: it appears in ``results`` only."""
    if backend == "auto":
        return None
    missing = sorted(k for k, v in summary.items() if v.get("backend") != backend)
    if not missing:
        return None
    return {"id": f"backend.{backend}", "stage": "backend", "kind": "positive", "status": "SKIP",
            "expected": {"backends": {k: backend for k in summary}},
            "observed": {"reason": f"--backend {backend}: kernel(s) {missing} not available as {backend} "
                                   "(optional component missing; build with `python -m vnxdna.native build`)",
                         "backends": {k: v.get("backend") for k, v in summary.items()}}, "seconds": 0.0}


def default_vectors() -> Path:
    """The vector set ``vnx conformance`` runs by default: the full set of a source checkout (``tests/conformance``, spec
    §6.1) when this package sits in one, otherwise the subset shipped inside the package."""
    full = Path(__file__).resolve().parents[3] / "tests" / "conformance"
    return full if (full / "index.json").is_file() else VECTORS


def run(vectors: str | os.PathLike | None = None, *, select=None, backend: str = "auto", services: dict | None = None) -> dict:
    """Run every vector under ``vectors`` (default: :func:`default_vectors`); ``vnx.conformance/1`` answer."""
    from vnxdna.conformance import operations
    from vnxdna.native import backend_summary
    operations.SERVICES.clear()
    operations.SERVICES.update(services or {})
    root = Path(vectors) if vectors is not None else default_vectors()
    index_path = root / "index.json"
    index = json.loads(index_path.read_text())
    if index.get("schema") != "vnx.conformance-index/1":
        from vnxdna.core.errors import VNXFormatError
        raise VNXFormatError(f"{index_path} is not a vnx.conformance-index/1 document", code="SCHEMA_UNSUPPORTED")
    results = []
    with _backend(backend):
        summary = backend_summary()
        backends = {k: (v.get("simd_level") or v.get("backend")) for k, v in summary.items()}
        skip = _backend_record(backend, summary)
        if skip:
            results.append(skip)
        seen = set()
        for entry in index["vectors"]:
            if select and entry["id"] not in select:
                continue
            if entry["id"] in seen:
                results.append({"id": entry["id"], "stage": entry["stage"], "kind": entry["kind"], "status": "FAIL",
                                "expected": {}, "observed": {"reason": "duplicate vector id in the index"}, "seconds": 0.0})
                continue
            seen.add(entry["id"])
            results.append(run_vector(root / entry["path"], entry))
    count = {s: sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "SKIP")}
    summary_doc = {"total": len(results), "passed": count["PASS"], "failed": count["FAIL"], "skipped": count["SKIP"],
                   "positive": sum(r["kind"] == "positive" for r in results),
                   "negative": sum(r["kind"] == "negative" for r in results)}
    by_stage: dict = {}
    for r in results:
        s = by_stage.setdefault(r["stage"], {"passed": 0, "failed": 0})
        s["passed" if r["status"] == "PASS" else "failed"] += 1
    verdict = "CONFORMANT" if summary_doc["total"] and summary_doc["passed"] == summary_doc["total"] else "NONCONFORMANT"
    return {"schema": "vnx.conformance/1", "software": __version__, "spec": SPEC_VERSION, "vectors_dir": str(root),
            "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(), "backend": backend, "backends": backends,
            "summary": summary_doc, "by_stage": by_stage, "results": results, "verdict": verdict}
