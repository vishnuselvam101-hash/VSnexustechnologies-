"""Native (C) streaming FASTQ / FASTA / plain-sequence parser with :mod:`vnxdna.v4.reads` as reference and fallback.

The kernel (``native/reads.c``) reproduces ``vnxdna.v4.reads.iter_reads`` exactly: the same batches (same
``Reads`` objects, dtypes, shapes and values) in the same order, and the same exception class and message
at the same point of the stream. Format detection, the ``.vxs`` path, the ``max_reads`` cap and the error
messages stay in Python and reuse the reference functions. It is a plain C shared library loaded with
:mod:`ctypes` (no Python headers); ctypes releases the GIL during each call.

Backend selection::

    VNXDNA_READS_BACKEND=auto       native if the library loads, else the reference (default; logged once)
    VNXDNA_READS_BACKEND=native     native; raise NativeReadsError if the library is unavailable
    VNXDNA_READS_BACKEND=reference  always vnxdna.v4.reads

The library is looked up in this order: ``VNXDNA_READS_LIB`` (explicit path, e.g. a sanitizer build), the
extension built by ``pip install`` (``vnxdna/v6/_vnx_reads*.so``) and the library built in place by :func:`build`
(``python -m vnxdna.v6.native_reads build`` writes ``vnxdna/v6/native/libvnx_reads.so``). A library whose ABI
version differs from :data:`ABI_VERSION` is ignored.

Buffer ownership and lifetime (also the minimal-copy entry point for the decoder):

* The file is read with ``readinto`` into one reusable NumPy buffer of ``reads.BLOCK`` bytes (8 MiB),
  aligned to the reference's block boundaries. The kernel only reads it during a call and keeps no
  pointer to it.
* For every batch the binding allocates fresh NumPy arrays (``codes``/``quals`` uint8, ``lengths`` int64,
  ``invalid`` bool) and the kernel writes the parsed values straight into them: there is no per-read Python
  object and no intermediate ``bytes``. Between calls the kernel keeps only offsets, so the arrays may be
  grown (``ndarray.resize``) while a batch fills. Before the batch is yielded the arrays are shrunk in place
  to their exact sizes; they own their memory and are never written again by the parser, so a consumer
  may keep, modify or hand them on freely.
* The parser state is a fixed-size NumPy byte array owned by the generator. The kernel never allocates;
  memory per batch is bounded by the batch's own bytes plus ``MAX_READ_NT`` of headroom.
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sysconfig
from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np

from vnxdna import _native_build as _nb
from vnxdna.core.errors import VNXFormatError, VNXResourceError
from vnxdna.core._alias import lazy_module

# the reference parser lives in a higher layer; it is resolved by name on first use (V6_ARCHITECTURE §3, R1/R5)
_ref = lazy_module("vnxdna.v4.reads")

ABI_VERSION = 1
BACKENDS = ("auto", "native", "reference")
FORMATS = {"fastq": 1, "fasta": 2, "plain": 3}

# the C source, the in-place library and the pip-built extension (vnxdna.v6._vnx_reads) stay in vnxdna/v6
_HERE = Path(__file__).resolve().parents[1] / "v6"
_SOURCE = _HERE / "native" / "reads.c"
_INPLACE = _HERE / "native" / "libvnx_reads.so"
CFLAGS = _nb.explicit_cflags(strict=False)        # -O3 -std=c11 -fPIC -shared -Wall -Wextra (as pip install)
STRICT_CFLAGS = _nb.explicit_cflags(strict=True)  # + -Werror: CI and sanitizer builds (build --strict)

NEED_INPUT, BATCH_FULL, NEED_SPACE, NEED_MORE, DONE = 0, 1, 2, 3, 4
_INTERNAL = {-1: "invalid argument", -2: "inconsistent parser state"}

log = logging.getLogger(__name__)


class NativeReadsError(RuntimeError):
    pass


_lib = None
_load_error: str | None = None
_lib_path: str | None = None
_fallback_logged = False


def _ext_candidates() -> list[Path]:
    """The extension built by ``pip install`` (setup.py: ``vnxdna.v6._vnx_reads``), if present."""
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    ext = _HERE / f"_vnx_reads{suffix}"
    return ([ext] if ext.is_file() else []) + [p for p in sorted(_HERE.glob("_vnx_reads*.so")) if p != ext]


def _candidates() -> list[Path]:
    out = []
    env = os.environ.get("VNXDNA_READS_LIB")
    if env:
        out.append(Path(env))
    out.extend(_ext_candidates())
    out.append(_INPLACE)
    return out


def _bind(lib) -> None:
    i64, i32, p = ctypes.c_int64, ctypes.c_int32, ctypes.c_void_p
    lib.vnx_reads_abi_version.restype = ctypes.c_int
    lib.vnx_reads_abi_version.argtypes = []
    lib.vnx_reads_state_size.restype = i64
    lib.vnx_reads_state_size.argtypes = []
    lib.vnx_reads_init.restype = ctypes.c_int
    lib.vnx_reads_init.argtypes = [p, i64, i32, i64, i64, i64]
    lib.vnx_reads_new_batch.restype = ctypes.c_int
    lib.vnx_reads_new_batch.argtypes = [p]
    lib.vnx_reads_feed.restype = ctypes.c_int
    lib.vnx_reads_feed.argtypes = [p, p, i64, i32, p, p, i64, p, p, i64, p, p]


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
            abi = lib.vnx_reads_abi_version()
            if abi != ABI_VERSION:
                errors.append(f"{path}: ABI {abi} != {ABI_VERSION}")
                continue
        except (OSError, AttributeError) as error:
            errors.append(f"{path}: {error}")
            continue
        _lib, _lib_path = lib, str(path)
        return _lib
    _load_error = "; ".join(errors) if errors else "native reads library not built (pip install, or python -m vnxdna.v6.native_reads build)"
    return None


def _reset_for_tests() -> None:
    global _lib, _load_error, _lib_path, _fallback_logged
    _lib = _load_error = _lib_path = None
    _fallback_logged = False


def available() -> bool:
    return _load() is not None


def requested_backend(explicit: str | None = None) -> str:
    name = (explicit or os.environ.get("VNXDNA_READS_BACKEND") or "auto").strip().lower()
    if name not in BACKENDS:
        raise ValueError(f"reads backend must be one of {BACKENDS}, got {name!r}")
    return name


def resolve_backend(explicit: str | None = None) -> str:
    """'native' or 'reference': the parser that will actually run."""
    global _fallback_logged
    want = requested_backend(explicit)
    if want == "reference":
        return "reference"
    if available():
        return "native"
    if want == "native":
        raise NativeReadsError(f"native reads parser requested but unavailable: {_load_error}")
    if not _fallback_logged:
        log.warning("native reads parser unavailable (%s); using the reference parser vnxdna.v4.reads", _load_error)
        _fallback_logged = True
    return "reference"


def status() -> dict:
    """Diagnostics; never raises."""
    want = os.environ.get("VNXDNA_READS_BACKEND") or "auto"
    try:
        active, err = resolve_backend(), None
    except (NativeReadsError, ValueError) as error:
        active, err = None, str(error)
    return {"requested_backend": want, "active_backend": active, "native_available": available(),
            "library": _lib_path, "load_error": _load_error, "abi_version": ABI_VERSION, "error": err}


def build(output: str | os.PathLike | None = None, extra_flags: list[str] | None = None, compiler: str | None = None,
          strict: bool | None = None) -> Path:
    """Compile ``native/reads.c`` into a shared library (default: next to the source). Returns its path."""
    cc = compiler or os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise NativeReadsError("no C compiler found (set CC)")
    out = Path(output) if output else _INPLACE
    cmd = [cc, *_nb.explicit_cflags(strict), *(extra_flags or []), str(_SOURCE), "-o", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise NativeReadsError(f"native build failed: {' '.join(cmd)}\n{res.stderr}")
    return out


# ============================================================================ iteration
def iter_reads(path: str | os.PathLike, batch: int = 8192, *, max_reads: int | None = None,
               backend: str | None = None) -> Iterator:   # of reference 'Reads' batches
    """Drop-in replacement for :func:`vnxdna.v4.reads.iter_reads` with backend selection."""
    if resolve_backend(backend) == "reference":
        yield from _ref.iter_reads(path, batch, max_reads=max_reads)
        return
    yield from iter_reads_native(path, batch, max_reads=max_reads)


def _ptr(a: np.ndarray) -> int:
    return a.ctypes.data


def _fill(f, view: memoryview) -> int:
    """readinto until ``view`` is full or the file ends; returns the number of bytes read."""
    got = 0
    while got < len(view):
        k = f.readinto(view[got:])
        if not k:
            break
        got += k
    return got


def _error(rc: int, p: Path, ctx: np.ndarray, info: np.ndarray) -> Exception:
    mx = _ref.MAX_READ_NT
    hdr = bytes(ctx[: min(int(info[3]), ctx.size)])
    if rc == -10:
        return VNXResourceError(f"{p}: a line is longer than {mx} bases", stage="input")
    if rc == -11:
        return VNXFormatError(f"{p}: malformed FASTQ record (expected '@', got {hdr[:20]!r})", stage="input")
    if rc == -12:
        return VNXFormatError(f"{p}: truncated FASTQ record", stage="input")
    if rc == -13:
        return VNXFormatError(f"{p}: malformed FASTQ record near {hdr[:40]!r}", stage="input")
    if rc in (-14, -17):
        return VNXResourceError(f"{p}: read longer than {mx} nt", stage="input")
    if rc == -15:
        return VNXFormatError(f"{p}: sequence data before the first FASTA header", stage="input")
    if rc == -16:
        return VNXResourceError(f"{p}: record longer than {mx} nt", stage="input")
    return NativeReadsError(f"native reads parser failed ({rc}: {_INTERNAL.get(rc, 'unknown')})")


def iter_reads_native(path: str | os.PathLike, batch: int = 8192, *, max_reads: int | None = None,
                      read_sizes: Iterable[int] | None = None) -> Iterator:   # of reference 'Reads' batches
    """The native parser. ``read_sizes`` (testing only) replaces the block-aligned reads by successive
    chunks of the given sizes (the last size repeats), to exercise arbitrary chunk boundaries."""
    p = Path(path)
    fmt = _ref.detect(p)
    if fmt == "vxs" or type(batch) is not int:
        yield from _ref.iter_reads(p, batch, max_reads=max_reads)
        return
    lib = _load()
    if lib is None:
        raise NativeReadsError(f"native reads parser unavailable: {_load_error}")
    max_nt = _ref.MAX_READ_NT
    block = _ref.BLOCK
    target = max(1, batch)        # the reference yields after every record when batch <= 0
    fastq = fmt == "fastq"
    state = np.zeros(int(lib.vnx_reads_state_size()), dtype=np.uint8)
    rc = lib.vnx_reads_init(_ptr(state), state.size, FORMATS[fmt], max_nt, max_nt * 2 + 4096, block)
    if rc != 0:
        raise NativeReadsError(f"native reads parser init failed ({rc})")
    info = np.zeros(4, dtype=np.int64)
    ctx = np.zeros(40, dtype=np.uint8)
    sizes = iter(read_sizes) if read_sizes is not None else None
    last_size = block

    def next_size() -> int:
        nonlocal last_size
        if sizes is not None:
            last_size = max(1, int(next(sizes, last_size)))
        return last_size

    est = min(target, 1 << 16) * 256          # first-batch byte estimate; later batches use the last one

    def alloc():
        cap = est + max_nt
        lcap = min(target, 1 << 16)
        return (np.empty(cap, dtype=np.uint8), np.empty(cap, dtype=np.uint8) if fastq else None,
                np.empty(lcap, dtype=np.int64), np.empty(lcap, dtype=bool))

    codes, quals, lengths, invalid = alloc()
    total = 0
    buf = np.empty(block, dtype=np.uint8)
    pend = 0                                   # unconsumed bytes kept at buf[:pend] after NEED_MORE
    st = _ptr(state)
    with open(p, "rb") as f:
        while True:
            want = next_size()
            if pend:
                want = max(want, pend)  # re-feed after NEED_MORE: grow geometrically, never quadratic
            if buf.size < pend + want:
                grown = np.empty(pend + want, dtype=np.uint8)
                grown[:pend] = buf[:pend]
                buf = grown
            got = _fill(f, memoryview(buf)[pend:pend + want])
            eof = got < want
            avail = pend + got
            base = _ptr(buf)
            off = 0
            while True:
                rc = lib.vnx_reads_feed(st, base + off, avail - off, 1 if eof else 0,
                                        _ptr(codes), _ptr(quals) if fastq else None, codes.size,
                                        _ptr(lengths), _ptr(invalid), lengths.size, _ptr(info), _ptr(ctx))
                off += int(info[0])
                if rc == NEED_INPUT:
                    pend = 0
                    break
                if rc == NEED_MORE:
                    if eof:
                        raise NativeReadsError("native reads parser asked for input after the end of the file")
                    pend = avail - off
                    buf[:pend] = buf[off:avail].copy()
                    break
                if rc == NEED_SPACE:
                    newcap = max(codes.size * 2, int(info[2]) + max_nt)
                    codes.resize(newcap, refcheck=False)
                    if quals is not None:
                        quals.resize(newcap, refcheck=False)
                    continue
                if rc == BATCH_FULL or rc == DONE:
                    n, used = int(info[1]), int(info[2])
                    if rc == BATCH_FULL and n < target:   # lengths/invalid full before the batch is
                        lcap = min(target, lengths.size * 2)
                        lengths.resize(lcap, refcheck=False)
                        invalid.resize(lcap, refcheck=False)
                        continue
                    if n:
                        total += n
                        _ref._cap(total, max_reads)
                        codes.resize(used, refcheck=False)
                        lengths.resize(n, refcheck=False)
                        invalid.resize(n, refcheck=False)
                        if quals is not None:
                            quals.resize(used, refcheck=False)
                        out = _ref.Reads(codes, lengths, quals, invalid)
                        est = used + (used >> 4)
                        codes = quals = lengths = invalid = None
                        if rc != DONE:
                            codes, quals, lengths, invalid = alloc()
                        yield out
                    if rc == DONE:
                        return
                    if lib.vnx_reads_new_batch(st) != 0:
                        raise NativeReadsError("native reads parser: batch reset with an open record")
                    continue
                raise _error(rc, p, ctx, info)


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
