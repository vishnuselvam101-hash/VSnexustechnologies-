"""Native (C) kernels of the V7 read-clustering stage, with the NumPy reference in ``vnxdna.recovery.cluster`` as
specification and fallback.

The kernel (``native/c/cluster.c``) reproduces the reference functions bit for bit:

    sketch          sketch.sketch_reads_reference        MinHash sketches of canonical k-mers
    candidates      graph.candidate_pairs_reference      bucketed candidate pairs, sorted for verification
    banded          editdist.banded_distance_reference   banded unit-cost edit distance (Myers bit-vector + exact band)
    verify          the verification loop of graph.cluster_reads_reference (chunked, union-find with parity)
    fb              consensus.fb_calls_reference         forward-backward certain calls (scalar or AVX2 lanes)
    edit_costs      polish.edit_costs_reference          full-template polish: exact cost of every single edit (V9)

It is a plain C shared library loaded with :mod:`ctypes` (no Python headers); ctypes releases the GIL during each call,
so :func:`fb` and :func:`edit_costs` can split a chunk over threads. The count is ``VNXDNA_CLUSTER_THREADS`` when set,
else the scope set by :func:`thread_scope` (the decoder sets its ``workers`` there for the clustering stage, V10 W1),
else 1. Every read's result depends only on that read, so the output is identical for any thread count.

Backend selection::

    VNXDNA_CLUSTER_BACKEND=auto       native if the library loads, else the reference (default; logged once)
    VNXDNA_CLUSTER_BACKEND=native     native; raise NativeClusterError if the library is unavailable
    VNXDNA_CLUSTER_BACKEND=reference  always the NumPy reference

The library is looked up in this order: ``VNXDNA_CLUSTER_LIB`` (explicit path, e.g. a sanitizer build), the extension
built by ``pip install`` (``vnxdna/_vnx_cluster*.so``, module name ``vnxdna._vnx_cluster``) and the library built in place by :func:`build`
(``python -m vnxdna.native.cluster build`` writes ``vnxdna/native/c/libvnx_cluster.so``). A library whose ABI version
differs from :data:`ABI_VERSION` is ignored. Inputs outside the native domain (codes >= 8, costs outside [0, 1024],
bands above 512, templates longer than 8192, negative slack) are routed to the reference by the callers
(:func:`fb_in_domain`, :func:`codes_in_domain`); that routing is part of the contract, not an error.
"""
from __future__ import annotations

import contextlib
import contextvars
import ctypes
import logging
import os
import shutil
import subprocess
import sysconfig
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from vnxdna import _native_build as _nb
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.native._loader import (INT32_MAX, INT32_MIN, INT64_MAX, INT64_MIN, checked_array, checked_int)

ABI_VERSION = 6
BACKENDS = ("auto", "native", "reference")
INF = 1 << 28
MAX_COST = 1024
MAX_BAND = 512
MAX_TEMPLATE = 8192
MAX_SLACK = 1 << 20
MAX_READ = 1 << 20
#: the sketch arguments the kernel accepts (native/c/cluster.c: vnx_cl_sketch)
MAX_SKETCH_K = 31
MAX_SKETCH_S = 256
LEVELS = {0: "best", 1: "scalar", 2: "avx2"}

# the pip-built extension (setup.py: vnxdna._vnx_cluster) lands in the package root; the C source and the in-place
# development library live in vnxdna/native/c
_HERE = Path(__file__).resolve().parents[1]
_SOURCE = _HERE / "native" / "c" / "cluster.c"
_INPLACE = _HERE / "native" / "c" / "libvnx_cluster.so"
CFLAGS = _nb.explicit_cflags(strict=False)        # -O3 -std=c11 -fPIC -shared -Wall -Wextra (as pip install)
STRICT_CFLAGS = _nb.explicit_cflags(strict=True)  # + -Werror: CI and sanitizer builds (build --strict)
ENV = "VNXDNA_CLUSTER_BACKEND"
ENV_LIB = "VNXDNA_CLUSTER_LIB"
_ERRORS = {-1: "invalid argument", -2: "out of memory"}

log = logging.getLogger(__name__)


class NativeClusterError(RuntimeError):
    pass


class NativeClusterArgumentError(NativeClusterError, ValueError):
    """An argument the cluster kernel cannot take: an integer outside the range of the ctypes argument, or an array
    whose values do not fit the dtype the kernel reads.

    Both a :class:`NativeClusterError` (the error type of this module) and a :class:`ValueError` (the standard refusal
    of a value outside its domain), so a caller may catch either.
    """


class NativeClusterMemoryError(NativeClusterError, MemoryError):
    """A kernel could not allocate its working memory (nothing was returned)."""


