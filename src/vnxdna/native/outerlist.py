"""Outer-code list decoding kernel (hybrid architecture, **C++**): ``native/cpp/outerlist.cpp``, C ABI 1, ctypes.

``assign`` maps failed consensus frames to missing addresses by header distance; ``decode_row`` recovers a short row
of the outer code from its verified symbols plus those unverified candidates (see the C++ file and
:mod:`vnxdna.recovery.outer_list`, whose Python implementation is the reference and the fallback).
C++ was chosen for this component because it is a combinatorial search with small dense GF(256) linear algebra per
subset, where std::vector-owned workspaces and exception-safe cleanup (bad_alloc -> error code) are simpler and safer
than manual C allocation, and no existing C kernel does this work.

Backend: ``VNXDNA_OUTERLIST_BACKEND`` = auto | native | reference; library override ``VNXDNA_OUTERLIST_LIB``.
Build: ``python -m vnxdna.native.outerlist build`` (needs a C++20 compiler) or ``pip install``.
"""
from __future__ import annotations

import ctypes

import numpy as np

from vnxdna.native._loader import (INT64_MAX, INT64_MIN, CppKernel, NativeArgumentError, NativeKernelError,
                                   checked_array, checked_int, module_contract)

ABI_VERSION = 1
MAX_PER_POS = 4
MAX_F = 8
_ERRORS = {-1: "invalid argument", -2: "out of memory", -3: "internal error (caught at the ABI boundary)"}


def _bind(lib) -> None:
    i64, p = ctypes.c_int64, ctypes.c_void_p
    lib.vnx_ol_assign.restype = ctypes.c_int
    lib.vnx_ol_assign.argtypes = [i64, i64, p, i64, i64, p, i64, p, i64, p, i64, i64, p, p, p, p]
    lib.vnx_ol_decode_row.restype = ctypes.c_int
    lib.vnx_ol_decode_row.argtypes = [i64, i64, i64, p, p, p, p, p, i64, p, p, p, i64, i64, i64, p, p]
    lib.vnx_ol_ransac.restype = ctypes.c_int
    lib.vnx_ol_ransac.argtypes = [i64, i64, i64, p, p, p, p, p, i64, p, p, i64, i64, p]


KERNEL = CppKernel("outerlist", "VNXDNA_OUTERLIST", ABI_VERSION, "vnx_ol_abi_version", _bind,
                   "vnxdna.recovery.outer_list", __name__)
globals().update(module_contract(KERNEL))   # _HERE, _SOURCE, _INPLACE, CFLAGS, STRICT_CFLAGS, ENV, ENV_LIB, _candidates
NativeOuterListError = NativeKernelError


def available() -> bool:
    return KERNEL.available()


def resolve_backend() -> str:
    return KERNEL.resolve()


def status() -> dict:
    return KERNEL.status()


def build(output=None, extra_flags=None, strict=None):
    return KERNEL.build(output, extra_flags, strict)


def _free(k: int, known: np.ndarray, what: str) -> None:
    """The kernels need 1 <= f = k - (known positions) <= MAX_F (OL_EARG otherwise); refuse it as the reference does."""
    f = int(k) - int(np.count_nonzero(known))
    if not 1 <= f <= MAX_F:
        raise ValueError(f"{what} needs 1 <= k - known <= {MAX_F} (got {f})")


def _check(rc: int, what: str) -> int:
    if rc < 0:
        raise NativeOuterListError(f"native {what} failed ({rc}: {_ERRORS.get(rc, 'unknown')})")
    return rc


