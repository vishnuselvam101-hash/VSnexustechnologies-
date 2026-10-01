"""VNX-DNA 3 additions that are not part of the format-5 codec itself.

The format-5 implementation lives in :mod:`vnxdna.v2` (the package name is kept
so that the VNX-DNA 2 Python API keeps working); V3 extends it in place and adds
:mod:`vnxdna.ecc.engine`, :mod:`vnxdna.ecc.rs_batch` and the modules here:

* :mod:`vnxdna.v3.sweep`: error-channel sweeps with recovery statistics
  (``vnx-dna simulate-errors``).
"""
