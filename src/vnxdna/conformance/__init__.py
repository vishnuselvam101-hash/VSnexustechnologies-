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


def _read(vdir: Path, ref: dict) -> bytes:
    data = (vdir / ref["path"]).read_bytes()
    if "sha256" in ref and hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise ValueError(f"input {ref['path']} does not match its recorded SHA-256")
    return data


# ---------------------------------------------------------------------------------------------------------- operations
def _op_crc32(vec, vdir):
    import numpy as np
    from vnxdna.core.crc import crc32_rows
    rows = np.frombuffer(bytes.fromhex(vec["params"]["rows_hex"]), dtype=np.uint8).reshape(-1, vec["params"]["row_bytes"])
    return {"crc32": [f"{int(c):08x}" for c in crc32_rows(rows)]}


def _op_keystream(vec, vdir):
    from vnxdna.dnaenc.scrambler import keystreams
    span = vec["params"]["span"]
    return {"keystream_hex": keystreams(span)[vec["params"]["variant"]][: vec["params"]["bytes"]].tobytes().hex()}


def _op_map(vec, vdir):
    import numpy as np
    from vnxdna.dnaenc.mapping import bytes_to_nt
    nt = bytes_to_nt(np.frombuffer(bytes.fromhex(vec["params"]["bytes_hex"]), dtype=np.uint8)[None, :])[0]
    return {"nt": "".join("ACGT"[int(c)] for c in nt)}


def _op_frame4_build(vec, vdir):
    import numpy as np
    from vnxdna.dnaenc.constraints import ConstraintConfig
    from vnxdna.dnaenc.frame4 import build_strands
    from vnxdna.dnaenc.layout import PROFILES
    p = vec["params"]
    lay = PROFILES[p["profile"]][0]
    payload = np.frombuffer(_read(vdir, vec["inputs"]["payload"]), dtype=np.uint8)[None, :]
    strands, variant = build_strands(lay, ConstraintConfig(), p["tag"], p["kind"], np.array([p["group"]]), np.array([p["symbol"]]),
                                     payload)
    s = "".join("ACGT"[int(c)] for c in strands[0])
    return {"strand_sha256": hashlib.sha256(s.encode()).hexdigest(), "variant": int(variant[0])}


def _op_frame4_parse(vec, vdir):
    import numpy as np
    from vnxdna.dnaenc.frame4 import decode_frames
    from vnxdna.dnaenc.layout import PROFILES
    from vnxdna.dnaenc.mapping import nt_to_bytes
    from vnxdna.sync.template import strip_markers_exact
    lay = PROFILES[vec["params"]["profile"]][0]
    seq = _read(vdir, vec["inputs"]["strand"]).decode().strip()
    codes = np.frombuffer(seq.encode(), dtype=np.uint8)
    table = np.full(256, 4, dtype=np.uint8)
    for i, b in enumerate(b"ACGT"):
        table[b] = i
    fb, _ = strip_markers_exact(lay, table[codes][None, :])
    P = decode_frames(lay, nt_to_bytes(np.minimum(fb, 3)))
    out = {"accepted": bool(P.ok[0])}
    if P.ok[0]:
        out.update(kind=int(P.kind[0]), tag=int(P.tag[0]), group=int(P.group[0]), symbol=int(P.symbol[0]),
                   payload_sha256=hashlib.sha256(P.payload[0].tobytes()).hexdigest())
    return out


def _op_superblock_unpack(vec, vdir):
    from vnxdna.dnaenc.superblock import Superblock
    sb = Superblock.unpack(_read(vdir, vec["inputs"]["superblock"]))
    return {"version": sb.version, "K": sb.K, "M": sb.M, "repacked_sha256": hashlib.sha256(sb.pack()).hexdigest()}


def _op_probe(vec, vdir):
    import tempfile
    from vnxdna.recovery.options import DecodeOptions
    from vnxdna.recovery.probe import detect_layout
    data = _read(vdir, vec["inputs"]["reads"])
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / Path(vec["inputs"]["reads"]["path"]).name
        p.write_bytes(data)
        lay = detect_layout(p, DecodeOptions())
    return {"layout": lay.to_dict()}


OPERATIONS = {"crc32": _op_crc32, "scrambler.keystream": _op_keystream, "mapping.bytes_to_nt": _op_map,
              "frame4.build": _op_frame4_build, "frame4.parse": _op_frame4_parse, "superblock.unpack": _op_superblock_unpack,
              "probe": _op_probe}


def _error_of(exc: BaseException) -> dict:
    from vnxdna.core.errors import error_json
    e = error_json(exc)
    return {k: e.get(k) for k in ("code", "category", "exit_code", "retryable")}


def run_vector(vdir: Path) -> dict:
    vec = json.loads((vdir / "vector.json").read_text())
    t0 = time.perf_counter()
    rec = {"id": vec["id"], "stage": vec["stage"], "kind": vec["kind"], "expected": vec["expected"]}
    op = OPERATIONS.get(vec["operation"])
    if op is None:
        rec.update(status="SKIP", observed={"reason": f"operation {vec['operation']!r} not implemented by this runner"})
    else:
        try:
            got = op(vec, vdir)
            observed = {"outputs": got}
        except Exception as exc:  # noqa: BLE001 - a negative vector expects one
            observed = {"error": _error_of(exc)}
        rec["observed"] = observed
        rec["status"] = "PASS" if observed == vec["expected"] else "FAIL"
    rec["seconds"] = round(time.perf_counter() - t0, 6)
    return rec


def run(vectors: str | os.PathLike | None = None, *, select=None, backend: str = "auto") -> dict:
    """Run every vector under ``vectors`` (default: the packaged subset); ``vnx.conformance/1`` answer."""
    from vnxdna.native import backend_summary
    root = Path(vectors) if vectors is not None else VECTORS
    index_path = root / "index.json"
    index = json.loads(index_path.read_text())
    results = []
    with _backend(backend):
        backends = {k: (v.get("simd_level") or v.get("backend")) for k, v in backend_summary().items()}
        for entry in index["vectors"]:
            if select and entry["id"] not in select:
                continue
            results.append(run_vector(root / entry["path"]))
    summary = {"total": len(results), "passed": sum(r["status"] == "PASS" for r in results),
               "failed": sum(r["status"] == "FAIL" for r in results), "skipped": sum(r["status"] == "SKIP" for r in results)}
    by_stage: dict = {}
    for r in results:
        s = by_stage.setdefault(r["stage"], {"passed": 0, "failed": 0})
        s["passed" if r["status"] == "PASS" else "failed"] += 1
    verdict = "CONFORMANT" if summary["total"] and summary["passed"] == summary["total"] else "NONCONFORMANT"
    return {"schema": "vnx.conformance/1", "software": __version__, "spec": SPEC_VERSION, "vectors_dir": str(root),
            "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(), "backend": backend, "backends": backends,
            "summary": summary, "by_stage": by_stage, "results": results, "verdict": verdict}
