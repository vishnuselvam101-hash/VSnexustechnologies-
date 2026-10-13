"""Native (C) marker-template aligner with the V4 NumPy aligner as reference and fallback.

The kernel (``native/align.c``) implements docs/V5_NATIVE_ALIGNMENT_CONTRACT.md bit for bit. It is a plain
C shared library loaded with :mod:`ctypes`, so it needs no Python headers and no binding dependency. ctypes
releases the GIL during the call.

Backend selection (contract §10)::

    VNXDNA_ALIGN_BACKEND=auto       native at the auto-policy SIMD level if the library loads, else the reference (default)
    VNXDNA_ALIGN_BACKEND=native     the same level as auto; raise if the library is unavailable
    VNXDNA_ALIGN_BACKEND=avx512     force 16 lanes (AVX-512F/BW); if the CPU/OS lacks it, the next lower level, recorded
    VNXDNA_ALIGN_BACKEND=avx2       force 8 lanes (AVX2); same fallback rule
    VNXDNA_ALIGN_BACKEND=scalar     force the portable level (4 lanes of 128-bit compiler vectors, no target attribute)
    VNXDNA_ALIGN_BACKEND=reference  always the V4 NumPy implementation

SIMD levels (ABI 3, V11 WP1): the kernel is compiled at 4, 8 and 16 lanes in one library and the level is an explicit
argument of every call. Every level is bit-exact with the reference (each lane runs the scalar recurrence). A level
the CPU/OS cannot run is refused by the library itself (cpuid + xgetbv) and never executes; a forced level degrades
to the best level below it and the reason is recorded by :func:`status` and logged once. Levels quarantined by the
differential self-check (:mod:`vnxdna.dispatch`, ``VNXDNA_ALIGN_QUARANTINED_LEVELS``) are skipped by auto.

The library is looked up in this order: ``VNXDNA_NATIVE_LIB`` (explicit path), the extension built by
``pip install`` (``vnxdna/v5/_vnx_align*.so``), and a library built in place by :func:`build`
(``vnxdna/v5/native/libvnx_align.so``). Inputs outside the native domain (contract §9) always use the reference.
That routing is part of the contract, not an error.
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import numpy as np

from vnxdna import _native_build as _nb

ABI_VERSION = 4
MAX_BAND = 64
MAX_TEMPLATE = 8192
MAX_COST = 65536
MAX_GUARD = 1024
INF = 1 << 28
BACKENDS = ("auto", "native", "avx512", "avx2", "scalar", "reference")
LEVELS = {"scalar": 4, "avx2": 8, "avx512": 16}     # level name -> lanes (the C level argument)
QUARANTINE_ENV = "VNXDNA_ALIGN_QUARANTINED_LEVELS"   # comma-separated level names auto must skip (set by dispatch)
AUTO_ORDER = ("avx512", "avx2", "scalar")
# auto order set by the WP1 micro-benchmark (experiments/v11/wp1/micro_levels.json, SIMULATED, one shared host): C call
# vs scalar avx512 2.07-2.18x, avx2 1.21-1.25x in 12/12 layout x error-point cases; end-to-end decode still to measure
AUTO_POLICY = "avx512 > avx2 > scalar > reference (micro-benchmark 2026-10-11; end-to-end pending)"
_log = logging.getLogger(__name__)
_warned: set[str] = set()

# the C source, the in-place library and the pip-built extension (vnxdna.v5._vnx_align) stay in vnxdna/v5
_HERE = Path(__file__).resolve().parents[1] / "v5"
_SOURCE = _HERE / "native" / "align.c"
_INPLACE = _HERE / "native" / "libvnx_align.so"
CFLAGS = _nb.explicit_cflags(strict=False)        # -O3 -std=c11 -fPIC -shared -Wall -Wextra (as pip install)
STRICT_CFLAGS = _nb.explicit_cflags(strict=True)  # + -Werror: CI and sanitizer builds (build --strict)

_ERRORS = {-1: "invalid argument", -2: "outside the native domain", -3: "read outside the band or bad offsets",
           -4: "out of memory", -5: "traceback left the band", -6: "SIMD level not supported by this CPU/OS"}


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
    lib.vnx_align_levels.restype = ctypes.c_int
    lib.vnx_align_levels.argtypes = []
    for name, extra in (("vnx_align_batch_lv", []), ("vnx_align_batch_path_lv", [p]), ("vnx_align_batch_profiled_lv", [p])):
        fn = getattr(lib, name)
        fn.restype = ctypes.c_int
        fn.argtypes = [*args, *extra, i32]
    # ABI 4 (V13): selected reads of a larger buffer, (n, codes, starts, lens, total, ...) — lens inserted after starts
    lib.vnx_align_batch_sel_lv.restype = ctypes.c_int
    lib.vnx_align_batch_sel_lv.argtypes = [i64, p, p, p, *args[3:], i32]


def _load():
    global _lib, _load_error, _lib_path
    if _lib is not None or _load_error is not None:
        return _lib
    errors = []
    for path in _candidates():
        if not path.is_file():
            continue
        try:
            from vnxdna.native._loader import open_library
            lib = open_library(path)
            lib.vnx_align_abi_version.restype = ctypes.c_int
            lib.vnx_align_abi_version.argtypes = []
            abi = lib.vnx_align_abi_version()
            if abi != ABI_VERSION:   # checked before binding anything else: a stale library lacks the level symbols
                errors.append(f"{path}: ABI {abi} != {ABI_VERSION}")
                continue
            _bind(lib)
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


def supported_levels() -> list[str]:
    """SIMD levels this library can run on this CPU/OS (lowest first); [] when the library is missing."""
    lib = _load()
    mask = 0 if lib is None else int(lib.vnx_align_levels())
    return [name for name, lanes in LEVELS.items() if mask & (1 << lanes)]


def quarantined_levels() -> list[str]:
    raw = os.environ.get(QUARANTINE_ENV, "")
    return [x for x in (t.strip().lower() for t in raw.split(",")) if x in LEVELS]


def quarantine_level(level: str) -> None:
    """Exclude ``level`` from auto selection in this process and its workers (environment, inherited)."""
    cur = quarantined_levels()
    if level not in cur:
        os.environ[QUARANTINE_ENV] = ",".join([*cur, level])


def _resolve(explicit: str | None = None) -> tuple[str, str | None]:
    """(active level name or 'reference', reason it differs from the request or None)."""
    want = requested_backend(explicit)
    if want == "reference":
        return "reference", None
    if _load() is None:
        if want == "native":
            raise NativeAlignmentError(f"native alignment requested but unavailable: {_load_error}")
        return "reference", f"native library unavailable ({_load_error})"
    supported = supported_levels()
    if want in ("auto", "native"):
        bad = quarantined_levels()
        for i, name in enumerate(AUTO_ORDER):
            if name in supported and name not in bad:
                skipped = [n for n in AUTO_ORDER[:i] if n in bad]
                return name, (f"quarantined level(s) skipped: {','.join(skipped)}" if skipped else None)
        if want == "native":
            raise NativeAlignmentError(f"native alignment requested but every level is quarantined ({','.join(bad)})")
        return "reference", f"every native level quarantined ({','.join(bad)})"
    if want in supported:
        return want, None      # an explicitly forced level that the CPU supports runs even if auto quarantined it
    # downgrade: the widest supported level below the request, skipping levels quarantined by the self-check
    bad = quarantined_levels()
    lower = [n for n, lanes in sorted(LEVELS.items(), key=lambda kv: -kv[1]) if lanes < LEVELS[want] and n in supported]
    usable = [n for n in lower if n not in bad]
    if usable:
        skipped = [n for n in lower if n in bad and LEVELS[n] > LEVELS[usable[0]]]
        note = f" (quarantined skipped: {','.join(skipped)})" if skipped else ""
        return usable[0], f"{want} requested but not supported by this CPU/OS; using {usable[0]}{note}"
    return lower[0], f"{want} requested but not supported by this CPU/OS; using {lower[0]} although it is quarantined"


def _warn_once(msg: str) -> None:
    if msg not in _warned:
        _warned.add(msg)
        _log.warning(msg)


def active_level(explicit: str | None = None) -> str:
    """The SIMD level :func:`align_usable` will use ('avx512', 'avx2', 'scalar') or 'reference'."""
    name, reason = _resolve(explicit)
    if reason and requested_backend(explicit) in LEVELS:
        _warn_once(f"vnxdna align backend: {reason}")
    return name


def resolve_backend(explicit: str | None = None) -> str:
    """'native' or 'reference' — the backend that will actually run for in-domain inputs."""
    return "reference" if active_level(explicit) == "reference" else "native"


def status() -> dict:
    """Diagnostics for ``vnx native`` and ``vnx version``: never raises."""
    want = os.environ.get("VNXDNA_ALIGN_BACKEND") or "auto"
    level = reason = None
    try:
        level, reason = _resolve()
        active = "reference" if level == "reference" else "native"
        err = None
    except (NativeAlignmentError, ValueError) as error:
        active, err = None, str(error)
    native = active == "native"
    return {"requested_backend": want, "active_backend": active, "native_available": available(), "library": _lib_path,
            "load_error": _load_error, "abi_version": ABI_VERSION, "error": err,
            "simd_level": level if native else None, "simd_lanes": LEVELS[level] if native and level else None,
            "fallback_reason": reason, "supported_levels": supported_levels(), "quarantined_levels": quarantined_levels(),
            "auto_policy": AUTO_POLICY,
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
                 readpos: bool = False, backend: str | None = None):
    """Align usable reads (|len − T| ≤ band) natively.

    Returns (bases, erased, ok, ins, del, mm, cost), the same arrays as ``TemplateAligner._align``, or None when the
    input is outside the native domain (the caller then runs the reference). With ``timings`` (benchmarks only) the
    profiled entry point adds the seconds spent in pack / dp / traceback / projection / wrapper to that dict. With
    ``readpos`` an eighth array (n, T) int16 is appended: the read index aligned to every template position on the
    traceback path, −1 where the template base was deleted or the read is not ok (V5 Phase 3). ``backend`` overrides
    ``VNXDNA_ALIGN_BACKEND`` for the SIMD level (tests, benchmarks); a 'reference' request returns None."""
    if readpos and timings is not None:
        raise ValueError("readpos and timings cannot be combined")
    import time
    t_start = time.perf_counter()
    lib = _load()
    if lib is None or not config_in_domain(aligner, min_quality) or not _reads_in_domain(reads, quals):
        return None
    level = active_level(backend)
    if level == "reference":
        return None
    n = len(reads)
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
    return _run_kernel(lib, aligner, n, codes, offsets, total, qflat, has_q, min_quality, LEVELS[level], timings, readpos,
                       t_start)


def _flat_in_domain(codes, starts, lengths, qflat) -> bool:
    """The flat-buffer counterpart of :func:`_reads_in_domain`: every read lies inside ``codes`` (and ``qflat``)."""
    if not isinstance(codes, np.ndarray) or codes.dtype != np.uint8 or codes.ndim != 1:
        return False
    for a in (starts, lengths):
        if not isinstance(a, np.ndarray) or a.ndim != 1 or a.dtype.kind not in "iu" or a.size != lengths.size:
            return False
    if qflat is not None and (not isinstance(qflat, np.ndarray) or qflat.dtype != np.uint8 or qflat.ndim != 1
                              or qflat.size != codes.size):
        return False
    if starts.size and (int(starts.min()) < 0 or int(lengths.min()) < 0
                        or int((starts.astype(np.int64) + lengths.astype(np.int64)).max()) > codes.size):
        return False
    return True


def align_usable_flat(aligner, codes: np.ndarray, starts: np.ndarray, lengths: np.ndarray, qflat: np.ndarray | None,
                      min_quality: int, backend: str | None = None):
    """:func:`align_usable` for reads given as ranges of flat buffers: read ``k`` is ``codes[starts[k]:starts[k] +
    lengths[k]]`` and, when ``qflat`` is given, has the qualities ``qflat[...]`` of the same range (every read has
    qualities then; the list form's per-read ``None`` is ``qflat=None`` for the whole call). Returns the same seven
    arrays as :func:`align_usable` on the equivalent lists (pinned by tests/v13/test_pass1_flat.py), or None outside
    the native domain. The kernel reads the selected ranges in place (ABI 4 entry ``vnx_align_batch_sel_lv``): no
    per-read views, no ``np.concatenate``, no copy (V13 C1)."""
    lib = _load()
    if lib is None or not config_in_domain(aligner, min_quality) or not _flat_in_domain(codes, starts, lengths, qflat):
        return None
    level = active_level(backend)
    if level == "reference":
        return None
    n = int(lengths.size)
    lens = np.ascontiguousarray(lengths, dtype=np.int64)
    starts = np.ascontiguousarray(starts, dtype=np.int64)
    codes = np.ascontiguousarray(codes)
    qflat = None if qflat is None else np.ascontiguousarray(qflat)
    has_q = None if qflat is None else np.ones(n, dtype=np.uint8)
    return _run_kernel(lib, aligner, n, codes, starts, int(codes.size), qflat, has_q, min_quality, LEVELS[level], None,
                       False, None, lens=lens)


def _run_kernel(lib, aligner, n: int, codes, offsets, total: int, qflat, has_q, min_quality: int, lanes: int, timings,
                readpos: bool, t_start, lens=None):
    """One call of the batch kernel on validated flat buffers; shared by the list and flat entry points. With ``lens``
    (ABI 4) ``offsets`` are the n read starts inside ``codes`` of ``total`` entries and the selected-reads entry runs."""
    import time
    lay = aligner.layout
    g = geometry(aligner)
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
    if lens is not None:
        if readpos or timings is not None:
            raise ValueError("the selected-reads entry has no readpos or timings form")
        rc = lib.vnx_align_batch_sel_lv(n, _ptr(codes) if total else None, _ptr(offsets), _ptr(lens), *args[3:], lanes)
    elif readpos:
        rpos = np.empty((n, aligner.T), dtype=np.int16)
        rc = lib.vnx_align_batch_path_lv(*args, _ptr(rpos), lanes)
    elif timings is None:
        rc = lib.vnx_align_batch_lv(*args, lanes)
    else:
        stages = np.zeros(4, dtype=np.float64)
        t_call = time.perf_counter()
        rc = lib.vnx_align_batch_profiled_lv(*args, _ptr(stages), lanes)
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


# ============================================================================ differential self-test (dispatch)
#: callable returning (aligner, reads, quals) for :func:`selftest`; registered by vnxdna.sync.template when it is
#: imported (the reference aligner lives in a higher layer, so the native layer never imports it). Every native
#: alignment runs through a TemplateAligner, so the workload is registered whenever this kernel is in use.
_SELFTEST_WORKLOAD = None


def register_selftest_workload(fn) -> None:
    """Install the fixed self-test workload provider (called once by vnxdna.sync.template)."""
    global _SELFTEST_WORKLOAD
    _SELFTEST_WORKLOAD = fn


def selftest_available() -> bool:
    """True once the self-test workload (and with it the reference aligner) is registered."""
    return _SELFTEST_WORKLOAD is not None


def _selftest_workload():
    if _SELFTEST_WORKLOAD is None:
        raise NativeAlignmentError("self-test workload not registered (vnxdna.sync.template is not imported)")
    return _SELFTEST_WORKLOAD()


def selftest(level: str) -> bool:
    """Run the fixed workload through ``level`` and the reference (TemplateAligner._align): True iff all 7 output
    arrays are byte-identical. Raises NativeAlignmentError if the level cannot run."""
    if level not in LEVELS:
        raise ValueError(f"unknown SIMD level {level!r}")
    if level not in supported_levels():
        raise NativeAlignmentError(f"SIMD level {level} is not supported on this CPU/OS")
    al, reads, quals = _selftest_workload()
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=len(reads))
    usable = [i for i in range(len(reads)) if abs(int(lengths[i]) - al.T) <= al.band]
    r = [reads[i] for i in usable]
    q = [quals[i] for i in usable]
    got = align_usable(al, r, q, 20, backend=level)
    if got is None:
        raise NativeAlignmentError("self-test workload left the native domain")
    want = al._align(r, lengths[usable], q, 20)
    return len(got) == len(want) == 7 and all(
        np.asarray(a).dtype == np.asarray(b).dtype and np.asarray(a).shape == np.asarray(b).shape
        and np.asarray(a).tobytes() == np.asarray(b).tobytes() for a, b in zip(got, want))


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


def main(argv: list[str] | None = None) -> int:
    """``python -m … build [--strict]`` compiles the in-place library; anything else prints :func:`status` as JSON."""
    import json
    import sys

    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["build"]:
        print(build(strict=True if "--strict" in args[1:] else None))
    else:
        print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
