"""Native (C) inner Reed-Solomon decoder with runtime SIMD dispatch; ``vnxdna.v4.rs_fast`` is the reference and fallback.

The kernel (``native/rs.c``) reproduces :func:`vnxdna.v4.rs_fast.decode_batch` exactly: the same corrected codewords,
``ok`` flags and errata counts, including every failure decision (too many erasures, Berlekamp-Massey degree checks,
Chien root count, erasures that are not roots, zero derivative, non-zero re-check syndromes). It is a plain C shared
library loaded with :mod:`ctypes` (no Python headers needed); ctypes releases the GIL during the call.

Backend selection (``VNXDNA_RS_BACKEND``)::

    auto       avx2 if the CPU/OS supports it, else scalar, else the reference (library missing)   (default)
    native     the same choice as auto; raise NativeRSError if the library is unavailable
    avx512     force AVX-512BW; if the CPU/OS lacks it, fall back to the next lower level and record why
    avx2       force AVX2; same fallback rule
    scalar     force the portable C level
    reference  always vnxdna.v4.rs_fast (NumPy)

AVX-512 is not selected automatically: on the development host (Xeon Gold 6240) it measured 0.94x the speed of AVX2
on the captured decode workload, i.e. no gain (benchmarks/v6/native_rs/results/bench.json); it stays available for
explicit selection and is tested bit for bit like every level.

A forced level that cannot run never executes: the C library checks cpuid + xgetbv itself and refuses
(``VNX_RS_EUNSUPPORTED``); this module degrades to the best level below it (finally the reference when the library is
missing) and logs one warning. :func:`status` reports the requested and the active backend for provenance.

The library is looked up at ``VNXDNA_RS_LIB`` (explicit path), then as an extension built by ``pip install``
(``vnxdna/v6/_vnx_rs*.so``), then next to the source (``native/libvnx_rs.so``, built by
``python -m vnxdna.v6.native_rs build``).
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
from vnxdna.core._alias import lazy_module

# the NumPy reference lives in the codec layer; it is resolved by name on first use (V6_ARCHITECTURE §3, R1/R5)
rs_fast = lazy_module("vnxdna.v4.rs_fast")

ABI_VERSION = 1
LEVELS = {"scalar": 1, "avx2": 2, "avx512": 3}
LEVEL_NAMES = {v: k for k, v in LEVELS.items()}
BACKENDS = ("auto", "native", "avx512", "avx2", "scalar", "reference")
ENV = "VNXDNA_RS_BACKEND"
ENV_LIB = "VNXDNA_RS_LIB"
AUTO_POLICY = "avx2 > scalar > reference; avx512 only when forced (measured 0.94x of avx2 on Xeon Gold 6240; benchmarks/v6/native_rs/results/bench.json)"

# the C source, the in-place library and the pip-built extension (vnxdna.v6._vnx_rs) stay in vnxdna/v6
_HERE = Path(__file__).resolve().parents[1] / "v6"
_SOURCE = _HERE / "native" / "rs.c"
_INPLACE = _HERE / "native" / "libvnx_rs.so"
CFLAGS = _nb.explicit_cflags(strict=False)        # -O3 -std=c11 -fPIC -shared -Wall -Wextra (as pip install)
STRICT_CFLAGS = _nb.explicit_cflags(strict=True)  # + -Werror: CI and sanitizer builds (build --strict)

_ERRORS = {-1: "invalid argument", -2: "SIMD level not supported by this CPU/OS", -3: "out of memory",
           -4: "output overlaps an input"}
_log = logging.getLogger(__name__)


class NativeRSError(RuntimeError):
    pass


_lib = None
_load_error: str | None = None
_lib_path: str | None = None
_warned: set[str] = set()


def _candidates() -> list[Path]:
    out = []
    env = os.environ.get(ENV_LIB)
    if env:
        out.append(Path(env))
    out.extend(_ext_candidates())
    out.append(_INPLACE)
    return out


def _ext_candidates() -> list[Path]:
    """The extension built by ``pip install`` (setup.py: ``vnxdna.v6._vnx_rs``), if present."""
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    ext = _HERE / f"_vnx_rs{suffix}"
    return ([ext] if ext.is_file() else []) + [p for p in sorted(_HERE.glob("_vnx_rs*.so")) if p != ext]


def _bind(lib) -> None:
    p, i32, i64 = ctypes.c_void_p, ctypes.c_int32, ctypes.c_int64
    for name in ("vnx_rs_levels", "vnx_rs_cpu_levels", "vnx_rs_best_level"):
        getattr(lib, name).restype = ctypes.c_int
        getattr(lib, name).argtypes = []
    lib.vnx_rs_restrict_levels.restype = None
    lib.vnx_rs_restrict_levels.argtypes = [ctypes.c_int]
    lib.vnx_rs_decode_batch.restype = ctypes.c_int
    lib.vnx_rs_decode_batch.argtypes = [i64, i32, i32, p, p, p, p, p, i32]


def _load():
    global _lib, _load_error, _lib_path
    if _lib is not None or _load_error is not None:
        return _lib
    errors = []
    for path in _candidates():
        if not path.is_file():
            errors.append(f"{path}: not found")
            continue
        try:
            lib = ctypes.CDLL(str(path))
            lib.vnx_rs_abi_version.restype = ctypes.c_int
            lib.vnx_rs_abi_version.argtypes = []
            abi = lib.vnx_rs_abi_version()
            if abi != ABI_VERSION:   # checked before binding anything else: other symbols may differ
                errors.append(f"{path}: ABI {abi} != {ABI_VERSION}")
                continue
            _bind(lib)
        except (OSError, AttributeError) as error:
            errors.append(f"{path}: {error}")
            continue
        _lib, _lib_path = lib, str(path)
        return _lib
    _load_error = "; ".join(errors) + " (pip install, or build with: python -m vnxdna.v6.native_rs build)"
    return None


def _reset_for_tests() -> None:
    global _lib, _load_error, _lib_path
    _lib = _load_error = _lib_path = None
    _warned.clear()


def _warn_once(msg: str) -> None:
    if msg not in _warned:
        _warned.add(msg)
        _log.warning(msg)


def available() -> bool:
    return _load() is not None


def _levels_mask() -> int:
    lib = _load()
    return 0 if lib is None else int(lib.vnx_rs_levels())


def supported_levels() -> list[str]:
    """Native levels usable on this machine (compiled in and supported by the CPU and the OS)."""
    mask = _levels_mask()
    return [name for name, v in LEVELS.items() if mask & (1 << v)]


def requested_backend(explicit: str | None = None) -> str:
    name = (explicit or os.environ.get(ENV) or "auto").strip().lower()
    if name not in BACKENDS:
        raise ValueError(f"RS backend must be one of {BACKENDS}, got {name!r}")
    return name


def _resolve(explicit: str | None = None) -> tuple[str, str | None]:
    """(active backend name, reason it differs from the request or None)."""
    want = requested_backend(explicit)
    if want == "reference":
        return "reference", None
    lib = _load()
    if lib is None:
        if want == "native":
            raise NativeRSError(f"native RS requested but unavailable: {_load_error}")
        reason = None if want == "auto" else f"{want} requested but the native library is unavailable ({_load_error})"
        return "reference", reason or f"native library unavailable ({_load_error})"
    best = LEVEL_NAMES[int(lib.vnx_rs_best_level())]
    if want in ("auto", "native"):
        return best, None
    mask = int(lib.vnx_rs_levels())
    if mask & (1 << LEVELS[want]):
        return want, None
    lower = [name for name, v in sorted(LEVELS.items(), key=lambda kv: -kv[1]) if v < LEVELS[want] and mask & (1 << v)]
    return lower[0], f"{want} requested but not supported by this CPU/OS; using {lower[0]}"


def active_backend(explicit: str | None = None) -> str:
    """The backend that :func:`decode_batch` will use: 'avx512', 'avx2', 'scalar' or 'reference'."""
    name, reason = _resolve(explicit)
    if reason and requested_backend(explicit) != "auto":
        _warn_once(f"vnxdna RS backend: {reason}")
    elif reason:
        _warn_once(f"vnxdna RS backend: {reason}; using the NumPy reference (vnxdna.v4.rs_fast)")
    return name


def status() -> dict:
    """Provenance/diagnostics; never raises."""
    want = os.environ.get(ENV) or "auto"
    try:
        active, reason = _resolve()
        err = None
    except (NativeRSError, ValueError) as error:
        active, reason, err = None, None, str(error)
    lib = _load()
    cpu = [] if lib is None else [n for n, v in LEVELS.items() if int(lib.vnx_rs_cpu_levels()) & (1 << v)]
    supported = supported_levels()
    return {"requested_backend": want, "active_backend": active, "fallback_reason": reason, "error": err,
            "native_available": lib is not None, "library": _lib_path, "load_error": _load_error, "abi_version": ABI_VERSION,
            "supported_levels": supported, "cpu_levels": cpu, "levels_restricted": supported != cpu,
            "auto_policy": AUTO_POLICY, "reference": "vnxdna.v4.rs_fast"}


def _ptr(a: np.ndarray | None):
    return None if a is None else a.ctypes.data_as(ctypes.c_void_p)


def _prepare(codewords, nsym, erasures):
    """Argument checks and conversions of rs_fast.decode_batch, without copying already-conforming arrays."""
    codewords = np.ascontiguousarray(codewords, dtype=np.uint8)
    if codewords.ndim != 2:
        raise ValueError("codewords must be a 2-D array")
    n_words, n = codewords.shape
    if n > 255 or nsym < 0 or nsym >= n and n_words:
        raise ValueError("invalid RS code length")
    if erasures is not None:
        erasures = np.asarray(erasures, dtype=bool)
        if erasures.shape != (n_words, n):
            raise ValueError("erasure mask has the wrong shape")
        erasures = np.ascontiguousarray(erasures)
    return codewords, erasures


def _run(lib, level: int, codewords: np.ndarray, nsym: int, erasures: np.ndarray | None, out: np.ndarray,
         ok: np.ndarray, errata: np.ndarray) -> None:
    n_words, n = codewords.shape
    er = None if erasures is None else erasures.view(np.uint8)
    rc = lib.vnx_rs_decode_batch(n_words, n, int(nsym), _ptr(codewords), _ptr(er), _ptr(out), _ptr(ok.view(np.uint8)),
                                 _ptr(errata), level)
    if rc != 0:
        # every argument was validated above; a kernel error is a bug, never silently ignored
        raise NativeRSError(f"native RS decoder failed: {_ERRORS.get(rc, rc)} (code {rc})")


def decode_batch(codewords: np.ndarray, nsym: int, erasures: np.ndarray | None = None, *, backend: str | None = None
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Drop-in for :func:`vnxdna.v4.rs_fast.decode_batch`: same arguments, results and exceptions.

    ``backend`` overrides ``VNXDNA_RS_BACKEND`` for this call (tests and benchmarks)."""
    name = active_backend(backend)
    if name == "reference":
        return rs_fast.decode_batch(codewords, nsym, erasures)
    codewords, erasures = _prepare(codewords, nsym, erasures)
    n_words, n = codewords.shape
    if nsym == 0 or n_words == 0:
        out = codewords.copy()
        return out, np.full(n_words, nsym == 0, dtype=bool), np.zeros(n_words, dtype=np.int64)
    out = np.empty_like(codewords)
    ok = np.empty(n_words, dtype=bool)
    errata = np.empty(n_words, dtype=np.int64)
    _run(_load(), LEVELS[name], codewords, nsym, erasures, out, ok, errata)
    return out, ok, errata


