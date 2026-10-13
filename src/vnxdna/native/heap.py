"""Return freed heap pages to the operating system between decode stages (V12 session 5).

glibc keeps the pages of freed heap chunks resident until ``malloc_trim``; a pass-1 worker that freed its batch
temporaries would otherwise carry them through pass 2, where it loads a spill bucket on top. Memory only: no result
can depend on this call. A no-op where the C library has no ``malloc_trim`` (not glibc)."""
from __future__ import annotations

import ctypes
from typing import Any

_TRIM: Any = None


def trim_heap() -> bool:
    """``malloc_trim(0)``; True when memory was released, False otherwise or where the call does not exist."""
    global _TRIM
    if _TRIM is None:
        _TRIM = False
        try:
            fn = ctypes.CDLL(None).malloc_trim
            fn.argtypes, fn.restype = (ctypes.c_size_t,), ctypes.c_int
            _TRIM = fn
        except (OSError, AttributeError):
            pass
    return bool(_TRIM(0)) if _TRIM else False
