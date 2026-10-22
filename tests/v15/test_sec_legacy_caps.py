"""F4: the legacy V0.1 decoder must not size an allocation from an untrusted manifest.

``legacy/v0_1.py`` read the manifest whole and took ``strand_count`` and ``stripe_count`` at face value: the list
of the missing strand indices and the dict of the stripes were sized by the manifest, so a forged manifest could
make the decoder allocate hundreds of megabytes before it refused the archive. The counts are capped now, the
manifest is read within a bound, the strands file is read line by line, and every cap is reported as a typed
``MetadataError``.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from vnxdna.errors import MetadataError
from vnxdna.legacy import v0_1

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "v0_1")


def _dataset(tmp_path, **overrides):
    """Write a forged V0.1 dataset (manifest plus an empty strands file) and return its directory."""
    manifest = {
        "format": "VNX-DNA",
        "format_version": 1,
        "dataset_id": "TEST01",
        "original_sha256": "0" * 64,
        "original_size": 16,
        "transformed_size": 16,
        "strand_count": 4,
        "config": {
            "compression": "none",
            "encryption": False,
            "ecc": "none",
            "encoding": "baseline_binary_2bit",
            "constraints": {},
        },
    }
    manifest.update(overrides)
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "strands.fasta").write_text("", encoding="ascii")
    return root


def _rs_dataset(tmp_path, stripe_count):
    """The same, but a format 2 dataset (Reed-Solomon stripes) declaring ``stripe_count`` stripes."""
    root = _dataset(tmp_path, format_version=2)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["ecc"] = {"data_shards": 2, "parity_shards": 1}
    manifest["config"]["ecc"] = "reed_solomon"
    manifest["stripe_count"] = stripe_count
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_a_strand_count_above_the_cap_is_refused(tmp_path):
    root = _dataset(tmp_path, strand_count=v0_1.MAX_LEGACY_STRANDS + 1)
    with pytest.raises(MetadataError, match="strand_count"):
        v0_1.decode_v01_dataset(root)


def test_a_stripe_count_above_the_cap_is_refused(tmp_path):
    root = _rs_dataset(tmp_path, v0_1.MAX_LEGACY_STRIPES + 1)
    with pytest.raises(MetadataError, match="stripe_count"):
        v0_1.decode_v01_dataset(root)


def test_a_manifest_larger_than_the_bound_is_refused(tmp_path):
    root = _dataset(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["padding"] = "x" * v0_1.MAX_LEGACY_MANIFEST_BYTES
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert (root / "manifest.json").stat().st_size > v0_1.MAX_LEGACY_MANIFEST_BYTES
    with pytest.raises(MetadataError, match="larger than"):
        v0_1.decode_v01_dataset(root)


def test_a_strand_line_longer_than_the_bound_is_refused(tmp_path):
    root = _dataset(tmp_path)
    (root / "strands.fasta").write_text(">VNX1|TEST01|0|4|16|" + "0" * 64 + "\n"
                                         + "A" * (v0_1.MAX_LEGACY_LINE_CHARS + 1) + "\n", encoding="ascii")
    with pytest.raises(MetadataError, match="line is longer than"):
        v0_1.decode_v01_dataset(root)


def test_a_chunk_count_above_the_cap_is_refused(tmp_path):
    archive = {
        "manifest": {
            "format": "VNXDNA",
            "format_version": "0.1",
            "ecc": {"name": "none"},
            "compression": {"enabled": False},
            "encryption": {"enabled": False},
        },
        "files": [
            {
                "manifest": {
                    "file_id": "f1",
                    "chunks": v0_1.MAX_LEGACY_STRANDS + 1,
                    "sha256": "0" * 64,
                    "original_size": 16,
                },
                "chunks": [],
            }
        ],
    }
    path = tmp_path / "rd1.json"
    path.write_text(json.dumps(archive), encoding="utf-8")
    with pytest.raises(MetadataError, match="chunks"):
        v0_1.decode_rd1_archive(path)


def test_a_huge_stripe_count_is_refused_without_exhausting_the_memory(tmp_path):
    """The stripe dict is sized by the manifest: a forged count must be refused, not allocated."""
    root = _rs_dataset(tmp_path, 10_000_000)
    code = (
        "import resource, sys\n"
        "resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))\n"
        "from vnxdna.legacy import v0_1\n"
        "from vnxdna.core.taxonomy import MetadataError\n"
        "try:\n"
        "    v0_1.decode_v01_dataset(sys.argv[1])\n"
        "except MetadataError:\n"
        "    print('REFUSED')\n"
    )
    result = subprocess.run([sys.executable, "-c", code, str(root)], capture_output=True, text=True, timeout=120)
    assert result.stdout.strip() == "REFUSED", (result.stdout, result.stderr[-800:])


def test_an_ordinary_fixture_still_decodes():
    """The caps do not change a valid archive: the V0.1 fixtures still decode to their recorded digest."""
    meta = json.load(open(os.path.join(FIX, "fixtures.json"), encoding="utf-8"))
    fixture = next(f for f in meta["fixtures"] if f["name"] == "dataset_v1_zlib")
    data, _ = v0_1.decode_legacy(os.path.join(FIX, fixture["name"]))
    assert hashlib.sha256(data).hexdigest() == fixture["input_sha256"]
