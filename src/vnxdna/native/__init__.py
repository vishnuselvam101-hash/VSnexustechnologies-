"""Layer 1 (V6_ARCHITECTURE §2): the native (C) kernels, their loaders, and which backend every kernel uses.

Kernel bindings: :mod:`vnxdna.native.align` (marker aligner), :mod:`vnxdna.native.reads` (read parser),
:mod:`vnxdna.native.rs` (inner RS decoder), :mod:`vnxdna.native.cluster` (V7 read clustering). Their bit-exact NumPy references live in higher layers and are resolved
by module name (``vnxdna.core._alias.lazy_module``), so this package imports nothing above ``vnxdna.core``.
The C sources and the libraries stay in ``vnxdna/v5/native`` and ``vnxdna/v6/native`` (extension names
``vnxdna.v5._vnx_align``, ``vnxdna.v6._vnx_reads``, ``vnxdna.v6._vnx_rs``), so installed layouts are unchanged. The V7 cluster kernel's source
is ``vnxdna/native/c/cluster.c`` (extension ``vnxdna._vnx_cluster``).

:func:`native_status` returns, for each kernel, the backend that actually runs (``native`` or ``reference``), the SIMD
level (RS decoder; forward-backward level of the cluster kernel), the library path and where it came from, the ABI version and why the native library is not used
when it is not. :func:`backend_summary` is the compact form recorded in decode reports (``report["native_backends"]``)
and in the ``decode_start`` event. ``python -m vnxdna.native`` prints :func:`native_status` as JSON
(``--require-native`` exits 1 unless every kernel runs natively, e.g. as a post-install check).

Kernels::

    align   V5 marker-template aligner     vnxdna.native.align   (old path vnxdna.v5.native_alignment)   reference: TemplateAligner (NumPy)
    reads   V6 FASTQ/FASTA read parser     vnxdna.native.reads   (old path vnxdna.v6.native_reads)       reference: the Python parser
    rs      V6 inner RS (GF(256)) decoder  vnxdna.native.rs      (old path vnxdna.v6.native_rs)          reference: rs_fast (NumPy)
    cluster V7 read clustering + consensus vnxdna.native.cluster                                         reference: vnxdna.recovery.cluster (NumPy)

Backend environment variables are documented in docs/NATIVE_KERNELS.md. Nothing here raises: an invalid backend
variable is reported in ``error`` (the decode itself would raise it).
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

from ..core._alias import lazy_module

KERNELS = ("align", "reads", "rs", "cluster")
_ENV_LIB = {"align": "VNXDNA_NATIVE_LIB", "reads": "VNXDNA_READS_LIB", "rs": "VNXDNA_RS_LIB", "cluster": "VNXDNA_CLUSTER_LIB"}
_REFERENCE = {"align": "vnxdna.v4.sync", "reads": "vnxdna.v4.reads", "rs": "vnxdna.v4.rs_fast",
              "cluster": "vnxdna.recovery.cluster"}   # reported names: the stable (old) paths
_CODECS = "vnxdna.codec.codecs"      # InnerRS; its VNX_RS_REFERENCE switch (read at import) bypasses the RS kernel


def _module(kernel: str):
    if kernel not in KERNELS:
        raise ValueError(f"unknown kernel {kernel!r}; one of {KERNELS}")
    return importlib.import_module(f"{__name__}.{kernel}")


def _origin(kernel: str, library: str | None) -> str | None:
    """'env' (explicit *_LIB path), 'packaged' (extension built by pip install) or 'in-place' (python -m … build)."""
    if library is None:
        return None
    env = os.environ.get(_ENV_LIB[kernel])
    if env and Path(env) == Path(library):
        return "env"
    return "packaged" if Path(library).name.startswith("_vnx_") else "in-place"


def _stale(module, origin: str | None, library: str | None) -> bool:
    """An in-place development library older than its C source (it may not match the source any more)."""
    if origin != "in-place" or library is None:
        return False
    try:
        return Path(library).stat().st_mtime < module._SOURCE.stat().st_mtime
    except OSError:
        return False


def kernel_status(kernel: str) -> dict:
    m = _module(kernel)
    st = m.status()
    active = st.get("active_backend")
    out = {"backend": None if active is None else ("reference" if active == "reference" else "native"),
           "requested": st.get("requested_backend"), "simd_level": None, "library": st.get("library"),
           "library_origin": _origin(kernel, st.get("library")), "abi_version": st.get("abi_version"),
           "reference": _REFERENCE[kernel], "load_error": st.get("load_error"), "error": st.get("error")}
    out["library_older_than_source"] = _stale(m, out["library_origin"], out["library"])
    if kernel == "rs":
        codecs = lazy_module(_CODECS)
        if codecs._REFERENCE_RS:        # VNX_RS_REFERENCE=1 (read at import): InnerRS bypasses native_rs entirely
            out.update(backend="reference", requested="VNX_RS_REFERENCE=1", reference="vnxdna.ecc.rs_batch")
        out["simd_level"] = active if out["backend"] == "native" else None
        out.update(supported_levels=st.get("supported_levels"), cpu_levels=st.get("cpu_levels"),
                   levels_restricted=st.get("levels_restricted"), fallback_reason=st.get("fallback_reason"),
                   auto_policy=st.get("auto_policy"))
    elif kernel == "cluster" and out["backend"] == "native":
        out["simd_level"] = st.get("fb_level")
    elif kernel == "align" and out["backend"] == "native":
        try:
            out["simd_lanes"] = int(m._load().vnx_align_lanes())
        except (AttributeError, OSError):
            out["simd_lanes"] = None
    return out


def native_status() -> dict:
    """Backend of every native kernel (see the module docstring); never raises."""
    from .._version import __version__
    kernels = {}
    for k in KERNELS:
        try:
            kernels[k] = kernel_status(k)
        except Exception as error:  # noqa: BLE001 - diagnostics must never raise
            kernels[k] = {"backend": None, "error": f"{type(error).__name__}: {error}"}
    return {"vnx_version": __version__, "kernels": kernels,
            "all_native": all(v.get("backend") == "native" for v in kernels.values())}


def backend_summary() -> dict:
    """Compact per-kernel record for reports and events: backend, SIMD level, ABI and library origin (no paths)."""
    out = {}
    for k, v in native_status()["kernels"].items():
        rec = {"backend": v.get("backend"), "abi_version": v.get("abi_version"), "library_origin": v.get("library_origin")}
        if k == "rs":
            rec["simd_level"] = v.get("simd_level")
        if v.get("error"):
            rec["error"] = v["error"]
        out[k] = rec
    return out


def main(argv: list[str] | None = None) -> int:
    import json
    import sys
    args = sys.argv[1:] if argv is None else argv
    st = native_status()
    print(json.dumps(st, indent=2))
    return 0 if st["all_native"] or "--require-native" not in args else 1