def assign(frames: np.ndarray, ks: np.ndarray, variants: np.ndarray, targets: np.ndarray, max_dist: int,
           exact_mask: int = 0):
    """See vnxdna.recovery.outer_list.assign_reference."""
    # the arguments are checked before the call: a ctypes int64 argument narrows an out-of-range Python int without
    # a word and a uint8 cast wraps the codes that do not fit, so the kernel would work on different values than the
    # caller passed.
    max_dist = checked_int("max_dist", max_dist, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    exact_mask = checked_int("exact_mask", exact_mask, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    frames = checked_array("frames", frames, np.uint8, error=NativeArgumentError)
    ks = checked_array("ks", ks, np.uint8, error=NativeArgumentError)
    variants = checked_array("variants", variants, np.int16, error=NativeArgumentError)
    targets = checked_array("targets", targets, np.uint8, error=NativeArgumentError).reshape(-1, 9)
    n = frames.shape[0]
    lib = KERNEL.lib()
    tgt = np.empty(n, np.int32)
    var = np.empty(n, np.int16)
    dist = np.empty(n, np.int8)
    sec = np.empty(n, np.int8)
    if n:
        _check(lib.vnx_ol_assign(n, frames.shape[1], frames.ctypes.data, ks.shape[0], ks.shape[1], ks.ctypes.data,
                                 variants.size, variants.ctypes.data, targets.shape[0],
                                 targets.ctypes.data if targets.size else None, max_dist, exact_mask, tgt.ctypes.data,
                                 var.ctypes.data, dist.ctypes.data, sec.ctypes.data), "assign")
    return tgt, var, dist.astype(np.int64), sec.astype(np.int64)


def _sizes(parity, mul, inv, known, sym, rows=None, rows_name="") -> None:
    """V11-RT-09: the C++ ABI takes k, m and P from parity and sym and reads known (n), sym (n x P), mul (256 x 256), inv
    (256) and the candidate rows without a length; refuse any array of another size before the call."""
    if parity.ndim != 2 or sym.ndim != 2:
        raise ValueError("parity and sym must be 2-D")
    n = parity.shape[0] + parity.shape[1]
    if mul.size != 256 * 256 or inv.size != 256:
        raise ValueError("mul must hold 256 x 256 and inv 256 GF(256) entries")
    if known.shape != (n,) or sym.shape[0] != n:
        raise ValueError(f"known and sym must have n = k + m = {n} rows")
    if rows is not None:
        pos, *payloads = rows
        if pos.ndim != 1 or any(a.shape != (pos.size, sym.shape[1]) for a in payloads):
            raise ValueError(f"{rows_name} must have one row of P = {sym.shape[1]} bytes per position")


def decode_row(parity: np.ndarray, mul: np.ndarray, inv: np.ndarray, known: np.ndarray, sym: np.ndarray,
               cpos: np.ndarray, cval: np.ndarray, cok: np.ndarray, min_extra: int, margin: int, max_subsets: int):
    """See vnxdna.recovery.outer_list.decode_row_reference. Returns (sym, accepted columns, best, second)."""
    min_extra = checked_int("min_extra", min_extra, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    margin = checked_int("margin", margin, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    max_subsets = checked_int("max_subsets", max_subsets, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    parity = checked_array("parity", parity, np.uint8, error=NativeArgumentError)
    m, k = parity.shape
    # the kernel writes the recovered row into sym, so it keeps its own copy
    sym = np.array(checked_array("sym", sym, np.uint8, error=NativeArgumentError), dtype=np.uint8, order="C", copy=True)
    P = sym.shape[1]
    known = checked_array("known", known, np.uint8, error=NativeArgumentError)
    cpos = checked_array("cpos", cpos, np.int32, error=NativeArgumentError)
    cval = checked_array("cval", cval, np.uint8, error=NativeArgumentError).reshape(-1, P)
    cok = checked_array("cok", cok, np.uint8, error=NativeArgumentError).reshape(-1, P)
    mul = checked_array("mul", mul, np.uint8, error=NativeArgumentError)
    inv = checked_array("inv", inv, np.uint8, error=NativeArgumentError)
    _sizes(parity, mul, inv, known, sym, (cpos, cval, cok), "cval and cok")
    _free(k, known, "decode_row")
    lib = KERNEL.lib()
    best = np.empty(P, np.int32)
    second = np.empty(P, np.int32)
    nc = cpos.size
    acc = _check(lib.vnx_ol_decode_row(k, m, P, parity.ctypes.data, mul.ctypes.data, inv.ctypes.data, known.ctypes.data,
                                       sym.ctypes.data, nc, cpos.ctypes.data if nc else None,
                                       cval.ctypes.data if nc else None, cok.ctypes.data if nc else None, min_extra,
                                       margin, max_subsets, best.ctypes.data, second.ctypes.data), "decode_row")
    return sym, acc, best.astype(np.int64), second.astype(np.int64)


def ransac(parity: np.ndarray, mul: np.ndarray, inv: np.ndarray, known: np.ndarray, sym: np.ndarray,
           hpos: np.ndarray, hval: np.ndarray, tau: int, max_tuples: int):
    """See vnxdna.recovery.outer_list.ransac_reference. Returns (support, per-hypothesis agreeing columns)."""
    tau = checked_int("tau", tau, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    max_tuples = checked_int("max_tuples", max_tuples, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    parity = checked_array("parity", parity, np.uint8, error=NativeArgumentError)
    m, k = parity.shape
    sym = checked_array("sym", sym, np.uint8, error=NativeArgumentError)
    P = sym.shape[1]
    known = checked_array("known", known, np.uint8, error=NativeArgumentError)
    hpos = checked_array("hpos", hpos, np.int32, error=NativeArgumentError)
    hval = checked_array("hval", hval, np.uint8, error=NativeArgumentError).reshape(-1, P)
    mul = checked_array("mul", mul, np.uint8, error=NativeArgumentError)
    inv = checked_array("inv", inv, np.uint8, error=NativeArgumentError)
    _sizes(parity, mul, inv, known, sym, (hpos, hval), "hval")
    _free(k, known, "ransac")
    lib = KERNEL.lib()
    agree = np.zeros(hpos.size, np.int32)
    nh = hpos.size
    best = _check(lib.vnx_ol_ransac(k, m, P, parity.ctypes.data, mul.ctypes.data, inv.ctypes.data, known.ctypes.data,
                                    sym.ctypes.data, nh, hpos.ctypes.data if nh else None,
                                    hval.ctypes.data if nh else None, tau, max_tuples,
                                    agree.ctypes.data if nh else None), "ransac")
    return best, agree.astype(np.int64)


def main(argv=None) -> int:
    return KERNEL.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
