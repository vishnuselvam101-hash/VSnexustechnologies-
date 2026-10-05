"""Version registry (spec §4): format identifiers and every version axis a reader reports.

The software version is the package version (``vnxdna._version``, recorded as ``encoder.version`` and
``extensions.vnx.software`` in new archives). Formats are identified by these constants, never by the software version.
"""
from vnxdna._version import __version__

__all__ = ["__version__", "FORMAT_VERSION", "FRAME_VERSION", "SPEC_VERSION", "CONTAINER_READ", "CONTAINER_WRITE",
           "FRAME_READ", "FRAME_WRITE", "LEGACY_DETECTED", "SUPERBLOCK_READ", "SUPERBLOCK_WRITE", "CODECS", "codec_id"]
FORMAT_VERSION = (4, 0)   # VNX4 container major, minor
FRAME_VERSION = 4         # strand frame version nibble
SPEC_VERSION = "6.0"      # docs/spec/VNX-DNA-SPEC-V6.md

CONTAINER_READ = [list(FORMAT_VERSION)]
CONTAINER_WRITE = list(FORMAT_VERSION)
FRAME_READ = [FRAME_VERSION]                  # "VNX4 scrambler" domain nibbles this reader decodes
FRAME_WRITE = [FRAME_VERSION]
LEGACY_DETECTED = ["v1-frame4", "v3-frame5"]  # recognised by the probe and referred to vnx-dna (spec §3.2, §3.10)
SUPERBLOCK_READ = [1, 2]
SUPERBLOCK_WRITE = [1, 2]
CODECS = ["f4-sb1-cauchy-rs", "f4-sb2-cauchy-rs", "f4-sb1-lt-fountain"]


def codec_id(frame: int, superblock: int, outer: str) -> str:
    """Derived codec identifier ``f<frame>-sb<superblock>-<outer>`` (spec §4.1)."""
    return f"f{frame}-sb{superblock}-{outer}"
