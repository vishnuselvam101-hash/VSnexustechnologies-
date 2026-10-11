"""Native encode kernel (hybrid architecture): the strand builder of ``vnxdna.dnaenc.frame4.build_strands``.

``native/c/strand.c`` reproduces the NumPy reference bit for bit: scrambler variant, inner RS parity (with the
reference's own GF(256) table and parity matrix, passed in), 2-bit mapping, sync markers and the sequence-dependent
constraint rules, first passing variant per row. Rows are split into contiguous ranges over threads (ctypes releases
the GIL); every row depends only on itself, so the result does not depend on the thread count.

Backend: ``VNXDNA_STRAND_BACKEND`` = auto | native | reference; library override ``VNXDNA_STRAND_LIB``.
Build: ``python -m vnxdna.native.strand build`` (or ``pip install``).
"""
from __future__ import annotations

import ctypes

import numpy as np

from vnxdna.native._loader import Kernel, NativeKernelError, module_contract

ABI_VERSION = 1
MAX_SPAN = 4096
MAX_MOTIF = 64
_ERRORS = {-1: "invalid argument", -2: "out of memory"}


def _bind(lib) -> None:
    i64, p = ctypes.c_int64, ctypes.c_void_p
    lib.vnx_st_build.restype = ctypes.c_int
    lib.vnx_st_build.argtypes = [i64, i64, i64, i64, p, i64, p, i64, p, p, i64, p,
                                 i64, i64, i64, i64, i64, i64, i64, i64, p, i64, p, p, p, p]


KERNEL = Kernel("strand", "VNXDNA_STRAND", ABI_VERSION, "vnx_st_abi_version", _bind, "vnxdna.dnaenc.frame4", __name__)
globals().update(module_contract(KERNEL))   # _HERE, _SOURCE, _INPLACE, CFLAGS, STRICT_CFLAGS, ENV, ENV_LIB, _candidates
NativeStrandError = NativeKernelError


def available() -> bool:
    return KERNEL.available()


def resolve_backend() -> str:
    return KERNEL.resolve()


def status() -> dict:
    return KERNEL.status()


def build(output=None, extra_flags=None, strict=None):
    return KERNEL.build(output, extra_flags, strict)


def build_rows(plain: np.ndarray, ks: np.ndarray, matrix: np.ndarray, mul: np.ndarray, tpl: np.ndarray, rules: dict,
               motifs: list[np.ndarray], nthreads: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """(N, strand_nt) codes and (N,) chosen variant (-1 = no variant passes), as frame4.build_strands before its error.
    ``rules``: gc_min, gc_max, win, wmin, wmax, hmax, tandem (ints)."""
    lib = KERNEL.lib()
    plain = np.ascontiguousarray(plain, dtype=np.uint8)
    ks = np.ascontiguousarray(ks, dtype=np.uint8)
    matrix = np.ascontiguousarray(matrix, dtype=np.uint8)
    mul = np.ascontiguousarray(mul, dtype=np.uint8)
    tpl = np.ascontiguousarray(tpl, dtype=np.int16)
    n, span = plain.shape
    nvar = ks.shape[0]
    r = matrix.shape[1] if matrix.ndim == 2 else 0
    if ks.shape[1:] != (span,) or matrix.shape != (1 + span, r) or mul.shape != (256, 256):
        raise ValueError("ks must be (V, span), matrix (1 + span, r) and mul (256, 256)")
    mbuf = np.ascontiguousarray(np.concatenate(motifs) if motifs else np.zeros(1), dtype=np.uint8)
    mlen = np.array([m.size for m in motifs], dtype=np.int64)
    moff = np.concatenate([[0], np.cumsum(mlen)[:-1]]).astype(np.int64) if motifs else np.zeros(0, np.int64)
    out = np.empty((n, tpl.size), dtype=np.uint8)
    chosen = np.empty(n, dtype=np.int16)
    parts = max(1, min(int(nthreads), n))
    bounds = [n * k // parts for k in range(parts)] + [n]

    def run(k: int) -> int:
        return lib.vnx_st_build(n, bounds[k], bounds[k + 1], span, plain.ctypes.data, nvar, ks.ctypes.data, r,
                                matrix.ctypes.data, mul.ctypes.data, tpl.size, tpl.ctypes.data,
                                rules["gc_min"], rules["gc_max"], rules["win"], rules["wmin"], rules["wmax"],
                                rules["hmax"], rules["tandem"], len(motifs), mbuf.ctypes.data, mbuf.size,
                                moff.ctypes.data if motifs else None, mlen.ctypes.data if motifs else None,
                                out.ctypes.data, chosen.ctypes.data)

    if parts == 1:
        rcs = [run(0)]
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(parts) as pool:
            rcs = list(pool.map(run, range(parts)))
    for rc in rcs:
        if rc < 0:
            raise NativeStrandError(f"native strand build failed ({rc}: {_ERRORS.get(rc, 'unknown')})")
    return out, chosen


def main(argv=None) -> int:
    return KERNEL.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
