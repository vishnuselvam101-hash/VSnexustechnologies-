"""Build configuration of the native (C) kernels: the single source for setup.py and the explicit ``build`` commands.

Standard library only: setup.py loads this file by path, before NumPy or the package can be imported.

Every kernel is a plain C shared library loaded with ctypes (no Python C API). ``pip install`` builds all of them as
*optional* extensions: without a working C compiler the installation still succeeds and VNX-DNA runs the bit-identical
NumPy reference implementations (``vnxdna.native_status()`` / ``python -m vnxdna.native`` shows which backend runs).

Flags: ``-O3 -std=c11 -Wall -Wextra`` everywhere. ``-Werror`` is only added to *strict* builds (CI and the sanitizer
scripts, ``python -m vnxdna.<module> build --strict`` or ``VNXDNA_NATIVE_STRICT=1``), so a warning introduced by a
newer compiler cannot make an install or a local build silently fall back to the reference. No ``-march``/``-mavx*``:
the RS decoder (scalar / AVX2 / AVX-512BW) and the cluster forward-backward kernel (baseline / AVX2) select their SIMD
level at run time from cpuid + xgetbv, so one binary (and one wheel) is correct on every x86-64 CPU.
"""
from __future__ import annotations

import os

#: extension module name (as built by setup.py) -> source file relative to the repository root
KERNELS = {
    "vnxdna.v5._vnx_align": "src/vnxdna/v5/native/align.c",
    "vnxdna.v6._vnx_reads": "src/vnxdna/v6/native/reads.c",
    "vnxdna.v6._vnx_rs": "src/vnxdna/v6/native/rs.c",
    "vnxdna._vnx_cluster": "src/vnxdna/native/c/cluster.c",
}

#: compile flags shared by setup.py and the explicit builds (setuptools adds -fPIC/-shared itself)
COMPILE_FLAGS = ["-O3", "-std=c11", "-Wall", "-Wextra"]
#: link/shape flags of an explicit build with a bare compiler
SHARED_FLAGS = ["-fPIC", "-shared"]
#: added to strict builds only (CI, sanitizers)
STRICT_FLAGS = ["-Werror"]
STRICT_ENV = "VNXDNA_NATIVE_STRICT"


def explicit_cflags(strict: bool | None = None) -> list[str]:
    """Flags of ``python -m vnxdna.<module> build``: the install flags plus -fPIC/-shared, and -Werror when strict."""
    if strict is None:
        strict = os.environ.get(STRICT_ENV) == "1"
    return ["-O3", "-std=c11", *SHARED_FLAGS, *COMPILE_FLAGS[2:], *(STRICT_FLAGS if strict else [])]
