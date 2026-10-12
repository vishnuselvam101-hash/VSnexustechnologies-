"""Adaptive execution dispatcher: capability probe, deterministic path selection, opt-in differential self-check.

Additive layer over :mod:`vnxdna.native`. It never changes what is computed: the native kernels are bit-exact
with their reference implementations, and this module only decides, records and (opt-in) verifies which one runs.

* :func:`probe` - what each native kernel can do on this host (loadable, ABI, SIMD level, self-check result).
* :func:`select_path` - deterministic per-kernel choice ``native`` | ``reference`` with a machine-readable reason.
  Given the same environment and library state the answer is the same; any doubt resolves to ``reference``.
* Differential self-check (opt-in: ``VNXDNA_DIFFERENTIAL=1`` or ``differential=True``): runs a fixed known-answer
  workload through the native kernel and the reference and compares the bytes. A mismatch or exception in
  ``auto`` mode quarantines the kernel (reference from then on); with ``VNXDNA_<K>_BACKEND=native`` it raises
  :class:`DispatchError` - a wrong or missing backend is never reported as success.
* :func:`path_report` - the selection placed under ``path_selection`` in every encode and decode report. The
  pipelines call it *before* any kernel runs, so the self-check protects the run and the report states the path that
  actually ran. It raises :class:`DispatchError` when an explicitly requested native kernel is unusable or fails.

Quarantine acts on kernels built on ``native._loader.Kernel`` (strand, integrity, outerlist). It is stored in the
environment (``VNXDNA_<K>_QUARANTINED``) so worker processes inherit it. The align, reads, rs and cluster loaders
keep their own static selection; their path is still probed and recorded.

The align kernel (V11 WP1) has a self-test per SIMD level: in ``auto`` a failing level is quarantined
(``VNXDNA_ALIGN_QUARANTINED_LEVELS``, inherited by workers) and selection steps down to the next level, finally the
reference; an explicit request (``native``, ``avx512``, ``avx2``, ``scalar``) that fails raises :class:`DispatchError`.

Known gap: only ``integrity`` and ``align`` have a differential self-test (:data:`SELFTESTS`). For every other kernel
``VNXDNA_DIFFERENTIAL=1`` records ``"differential": "not-available"``: the kernel is NOT verified at run time and
correctness rests on the bit-exactness test suites (tests/native, tests/backend_equivalence) only.
"""
from __future__ import annotations

import os
from collections.abc import Callable

import numpy as np

SCHEMA = "vnx.path/1"
DIFFERENTIAL_ENV = "VNXDNA_DIFFERENTIAL"
_LOADER_KERNELS = ("strand", "orient", "integrity", "outerlist")


class DispatchError(RuntimeError):
    """A requested native path failed validation (missing, failing or returning wrong output)."""


def _selftest_integrity() -> bool:
    """Fixed pseudo-random rows: native CRC-32 must equal the NumPy reference byte for byte."""
    from .core import crc
    from .native import integrity
    rows = (np.arange(37 * 29, dtype=np.uint32) * 2654435761 >> 7).astype(np.uint8).reshape(37, 29)
    got = np.asarray(integrity.crc32_rows_native(rows, 1))
    want = crc.crc32_rows(rows)
    return got.dtype == want.dtype and got.shape == want.shape and got.tobytes() == want.tobytes()


def _selftest_align() -> bool:
    """Fixed seeded reads through the active SIMD level of the aligner vs TemplateAligner._align, all 7 arrays byte for
    byte (vnxdna.native.align.selftest)."""
    from .native import align
    level = align.active_level()
    return True if level == "reference" else align.selftest(level)


# kernel -> callable returning True when native == reference on a fixed workload. Only integrity and align are covered;
# the other kernels report "not-available" under the differential check (see the module docstring, "Known gap").
SELFTESTS: dict[str, Callable[[], bool]] = {"integrity": _selftest_integrity, "align": _selftest_align}


def _check_align_levels(requested: str, entry: dict) -> None:
    """Differential check of the aligner, level by level (see the module docstring). Updates ``entry`` in place."""
    from .native import align
    if not align.selftest_available():      # never a fail: no level is quarantined for a missing reference
        entry.update(differential="not-available", detail=_NO_ALIGN_WORKLOAD)
        return
    quarantined: list[str] = []
    while True:
        level = align.active_level()
        if level == "reference":
            entry.update(path="reference", simd_level=None, reason="differential-fail-fallback", differential="fail",
                         detail=f"every native SIMD level failed the self-check: {','.join(quarantined)}")
            return
        try:
            passed, detail = align.selftest(level), "native output differs from the reference"
        except Exception as error:  # noqa: BLE001 - any failure of the native path is a failed check
            passed, detail = False, f"{type(error).__name__}: {error}"
        if passed:
            entry.update(path="native", simd_level=level, differential="pass")
            if quarantined:
                entry.update(reason="differential-level-fallback", detail=f"quarantined: {','.join(quarantined)}")
            return
        if requested != "auto":
            raise DispatchError(f"native align kernel ({level}) failed the differential self-check: {detail}")
        align.quarantine_level(level)
        quarantined.append(level)


