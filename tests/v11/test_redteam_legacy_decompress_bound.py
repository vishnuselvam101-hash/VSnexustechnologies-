"""V11-RT-04 (MEDIUM): the legacy v0.1 / RD-1 reader (``vnx-dna`` legacy restore) bounded zstd with
``ZstdDecompressor.decompress(max_output_size=...)``, which allocates the size a frame header *declares* (a 50-byte
frame can ask for ~4 GiB; an 8 GiB-declaring 262 KB file raised MemoryError). Decompression is now bounded by the
manifest's declared plaintext size with the stream reader, and a mismatch is a typed IntegrityError.
SYNTHETIC SOFTWARE TEST data."""
from __future__ import annotations

import subprocess
import sys
import zlib

import pytest
import zstandard

from vnxdna.core.taxonomy import IntegrityError
from vnxdna.legacy import v0_1


def _forged_frame() -> bytes:
    frame = bytearray(zstandard.ZstdCompressor(level=3, write_content_size=True).compress(bytes(1 << 20)))
    assert frame[4] >> 6 == 2 and (frame[4] >> 5) & 1      # 4-byte content size, single segment: bytes 5..8
    frame[5:9] = (0xF0000000).to_bytes(4, "little")         # declares ~3.75 GiB
    return bytes(frame)


def test_forged_declared_size_is_refused_in_bounded_memory(tmp_path):
    (tmp_path / "f.zst").write_bytes(_forged_frame())
    code = (
        "import resource, sys\n"
        "resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))\n"
        "from vnxdna.legacy import v0_1\n"
        "from vnxdna.core.taxonomy import IntegrityError\n"
        "data = open(sys.argv[1], 'rb').read()\n"
        "try:\n"
        "    v0_1._decompress(data, 'zstandard', 1 << 20)\n"
        "except IntegrityError:\n"
        "    print('REFUSED')\n"
    )
    r = subprocess.run([sys.executable, "-c", code, str(tmp_path / "f.zst")], capture_output=True, text=True, timeout=120)
    assert r.stdout.strip() == "REFUSED", (r.stdout, r.stderr[-800:])


@pytest.mark.parametrize("algorithm", ["zstandard", "zlib"])
def test_output_longer_or_shorter_than_declared_is_refused(algorithm):
    plain = bytes(range(256)) * 64
    blob = zlib.compress(plain) if algorithm == "zlib" else zstandard.ZstdCompressor().compress(plain)
    assert v0_1._decompress(blob, algorithm, len(plain)) == plain
    with pytest.raises(IntegrityError):
        v0_1._decompress(blob, algorithm, len(plain) - 1)
    with pytest.raises(IntegrityError):
        v0_1._decompress(blob, algorithm, len(plain) + 1)


def test_none_is_unchanged():
    assert v0_1._decompress(b"abc", "none", 3) == b"abc"
