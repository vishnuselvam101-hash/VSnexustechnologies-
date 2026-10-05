"""Generate the conformance vector subset shipped with the package (``src/vnxdna/conformance/vectors``; spec §6.1).

    PYTHONPATH=src python tests/conformance/generate_packaged_vectors.py

Expected values are computed by the current implementation and then frozen: the vectors are committed and are never
regenerated to make a failing implementation pass. SYNTHETIC SOFTWARE TEST data; no DNA was synthesised or sequenced.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "src" / "vnxdna" / "conformance" / "vectors"
sys.path.insert(0, str(ROOT / "src"))

from vnxdna.conformance import run_vector  # noqa: E402
from vnxdna.dnaenc.layout import PROFILES  # noqa: E402
from vnxdna.dnaenc.superblock import Superblock  # noqa: E402

EVIDENCE = "SYNTHETIC SOFTWARE TEST"
index: list[dict] = []


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def vector(group: str, vid: str, stage: str, operation: str, params: dict, inputs: dict[str, bytes] | None = None,
           kind: str = "positive", formats: dict | None = None, expected_error: dict | None = None) -> None:
    d = OUT / group / vid
    d.mkdir(parents=True)
    refs = {}
    for name, data in (inputs or {}).items():
        fn = f"{name}.bin" if not name.startswith("reads") else f"{name}.fasta"
        (d / fn).write_bytes(data)
        refs[name] = {"path": fn, "sha256": sha(data)}
    vec = {"schema": "vnx.conformance-vector/1", "id": vid, "stage": stage,
           "formats": formats or {"frame": None, "superblock": None, "container": None}, "kind": kind,
           "operation": operation, "params": params, "inputs": refs, "expected": {}, "since_spec": "6.0", "evidence": EVIDENCE}
    if expected_error is not None:
        vec["expected"] = {"error": expected_error}
    (d / "vector.json").write_text(json.dumps(vec, indent=1) + "\n")
    if expected_error is None:
        got = run_vector(d)
        assert "outputs" in got["observed"], (vid, got)
        vec["expected"] = got["observed"]
        (d / "vector.json").write_text(json.dumps(vec, indent=1) + "\n")
    rec = run_vector(d)
    assert rec["status"] == "PASS", (vid, rec)
    index.append({"id": vid, "stage": stage, "kind": kind, "formats": vec["formats"], "path": f"{group}/{vid}"})


def sb_bytes(version: int, **kw) -> bytes:
    """A CRC-valid superblock with the given version byte (forgeries for negative vectors)."""
    lay = PROFILES["v4-balanced"][0]
    sb = Superblock("cauchy-rs", 64, 16, lay, "dense", 7, bytes(range(16)), 123_456, b"\x11" * 32, 1000, 49)
    raw = bytearray(sb.pack())
    raw[6] = version
    for off, val in kw.items():
        raw[int(off[1:])] = val
    body = bytes(raw[:-4])
    return body + struct.pack(">I", zlib.crc32(body))


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    rng = np.random.default_rng(6_000)
    rows = rng.integers(0, 256, (3, 49), dtype=np.uint8)
    vector("stage", "crc32.rows.001", "E10", "crc32", {"rows_hex": rows.tobytes().hex(), "row_bytes": 49})
    for v in (0, 1, 255):
        vector("stage", f"scrambler.keystream.v{v:03d}", "E11", "scrambler.keystream", {"variant": v, "span": 53, "bytes": 16})
    vector("stage", "mapping.bytes_to_nt.001", "E12", "mapping.bytes_to_nt", {"bytes_hex": bytes(range(0, 256, 17)).hex()})
    f4 = {"frame": 4, "superblock": None, "container": None}
    for prof in ("v4-balanced", "v4-dense", "v4-indel", "v4-archival"):
        lay = PROFILES[prof][0]
        for kind in (0, 1):
            payload = rng.integers(0, 256, lay.payload_bytes, dtype=np.uint8).tobytes()
            vector("stage", f"frame4.build.{prof}.k{kind}", "E10-E13", "frame4.build",
                   {"profile": prof, "tag": 4660, "kind": kind, "group": 7, "symbol": 3}, {"payload": payload}, formats=f4)
    sb1 = {"frame": None, "superblock": 1, "container": None}
    vector("stage", "superblock.unpack.v1.001", "E9", "superblock.unpack", {}, {"superblock": sb_bytes(1)}, formats=sb1)
    unsupported = {"code": "SUPERBLOCK_VERSION_UNSUPPORTED", "category": "UNSUPPORTED_FORMAT", "exit_code": 6, "retryable": False}
    for v in (0, 3, 255):
        vector("negative", f"superblock.version.{v:03d}", "D8", "superblock.unpack", {}, {"superblock": sb_bytes(v)},
               kind="negative", formats={"frame": None, "superblock": v, "container": None}, expected_error=unsupported)
    forged = {"code": "FORMAT_ERROR", "category": "INVALID_INPUT", "exit_code": 3, "retryable": False}
    vector("negative", "superblock.forged.k0", "D8", "superblock.unpack", {}, {"superblock": sb_bytes(1, b8=0, b9=0)},
           kind="negative", formats=sb1, expected_error=forged)
    (OUT / "index.json").write_text(json.dumps({"schema": "vnx.conformance-index/1", "evidence": EVIDENCE,
                                                "vectors": index}, indent=1) + "\n")
    (OUT / "README.md").write_text("Conformance vector subset shipped with vnxdna (spec §6). Generated once by "
                                   "tests/conformance/generate_packaged_vectors.py and frozen.\n\n"
                                   f"{EVIDENCE} data: no DNA was synthesised, stored or sequenced.\n")
    print(f"{len(index)} vectors in {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
