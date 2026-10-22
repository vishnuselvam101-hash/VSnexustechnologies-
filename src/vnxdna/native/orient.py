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

from vnxdna.native._loader import (INT64_MAX, INT64_MIN, Kernel, NativeArgumentError, NativeKernelError,
                                   checked_array, checked_int, module_contract)

ABI_VERSION = 4            # 2 (V13 D1): vnx_or_rc_bound; 3 (V13 B1): vnx_or_spec_project; 4 (V13 S3): vnx_or_pack_*
_ERRORS = {-1: "invalid argument", -2: "out of memory"}


def _bind(lib) -> None:
    i64, p = ctypes.c_int64, ctypes.c_void_p
    lib.vnx_or_scan.restype = ctypes.c_int
    lib.vnx_or_scan.argtypes = [i64, p, i64, p, p, i64, p, p, p, i64, i64, i64, p]
    lib.vnx_or_rc_bound.restype = ctypes.c_int
    lib.vnx_or_rc_bound.argtypes = [i64, p, i64, p, p, i64, p, p, p, i64, i64, i64, i64, i64, p]
    lib.vnx_or_spec_project.restype = ctypes.c_int
    lib.vnx_or_spec_project.argtypes = [i64, p, i64, p, p, p, i64, i64, i64, p, p, p, i64, i64, p, i64, p, p, p, p, p]
    lib.vnx_or_pack_projection.restype = ctypes.c_int
    lib.vnx_or_pack_projection.argtypes = [i64, p, p, i64, p, p, p, p]
    lib.vnx_or_pack_exact.restype = ctypes.c_int
    lib.vnx_or_pack_exact.argtypes = [i64, p, i64, p, p, i64, i64, i64, p, p, i64, p, p, p, p, p, p]


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
    # the arguments are checked before the call: a ctypes int64 argument narrows an out-of-range Python int without
    # a word and a uint8 cast wraps the codes that do not fit, so the kernel would work on different values than the
    # caller passed.
    margin = checked_int("margin", margin, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    max_shift = checked_int("max_shift", max_shift, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    codes = checked_array("codes", codes, np.uint8, error=NativeArgumentError)
    lengths = checked_array("lengths", lengths, np.int64, error=NativeArgumentError)
    mpos = checked_array("mpos", mpos, np.int64, error=NativeArgumentError)
    want = checked_array("want", want, np.uint8, error=NativeArgumentError)
    starts = checked_array("starts", starts, np.int64, error=NativeArgumentError)
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
    lib = KERNEL.lib()
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
    codes = checked_array("codes", codes, np.uint8, error=NativeArgumentError)
    lengths = checked_array("lengths", lengths, np.int64, error=NativeArgumentError)
    mpos = checked_array("mpos", mpos, np.int64, error=NativeArgumentError)
    want = checked_array("want", want, np.uint8, error=NativeArgumentError)
    starts = checked_array("starts", starts, np.int64, error=NativeArgumentError)
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
    band = checked_int("band", band, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    c_mm = checked_int("c_mm", c_mm, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    c_ins = checked_int("c_ins", c_ins, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    c_del = checked_int("c_del", c_del, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    codes, lengths, mpos, want, starts, block = _template_args(codes, lengths, mpos, want, starts)
    offs = checked_array("offs", offs, np.int64, error=NativeArgumentError)
    if offs.ndim != 1 or offs.size != lengths.size:
        raise ValueError("offs must give one start per selected read")
    if lengths.size and (lengths.min() < 0 or offs.min() < 0 or (offs + lengths).max() > codes.size):
        raise ValueError("every selected read must lie inside codes")
    if min(band, c_mm, c_ins, c_del) < 0:
        raise ValueError("band and costs must be non-negative")
    lib = KERNEL.lib()
    out = np.zeros(lengths.size, dtype=np.int64)
    rc = lib.vnx_or_rc_bound(lengths.size, codes.ctypes.data, codes.size, lengths.ctypes.data, offs.ctypes.data,
                             mpos.size, mpos.ctypes.data, want.ctypes.data, block.ctypes.data,
                             starts.size if mpos.size else 0, int(band), int(c_mm), int(c_ins), int(c_del),
                             out.ctypes.data)
    if rc < 0:
        raise NativeOrientError(f"native reverse-complement bound failed ({rc}: {_ERRORS.get(rc, 'unknown')})")
    return out


def spec_project(codes: np.ndarray, lengths: np.ndarray, offs: np.ndarray, qflat: np.ndarray | None, min_q: int,
                 T: int, mpos: np.ndarray, want: np.ndarray, starts: np.ndarray, frame_pos: np.ndarray,
                 band: int) -> tuple:
    """Marker-anchored speculative projection of the selected reads (``pass1._spec_project_reference``; ABI 3):
    (ok, bases, erased, mm, indels) — ok (n,) bool, bases/erased (n, frame_nt) uint8/bool, mm/indels (n,) int64.
    ``offs``/``lengths`` select any reads of ``codes``; ``qflat`` (same size as ``codes``) or None."""
    min_q = checked_int("min_q", min_q, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    T = checked_int("T", T, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    band = checked_int("band", band, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    codes, lengths, mpos, want, starts, block = _template_args(codes, lengths, mpos, want, starts)
    offs = checked_array("offs", offs, np.int64, error=NativeArgumentError)
    frame_pos = checked_array("frame_pos", frame_pos, np.int64, error=NativeArgumentError)
    if offs.ndim != 1 or offs.size != lengths.size:
        raise ValueError("offs must give one start per selected read")
    if lengths.size and (lengths.min() < 0 or offs.min() < 0 or (offs + lengths).max() > codes.size):
        raise ValueError("every selected read must lie inside codes")
    if qflat is not None:
        qflat = checked_array("qflat", qflat, np.uint8, error=NativeArgumentError)
        if qflat.shape != codes.shape:
            raise ValueError("qflat must have one quality per code")
    if band < 0 or T < 1 or frame_pos.ndim != 1 or frame_pos.size > T:
        raise ValueError("band must be non-negative, T positive and frame_pos at most T positions")
    if frame_pos.size and (frame_pos.min() < 0 or frame_pos.max() >= T):
        raise ValueError("frame_pos must index the template")
    if mpos.size and (mpos.min() < 0 or mpos.max() >= T):
        raise ValueError("mpos must index the template")
    n, F = lengths.size, frame_pos.size
    lib = KERNEL.lib()
    ok = np.zeros(n, dtype=np.uint8)
    bases = np.full((n, F), 4, dtype=np.uint8)
    erased = np.ones((n, F), dtype=np.uint8)
    mm = np.zeros(n, dtype=np.int64)
    indels = np.zeros(n, dtype=np.int64)
    rc = lib.vnx_or_spec_project(n, codes.ctypes.data, codes.size, lengths.ctypes.data, offs.ctypes.data,
                                 None if qflat is None else qflat.ctypes.data, int(min_q), int(T), mpos.size,
                                 mpos.ctypes.data, want.ctypes.data, block.ctypes.data, starts.size if mpos.size else 0,
                                 F, frame_pos.ctypes.data, int(band), ok.ctypes.data, bases.ctypes.data,
                                 erased.ctypes.data, mm.ctypes.data, indels.ctypes.data)
    if rc < 0:
        raise NativeOrientError(f"native speculative projection failed ({rc}: {_ERRORS.get(rc, 'unknown')})")
    return ok.view(bool), bases, erased.view(bool), mm, indels


def pack_projection(bases: np.ndarray, erased: np.ndarray) -> tuple:
    """Frame packing of (n, F) projected bases and erasure flags (``pass1._pack_projection_reference``; ABI 4):
    (hard (n, F) uint8, proj (n, F) uint8, frames (n, F/4) uint8, erased_bytes (n, F/4) bool)."""
    bases = checked_array("bases", bases, np.uint8, error=NativeArgumentError)
    erased = checked_array("erased", erased, np.uint8, error=NativeArgumentError)
    if bases.ndim != 2 or erased.shape != bases.shape or bases.shape[1] % 4:
        raise ValueError("bases and erased must be (n, F) with F a multiple of 4")
    n, F = bases.shape
    lib = KERNEL.lib()
    hard = np.empty((n, F), dtype=np.uint8)
    proj = np.empty((n, F), dtype=np.uint8)
    frames = np.empty((n, F // 4), dtype=np.uint8)
    ebytes = np.empty((n, F // 4), dtype=np.uint8)
    rc = lib.vnx_or_pack_projection(n, bases.ctypes.data, erased.ctypes.data, F, hard.ctypes.data, proj.ctypes.data,
                                    frames.ctypes.data, ebytes.ctypes.data)
    if rc < 0:
        raise NativeOrientError(f"native frame packing failed ({rc}: {_ERRORS.get(rc, 'unknown')})")
    return hard, proj, frames, ebytes.view(bool)


def pack_exact(codes: np.ndarray, offs: np.ndarray, qflat: np.ndarray | None, min_q: int, T: int, mpos: np.ndarray,
               want: np.ndarray, frame_pos: np.ndarray) -> tuple:
    """Frame packing of the exact-length reads at ``offs`` (``pass1._pack_exact_reference``; ABI 4):
    (hard, proj, frames, erased_bytes, marker mismatches (n,) int64)."""
    min_q = checked_int("min_q", min_q, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    T = checked_int("T", T, INT64_MIN, INT64_MAX, error=NativeArgumentError)
    codes = checked_array("codes", codes, np.uint8, error=NativeArgumentError)
    offs = checked_array("offs", offs, np.int64, error=NativeArgumentError)
    mpos = checked_array("mpos", mpos, np.int64, error=NativeArgumentError)
    want = checked_array("want", want, np.uint8, error=NativeArgumentError)
    frame_pos = checked_array("frame_pos", frame_pos, np.int64, error=NativeArgumentError)
    if codes.ndim != 1 or offs.ndim != 1 or mpos.ndim != 1 or want.shape != mpos.shape or frame_pos.ndim != 1:
        raise ValueError("codes, offs, mpos, want and frame_pos must be one-dimensional (want one code per marker)")
    if T < 1 or frame_pos.size % 4 or frame_pos.size > T:
        raise ValueError("T must be positive and frame_pos a multiple of four positions of the template")
    if (frame_pos.size and (frame_pos.min() < 0 or frame_pos.max() >= T)) or (mpos.size and (mpos.min() < 0 or mpos.max() >= T)):
        raise ValueError("frame_pos and mpos must index the template")
    if offs.size and (offs.min() < 0 or offs.max() > codes.size - T):
        raise ValueError("every read must lie inside codes")
    if qflat is not None:
        qflat = checked_array("qflat", qflat, np.uint8, error=NativeArgumentError)
        if qflat.shape != codes.shape:
            raise ValueError("qflat must have one quality per code")
    n, F = offs.size, frame_pos.size
    lib = KERNEL.lib()
    hard = np.empty((n, F), dtype=np.uint8)
    proj = np.empty((n, F), dtype=np.uint8)
    frames = np.empty((n, F // 4), dtype=np.uint8)
    ebytes = np.empty((n, F // 4), dtype=np.uint8)
    mm = np.empty(n, dtype=np.int64)
    rc = lib.vnx_or_pack_exact(n, codes.ctypes.data, codes.size, offs.ctypes.data,
                               None if qflat is None else qflat.ctypes.data, int(min_q), int(T), mpos.size,
                               mpos.ctypes.data, want.ctypes.data, F, frame_pos.ctypes.data, hard.ctypes.data,
                               proj.ctypes.data, frames.ctypes.data, ebytes.ctypes.data, mm.ctypes.data)
    if rc < 0:
        raise NativeOrientError(f"native frame packing failed ({rc}: {_ERRORS.get(rc, 'unknown')})")
    return hard, proj, frames, ebytes.view(bool), mm


def main(argv=None) -> int:
    return KERNEL.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