#: slot pairs materialised at once by the candidate step (8 bytes each: 256 MiB); results do not depend on it
DEFAULT_CHUNK_PAIRS = 1 << 25
_INT64_MAX = INT64_MAX
_INT64_MIN = INT64_MIN
_INT32_MIN = INT32_MIN
_INT32_MAX = INT32_MAX


_lib = None
_load_error: str | None = None
_lib_path: str | None = None
_fallback_logged = False
_pool: ThreadPoolExecutor | None = None
_pool_size = 0
_pool_lock = threading.Lock()


def _candidates() -> list[Path]:
    from vnxdna.native._loader import freshest_first
    out = []
    env = os.environ.get(ENV_LIB)
    pinned = Path(env) if env else None
    if pinned is not None:
        out.append(pinned)
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    ext = _HERE / f"_vnx_cluster{suffix}"
    if ext.is_file():
        out.append(ext)
    out.extend(p for p in sorted(_HERE.glob("_vnx_cluster*.so")) if p != ext)
    out.append(_INPLACE)
    return freshest_first(out, _INPLACE, pinned)


def _bind(lib) -> None:
    i64, i32, p, dbl = ctypes.c_int64, ctypes.c_int32, ctypes.c_void_p, ctypes.c_double
    lib.vnx_cl_abi_version.restype = ctypes.c_int
    lib.vnx_cl_abi_version.argtypes = []
    lib.vnx_cl_fb_level.restype = ctypes.c_int
    lib.vnx_cl_fb_level.argtypes = []
    lib.vnx_cl_sketch.restype = ctypes.c_int
    lib.vnx_cl_sketch.argtypes = [i64, i64, p, i64, p, i32, i32, p, p]
    lib.vnx_cl_candidates.restype = ctypes.c_int
    lib.vnx_cl_candidates.argtypes = [i64, i32, p, p, i64, i64, i64, p, p, p, i64, p]
    lib.vnx_cl_cand_run.restype = ctypes.c_int
    lib.vnx_cl_cand_run.argtypes = [i64, i32, p, p, i64, i64, i64, i64, p, ctypes.POINTER(ctypes.c_void_p)]
    lib.vnx_cl_cand_run_capped.restype = ctypes.c_int
    lib.vnx_cl_cand_run_capped.argtypes = [i64, i32, p, p, i64, i64, i64, i64, i64, p, ctypes.POINTER(ctypes.c_void_p)]
    lib.vnx_cl_cand_take.restype = ctypes.c_int
    lib.vnx_cl_cand_take.argtypes = [p, i64, p, p, p, i64]
    lib.vnx_cl_cand_free.restype = None
    lib.vnx_cl_cand_free.argtypes = [p]
    lib.vnx_cl_banded.restype = ctypes.c_int
    lib.vnx_cl_banded.argtypes = [i64, p, i64, p, p, i64, p, p, p, i64, p]
    lib.vnx_cl_verify.restype = ctypes.c_int
    lib.vnx_cl_verify.argtypes = [i64, p, i64, p, p, i64, p, p, p, p, dbl, i64, i64, p, p, p, p]
    lib.vnx_cl_fb.restype = ctypes.c_int
    lib.vnx_cl_fb.argtypes = [i64, i64, i64, p, p, p, i64, p, p, p, i32, i32, p, p, i32, i32]
    lib.vnx_cl_edit_costs.restype = ctypes.c_int
    lib.vnx_cl_edit_costs.argtypes = [i64, i64, i64, p, p, p, i64, p, p, p, i32, i32, p, p, p, p]
    lib.vnx_cl_trial_costs.restype = ctypes.c_int
    lib.vnx_cl_trial_costs.argtypes = [i64, i64, i64, i64, p, p, p, i64, p, p, p, p, p, i32, i64, p, p, p, i64, p, p, p]
    lib.vnx_cl_polish_deltas.restype = ctypes.c_int
    lib.vnx_cl_polish_deltas.argtypes = [i64, i64, i64, i64, p, p, p, i64, p, p, p, p, p, i32, i32, i64, p, p, p, p,
                                         ctypes.c_int]


def _load():
    global _lib, _load_error, _lib_path
    if _lib is not None or _load_error is not None:
        return _lib
    errors = []
    for path in _candidates():
        if not path.is_file():
            if path != _INPLACE:
                errors.append(f"{path}: not found")
            continue
        try:
            from vnxdna.native._loader import open_library
            lib = open_library(path)
            _bind(lib)
            abi = lib.vnx_cl_abi_version()
            if abi != ABI_VERSION:
                errors.append(f"{path}: ABI {abi} != {ABI_VERSION}")
                continue
        except (OSError, AttributeError) as error:
            errors.append(f"{path}: {error}")
            continue
        _lib, _lib_path = lib, str(path)
        return _lib
    _load_error = "; ".join(errors) if errors else ("native cluster library not built (pip install, or python -m "
                                                    "vnxdna.native.cluster build)")
    return None


