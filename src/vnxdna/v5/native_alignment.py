"""Native (C) marker-template aligner with the V4 NumPy aligner as reference and fallback.

The kernel (``native/align.c``) implements docs/V5_NATIVE_ALIGNMENT_CONTRACT.md bit for bit. It is a plain
C shared library loaded with :mod:`ctypes`, so it needs no Python headers and no binding dependency. ctypes
releases the GIL during the call.

Backend selection (contract §10)::

    VNXDNA_ALIGN_BACKEND=auto       native if the library loads, else the reference (default)
    VNXDNA_ALIGN_BACKEND=native     native; raise if the library is unavailable
    VNXDNA_ALIGN_BACKEND=reference  always the V4 NumPy implementation

The library is looked up in this order: ``VNXDNA_NATIVE_LIB`` (explicit path), the extension built by
``pip install`` (``vnxdna/v5/_vnx_align*.so``), and a library built in place by :func:`build`
(``vnxdna/v5/native/libvnx_align.so``). Inputs outside the native domain (contract §9) always use the reference.
That routing is part of the contract, not an error.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import numpy as np

from .. import _native_build as _nb

ABI_VERSION = 2
MAX_BAND = 64
MAX_TEMPLATE = 8192
MAX_COST = 65536
MAX_GUARD = 1024
INF = 1 << 28
BACKENDS = ("auto", "native", "reference")

_HERE = Path(__file__).resolve().parent
_SOURCE = _HERE / "native" / "align.c"
_INPLACE = _HERE / "native" / "libvnx_align.so"
CFLAGS = _nb.explicit_cflags(strict=False)        # -O3 -std=c11 -fPIC -shared -Wall -Wextra (as pip install)
STRICT_CFLAGS = _nb.explicit_cflags(strict=True)  # + -Werror: CI and sanitizer builds (build --strict)

_ERRORS = {-1: "invalid argument", -2: "outside the native domain", -3: "read outside the band or bad offsets",
           -4: "out of memory", -5: "traceback left the band"}


class NativeAlignmentError(RuntimeError):
    pass


_lib = None
_load_error: str | None = None
_lib_path: str | None = None


def _candidates() -> list[Path]:
    out = []
    env = os.environ.get("VNXDNA_NATIVE_LIB")
    if env:
        out.append(Path(env))
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    out.append(_HERE / f"_vnx_align{suffix}")
    out.extend(sorted(_HERE.glob("_vnx_align*.so")))
    out.append(_INPLACE)
    return out


def _bind(lib) -> None:
    i64, i32, p = ctypes.c_int64, ctypes.c_int32, ctypes.c_void_p
    lib.vnx_align_abi_version.restype = ctypes.c_int
    lib.vnx_align_abi_version.argtypes = []
    lib.vnx_align_batch.restype = ctypes.c_int
    args = [i64, p, p, i64, p, p, i32, p, p, p, p, i32, i32, p, p, i32, i32, i32, i32, i32, i32, i32, p, p, p, p, p, p, p]
    lib.vnx_align_batch.argtypes = args
    lib.vnx_align_batch_profiled.restype = ctypes.c_int
    lib.vnx_align_batch_profiled.argtypes = [*args, p]
    lib.vnx_align_batch_path.restype = ctypes.c_int
    lib.vnx_align_batch_path.argtypes = [*args, p]
    lib.vnx_align_lanes.restype = ctypes.c_int
    lib.vnx_align_lanes.argtypes = []


def _load():
    global _lib, _load_error, _lib_path
    if _lib is not None or _load_error is not None:
        return _lib
    errors = []
    for path in _candidates():
        if not path.is_file():
            continue
        try:
            lib = ctypes.CDLL(str(path))
            _bind(lib)
            abi = lib.vnx_align_abi_version()
            if abi != ABI_VERSION:
                errors.append(f"{path}: ABI {abi} != {ABI_VERSION}")
                continue
        except (OSError, AttributeError) as error:
            errors.append(f"{path}: {error}")
            continue
        _lib, _lib_path = lib, str(path)
        return _lib
    _load_error = "; ".join(errors) if errors else "native library not built (pip install, or python -m vnxdna.v5.native_alignment build)"
    return None


def _reset_for_tests() -> None:
    global _lib, _load_error, _lib_path
    _lib = _load_error = _lib_path = None


def available() -> bool:
    return _load() is not None


def requested_backend(explicit: str | None = None) -> str:
    name = (explicit or os.environ.get("VNXDNA_ALIGN_BACKEND") or "auto").strip().lower()
    if name not in BACKENDS:
        raise ValueError(f"alignment backend must be one of {BACKENDS}, got {name!r}")
    return name


def resolve_backend(explicit: str | None = None) -> str:
    """'native' or 'reference' — the backend that will actually run for in-domain inputs."""
    want = requested_backend(explicit)
    if want == "reference":
        return "reference"
    if available():
        return "native"
    if want == "native":
        raise NativeAlignmentError(f"native alignment requested but unavailable: {_load_error}")
    return "reference"


def status() -> dict:
    """Diagnostics for ``vnx native`` and ``vnx version``: never raises."""
    want = os.environ.get("VNXDNA_ALIGN_BACKEND") or "auto"
    try:
        active = resolve_backend()
        err = None
    except (NativeAlignmentError, ValueError) as error:
        active, err = None, str(error)
    return {"requested_backend": want, "active_backend": active, "native_available": available(), "library": _lib_path,
            "load_error": _load_error, "abi_version": ABI_VERSION, "error": err,
            "domain": {"max_band": MAX_BAND, "max_template_nt": MAX_TEMPLATE, "max_cost": MAX_COST, "max_guard": MAX_GUARD,
                       "read_dtype": "uint8"}}


# ============================================================================ geometry and domain
def geometry(aligner) -> dict:
    """Contract §1 arrays as C-contiguous int16/int32, cached on the aligner."""
    g = getattr(aligner, "_native_geometry", None)
    if g is None:
        lay = aligner.layout
        g = {"tpl": np.ascontiguousarray(aligner.tpl, dtype=np.int16),
             "seg_of": np.ascontiguousarray(aligner.seg_of, dtype=np.int32),
             "prev_seg": np.ascontiguousarray(aligner.prev_seg, dtype=np.int32),
             "next_seg": np.ascontiguousarray(aligner.next_seg, dtype=np.int32),
             "frame_pos": np.ascontiguousarray(aligner.frame_pos, dtype=np.int32),
             "seg_frame": np.ascontiguousarray(np.arange(lay.frame_nt) // (lay.marker_period or lay.frame_nt), dtype=np.int32)}
        aligner._native_geometry = g
    return g


def _int_in(v, lo: int, hi: int) -> bool:
    return isinstance(v, (int, np.integer)) and not isinstance(v, bool) and lo <= int(v) <= hi


def config_in_domain(aligner, min_quality) -> bool:
    c = aligner.costs
    return (_int_in(aligner.band, 0, MAX_BAND) and 1 <= aligner.T <= MAX_TEMPLATE
            and all(_int_in(v, 0, MAX_COST) for v in (c.marker_mismatch, c.insertion, c.deletion, c.marker_deletion_extra))
            and _int_in(c.guard_segments, 0, MAX_GUARD) and _int_in(min_quality, -(1 << 31), (1 << 31) - 1))


def _reads_in_domain(reads: list, quals: list | None) -> bool:
    for r in reads:
        if not isinstance(r, np.ndarray) or r.dtype != np.uint8 or r.ndim != 1:
            return False
    if quals is not None:
        for r, q in zip(reads, quals):
            if q is None:
                continue
            if not isinstance(q, np.ndarray) or q.dtype != np.uint8 or q.ndim != 1 or q.size != r.size:
                return False
    return True


def _ptr(a: np.ndarray | None):
    return None if a is None else a.ctypes.data_as(ctypes.c_void_p)


# ============================================================================ alignment
def align_usable(aligner, reads: list, quals: list | None, min_quality: int, timings: dict | None = None,
                 readpos: bool = False):
    """Align usable reads (|len − T| ≤ band) natively.

    Returns (bases, erased, ok, ins, del, mm, cost), the same arrays as ``TemplateAligner._align``, or None when the
    input is outside the native domain (the caller then runs the reference). With ``timings`` (benchmarks only) the
    profiled entry point adds the seconds spent in pack / dp / traceback / projection / wrapper to that dict. With
    ``readpos`` an eighth array (n, T) int16 is appended: the read index aligned to every template position on the
    traceback path, −1 where the template base was deleted or the read is not ok (V5 Phase 3)."""
    if readpos and timings is not None:
        raise ValueError("readpos and timings cannot be combined")
    import time
    t_start = time.perf_counter()
    lib = _load()
    if lib is None or not config_in_domain(aligner, min_quality) or not _reads_in_domain(reads, quals):
        return None
    n = len(reads)
    lay = aligner.layout
    g = geometry(aligner)
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    offsets = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(lengths, out=offsets[1:])
    total = int(offsets[-1])
    codes = np.concatenate(reads) if n else np.zeros(0, dtype=np.uint8)
    qflat = has_q = None
    if quals is not None:
        has_q = np.fromiter((q is not None for q in quals), dtype=np.uint8, count=n)
        qflat = np.zeros(total, dtype=np.uint8)
        for k in np.flatnonzero(has_q).tolist():
            qflat[offsets[k]:offsets[k + 1]] = quals[k]
    bases = np.empty((n, lay.frame_nt), dtype=np.uint8)
    erased = np.empty((n, lay.frame_nt), dtype=np.uint8)
    ok = np.empty(n, dtype=np.uint8)
    ins = np.empty(n, dtype=np.int64)
    dele = np.empty(n, dtype=np.int64)
    mm = np.empty(n, dtype=np.int64)
    cost = np.empty(n, dtype=np.int64)
    c = aligner.costs
    args = (n, _ptr(codes) if total else None, _ptr(offsets), total, _ptr(qflat) if total and qflat is not None else None,
            _ptr(has_q), aligner.T, _ptr(g["tpl"]), _ptr(g["seg_of"]), _ptr(g["prev_seg"]), _ptr(g["next_seg"]), aligner.n_segments,
            lay.frame_nt, _ptr(g["frame_pos"]), _ptr(g["seg_frame"]), int(aligner.band), int(c.marker_mismatch), int(c.insertion),
            int(c.deletion), int(c.marker_deletion_extra), int(c.guard_segments), int(min_quality),
            _ptr(bases), _ptr(erased), _ptr(ok), _ptr(ins), _ptr(dele), _ptr(mm), _ptr(cost))
    rpos = None
    if readpos:
        rpos = np.empty((n, aligner.T), dtype=np.int16)
        rc = lib.vnx_align_batch_path(*args, _ptr(rpos))
    elif timings is None:
        rc = lib.vnx_align_batch(*args)
    else:
        stages = np.zeros(4, dtype=np.float64)
        t_call = time.perf_counter()
        rc = lib.vnx_align_batch_profiled(*args, _ptr(stages))
        t_end = time.perf_counter()
        for key, v in zip(("pack", "dp", "traceback", "projection"), stages.tolist()):
            timings[key] = timings.get(key, 0.0) + v
        timings["c_call"] = timings.get("c_call", 0.0) + (t_end - t_call)
        timings["wrapper"] = timings.get("wrapper", 0.0) + (t_call - t_start)
    if rc != 0:
        # every argument was validated above; a kernel error is a bug, never silently ignored
        raise NativeAlignmentError(f"native aligner failed: {_ERRORS.get(rc, rc)} (code {rc})")
    if rpos is not None:
        return bases, erased.view(bool), ok.view(bool), ins, dele, mm, cost, rpos
    return bases, erased.view(bool), ok.view(bool), ins, dele, mm, cost


# ============================================================================ in-place build (development / no pip build)
def build(output: str | os.PathLike | None = None, extra_flags: list[str] | None = None, compiler: str | None = None,
          strict: bool | None = None) -> Path:
    """Compile ``native/align.c`` into a shared library (default: next to the source). Returns its path."""
    cc = compiler or os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise NativeAlignmentError("no C compiler found (set CC)")
    out = Path(output) if output else _INPLACE
    cmd = [cc, *_nb.explicit_cflags(strict), *(extra_flags or []), str(_SOURCE), "-o", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise NativeAlignmentError(f"native build failed: {' '.join(cmd)}\n{res.stderr}")
    return out


if __name__ == "__main__":
    import json
    import sys

    if sys.argv[1:2] == ["build"]:
        print(build(strict=True if "--strict" in sys.argv[2:] else None))
    else:
        print(json.dumps(status(), indent=2))
