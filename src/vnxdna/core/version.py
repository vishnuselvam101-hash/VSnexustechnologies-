"""V4 format identifiers. The software version is the package version (the encoder recorded in new archives)."""
from vnxdna._version import __version__

__all__ = ["__version__", "FORMAT_VERSION", "FRAME_VERSION"]
FORMAT_VERSION = (4, 0)   # VNX4 container major, minor
FRAME_VERSION = 4         # strand frame version nibble