def _reset_for_tests() -> None:
    global _lib, _load_error, _lib_path, _fallback_logged
    _lib = _load_error = _lib_path = None
    _fallback_logged = False


def available() -> bool:
    return _load() is not None


def requested_backend(explicit: str | None = None) -> str:
    name = (explicit or os.environ.get(ENV) or "auto").strip().lower()
    if name not in BACKENDS:
        raise ValueError(f"cluster backend must be one of {BACKENDS}, got {name!r}")
    return name


def resolve_backend(explicit: str | None = None) -> str:
    """'native' or 'reference': the implementation that will actually run."""
    global _fallback_logged
    want = requested_backend(explicit)
    if want == "reference":
        return "reference"
    if available():
        return "native"
    if want == "native":
        raise NativeClusterError(f"native cluster kernels requested but unavailable: {_load_error}")
    if not _fallback_logged:
        log.warning("native cluster kernels unavailable (%s); using the NumPy reference vnxdna.recovery.cluster",
                    _load_error)
        _fallback_logged = True
    return "reference"


_scope_threads: contextvars.ContextVar[int | None] = contextvars.ContextVar("vnx_cluster_threads", default=None)


def threads() -> int:
    """Threads of :func:`fb` and :func:`edit_costs``: ``VNXDNA_CLUSTER_THREADS`` if set, else the :func:`thread_scope`
    value, else 1; at most 64. Results do not depend on it.

    A set but unusable value is a configuration error, not a silent fallback to one thread: the value must be a plain
    decimal count (``"4"``), and an empty, blank, padded or otherwise decorated one is as unusable as a non-numeric
    one.
    """
    env = os.environ.get("VNXDNA_CLUSTER_THREADS")
    if env is not None:
        if not env.isascii() or not env.isdecimal():
            raise VNXConfigurationError(f"VNXDNA_CLUSTER_THREADS must be a positive integer, got {env!r}")
        n = int(env)
        if n < 1:
            raise VNXConfigurationError(f"VNXDNA_CLUSTER_THREADS must be a positive integer, got {env!r}")
        return max(1, min(64, n))
    scoped = _scope_threads.get()
    return 1 if scoped is None else max(1, min(64, int(scoped)))


@contextlib.contextmanager
def thread_scope(n: int | None):
    """Use ``n`` kernel threads inside the block when ``VNXDNA_CLUSTER_THREADS`` is unset (``None`` leaves it as is)."""
    if n is None:
        yield
        return
    token = _scope_threads.set(int(n))
    try:
        yield
    finally:
        _scope_threads.reset(token)


def fb_level() -> str | None:
    lib = _load()
    return None if lib is None else LEVELS.get(int(lib.vnx_cl_fb_level()))


def status() -> dict:
    """Diagnostics; never raises."""
    want = os.environ.get(ENV) or "auto"
    try:
        active, err = resolve_backend(), None
    except (NativeClusterError, ValueError) as error:
        active, err = None, str(error)
    return {"requested_backend": want, "active_backend": active, "native_available": available(),
            "library": _lib_path, "load_error": _load_error, "abi_version": ABI_VERSION, "error": err,
            "fb_level": fb_level(), "threads": threads()}


