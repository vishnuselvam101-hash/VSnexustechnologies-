"""F8: the native cluster kernel must check the byte size of every buffer before it allocates.

``native/c/cluster.c`` sized its buffers with products of counts (the number of reads and slots, the number of
trials, the band width) and allocated them without checking that the byte size fits ``size_t``: a count that does
not fit wraps, and the kernel writes the full size into a buffer that is smaller than it believes. The verification
path already refused such a chunk (``cluster.c``, the ``SIZE_MAX / 8`` bound); every other allocation site refuses
it the same way now, and a count that does not fit is reported as an out-of-memory error instead of being allocated.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import pytest

from vnxdna.native import cluster as nc

SOURCE = Path(nc.__file__).resolve().parent / "c" / "cluster.c"
ALLOCATION = re.compile(r"\b(?:malloc|calloc|realloc)\s*\(")
CHECK = "cl_fits("


@pytest.fixture(scope="module", autouse=True)
def _library(tmp_path_factory):
    """The native library: installed / in-place / VNXDNA_CLUSTER_LIB, else built here (skip without a compiler)."""
    saved = os.environ.get(nc.ENV_LIB)
    nc._reset_for_tests()
    if not nc.available():
        try:
            lib = nc.build(tmp_path_factory.mktemp("cluster") / "libvnx_cluster.so")
        except nc.NativeClusterError as error:
            pytest.skip(f"native cluster kernel unavailable and cannot be built here: {error}")
        os.environ[nc.ENV_LIB] = str(lib)
        nc._reset_for_tests()
    assert nc.available()
    yield
    if saved is None:
        os.environ.pop(nc.ENV_LIB, None)
    else:
        os.environ[nc.ENV_LIB] = saved
    nc._reset_for_tests()


def _blocks(lines: list[str]) -> list[list[str]]:
    """The source split into its top-level blocks (the function bodies)."""
    blocks: list[list[str]] = []
    depth = 0
    start = None
    for number, line in enumerate(lines):
        if depth == 0 and "{" in line:
            start = number
        depth += line.count("{") - line.count("}")
        if depth == 0 and start is not None:
            blocks.append(lines[start:number + 1])
            start = None
    return blocks


def test_every_allocation_checks_the_byte_size_of_its_buffer():
    """Every function that allocates must check the byte size before it allocates."""
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    checked = 0
    for block in _blocks(lines):
        for number, line in enumerate(block):
            if not ALLOCATION.search(line):
                continue
            if not any(CHECK in earlier for earlier in block[:number]):
                raise AssertionError(
                    "an allocation without a size check: " + "\n".join(block[max(0, number - 3):number + 1])
                )
            checked += 1
    assert checked >= 30, f"only {checked} allocations found in the kernel: the scan is broken"


def test_a_trial_count_whose_byte_size_does_not_fit_is_refused():
    """A trial count whose byte size does not fit ``size_t`` is refused, not allocated."""
    lib = nc._lib_or_raise()
    T = 3
    base = np.zeros((2, T), np.int16)
    mc = np.ones(T, np.int32)
    buf = np.zeros(6, np.uint8)
    off = np.array([0, 3], np.int64)
    lens = np.array([3, 3], np.int64)
    band = np.array([1, 2], np.int64)
    read_start = np.array([0, 0, 0], np.int64)
    base_dip = np.zeros(2, np.uint8)
    trial_tpl = np.ones((1, T), np.int16)
    trial_row = np.zeros(1, np.int64)
    trial_dip = np.zeros(2, np.uint8)
    cost = np.zeros(1, np.int64)
    basec = np.zeros(1, np.int64)

    def call(trial_start: np.ndarray) -> int:
        return lib.vnx_cl_trial_costs(0, 1, 2, T, base.ctypes.data, mc.ctypes.data, buf.ctypes.data, buf.size,
                                      off.ctypes.data, lens.ctypes.data, band.ctypes.data, read_start.ctypes.data,
                                      base_dip.ctypes.data, 1, 10, trial_tpl.ctypes.data, trial_start.ctypes.data,
                                      trial_row.ctypes.data, trial_row.size, trial_dip.ctypes.data, cost.ctypes.data,
                                      basec.ctypes.data)

    # a trial count that fits: the kernel works as before
    assert call(np.array([0, 0, 1], np.int64)) == 0
    # 2^61 trials are 2^64 bytes: the size does not fit size_t, so the count is refused
    assert call(np.array([0, 0, 1 << 61], np.int64)) == -2
