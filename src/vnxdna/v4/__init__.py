"""VNX-DNA V4: multi-file archive format (VNX4), strand frame v4 with sync markers, configurable channel simulator,
indel-aware reconstruction, pluggable outer codes, benchmarks and reproducible experiments.

V3 (format 5, ``vnx-dna``) is unchanged and remains available; V4 is a separate format (``vnx``).
Everything here is software and simulation; nothing has been synthesised or sequenced.
"""
from .version import FORMAT_VERSION, __version__

__all__ = ["__version__", "FORMAT_VERSION"]
