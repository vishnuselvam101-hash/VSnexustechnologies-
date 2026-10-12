"""V11 WP1: the V4, V5 and V6 golden read fixtures decode to the same bytes at every SIMD level of the native aligner.

Each noisy read file (SIMULATED channel, see the fixture READMEs) is decoded once per forced level (scalar / avx2 /
avx512, via VNXDNA_ALIGN_BACKEND) and once with the NumPy reference. Every run must report SUCCESS with the manifest's
container SHA-256, and the read statistics and recovery plan must be identical across all runs: the level changes speed
only, never a decision of the decoder.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from vnxdna.native import align as na
from vnxdna.v4 import decoder as de

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CASES = [("v4_0", "balanced"), ("v4_0", "archival"), ("v5_0", "balanced"), ("v5_0", "multifile"),
         ("v6_0", "stripes-seq"), ("v6_0", "adaptive-interleaved"), ("v6_0", "max-recovery")]


def _decode(reads: Path, out: Path, backend: str, monkeypatch) -> tuple[str, dict]:
    monkeypatch.setenv("VNXDNA_ALIGN_BACKEND", backend)
    res = de.decode_reads(reads, out, de.DecodeOptions(), overwrite=True)
    r = res.report
    assert res.status == "SUCCESS"
    assert hashlib.sha256(out.read_bytes()).hexdigest() == r["container_sha256"]
    return r["container_sha256"], json.loads(json.dumps(
        {k: r.get(k) for k in ("reads", "recovery_plan", "groups_decoded", "groups_failed", "failed_groups")}, default=str))


@pytest.mark.parametrize("series,case", CASES, ids=[f"{s}-{c}" for s, c in CASES])
def test_golden_reads_identical_at_every_level(series, case, tmp_path, monkeypatch, native_ready):
    manifest = json.loads((FIXTURES / series / "manifest.json").read_text())["cases"][case]
    assert not manifest.get("encrypted")
    reads = tmp_path / "reads.fastq"
    reads.write_bytes(gzip.decompress((FIXTURES / series / f"{case}.reads.fastq.gz").read_bytes()))
    want_sha, want = _decode(reads, tmp_path / "ref.vnx", "reference", monkeypatch)
    assert want_sha == manifest["container_sha256"]
    levels = na.supported_levels()
    assert "scalar" in levels
    for level in levels:
        sha, got = _decode(reads, tmp_path / f"{level}.vnx", level, monkeypatch)
        assert na.active_level(level) == level
        assert sha == want_sha, f"{series}/{case} at {level}: container differs"
        assert got == want, f"{series}/{case} at {level}: decoder statistics differ from the reference"
