"""Test for V15 Task J8: Stream manifest extent fields should require type(int) not bool.

This test ensures that True/False in index, length, strands_offset, strands_bytes 
raise VNXFormatError instead of being accepted.
"""
import tempfile
from pathlib import Path

import pytest

from vnxdna.core.errors import VNXFormatError
from vnxdna.core.util import canonical_json
from vnxdna.pipeline.stream import encode_stream, load_manifest, StreamOptions


def test_valid_manifest_still_loads():
    """A valid manifest should still load correctly."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create a simple stream and get its manifest - use valid extent_bytes
        data = b"test data for streaming" * 10
        src = Path(tmp) / "input.bin"
        src.write_bytes(data)
        
        sink = Path(tmp) / "output.fasta"
        manifest = Path(tmp) / "manifest.json"
        
        # Encode a stream to get a valid manifest - use valid extent_bytes (at least 4096)
        doc = encode_stream(src, sink, options=StreamOptions(extent_bytes=4096), manifest=manifest)
        assert doc["format"] == "vnx.stream/1"
        
        # Load the manifest - this should work fine
        loaded_doc = load_manifest(manifest)
        assert loaded_doc["format"] == "vnx.stream/1"
        assert len(loaded_doc["extents"]) > 0
        
        # Check that the fields are integers (not booleans)
        for extent in loaded_doc["extents"]:
            assert isinstance(extent["index"], int)
            assert isinstance(extent["length"], int)
            assert isinstance(extent["strands_offset"], int)
            assert isinstance(extent["strands_bytes"], int)


def test_bool_values_in_index_raise_format_error():
    """Test that boolean values in index field raise VNXFormatError."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create a manifest manually with boolean values in index
        manifest_data = {
            "format": "vnx.stream/1",
            "extent_bytes": 4096,
            "strands_format": "fasta",
            "total_bytes": 100,
            "sha256": "a" * 64,
            "extents": [
                {
                    "index": True,  # This should raise an error (boolean, not int)
                    "length": 50,
                    "sha256": "b" * 64,
                    "archive_id": "test-id",
                    "archive_tag": "abcd",
                    "strands_offset": 0,
                    "strands_bytes": 100,
                    "strands_sha256": "c" * 64
                }
            ],
            "strands_sha256": "d" * 64,
            "strands_bytes": 100,
            "dna": {"profile": "test", "outer_code": "test"},
            "encrypted": False
        }

        manifest_path = Path(tmp) / "manifest.json"
        manifest_path.write_bytes(canonical_json(manifest_data))

        # This should raise VNXFormatError
        with pytest.raises(VNXFormatError) as exc_info:
            load_manifest(manifest_path)

        assert "must be a non-negative integer" in str(exc_info.value)


def test_bool_index_on_extent_one_raises_format_error():
    """A boolean index on extent 1 is refused (O-9).

    ``True == 1`` in Python, so the order check alone (``rec["index"] != i``) accepts a boolean on extent 1; the
    loader must refuse it with the same error it raises for any other bad extent field.
    """
    with tempfile.TemporaryDirectory() as tmp:
        manifest_data = {
            "format": "vnx.stream/1",
            "extent_bytes": 4096,
            "strands_format": "fasta",
            "total_bytes": 100,
            "sha256": "a" * 64,
            "extents": [
                {
                    "index": 0,
                    "length": 50,
                    "sha256": "b" * 64,
                    "archive_id": "test-id",
                    "archive_tag": "abcd",
                    "strands_offset": 0,
                    "strands_bytes": 50,
                    "strands_sha256": "c" * 64
                },
                {
                    "index": True,   # True == 1, so the order check alone would accept this
                    "length": 50,
                    "sha256": "d" * 64,
                    "archive_id": "test-id-2",
                    "archive_tag": "efgh",
                    "strands_offset": 50,
                    "strands_bytes": 50,
                    "strands_sha256": "f" * 64
                }
            ],
            "strands_sha256": "e" * 64,
            "strands_bytes": 100,
            "dna": {"profile": "test", "outer_code": "test"},
            "encrypted": False
        }

        manifest_path = Path(tmp) / "manifest.json"
        manifest_path.write_bytes(canonical_json(manifest_data))

        with pytest.raises(VNXFormatError) as exc_info:
            load_manifest(manifest_path)

        assert "must be a non-negative integer" in str(exc_info.value)
        assert "'index'" in str(exc_info.value)


