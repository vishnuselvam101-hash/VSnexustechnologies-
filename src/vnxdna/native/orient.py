"""Native kernel of the V12 pass-1 orientation pre-pass (``vnxdna.recovery.pass1._orientation``).

``native/c/orient.c`` reproduces ``pass1._orientation_reference`` bit for bit: per marker block, the best agreement
over shifts of up to ``max_shift`` nt, forward against reverse complement, with the reference's early exit for reads
whose forward agreement already settles the decision. Every read depends only on itself; the call releases the GIL
(ctypes) and keeps no state, so the result does not depend on batching or on the worker count.

Backend: ``VNXDNA_ORIENT_BACKEND`` = auto | native | reference; library override ``VNXDNA_ORIENT_LIB``.
Build: ``python -m vnxdna.native.orient build`` (or ``pip install``).
"""
from __future__ import annotations

import ctypes

import numpy as np

from vnxdna.native._loader import Kernel, NativeKernelError, module_contract

ABI_VERSION = 2            # 2 (V13): adds vnx_or_rc_bound
_ERRORS = {-1: "invalid argument", -2: "out of memory"}


def _bind(lib) -> None:
    i64, p = ctypes.c_int64, ctypes.c_void_p
    lib.vnx_or_scan.restype = ctypes.c_int
    lib.vnx_or_scan.argtypes = [i64, p, i64, p, p, i64, p, p, p, i64, i64, i64, p]
    lib.vnx_or_rc_bound.restype = ctypes.c_int
    lib.vnx_or_rc_bound.argtypes = [i64, p, i64, p, p, i64, p, p, p, i64, i64, i64, i64, i64, p]


KERNEL = Kernel("orient", "VNXDNA_ORIENT", ABI_VERSION, "vnx_or_abi_version", _bind, "vnxdna.recovery.pass1", __name__)
globals().update(module_contract(KERNEL))   # _HERE, _SOURCE, _INPLACE, CFLAGS, STRICT_CFLAGS, ENV, ENV_LIB, _candidates
NativeOrientError = NativeKernelError


def available() -> bool:
    return KERNEL.available()


def resolve_backend() -> str:
    return KERNEL.resolve()


def status() -> dict:
    return KERNEL.status()


def build(output=None, extra_flags=None, strict=None):
    return KERNEL.build(output, extra_flags, strict)


def scan(codes: np.ndarray, lengths: np.ndarray, mpos: np.ndarray, want: np.ndarray, starts: np.ndarray,
         margin: int, max_shift: int) -> np.ndarray:
    """(n,) bool: True where the read is better explained as a reverse complement (pass1._orientation_reference).

    ``codes``: flat uint8 codes of the batch; ``lengths``: per-read lengths; ``mpos``/``want``: marker positions and
    codes of the layout template; ``starts``: index into ``mpos`` of the first marker of each block."""
    lib = KERNEL.lib()
    codes = np.ascontiguousarray(codes, dtype=np.uint8)
    lengths = np.ascontiguousarray(lengths, dtype=np.int64)
    mpos = np.ascontiguousarray(mpos, dtype=np.int64)
    want = np.ascontiguousarray(want, dtype=np.uint8)
    starts = np.ascontiguousarray(starts, dtype=np.int64)
    if codes.ndim != 1 or lengths.ndim != 1 or mpos.ndim != 1 or want.ndim != 1 or starts.ndim != 1:
        raise ValueError("codes, lengths, mpos, want and starts must be one-dimensional")
    if want.size != mpos.size:
        raise ValueError("want must have one code per marker position")
    if lengths.size and (lengths.min() < 0 or lengths.sum() > codes.size):
        raise ValueError("lengths must be non-negative and sum to at most codes.size")
    if margin < 0 or max_shift < 0:
        raise ValueError("margin and max_shift must be non-negative")
    n = lengths.size
    offs = np.zeros(n, dtype=np.int64)
    if n > 1:
        np.cumsum(lengths[:-1], out=offs[1:])
    block = np.zeros(mpos.size, dtype=np.int64)
    if mpos.size:
        if starts.size == 0 or starts[0] != 0 or (starts.min() < 0 or starts.max() >= mpos.size):
            raise ValueError("starts must begin at 0 and index mpos")
        block[starts[1:]] = 1
        np.cumsum(block, out=block)
    flip = np.zeros(n, dtype=np.uint8)
    rc = lib.vnx_or_scan(n, codes.ctypes.data, codes.size, lengths.ctypes.data, offs.ctypes.data, mpos.size,
                         mpos.ctypes.data, want.ctypes.data, block.ctypes.data, starts.size if mpos.size else 0,
                         int(margin), int(max_shift), flip.ctypes.data)
    if rc < 0:
        raise NativeOrientError(f"native orientation scan failed ({rc}: {_ERRORS.get(rc, 'unknown')})")
    return flip.astype(bool)


