"""Shared loader of the native kernels added by the hybrid architecture (the four V5–V10 kernels keep their own, tested
loaders with the same contract).

A :class:`Kernel` is one plain C11 shared library loaded with :mod:`ctypes`:

* lookup order: the explicit ``<PREFIX>_LIB`` path (e.g. a sanitizer build), the extension built by ``pip install``
  (``vnxdna/_vnx_<name>*.so``), then the in-place development build (``python -m vnxdna.native.<name> build`` writes
  ``vnxdna/native/c/libvnx_<name>.so``);
* a library whose ABI version differs from the binding's is ignored (recorded in ``load_error``);
* backend selection ``<PREFIX>_BACKEND`` = ``auto`` (native if it loads, else the NumPy reference; logged once),
  ``native`` (raise if unavailable) or ``reference``;
* :meth:`Kernel.status` never raises and reports which implementation actually runs.

The binding module keeps the module-level contract of the older loaders (``_HERE``, ``_SOURCE``, ``_INPLACE``,
``CFLAGS``, ``STRICT_CFLAGS``, ``_candidates()``, ``_reset_for_tests()``): :func:`module_contract` creates it, and the
kernel reads ``_HERE``/``_INPLACE``/``_candidates`` from the module at call time, so tests and tools can patch them
exactly as they patch the other kernels.
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import sysconfig
import threading
from pathlib import Path
from typing import Callable

from vnxdna import _native_build as _nb

BACKENDS = ("auto", "native", "reference")
_PKG = Path(__file__).resolve().parents[1]          # vnxdna/
log = logging.getLogger(__name__)


class NativeKernelError(RuntimeError):
    pass


class Kernel:
    def __init__(self, name: str, prefix: str, abi: int, abi_symbol: str, bind: Callable, reference: str, module: str):
        self.name, self.prefix, self.abi, self.abi_symbol, self._bind, self.reference = name, prefix, abi, abi_symbol, bind, reference
        self.module = module
        self.source = _PKG / "native" / "c" / f"{name}.c"
        self.env, self.env_lib = f"{prefix}_BACKEND", f"{prefix}_LIB"
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self._lib = self._load_error = self._path = None
        self._fallback_logged = False

    @property
    def inplace(self) -> Path:
        return sys.modules[self.module]._INPLACE

    def candidates(self) -> list[Path]:
        here = sys.modules[self.module]._HERE
        out = []
        env = os.environ.get(self.env_lib)
        if env:
            out.append(Path(env))
        ext = here / f"_vnx_{self.name}{sysconfig.get_config_var('EXT_SUFFIX') or '.so'}"
        if ext.is_file():
            out.append(ext)
        out.extend(p for p in sorted(here.glob(f"_vnx_{self.name}*.so")) if p != ext)
        out.append(self.inplace)
        return out

    def load(self):
        with self._lock:
            if self._lib is not None or self._load_error is not None:
                return self._lib
            errors = []
            for path in sys.modules[self.module]._candidates():
                if not path.is_file():
                    if path != self.inplace:
                        errors.append(f"{path}: not found")
                    continue
                try:
                    lib = ctypes.CDLL(str(path))
                    getattr(lib, self.abi_symbol).restype = ctypes.c_int
                    abi = getattr(lib, self.abi_symbol)()
                    if abi != self.abi:
                        errors.append(f"{path}: ABI {abi} != {self.abi}")
                        continue
                    self._bind(lib)
                except (OSError, AttributeError) as error:
                    errors.append(f"{path}: {error}")
                    continue
                self._lib, self._path = lib, str(path)
                return lib
            self._load_error = "; ".join(errors) if errors else (
                f"native {self.name} library not built (pip install, or python -m vnxdna.native.{self.name} build)")
            return None

    def available(self) -> bool:
        return self.load() is not None

    def lib(self):
        lib = self.load()
        if lib is None:
            raise NativeKernelError(f"native {self.name} kernel unavailable: {self._load_error}")
        return lib

    def requested(self) -> str:
        name = (os.environ.get(self.env) or "auto").strip().lower()
        if name not in BACKENDS:
            raise ValueError(f"{self.env} must be one of {BACKENDS}, got {name!r}")
        return name

    def resolve(self) -> str:
        """'native' or 'reference': the implementation that will actually run."""
        want = self.requested()
        if want == "reference":
            return "reference"
        if self.available():
            return "native"
        if want == "native":
            raise NativeKernelError(f"native {self.name} kernel requested but unavailable: {self._load_error}")
        if not self._fallback_logged:
            log.warning("native %s kernel unavailable (%s); using the NumPy reference %s", self.name, self._load_error,
                        self.reference)
            self._fallback_logged = True
        return "reference"

    def status(self) -> dict:
        """Diagnostics in the shape of the other kernels' status(); never raises."""
        try:
            active, err = self.resolve(), None
        except (NativeKernelError, ValueError) as error:
            active, err = None, str(error)
        return {"requested_backend": os.environ.get(self.env) or "auto", "active_backend": active,
                "native_available": self.available(), "library": self._path, "load_error": self._load_error,
                "abi_version": self.abi, "error": err}

    def build(self, output: str | os.PathLike | None = None, extra_flags: list[str] | None = None,
              strict: bool | None = None) -> Path:
        cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if not cc:
            raise NativeKernelError("no C compiler found (set CC)")
        out = Path(output) if output else self.inplace
        cmd = [cc, *_nb.explicit_cflags(strict), *(extra_flags or []), str(self.source), "-o", str(out)]
        out.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise NativeKernelError(f"native build failed: {' '.join(cmd)}\n{res.stderr}")
        return out

    def main(self, argv: list[str] | None = None) -> int:
        """``python -m vnxdna.native.<name> build [--strict]`` builds in place; without arguments prints status()."""
        import json
        import sys
        argv = sys.argv[1:] if argv is None else argv
        if argv and argv[0] == "build":
            print(self.build(strict=True if "--strict" in argv else None))
            return 0
        print(json.dumps(self.status(), indent=2))
        return 0


def module_contract(kernel: Kernel) -> dict:
    """Module globals of a binding built on :class:`Kernel` (the older loaders' names; see the module docstring)."""
    return {"_HERE": _PKG, "_SOURCE": kernel.source, "_INPLACE": _PKG / "native" / "c" / f"libvnx_{kernel.name}.so",
            "CFLAGS": _nb.explicit_cflags(strict=False), "STRICT_CFLAGS": _nb.explicit_cflags(strict=True),
            "ENV": kernel.env, "ENV_LIB": kernel.env_lib, "_candidates": kernel.candidates,
            "_reset_for_tests": kernel.reset}
