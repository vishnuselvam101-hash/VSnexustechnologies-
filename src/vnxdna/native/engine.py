"""Streaming decode engine (V13, **Rust** core ``native/rust/engine``): pass 1 of a decode in one native call.

The crate ``vnx_engine`` (C ABI 1, loaded with ctypes) owns the whole pass-1 pipeline: file IO in large blocks, the
FASTQ/FASTA scanner, the orientation pre-pass, the address scheduler, the decode attempts (exact-length packing, the
marker-anchored projection, the banded DP, inner RS + CRC, the bounded reverse-complement retry, pending records) and the
spill bucket files that pass 2 reads. The C kernels it reuses (inner RS, DP aligner, orientation/projection/packing) are
compiled into the library. Every attempted read gets exactly the result of :func:`vnxdna.recovery.pass1._process`
(default recovery configuration); see docs/V13_ENGINE_ARCHITECTURE.md.

Backend: ``VNXDNA_ENGINE_BACKEND`` = auto | native | reference (reference = never use the engine); library override
``VNXDNA_ENGINE_LIB``. Build: ``python -m vnxdna.native.engine build`` (cargo, offline, no third-party crates).
Layering: this binding takes :class:`EngineParams` (plain arrays and integers); :mod:`vnxdna.recovery.engine` builds
them from a layout and the decode options and is what the decoder calls.
"""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

import numpy as np

from vnxdna.native._loader import NativeKernelError, RustKernel, module_contract

ABI_VERSION = 1
N_STATS = 32
IO_BLOCK = 8 << 20
TABLE_BITS = 21     # scheduler fingerprint tables: 2^21 window + 2^20 address slots (24 MiB, fixed)

RC_OK, RC_UNSUPPORTED, RC_CANCELLED = 0, 1, 2
_ERRORS = {-1: "invalid argument", -2: "IO error", -3: "kernel error", -4: "panic (caught at the ABI boundary)"}

STAT_NAMES = ("reads", "fast", "sync", "speculative", "reverse_complement", "pending", "orphans", "unaligned",
              "rc_retry_skipped", "attempted", "skipped", "input_bytes", "batches", "rounds", "ns_io", "ns_scan",
              "ns_prep", "ns_attempt", "ns_spill", "ns_total", "acc_records", "max_batch", "table_evictions", "table_bytes", "cheap_attempts", "full_attempts",
              "ns_cheap", "ns_full", "ns_group", "split_groups", "reserved30", "reserved31")


class EngineUnsupported(RuntimeError):
    """The input or configuration lies outside what the engine reproduces exactly (the caller falls back)."""


class EngineCancelled(RuntimeError):
    """The decode was cancelled (timeout or caller request) while the engine ran."""


class EngineError(NativeKernelError):
    """An internal engine error (kernel error, IO error, panic); the caller falls back."""


_FIELDS = ("abi t frame_nt frame_bytes payload parity nm nblocks *mpos *want *block *frame_pos64 *tpl16 *seg_of "
           "*prev_seg *next_seg n_segments *frame_pos32 *seg_frame band c_mm c_ins c_del c_x guard min_q rc rc_bounded "
           "spec orient_margin orient_shift *ks span threads batch buckets align_lanes rs_level max_reads fmt dedup "
           "frame_version *cancel io_block table_bits").split()


class _Cfg(ctypes.Structure):
    _fields_ = [(f.lstrip("*"), ctypes.c_void_p if f.startswith("*") else ctypes.c_int64) for f in _FIELDS]


assert ctypes.sizeof(_Cfg) == 45 * 8


def _bind(lib) -> None:
    lib.vnx_eng_pass1.restype = ctypes.c_int32
    lib.vnx_eng_pass1.argtypes = [ctypes.POINTER(_Cfg), ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                                  ctypes.c_int64, ctypes.c_char_p, ctypes.c_int64]
    lib.vnx_eng_n_stats.restype = ctypes.c_int64
    i64, p = ctypes.c_int64, ctypes.c_void_p
    lib.vnx_eng_scan_file.restype = ctypes.c_int32
    lib.vnx_eng_scan_file.argtypes = [ctypes.c_char_p, ctypes.c_int32, i64, ctypes.c_int32, p, i64, p, p, i64, p,
                                      ctypes.c_char_p, i64]


