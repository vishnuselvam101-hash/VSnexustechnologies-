"""VNX-DNA: computational DNA data-storage research platform.

The supported entry points are the ``vnx-dna`` CLI and :mod:`vnxdna.api`. :func:`native_status` reports which backend
(native C kernel or NumPy reference) each accelerated kernel uses.
"""
from ._version import __version__

__all__ = ["__version__", "native_status"]


def native_status() -> dict:
    """Backend of every native kernel (aligner, read parser, RS decoder); see :mod:`vnxdna.native`. Never raises."""
    from .native import native_status as _status
    return _status()
