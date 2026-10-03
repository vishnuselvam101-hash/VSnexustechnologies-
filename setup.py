"""Build hook for the optional V5 native aligner (all other metadata is in pyproject.toml).

The extension is a plain C shared library loaded with ctypes (no Python C API). It is ``optional``: if no C compiler
is available, installation still succeeds and VNX-DNA uses the bit-identical NumPy reference aligner.
`python -m vnxdna.v5.native_alignment` reports which backend is active.
"""
from setuptools import Extension, setup

setup(ext_modules=[Extension("vnxdna.v5._vnx_align", ["src/vnxdna/v5/native/align.c"],
                             extra_compile_args=["-O3", "-std=c11"], optional=True)])
