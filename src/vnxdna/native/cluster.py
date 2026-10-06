"""Native (C) kernels of the V7 read-clustering stage, with the NumPy reference in ``vnxdna.recovery.cluster`` as
specification and fallback.

The kernel (``native/c/cluster.c``) reproduces the reference functions bit for bit:

    sketch          sketch.sketch_reads_reference        MinHash sketches of canonical k-mers
    candidates      graph.candidate_pairs_reference      bucketed candidate pairs, sorted for verification
    banded          editdist.banded_distance_reference   banded unit-cost edit distance (Myers bit-vector + exact band)
    verify          the verification loop of graph.cluster_reads_reference (chunked, union-find with parity)
    fb              consensus.fb_calls_reference         forward-backward certain calls (scalar or AVX2 lanes)

It is a plain C shared library loaded with :mod:`ctypes` (no Python headers); ctypes releases the GIL during each call,
so :func:`fb` can split a chunk over threads (``VNXDNA_CLUSTER_THREADS``, default 1). Every read's result depends only
on that read, so the output is identical for any thread count.

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

ABI_VERSION = 1
BACKENDS = ("auto", "native", "reference")
INF = 1 << 28
MAX_COST = 1024
MAX_BAND = 512
MAX_TEMPLATE = 8192
MAX_SLACK = 1 << 20
MAX_READ = 1 << 20
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


_lib = None
_load_error: str | None = None
_lib_path: str | None = None
_fallback_logged = False
_pool: ThreadPoolExecutor | None = None
_pool_size = 0
_pool_lock = threading.Lock()


def _candidates() -> list[Path]:
    out = []
    env = os.environ.get(ENV_LIB)
    if env:
        out.append(Path(env))
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    ext = _HERE / f"_vnx_cluster{suffix}"
    if ext.is_file():
        out.append(ext)
    out.extend(p for p in sorted(_HERE.glob("_vnx_cluster*.so")) if p != ext)
    out.append(_INPLACE)
    return out


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
    lib.vnx_cl_banded.restype = ctypes.c_int
    lib.vnx_cl_banded.argtypes = [i64, p, i64, p, p, i64, p, p, p, i64, p]
    lib.vnx_cl_verify.restype = ctypes.c_int
    lib.vnx_cl_verify.argtypes = [i64, p, i64, p, p, i64, p, p, p, p, dbl, i64, i64, p, p, p, p]
    lib.vnx_cl_fb.restype = ctypes.c_int
    lib.vnx_cl_fb.argtypes = [i64, i64, i64, p, p, p, i64, p, p, p, i32, i32, p, p, i32, i32]


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
            lib = ctypes.CDLL(str(path))
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


def threads() -> int:
    """Threads of :func:`fb` (``VNXDNA_CLUSTER_THREADS``, default 1, at most 64). Results do not depend on it."""
    try:
        return max(1, min(64, int(os.environ.get("VNXDNA_CLUSTER_THREADS") or 1)))
    except ValueError:
        return 1


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
    if rc < 0:
        raise NativeClusterError(f"native {what} failed ({rc}: {_ERRORS.get(rc, 'unknown')})")


def _lib_or_raise():
    lib = _load()
    if lib is None:
        raise NativeClusterError(f"native cluster kernels unavailable: {_load_error}")
    return lib


def _seqs(buf: np.ndarray, off: np.ndarray, lens: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Contiguous uint8 buffer and int64 offsets/lengths of equal size (the kernels check every [off, off + len)
    against the buffer size)."""
    buf = np.ascontiguousarray(buf, dtype=np.uint8)
    off = np.ascontiguousarray(off, dtype=np.int64)
    lens = np.ascontiguousarray(lens, dtype=np.int64)
    if off.ndim != 1 or off.shape != lens.shape:
        raise ValueError("offsets and lengths must be 1-D of equal size")
    if buf.size == 0:
        buf = np.zeros(1, dtype=np.uint8)
    return buf, off, lens