KERNEL = RustKernel("engine", "VNXDNA_ENGINE", ABI_VERSION, "vnx_eng_abi_version", _bind, "vnxdna.recovery.pass1",
                    __name__, crate="engine")
globals().update(module_contract(KERNEL))   # _HERE, _SOURCE, _INPLACE, CFLAGS, STRICT_CFLAGS, ENV, ENV_LIB, _candidates


def available() -> bool:
    return KERNEL.available()


def resolve_backend() -> str:
    return KERNEL.resolve()


def status() -> dict:
    return KERNEL.status()


def build(output=None, extra_flags=None, strict=None):
    return KERNEL.build(output, extra_flags, strict)


class Cancel:
    """A cancellation flag the engine polls once per IO block and batch (set from any thread, e.g. a timer)."""

    def __init__(self) -> None:
        self._flag = ctypes.c_int64(0)

    def set(self) -> None:
        self._flag.value = 1

    @property
    def is_set(self) -> bool:
        return bool(self._flag.value)

    def address(self) -> int:
        return ctypes.addressof(self._flag)


def _ptr(a: np.ndarray) -> int:
    return a.ctypes.data


def kernel_levels() -> tuple[int, int]:
    """(DP lanes, RS level) the engine uses: the levels the align and rs kernels would run (their environment
    selection applies); raises EngineUnsupported when either is pinned to its Python reference."""
    from vnxdna.native import align as na
    from vnxdna.native import rs as nrs
    level = na.active_level()
    if level == "reference":
        raise EngineUnsupported("VNXDNA_ALIGN_BACKEND selects the Python reference")
    rs_name = nrs.active_backend()
    if rs_name == "reference":
        raise EngineUnsupported("VNXDNA_RS_BACKEND selects the Python reference")
    return na.LEVELS[level], nrs.LEVELS[rs_name]


@dataclass
class EngineParams:
    """What the engine needs of a layout and the decode options, as plain integers and arrays (built by
    :func:`vnxdna.recovery.engine.params_for`; this module imports nothing above ``vnxdna.native``)."""

    t: int                      # template length (nt)
    frame_nt: int
    frame_bytes: int
    payload: int
    parity: int
    mpos: np.ndarray            # int64 marker positions (ascending)
    want: np.ndarray            # uint8 marker codes
    block: np.ndarray           # int64 marker block index per marker
    nblocks: int
    frame_pos64: np.ndarray     # int64 template position of every frame base
    tpl16: np.ndarray           # aligner geometry (vnxdna.native.align.geometry)
    seg_of: np.ndarray
    prev_seg: np.ndarray
    next_seg: np.ndarray
    n_segments: int
    frame_pos32: np.ndarray
    seg_frame: np.ndarray
    band: int
    c_mm: int
    c_ins: int
    c_del: int
    c_x: int
    guard: int
    min_q: int
    rc: bool
    rc_bounded: bool
    spec: bool
    ks: np.ndarray              # uint8 keystreams, 256 x span
    span: int
    max_reads: int
    frame_version: int
    orient_margin: int
    orient_shift: int = 2