def test_bool_values_in_length_raise_format_error():
    """Test that boolean values in length field raise VNXFormatError."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create a manifest manually with boolean values in length
        manifest_data = {
            "format": "vnx.stream/1",
            "extent_bytes": 4096,
            "strands_format": "fasta",
            "total_bytes": 100,
            "sha256": "a" * 64,
            "extents": [
                {
                    "index": 0,
                    "length": True,  # This should raise an error (boolean, not int)
                    "sha256": "b" * 64,
                    "archive_id": "test-id",
                    "archive_tag": "abcd",
                    "strands_offset": 0,
                    "strands_bytes": 100,
                    "strands_sha256": "c" * 64
                }
            ],
            "strands_sha256": "d" * 64,
            "strands_bytes": 100,
            "dna": {"profile": "test", "outer_code": "test"},
            "encrypted": False
        }
        
        manifest_path = Path(tmp) / "manifest.json"
        manifest_path.write_bytes(canonical_json(manifest_data))
        
        # This should raise VNXFormatError
        with pytest.raises(VNXFormatError) as exc_info:
            load_manifest(manifest_path)
        
        assert "must be a non-negative integer" in str(exc_info.value)


def test_bool_values_in_strands_offset_raise_format_error():
    """Test that boolean values in strands_offset field raise VNXFormatError."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create a manifest manually with boolean values in strands_offset
        manifest_data = {
            "format": "vnx.stream/1",
            "extent_bytes": 4096,
            "strands_format": "fasta",
            "total_bytes": 100,
            "sha256": "a" * 64,
            "extents": [
                {
                    "index": 0,
                    "length": 50,
                    "sha256": "b" * 64,
                    "archive_id": "test-id",
                    "archive_tag": "abcd",
                    "strands_offset": True,  # This should raise an error (boolean, not int)
                    "strands_bytes": 100,
                    "strands_sha256": "c" * 64
                }
            ],
            "strands_sha256": "d" * 64,
            "strands_bytes": 100,
            "dna": {"profile": "test", "outer_code": "test"},
            "encrypted": False
        }
        
        manifest_path = Path(tmp) / "manifest.json"
        manifest_path.write_bytes(canonical_json(manifest_data))
        
        # This should raise VNXFormatError
        with pytest.raises(VNXFormatError) as exc_info:
            load_manifest(manifest_path)
        
        assert "must be a non-negative integer" in str(exc_info.value)


def test_bool_values_in_strands_bytes_raise_format_error():
    """Test that boolean values in strands_bytes field raise VNXFormatError."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create a manifest manually with boolean values in strands_bytes
        manifest_data = {
            "format": "vnx.stream/1",
            "extent_bytes": 4096,
            "strands_format": "fasta",
            "total_bytes": 100,
            "sha256": "a" * 64,
            "extents": [
                {
                    "index": 0,
                    "length": 50,
                    "sha256": "b" * 64,
                    "archive_id": "test-id",
                    "archive_tag": "abcd",
                    "strands_offset": 0,
                    "strands_bytes": True,  # This should raise an error (boolean, not int)
                    "strands_sha256": "c" * 64
                }
            ],
            "strands_sha256": "d" * 64,
            "strands_bytes": 100,
            "dna": {"profile": "test", "outer_code": "test"},
            "encrypted": False
        }
        
        manifest_path = Path(tmp) / "manifest.json"
        manifest_path.write_bytes(canonical_json(manifest_data))
        
        # This should raise VNXFormatError
        with pytest.raises(VNXFormatError) as exc_info:
            load_manifest(manifest_path)
        
        assert "must be a non-negative integer" in str(exc_info.value)


def test_negative_values_still_raise_format_error():
    """Test that negative values still raise VNXFormatError (this should already work)."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create a manifest manually with negative value
        manifest_data = {
            "format": "vnx.stream/1",
            "extent_bytes": 4096,
            "strands_format": "fasta",
            "total_bytes": 100,
            "sha256": "a" * 64,
            "extents": [
                {
                    "index": 0,  # This should be correct for the order check
                    "length": 50,
                    "sha256": "b" * 64,
                    "archive_id": "test-id",
                    "archive_tag": "abcd",
                    "strands_offset": 0,
                    "strands_bytes": -1,  # This should raise an error (negative)
                    "strands_sha256": "c" * 64
                }
            ],
            "strands_sha256": "d" * 64,
            "strands_bytes": 100,
            "dna": {"profile": "test", "outer_code": "test"},
            "encrypted": False
        }
        
        manifest_path = Path(tmp) / "manifest.json"
        manifest_path.write_bytes(canonical_json(manifest_data))
        
        # This should raise VNXFormatError
        with pytest.raises(VNXFormatError) as exc_info:
            load_manifest(manifest_path)
        
        assert "must be a non-negative integer" in str(exc_info.value)