def pack(seqs: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Concatenate uint8 code arrays: (buffer, offsets, lengths)."""
    n = len(seqs)
    lens = np.fromiter((s.size for s in seqs), dtype=np.int64, count=n)
    off = np.zeros(n, dtype=np.int64)
    if n:
        np.cumsum(lens[:-1], out=off[1:])
    buf = np.concatenate([np.asarray(s, dtype=np.uint8).ravel() for s in seqs]) if n and lens.sum() else \
        np.zeros(1, dtype=np.uint8)
    return np.ascontiguousarray(buf, dtype=np.uint8), off, lens


def codes_in_domain(buf: np.ndarray) -> bool:
    """Reverse complements in the kernel need codes < 8 (the reference raises IndexError on larger codes)."""
    return buf.size == 0 or int(buf.max()) < 8


def lengths_in_domain(lens: np.ndarray) -> bool:
    return lens.size == 0 or int(lens.max()) <= MAX_READ


# ============================================================================ kernels
def sketch(raw: np.ndarray, lengths: np.ndarray, k: int, s: int) -> tuple[np.ndarray, np.ndarray]:
    lib = _lib_or_raise()
    raw = np.ascontiguousarray(raw, dtype=np.uint8)
    n = raw.shape[0]
    width = raw.shape[1] if raw.ndim == 2 else 0
    lengths = np.ascontiguousarray(lengths, dtype=np.int64)
    hashes = np.empty((n, s), dtype=np.uint32)
    orient = np.empty((n, s), dtype=np.uint8)
    if lengths.shape != (n,):
        raise ValueError(f"lengths must have shape ({n},), got {lengths.shape}")
    _check(lib.vnx_cl_sketch(n, width, _ptr(raw), raw.size, _ptr(lengths), int(k), int(s), _ptr(hashes), _ptr(orient)),
           "sketch")
    return hashes, orient


def candidates(hashes: np.ndarray, orient: np.ndarray, bucket_cap: int, max_pairs: int, min_shared: int) -> tuple:
    """(a, b, rel, stats); stats = [buckets_over_cap, slot pairs, budget hit, kept, below min_shared, slots with >= 2
    sketched reads, retained buckets]."""
    lib = _lib_or_raise()
    hashes = np.ascontiguousarray(hashes, dtype=np.uint32)
    orient = np.ascontiguousarray(orient, dtype=np.uint8)
    if hashes.ndim != 2 or orient.shape != hashes.shape:
        raise ValueError(f"hashes must be 2-D and orient the same shape, got {hashes.shape} and {orient.shape}")
    n, s = hashes.shape
    stats = np.zeros(8, dtype=np.int64)
    cap = max(16, 8 * n)
    while True:
        a = np.empty(cap, dtype=np.int64)
        b = np.empty(cap, dtype=np.int64)
        rel = np.empty(cap, dtype=np.uint8)
        rc = lib.vnx_cl_candidates(n, s, _ptr(hashes), _ptr(orient), int(bucket_cap), int(max_pairs), int(min_shared),
                                   _ptr(a), _ptr(b), _ptr(rel), cap, _ptr(stats))
        _check(rc, "candidates")
        if rc == 1:
            cap = int(stats[3])
            continue
        k = int(stats[3]) if not stats[2] else 0
        return a[:k].copy(), b[:k].copy(), rel[:k].copy(), stats


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
    lib = _lib_or_raise()
    n, T = tpl.shape
    tpl = np.ascontiguousarray(tpl, dtype=np.int16)
    mc = np.ascontiguousarray(mc, dtype=np.int32)
    band = np.ascontiguousarray(band, dtype=np.int64)
    buf, off, lens = _seqs(buf, off, lens)
    if mc.shape != (n, T) or off.shape != (n,) or band.shape != (n,):
        raise ValueError("tpl and mc must be (n, T); off, lens and band (n,)")
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
                             off[r0:].ctypes.data, lens[r0:].ctypes.data, band[r0:].ctypes.data, int(c_indel),
                             int(slack), calls[r0:].ctypes.data, opt[r0:].ctypes.data, int(level), int(width))

    if parts == 1:
        rcs = [run(0)]
    else:
        rcs = list(_get_pool(nt).map(run, range(parts)))
    for rc in rcs:
        _check(rc, "forward-backward")
    return calls, opt


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