def decode_batch_inplace(codewords: np.ndarray, nsym: int, erasures: np.ndarray | None = None, *,
                         backend: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """In-place variant: ``codewords`` (C-contiguous, writable uint8, 2-D) receives the corrected rows; failed rows are
    left unchanged. Returns ``(ok, errata)``, identical to ``decode_batch``'s second and third results."""
    if not (isinstance(codewords, np.ndarray) and codewords.dtype == np.uint8 and codewords.ndim == 2
            and codewords.flags.c_contiguous and codewords.flags.writeable):
        raise ValueError("decode_batch_inplace needs a writable C-contiguous 2-D uint8 array")
    name = active_backend(backend)
    if name == "reference":
        out, ok, errata = rs_fast.decode_batch(codewords, nsym, erasures)
        codewords[...] = out
        return ok, errata
    codewords, erasures = _prepare(codewords, nsym, erasures)
    n_words, n = codewords.shape
    if nsym == 0 or n_words == 0:
        return np.full(n_words, nsym == 0, dtype=bool), np.zeros(n_words, dtype=np.int64)
    ok = np.empty(n_words, dtype=bool)
    errata = np.empty(n_words, dtype=np.int64)
    _run(_load(), LEVELS[name], codewords, nsym, erasures, codewords, ok, errata)
    return ok, errata


def _restrict_levels_for_tests(names: list[str] | None) -> None:
    """TEST-ONLY hook: make the library behave as if the CPU only had ``names`` (None = everything detected).

    It calls the exported C symbol ``vnx_rs_restrict_levels``, which changes a process-wide mask: every later
    ``decode_batch`` in this process (all threads) is affected until it is reset with ``None``. Production code never
    calls it (tests/v6/native/test_native_status.py checks that); :func:`status` reports ``levels_restricted`` when a
    restriction is active."""
    lib = _load()
    if lib is None:
        return
    lib.vnx_rs_restrict_levels(-1 if names is None else sum(1 << LEVELS[k] for k in names))


# ============================================================================ in-place build (development / no pip build)
def build_command(output: str | os.PathLike | None = None, extra_flags: list[str] | None = None,
                  compiler: str | None = None, strict: bool | None = None) -> list[str]:
    cc = compiler or os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise NativeRSError("no C compiler found (set CC)")
    out = Path(output) if output else _INPLACE
    return [cc, *_nb.explicit_cflags(strict), *(extra_flags or []), str(_SOURCE), "-o", str(out)]


def build(output: str | os.PathLike | None = None, extra_flags: list[str] | None = None, compiler: str | None = None,
          strict: bool | None = None) -> Path:
    """Compile ``native/rs.c`` into a shared library (default: next to the source). Returns its path.

    No ``-march``: the AVX2/AVX-512 kernels are compiled through per-function target attributes and only run after the
    runtime CPU check, so the library is safe on any x86-64 CPU."""
    cmd = build_command(output, extra_flags, compiler, strict)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise NativeRSError(f"native RS build failed: {' '.join(cmd)}\n{res.stderr}")
    return Path(cmd[-1])


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
