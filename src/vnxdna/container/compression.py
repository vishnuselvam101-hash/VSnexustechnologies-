"""Compression with bounded decompression (decompression-bomb safe)."""
from __future__ import annotations

import io
import zlib

import zstandard

from vnxdna.core.taxonomy import ConfigurationError, IntegrityError

ALGORITHMS = ("none", "zlib", "zstd")
LEVELS = {"none": (0, 0), "zlib": (0, 9), "zstd": (1, 22)}


def validate(algorithm: str, level: int) -> None:
    if algorithm not in ALGORITHMS:
        raise ConfigurationError(f"unknown compression {algorithm!r}; supported: {list(ALGORITHMS)}")
    low, high = LEVELS[algorithm]
    if not isinstance(level, int) or isinstance(level, bool) or not low <= level <= high:
        raise ConfigurationError(f"{algorithm} level must be in {low}..{high}")


def compress(data: bytes, algorithm: str, level: int) -> bytes:
    validate(algorithm, level)
    if algorithm == "none":
        return data
    if algorithm == "zlib":
        return zlib.compress(data, level)
    return zstandard.ZstdCompressor(level=level, write_content_size=True, write_checksum=False).compress(data)


def decompress(data: bytes, algorithm: str, expected_size: int) -> bytes:
    """Decompress, refusing to produce more than ``expected_size`` bytes."""
    if algorithm == "none":
        out = data
    elif algorithm == "zlib":
        try:
            engine = zlib.decompressobj()
            out = engine.decompress(data, expected_size + 1)
            if engine.unconsumed_tail or not engine.eof:
                raise IntegrityError("zlib stream is truncated or longer than the recorded size")
        except zlib.error as error:
            raise IntegrityError(f"zlib stream is corrupt: {error}") from error
    elif algorithm == "zstd":
        try:
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data), read_across_frames=False) as reader:
                out = reader.read(expected_size + 1)
        except zstandard.ZstdError as error:
            raise IntegrityError(f"zstd stream is corrupt: {error}") from error
    else:
        raise ConfigurationError(f"unknown compression {algorithm!r}")
    if len(out) != expected_size:
        raise IntegrityError(f"decompressed size {len(out)} does not match recorded size {expected_size}")
    return out