def _differential_requested(differential: bool | None) -> bool:
    if differential is not None:
        return differential
    return os.environ.get(DIFFERENTIAL_ENV, "").strip() in ("1", "true", "yes")


def validate_workload(workload: dict | None) -> dict:
    """Reject nonsensical workload descriptors (negative or non-integer sizes) before any path is chosen."""
    wl = dict(workload or {})
    for key, val in wl.items():
        if key.endswith(("bytes", "reads", "strands", "workers")):      # "reads" and "n_reads" alike
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise DispatchError(f"invalid workload field {key}={val!r}: expected a non-negative integer")
    return wl


_NO_ALIGN_WORKLOAD = "reference aligner not loaded (vnxdna.sync.template is not imported)"


def _run_selftest(kernel: str) -> tuple[str, str | None]:
    fn = SELFTESTS.get(kernel)
    if fn is None:
        return "not-available", None
    if kernel == "align":
        from .native import align
        if not align.selftest_available():
            return "not-available", _NO_ALIGN_WORKLOAD
    try:
        return ("pass", None) if fn() else ("fail", "native output differs from the reference")
    except Exception as error:  # noqa: BLE001 - any failure of the native path is a failed check
        return "fail", f"{type(error).__name__}: {error}"


def _kernel_obj(kernel: str):
    from .native import _module
    return _module(kernel).KERNEL if kernel in _LOADER_KERNELS else None


def probe(selftest: bool = False) -> dict:
    """Capabilities of every native kernel; ``selftest=True`` adds the differential check result. Never raises."""
    from .native import native_status
    kernels = {}
    for k, st in native_status()["kernels"].items():
        rec = {"backend": st.get("backend"), "requested": st.get("requested"), "abi_version": st.get("abi_version"),
               "simd_level": st.get("simd_level"), "load_error": st.get("load_error"), "error": st.get("error"),
               "library_older_than_source": st.get("library_older_than_source", False)}
        if selftest:
            rec["selftest"], rec["selftest_detail"] = _run_selftest(k) if st.get("backend") == "native" else ("skipped", None)
        kernels[k] = rec
    return {"schema": SCHEMA, "kernels": kernels}


def select_path(workload: dict | None = None, *, differential: bool | None = None) -> dict:
    """Deterministic path per kernel. Raises :class:`DispatchError` only for an explicit native request that fails."""
    wl = validate_workload(workload)
    check = _differential_requested(differential)
    caps = probe(selftest=False)["kernels"]
    out: dict = {}
    for k, cap in caps.items():
        requested = cap["requested"] or "auto"
        backend = cap["backend"]
        quarantined = getattr(_kernel_obj(k), "quarantine_reason", None)
        if backend == "reference" and quarantined and requested != "reference":
            out[k] = {"path": "reference", "reason": "differential-fail-fallback", "detail": quarantined}
            if check:
                out[k]["differential"] = "fail"
            continue
        if backend == "reference":
            reason = "requested" if requested == "reference" else ("native-unavailable" if cap["load_error"] else "reference")
        elif backend == "native":
            reason = "requested" if requested == "native" else "native-available"
            if cap["library_older_than_source"]:
                reason += ";library-older-than-source"
        else:
            reason = f"error: {cap['error']}"
            if requested == "native":
                raise DispatchError(f"native {k} kernel requested but unusable: {cap['error']}")
            backend = "reference"
        entry = {"path": backend, "reason": reason}
        if k == "align":
            entry["simd_level"] = cap["simd_level"]
            if check and backend == "native":
                _check_align_levels(requested, entry)
            out[k] = entry
            continue
        if check and backend == "native":
            status, detail = _run_selftest(k)
            entry["differential"] = status
            if status == "fail":
                if requested == "native":
                    raise DispatchError(f"native {k} kernel failed the differential self-check: {detail}")
                obj = _kernel_obj(k)
                if obj is not None:
                    obj.quarantine_reason = detail
                    entry.update(path="reference", reason="differential-fail-fallback", detail=detail)
                else:
                    raise DispatchError(f"native {k} kernel failed the differential self-check and cannot be bypassed: {detail}")
        out[k] = entry
    return {"schema": SCHEMA, "differential": check, "workload": wl, "kernels": out}


def path_report() -> dict:
    """The ``path_selection`` block of encode/decode reports (honours ``VNXDNA_DIFFERENTIAL``).

    Must be called before any kernel runs: a failing self-check then quarantines the kernel for the whole run
    (workers included) and the returned record is the path that actually ran. Raises :class:`DispatchError` when an
    explicitly requested native kernel is unusable or fails the check - a wrong backend is never reported as success."""
    return select_path()
