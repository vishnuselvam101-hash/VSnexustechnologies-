"""Build hook for the optional native kernels (all other metadata is in pyproject.toml).

Four plain C shared libraries loaded with ctypes (no Python C API): the V5 marker aligner, the V6 read parser, the
V6 inner Reed-Solomon decoder (runtime SIMD dispatch, no -march) and the V7 read-clustering kernels. Each extension
is ``optional``: if no C compiler is available (or one kernel fails to compile), installation still succeeds and
VNX-DNA uses the bit-identical NumPy reference for that kernel. ``python -m vnxdna.native`` reports which backend every kernel uses.
Flags and sources come from src/vnxdna/_native_build.py, shared with the explicit ``python -m … build`` commands.
"""
import importlib.util
from pathlib import Path

from setuptools import Extension, setup

_spec = importlib.util.spec_from_file_location("_vnx_native_build", Path(__file__).parent / "src" / "vnxdna" / "_native_build.py")
_nb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nb)

setup(ext_modules=[Extension(name, [source], extra_compile_args=list(_nb.COMPILE_FLAGS), optional=True)
                   for name, source in _nb.KERNELS.items()])