def _template_args(codes, lengths, mpos, want, starts):
    """Validated, contiguous copies of the batch and template arguments shared by :func:`scan` and :func:`rc_bound`
    plus the per-marker block index."""
    codes = np.ascontiguousarray(codes, dtype=np.uint8)
    lengths = np.ascontiguousarray(lengths, dtype=np.int64)
    mpos = np.ascontiguousarray(mpos, dtype=np.int64)
    want = np.ascontiguousarray(want, dtype=np.uint8)
    starts = np.ascontiguousarray(starts, dtype=np.int64)
    if codes.ndim != 1 or lengths.ndim != 1 or mpos.ndim != 1 or want.ndim != 1 or starts.ndim != 1:
        raise ValueError("codes, lengths, mpos, want and starts must be one-dimensional")
    if want.size != mpos.size:
        raise ValueError("want must have one code per marker position")
    block = np.zeros(mpos.size, dtype=np.int64)
    if mpos.size:
        if starts.size == 0 or starts[0] != 0 or (starts.min() < 0 or starts.max() >= mpos.size):
            raise ValueError("starts must begin at 0 and index mpos")
        block[starts[1:]] = 1
        np.cumsum(block, out=block)
    return codes, lengths, mpos, want, starts, block


def rc_bound(codes: np.ndarray, lengths: np.ndarray, offs: np.ndarray, mpos: np.ndarray, want: np.ndarray,
             starts: np.ndarray, band: int, c_mm: int, c_ins: int, c_del: int) -> np.ndarray:
    """(n,) int64: a lower bound on the band-``band`` marker-template alignment cost of each selected read taken as a
    reverse complement (``pass1._rc_bound_reference``; ABI 2). ``offs``/``lengths`` select any reads of ``codes``."""
    lib = KERNEL.lib()
    codes, lengths, mpos, want, starts, block = _template_args(codes, lengths, mpos, want, starts)
    offs = np.ascontiguousarray(offs, dtype=np.int64)
    if offs.ndim != 1 or offs.size != lengths.size:
        raise ValueError("offs must give one start per selected read")
    if lengths.size and (lengths.min() < 0 or offs.min() < 0 or (offs + lengths).max() > codes.size):
        raise ValueError("every selected read must lie inside codes")
    if min(band, c_mm, c_ins, c_del) < 0:
        raise ValueError("band and costs must be non-negative")
    out = np.zeros(lengths.size, dtype=np.int64)
    rc = lib.vnx_or_rc_bound(lengths.size, codes.ctypes.data, codes.size, lengths.ctypes.data, offs.ctypes.data,
                             mpos.size, mpos.ctypes.data, want.ctypes.data, block.ctypes.data,
                             starts.size if mpos.size else 0, int(band), int(c_mm), int(c_ins), int(c_del),
                             out.ctypes.data)
    if rc < 0:
        raise NativeOrientError(f"native reverse-complement bound failed ({rc}: {_ERRORS.get(rc, 'unknown')})")
    return out


def main(argv=None) -> int:
    return KERNEL.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