def run_pass1(reads_path: str | os.PathLike, spill_dir: str | os.PathLike, params: EngineParams, buckets: int, *, fmt: str,
              threads: int, dedup: bool = True, batch: int | None = None, cancel: Cancel | None = None) -> dict:
    """Pass 1 of a decode of ``reads_path`` into the spill bucket files of ``spill_dir`` (record format of
    :class:`vnxdna.recovery.spill.Spill`, appended). Returns the statistics (``STAT_NAMES``). Raises EngineUnsupported
    (outside the exact domain: fall back), EngineCancelled or EngineError."""
    if fmt not in ("fastq", "fasta"):
        raise EngineUnsupported(f"input format {fmt!r}")
    lib = KERNEL.lib()
    lanes, rs_level = kernel_levels()
    q = params
    arrays = {k: np.ascontiguousarray(getattr(q, k)) for k in ("mpos", "want", "block", "frame_pos64", "tpl16", "seg_of",
                                                                "prev_seg", "next_seg", "frame_pos32", "seg_frame", "ks")}
    cancel = cancel or Cancel()
    cfg = _Cfg(abi=ABI_VERSION, t=int(q.t), frame_nt=int(q.frame_nt), frame_bytes=int(q.frame_bytes), payload=int(q.payload),
               parity=int(q.parity), nm=int(arrays["mpos"].size), nblocks=int(q.nblocks), mpos=_ptr(arrays["mpos"]),
               want=_ptr(arrays["want"]), block=_ptr(arrays["block"]), frame_pos64=_ptr(arrays["frame_pos64"]),
               tpl16=_ptr(arrays["tpl16"]), seg_of=_ptr(arrays["seg_of"]), prev_seg=_ptr(arrays["prev_seg"]),
               next_seg=_ptr(arrays["next_seg"]), n_segments=int(q.n_segments), frame_pos32=_ptr(arrays["frame_pos32"]),
               seg_frame=_ptr(arrays["seg_frame"]), band=int(q.band), c_mm=int(q.c_mm), c_ins=int(q.c_ins),
               c_del=int(q.c_del), c_x=int(q.c_x), guard=int(q.guard), min_q=int(q.min_q), rc=int(bool(q.rc)),
               rc_bounded=int(bool(q.rc_bounded)), spec=int(bool(q.spec)), orient_margin=int(q.orient_margin),
               orient_shift=int(q.orient_shift), ks=_ptr(arrays["ks"]), span=int(q.span), threads=int(threads),
               batch=int(batch or 16384), buckets=int(buckets), align_lanes=lanes, rs_level=rs_level,
               max_reads=int(q.max_reads), fmt=1 if fmt == "fastq" else 2, dedup=int(bool(dedup)),
               frame_version=int(q.frame_version), cancel=cancel.address(), io_block=IO_BLOCK, table_bits=TABLE_BITS)
    stats = np.zeros(int(lib.vnx_eng_n_stats()), dtype=np.int64)
    err = ctypes.create_string_buffer(512)
    rc = lib.vnx_eng_pass1(ctypes.byref(cfg), os.fsencode(reads_path), os.fsencode(spill_dir), _ptr(stats),
                           stats.size, err, len(err))
    del arrays                                  # alive for the call
    msg = err.value.decode("utf-8", "replace")
    if rc == RC_UNSUPPORTED:
        raise EngineUnsupported(msg)
    if rc == RC_CANCELLED:
        raise EngineCancelled(msg)
    if rc != RC_OK:
        raise EngineError(f"engine failed: {_ERRORS.get(rc, rc)}: {msg}")
    return dict(zip(STAT_NAMES, stats.tolist()))


def scan_file(path: str | os.PathLike, fmt: str, *, block: int = IO_BLOCK, use_map: bool = True) -> tuple:
    """Parse a FASTQ/FASTA file with the engine's scanner: (codes uint8, lengths int64, qualities uint8 or None),
    the values ``vnxdna.dnaenc.reads.iter_reads`` yields (concatenated over its batches). Raises EngineUnsupported for
    input outside the scanner's exact domain (the reference parser then decides: it parses it or raises)."""
    lib = KERNEL.lib()
    size = max(1, os.path.getsize(path))
    codes = np.empty(size, dtype=np.uint8)
    quals = np.empty(size, dtype=np.uint8) if fmt == "fastq" else None
    lens = np.empty(size // 2 + 1, dtype=np.int64)
    counts = np.zeros(2, dtype=np.int64)
    err = ctypes.create_string_buffer(512)
    rc = lib.vnx_eng_scan_file(os.fsencode(path), 1 if fmt == "fastq" else 2, int(block), int(bool(use_map)),
                               _ptr(codes), codes.size, None if quals is None else _ptr(quals), _ptr(lens), lens.size,
                               _ptr(counts), err, len(err))
    msg = err.value.decode("utf-8", "replace")
    if rc == RC_UNSUPPORTED:
        raise EngineUnsupported(msg)
    if rc != RC_OK:
        raise EngineError(f"engine scan failed: {_ERRORS.get(rc, rc)}: {msg}")
    n, nb = int(counts[0]), int(counts[1])
    return codes[:nb].copy(), lens[:n].copy(), None if quals is None else quals[:nb].copy()


def main(argv=None) -> int:
    return KERNEL.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