def build(output: str | os.PathLike | None = None, extra_flags: list[str] | None = None, compiler: str | None = None,
          strict: bool | None = None) -> Path:
    """Compile ``native/c/cluster.c`` into a shared library (default: next to the source). Returns its path."""
    cc = compiler or os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise NativeClusterError("no C compiler found (set CC)")
    out = Path(output) if output else _INPLACE
    cmd = [cc, *_nb.explicit_cflags(strict), *(extra_flags or []), str(_SOURCE), "-o", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise NativeClusterError(f"native build failed: {' '.join(cmd)}\n{res.stderr}")
    return out


# ============================================================================ helpers
def _ptr(a: np.ndarray | None):
    return None if a is None else a.ctypes.data


def _check(rc: int, what: str) -> None:
    if rc == -2:
        raise NativeClusterMemoryError(f"native {what} failed ({rc}: {_ERRORS[rc]})")
    if rc < 0:
        raise NativeClusterError(f"native {what} failed ({rc}: {_ERRORS.get(rc, 'unknown')})")


def _i32(value, name: str) -> int:
    """Return ``value`` as the int32 the kernel takes, refusing a value that does not fit.

    ctypes narrows an out-of-range Python int to the width of the argument type without a word, so a value of
    ``2 ** 32 + 5`` would reach the kernel as ``5`` and the kernel would answer for a different value than the
    caller asked for. Such a value is refused here instead, with the same error the kernel's own refusal raises.
    """
    return checked_int(name, value, _INT32_MIN, _INT32_MAX, error=NativeClusterArgumentError)


def _i64(value, name: str) -> int:
    """Return ``value`` as the int64 the kernel takes, refusing a value that does not fit (as :func:`_i32`)."""
    return checked_int(name, value, _INT64_MIN, _INT64_MAX, error=NativeClusterArgumentError)


def _lib_or_raise():
    lib = _load()
    if lib is None:
        raise NativeClusterError(f"native cluster kernels unavailable: {_load_error}")
    return lib


def _seqs(buf: np.ndarray, off: np.ndarray, lens: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Contiguous uint8 buffer and int64 offsets/lengths of equal size (the kernels check every [off, off + len)
    against the buffer size)."""
    buf = checked_array("buf", buf, np.uint8, error=NativeClusterArgumentError)
    off = checked_array("off", off, np.int64, error=NativeClusterArgumentError)
    lens = checked_array("lens", lens, np.int64, error=NativeClusterArgumentError)
    if off.ndim != 1 or off.shape != lens.shape:
        raise ValueError("offsets and lengths must be 1-D of equal size")
    if buf.size == 0:
        buf = np.zeros(1, dtype=np.uint8)
    return buf, off, lens


def pack(seqs: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Concatenate uint8 code arrays: (buffer, offsets, lengths).

    A code that does not fit a uint8 is refused: the cast would narrow it (a code of 300 becomes 44) and the kernel
    would read a different read than the caller passed."""
    n = len(seqs)
    lens = np.fromiter((s.size for s in seqs), dtype=np.int64, count=n)
    off = np.zeros(n, dtype=np.int64)
    if n:
        np.cumsum(lens[:-1], out=off[1:])
    buf = np.concatenate([checked_array("read", s, np.uint8, error=NativeClusterArgumentError).ravel() for s in seqs]) \
        if n and lens.sum() else np.zeros(1, dtype=np.uint8)
    return np.ascontiguousarray(buf, dtype=np.uint8), off, lens


def codes_in_domain(buf: np.ndarray) -> bool:
    """Reverse complements in the kernel need codes < 8 (the reference raises IndexError on larger codes)."""
    return buf.size == 0 or int(buf.max()) < 8


def lengths_in_domain(lens: np.ndarray) -> bool:
    return lens.size == 0 or int(lens.max()) <= MAX_READ


# ============================================================================ kernels
def sketch(raw: np.ndarray, lengths: np.ndarray, k: int, s: int) -> tuple[np.ndarray, np.ndarray]:
    # the arguments are checked before anything is allocated: the sketch arrays are sized by s, and an int32 argument
    # is narrowed by ctypes, so both must be refused here rather than by the kernel (too late) or by ctypes (silently)
    k = _i32(k, "k")
    s = _i32(s, "s")
    if not 1 <= k <= MAX_SKETCH_K:
        raise NativeClusterError(f"native cluster: k must be in 1..{MAX_SKETCH_K}, got {k}")
    if not 1 <= s <= MAX_SKETCH_S:
        raise NativeClusterError(f"native cluster: s must be in 1..{MAX_SKETCH_S}, got {s}")
    raw = checked_array("raw", raw, np.uint8, error=NativeClusterArgumentError)
    n = raw.shape[0]
    width = raw.shape[1] if raw.ndim == 2 else 0
    lengths = checked_array("lengths", lengths, np.int64, error=NativeClusterArgumentError)
    if lengths.shape != (n,):
        raise ValueError(f"lengths must have shape ({n},), got {lengths.shape}")
    lib = _lib_or_raise()
    hashes = np.empty((n, s), dtype=np.uint32)
    orient = np.empty((n, s), dtype=np.uint8)
    _check(lib.vnx_cl_sketch(n, width, _ptr(raw), raw.size, _ptr(lengths), k, s, _ptr(hashes), _ptr(orient)), "sketch")
    return hashes, orient


def candidates(hashes: np.ndarray, orient: np.ndarray, bucket_cap: int, max_pairs: int, min_shared: int,
               chunk_pairs: int = DEFAULT_CHUNK_PAIRS, cap_floor: int | None = None) -> tuple:
    """(a, b, rel, stats); stats = [buckets_over_cap, slot pairs, budget hit, kept, below min_shared, slots with >= 2
    sketched reads, retained buckets, partitions, slot-pair buffer entries, lowered bucket cap (0 = not lowered),
    buckets dropped by it, slot pairs at ``bucket_cap``]. At most ``chunk_pairs`` slot pairs (or one read's, if more)
    are held at once; the result does not depend on it. ``cap_floor`` (None = never): over the budget, the bucket cap
    may be lowered down to it (see ``graph.candidate_pairs_reference``). Raises NativeClusterMemoryError when the
    working memory cannot be allocated (nothing is returned in that case)."""
    lib = _lib_or_raise()
    hashes = np.ascontiguousarray(hashes, dtype=np.uint32)
    orient = np.ascontiguousarray(orient, dtype=np.uint8)
    if hashes.ndim != 2 or orient.shape != hashes.shape:
        raise ValueError(f"hashes must be 2-D and orient the same shape, got {hashes.shape} and {orient.shape}")
    n, s = hashes.shape
    s = _i32(s, "s")
    stats = np.zeros(12, dtype=np.int64)
    handle = ctypes.c_void_p()
    floor = _INT64_MAX if cap_floor is None else min(int(cap_floor), _INT64_MAX)
    _check(lib.vnx_cl_cand_run_capped(n, s, _ptr(hashes), _ptr(orient), int(bucket_cap), int(max_pairs),
                                      int(min_shared), int(chunk_pairs), floor, _ptr(stats), ctypes.byref(handle)),
           "candidates")
    try:
        k = int(stats[3]) if not stats[2] else 0
        a = np.empty(k, dtype=np.int64)
        b = np.empty(k, dtype=np.int64)
        rel = np.empty(k, dtype=np.uint8)
        _check(lib.vnx_cl_cand_take(handle, max(n, 1), _ptr(a), _ptr(b), _ptr(rel), k), "candidates")
    finally:
        lib.vnx_cl_cand_free(handle)
    return a, b, rel, stats


def banded(buf: np.ndarray, off: np.ndarray, lens: np.ndarray, ia: np.ndarray, ib: np.ndarray,
           brc: np.ndarray | None, slack: int) -> np.ndarray:
    """Banded distances of the pairs (seq ia[p], seq ib[p] reverse-complemented when brc[p])."""
    lib = _lib_or_raise()
    ia = np.ascontiguousarray(ia, dtype=np.int64)
    ib = np.ascontiguousarray(ib, dtype=np.int64)
    brc = None if brc is None else np.ascontiguousarray(brc, dtype=np.uint8)
    out = np.empty(ia.size, dtype=np.int64)
    buf, off, lens = _seqs(buf, off, lens)
    if ib.size != ia.size or (brc is not None and brc.size != ia.size):
        raise ValueError("ia, ib and brc must have one entry per pair")
    _check(lib.vnx_cl_banded(off.size, _ptr(buf), buf.size, _ptr(off), _ptr(lens), ia.size, _ptr(ia), _ptr(ib),
                             _ptr(brc), int(slack), _ptr(out)), "banded distance")
    return out


def banded_distance(a: list, b: list, slack: int) -> np.ndarray:
    """Drop-in for editdist.banded_distance_reference on lists of code arrays."""
    p = len(a)
    if p == 0:
        return np.zeros(0, dtype=np.int64)
    buf, off, lens = pack(list(a) + list(b))
    idx = np.arange(p, dtype=np.int64)
    return banded(buf, off, lens, idx, idx + p, None, slack)


def verify(buf: np.ndarray, off: np.ndarray, lens: np.ndarray, pa: np.ndarray, pb: np.ndarray, prel: np.ndarray,
           pmax: np.ndarray, theta: float, slack: int, chunk: int) -> tuple:
    """(root, orient, best, counters[skipped, verified, accepted, rejected, conflicts])."""
    lib = _lib_or_raise()
    buf, off, lens = _seqs(buf, off, lens)
    n = off.size
    pa, pb, pmax = (np.ascontiguousarray(x, dtype=np.int64) for x in (pa, pb, pmax))
    prel = np.ascontiguousarray(prel, dtype=np.uint8)
    if not pb.size == prel.size == pmax.size == pa.size:
        raise ValueError("pa, pb, prel and pmax must have one entry per pair")
    root = np.empty(n, dtype=np.int64)
    orient = np.empty(n, dtype=np.uint8)
    best = np.full(n, -np.inf)
    counters = np.zeros(5, dtype=np.int64)
    _check(lib.vnx_cl_verify(n, _ptr(buf), buf.size, _ptr(off), _ptr(lens), pa.size, _ptr(pa), _ptr(pb), _ptr(prel), _ptr(pmax),
                             float(theta), int(slack), int(chunk), _ptr(root), _ptr(orient), _ptr(best),
                             _ptr(counters)), "verification")
    return root, orient, best, counters


def fb_in_domain(T: int, B: int, mc: np.ndarray, c_indel: int, slack: int, lens: np.ndarray) -> bool:
    return (0 <= T <= MAX_TEMPLATE and 0 <= B <= MAX_BAND and 0 <= c_indel <= MAX_COST and 0 <= slack <= MAX_SLACK
            and (mc.size == 0 or (int(mc.min()) >= 0 and int(mc.max()) <= MAX_COST)) and lengths_in_domain(lens))


def _get_pool(n: int) -> ThreadPoolExecutor:
    """A shared pool with at least ``n`` workers. Creation and replacement hold a lock, and the pool only grows: a
    replaced pool is not shut down (another thread may be about to submit to it); it is dropped and its idle workers
    exit once nothing references it."""
    global _pool, _pool_size
    with _pool_lock:
        if _pool is None or _pool_size < n:
            _pool, _pool_size = ThreadPoolExecutor(max_workers=n, thread_name_prefix="vnx-cluster"), n
        return _pool


def fb(tpl: np.ndarray, mc: np.ndarray, buf: np.ndarray, off: np.ndarray, lens: np.ndarray, band: np.ndarray, B: int,
       c_indel: int, slack: int, level: int = 0, nthreads: int | None = None,
       width: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Forward-backward certain calls of one chunk with shared band ``B`` (as consensus._fb). ``level``: 0 best, 1 scalar,
    2 AVX2; ``width``: 0 auto (int16 lanes when the cost bound allows), 16, 32. Results do not depend on either."""
    c_indel = _i32(c_indel, "c_indel")
    slack = _i32(slack, "slack")
    level = _i32(level, "level")
    width = _i32(width, "width")
    B = _i64(B, "B")
    n, T = tpl.shape
    tpl = checked_array("tpl", tpl, np.int16, error=NativeClusterArgumentError)
    mc = checked_array("mc", mc, np.int32, error=NativeClusterArgumentError)
    band = checked_array("band", band, np.int64, error=NativeClusterArgumentError)
    buf, off, lens = _seqs(buf, off, lens)
    if mc.shape != (n, T) or off.shape != (n,) or band.shape != (n,):
        raise ValueError("tpl and mc must be (n, T); off, lens and band (n,)")
    lib = _lib_or_raise()
    calls = np.empty((n, T), dtype=np.uint8)
    opt = np.empty(n, dtype=np.int64)
    nt = threads() if nthreads is None else max(1, int(nthreads))
    # split on multiples of 16 reads (the kernel's lane groups); each part is an independent call on disjoint outputs
    parts = max(1, min(nt, (n + 15) // 16))
    bounds = [((n * k // parts) // 16) * 16 for k in range(parts)] + [n]

    def run(k: int) -> int:
        r0, r1 = bounds[k], bounds[k + 1]
        if r1 <= r0:
            return 0
        return lib.vnx_cl_fb(r1 - r0, T, int(B), tpl[r0:].ctypes.data, mc[r0:].ctypes.data, _ptr(buf), buf.size,
                             off[r0:].ctypes.data, lens[r0:].ctypes.data, band[r0:].ctypes.data, c_indel,
                             slack, calls[r0:].ctypes.data, opt[r0:].ctypes.data, level, width)

    if parts == 1:
        rcs = [run(0)]
    else:
        rcs = list(_get_pool(nt).map(run, range(parts)))
    for rc in rcs:
        _check(rc, "forward-backward")
    return calls, opt


def edit_costs_in_domain(T: int, B: int, mc: np.ndarray, c_indel: int, c_sub: int, lens: np.ndarray) -> bool:
    return fb_in_domain(T, B, mc, c_indel, 0, lens) and 0 <= c_sub <= MAX_COST


def edit_costs(tpl: np.ndarray, mc: np.ndarray, buf: np.ndarray, off: np.ndarray, lens: np.ndarray, band: np.ndarray,
               B: int, c_indel: int, c_sub: int,
               nthreads: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Polish edit costs of one batch with shared band ``B`` (as polish.edit_costs_reference): (opt (n,), sub (n, T, 4),
    dele (n, T), ins (n, T, 4)), int64. Every read's result depends only on that read, so threads do not change it."""
    c_indel = _i32(c_indel, "c_indel")
    c_sub = _i32(c_sub, "c_sub")
    B = _i64(B, "B")
    n, T = tpl.shape
    tpl = checked_array("tpl", tpl, np.int16, error=NativeClusterArgumentError)
    mc = checked_array("mc", mc, np.int32, error=NativeClusterArgumentError)
    band = checked_array("band", band, np.int64, error=NativeClusterArgumentError)
    buf, off, lens = _seqs(buf, off, lens)
    if mc.shape != (n, T) or off.shape != (n,) or band.shape != (n,):
        raise ValueError("tpl and mc must be (n, T); off, lens and band (n,)")
    lib = _lib_or_raise()
    opt = np.empty(n, dtype=np.int64)
    sub = np.empty((n, T, 4), dtype=np.int64)
    dele = np.empty((n, T), dtype=np.int64)
    ins = np.empty((n, T, 4), dtype=np.int64)
    nt = threads() if nthreads is None else max(1, int(nthreads))
    parts = max(1, min(nt, n))
    bounds = [n * k // parts for k in range(parts)] + [n]

    def run(k: int) -> int:
        r0, r1 = bounds[k], bounds[k + 1]
        if r1 <= r0:
            return 0
        return lib.vnx_cl_edit_costs(r1 - r0, T, int(B), tpl[r0:].ctypes.data, mc[r0:].ctypes.data, _ptr(buf),
                                     buf.size, off[r0:].ctypes.data, lens[r0:].ctypes.data, band[r0:].ctypes.data,
                                     c_indel, c_sub, opt[r0:].ctypes.data, sub[r0:].ctypes.data,
                                     dele[r0:].ctypes.data, ins[r0:].ctypes.data)

    rcs = [run(0)] if parts == 1 else list(_get_pool(nt).map(run, range(parts)))
    for rc in rcs:
        _check(rc, "polish edit costs")
    return opt, sub, dele, ins


def polish_deltas(tpls: np.ndarray, mc: np.ndarray, buf: np.ndarray, off: np.ndarray, lens: np.ndarray,
                  read_start: np.ndarray, band: np.ndarray, dip: np.ndarray, c_indel: int, c_sub: int, pen: int,
                  nthreads: int | None = None, level: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-candidate polish deltas (ABI 5): for every candidate k, the sums over its reads with an optimal cost below
    INF of ``min(cost, pen) - opt`` of the substitution (T, 4), deletion (T,) and insertion (T, 4) edit costs, i.e. the
    fold that consensus._polish applies to :func:`edit_costs`, computed and reduced in one native pass.

    ``tpls`` (ncand, T) templates; ``mc`` (T,) mismatch costs shared by all reads; reads of candidate k are
    ``read_start[k]:read_start[k + 1]`` (CSR); ``dip[r]`` is 1 when read r's band is below the shared band of the
    edit_costs chunk it would be evaluated in (that changes which cells exist, see cluster.c). Candidates are
    partitioned into contiguous ranges over threads; each range writes only its own rows, so the result does not
    depend on the thread count. ``level``: 0 best available SIMD, 1 baseline, 2 AVX2."""
    lib = _lib_or_raise()
    c_indel = _i32(c_indel, "c_indel")
    c_sub = _i32(c_sub, "c_sub")
    level = _i32(level, "level")
    tpls = np.ascontiguousarray(tpls, dtype=np.int16)
    ncand, T = tpls.shape
    mc = np.ascontiguousarray(mc, dtype=np.int32)
    read_start = np.ascontiguousarray(read_start, dtype=np.int64)
    band = np.ascontiguousarray(band, dtype=np.int64)
    dip = np.ascontiguousarray(dip, dtype=np.uint8)
    buf, off, lens = _seqs(buf, off, lens)
    n = off.size
    if (mc.shape != (T,) or read_start.shape != (ncand + 1,) or band.shape != (n,) or dip.shape != (n,)
            or (ncand and (read_start[0] != 0 or read_start[-1] != n))
            or bool(np.any(np.diff(read_start) < 0))):         # V14 F (H-8): C indexes reads by read_start[k + 1]
        raise ValueError("mc must be (T,), read_start (ncand + 1,) non-decreasing from 0 to n, band and dip (n,)")
    dsub = np.empty((ncand, T, 4), dtype=np.int64)
    ddel = np.empty((ncand, T), dtype=np.int64)
    dins = np.empty((ncand, T, 4), dtype=np.int64)
    live = np.empty(ncand, dtype=np.int64)
    nt = threads() if nthreads is None else max(1, int(nthreads))
    parts = max(1, min(nt, ncand))
    # contiguous candidate ranges of about equal read counts
    cuts: list[int] = ([int(c) for c in np.searchsorted(read_start, [n * k / parts for k in range(1, parts)], side="left")]
                       if parts > 1 else [])
    bounds = sorted({0, ncand, *cuts})

    def run(k: int) -> int:
        return lib.vnx_cl_polish_deltas(ncand, bounds[k], bounds[k + 1], T, tpls.ctypes.data, mc.ctypes.data,
                                        _ptr(buf), buf.size, off.ctypes.data, lens.ctypes.data, read_start.ctypes.data,
                                        band.ctypes.data, dip.ctypes.data, c_indel, c_sub, int(pen),
                                        dsub.ctypes.data, ddel.ctypes.data, dins.ctypes.data, live.ctypes.data,
                                        level)

    ranges = len(bounds) - 1
    rcs = [run(k) for k in range(ranges)] if ranges <= 1 else list(_get_pool(nt).map(run, range(ranges)))
    for rc in rcs:
        _check(rc, "polish deltas")
    return dsub, ddel, dins


def main(argv: list[str] | None = None) -> int:
    """``python -m vnxdna.native.cluster build [--strict]`` compiles the in-place library; else prints :func:`status`."""
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


def trial_costs_in_domain(T: int, B: int, mc: np.ndarray, c_indel: int, lens: np.ndarray) -> bool:
    return fb_in_domain(T, B, mc, c_indel, 0, lens)


def trial_costs(base_tpl: np.ndarray, mc: np.ndarray, buf: np.ndarray, off: np.ndarray, lens: np.ndarray,
                band: np.ndarray, read_start: np.ndarray, base_dip: np.ndarray, c_indel: int, pen: int,
                trial_tpl: np.ndarray, trial_start: np.ndarray, trial_row: np.ndarray, trial_dip: np.ndarray,
                nthreads: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Polish trial costs (as consensus.trial_costs_reference, V10 W2): (summed clipped cost per trial, per candidate
    base). Reads and trials are grouped by candidate (``read_start``/``trial_start``, CSR); ``trial_row[x]`` is the
    offset of trial x's rows in ``trial_dip``. Candidates are independent, so threads do not change the result."""
    c_indel = _i32(c_indel, "c_indel")
    pen = _i64(pen, "pen")
    ncand, T = base_tpl.shape
    base_tpl = checked_array("base_tpl", base_tpl, np.int16, error=NativeClusterArgumentError)
    trial_tpl = checked_array("trial_tpl", trial_tpl, np.int16, error=NativeClusterArgumentError).reshape(-1, T)
    mc = checked_array("mc", mc, np.int32, error=NativeClusterArgumentError)
    band = checked_array("band", band, np.int64, error=NativeClusterArgumentError)
    read_start = checked_array("read_start", read_start, np.int64, error=NativeClusterArgumentError)
    trial_start = checked_array("trial_start", trial_start, np.int64, error=NativeClusterArgumentError)
    trial_row = checked_array("trial_row", trial_row, np.int64, error=NativeClusterArgumentError)
    base_dip = checked_array("base_dip", base_dip, np.uint8, error=NativeClusterArgumentError)
    trial_dip = checked_array("trial_dip", trial_dip, np.uint8, error=NativeClusterArgumentError)
    buf, off, lens = _seqs(buf, off, lens)
    ntrial = trial_tpl.shape[0]
    if mc.shape != (T,) or read_start.shape != (ncand + 1,) or trial_start.shape != (ncand + 1,) \
            or trial_row.shape != (ntrial,) or int(trial_start[-1]) != ntrial or band.shape != off.shape \
            or base_dip.shape != off.shape or int(read_start[-1]) != off.size:
        raise ValueError("inconsistent trial-cost arrays")
    lib = _lib_or_raise()
    cost = np.zeros(ntrial, dtype=np.int64)
    basec = np.zeros(ncand, dtype=np.int64)
    nt = threads() if nthreads is None else max(1, int(nthreads))
    # split candidates by work (reads x (1 + trials)); every candidate's outputs belong to exactly one part
    work = np.cumsum(np.diff(read_start) * (1 + np.diff(trial_start)))
    parts = max(1, min(nt, ncand))
    total = int(work[-1]) if ncand else 0
    cuts = [0] + [int(np.searchsorted(work, total * k / parts, side="right")) for k in range(1, parts)] + [ncand]

    def run(k: int) -> int:
        c0, c1 = cuts[k], max(cuts[k], cuts[k + 1])
        if c1 <= c0:
            return 0
        return lib.vnx_cl_trial_costs(c0, c1, ncand, T, base_tpl.ctypes.data, mc.ctypes.data, _ptr(buf), buf.size,
                                      off.ctypes.data, lens.ctypes.data, band.ctypes.data, read_start.ctypes.data,
                                      base_dip.ctypes.data, c_indel, int(pen), _ptr(trial_tpl),
                                      trial_start.ctypes.data, _ptr(trial_row), trial_dip.size, _ptr(trial_dip),
                                      _ptr(cost), basec.ctypes.data)

    rcs = [run(0)] if parts == 1 else list(_get_pool(nt).map(run, range(parts)))
    for rc in rcs:
        _check(rc, "polish trial costs")
    return cost, basec
