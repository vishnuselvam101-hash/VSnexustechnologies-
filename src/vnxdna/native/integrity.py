"""Integrity kernel (hybrid architecture, **Rust**): batched CRC-32 over frame rows.

``native/rust/integrity`` (crate ``vnx_integrity``, C ABI 1, loaded with ctypes like every kernel) computes the CRC-32
(zlib/IEEE) of each row of a strided byte matrix, bit-exact with the NumPy reference :func:`vnxdna.core.crc.crc32_rows`.
Rust was chosen for this component because it validates untrusted read data: lengths and offsets are checked with
checked arithmetic before any slice exists, rows are processed by scoped threads that each own a disjoint part of the
output, and a panic is caught at the ABI boundary and returned as an error code, never unwound into Python.

Backend: ``VNXDNA_INTEGRITY_BACKEND`` = auto | native | reference; library override ``VNXDNA_INTEGRITY_LIB``.
Build: ``python -m vnxdna.native.integrity build`` (needs cargo; offline, no third-party crates) or ``pip install``.
"""
from __future__ import annotations

import ctypes

import numpy as np

from vnxdna.core.crc import crc32_rows as crc32_rows_reference
from vnxdna.native._loader import NativeKernelError, RustKernel, module_contract

ABI_VERSION = 1
MAX_THREADS = 64
_ERRORS = {-1: "invalid argument", -3: "internal panic (caught at the ABI boundary)"}


def _bind(lib) -> None:
    i64, p = ctypes.c_int64, ctypes.c_void_p
    lib.vnx_ri_crc32_rows.restype = ctypes.c_int32
    lib.vnx_ri_crc32_rows.argtypes = [i64, i64, i64, p, i64, p, i64, i64]


KERNEL = RustKernel("integrity", "VNXDNA_INTEGRITY", ABI_VERSION, "vnx_ri_abi_version", _bind, "vnxdna.core.crc",
                    __name__, crate="integrity")
globals().update(module_contract(KERNEL))   # _HERE, _SOURCE, _INPLACE, CFLAGS, STRICT_CFLAGS, ENV, ENV_LIB, _candidates
NativeIntegrityError = NativeKernelError


def available() -> bool:
    return KERNEL.available()


def resolve_backend() -> str:
    return KERNEL.resolve()


def status() -> dict:
    return KERNEL.status()


def build(output=None, extra_flags=None, strict=None):
    return KERNEL.build(output, extra_flags, strict)


def crc32_rows_native(rows: np.ndarray, threads: int = 1) -> np.ndarray:
    """CRC-32 of every row of a (N, W) uint8 array with the Rust kernel; rows may be a strided view (no copy when
    the rows are C-contiguous in their last axis)."""
    lib = KERNEL.lib()
    
    # Validate input for list types - check for out-of-range or boolean values
    # We do this before np.asarray() to catch list inputs specifically
    if isinstance(rows, (list, tuple)):
        # Check each element for invalid values (only apply to list inputs)
        for i, row in enumerate(rows):
            if not isinstance(row, (list, tuple)):
                raise ValueError('Each row must be a list or tuple')
            for j, val in enumerate(row):
                # Check for boolean values
                if type(val) is bool:
                    raise ValueError(f'crc32_rows input contains boolean values')
                # Check for out-of-range values  
                if not isinstance(val, int) or val < 0 or val > 255:
                    raise ValueError(f'crc32_rows input contains values outside the range 0..255')
    
    rows = np.asarray(rows)
    if rows.ndim != 2:
        raise ValueError("crc32_rows expects a 2-D array")
    
    if rows.dtype != np.uint8:
        rows = rows.astype(np.uint8)
    n, w = rows.shape
    out = np.empty(n, dtype=np.uint32)
    if n == 0:
        return out
    if rows.strides[1] != 1 or rows.strides[0] < w or w == 0:
        rows = np.ascontiguousarray(rows)
    stride = rows.strides[0] if n > 1 else w
    data_len = (n - 1) * stride + w
    t = max(1, min(int(threads), MAX_THREADS))
    rc = lib.vnx_ri_crc32_rows(n, w, stride, rows.ctypes.data, data_len, out.ctypes.data, n, t)
    if rc != 0:
        raise NativeIntegrityError(f"native crc32_rows failed ({rc}: {_ERRORS.get(rc, 'unknown')})")
    return out


def crc32_rows(rows: np.ndarray, threads: int = 1) -> np.ndarray:
    """CRC-32 of every row of a (N, W) uint8 array, (N,) uint32: the Rust kernel when selected and loadable, else the
    NumPy reference (identical results)."""
    if resolve_backend() == "native":
        return crc32_rows_native(rows, threads)
    return crc32_rows_reference(rows)


def main(argv=None) -> int:
    return KERNEL.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
