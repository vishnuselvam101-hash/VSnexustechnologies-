"""From (reference, reads) pairs to the reference-by-count matrix, with worker processes (results independent of workers)."""
from __future__ import annotations

import multiprocessing as mp

import numpy as np

from vnxdna.simulation.fit.tally import EDIT_BINS, DRIFT_BINS, Layout, tally_reference

CHUNK = 32


def _work(args):
    layout, mode, items, opts = args
    rows, rss = [], np.zeros(EDIT_BINS + DRIFT_BINS, dtype=np.int64)
    for ref, reads, quals in items:
        v, rs = tally_reference(ref, reads, layout, mode, quals, **opts)
        rows.append(v.astype(np.int32))
        rss += rs
    return np.stack(rows), rss


def _chunks(pairs, layout, mode, opts):
    buf = []
    for item in pairs:
        buf.append(item if len(item) == 3 else (item[0], item[1], None))
        if len(buf) >= CHUNK:
            yield layout, mode, buf, opts
            buf = []
    if buf:
        yield layout, mode, buf, opts


def tally_matrix(pairs, layout: Layout, *, mode: str = "NW", workers: int = 1, aligner: str = "edlib",
                 shift: str = "left", length_window: tuple | None = None,
                 max_edit_frac: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(M, read histogram totals) for an iterable of ``(ref, reads)`` or ``(ref, reads, quals)``. Row order = input order.
    ``length_window``: see :func:`tally_reference`."""
    blocks, total = [], np.zeros(EDIT_BINS + DRIFT_BINS, dtype=np.int64)
    opts: dict = {"aligner": aligner, "shift": shift, "length_window": length_window}
    if max_edit_frac is not None:               # V8 A1; absent otherwise, so V7 calls are unchanged
        opts["max_edit_frac"] = max_edit_frac
    tasks = _chunks(pairs, layout, mode, opts)
    if workers <= 1:
        results = map(_work, tasks)
        for m, rs in results:
            blocks.append(m)
            total += rs
    else:
        with mp.get_context("fork").Pool(workers) as pool:
            for m, rs in pool.imap(_work, tasks):
                blocks.append(m)
                total += rs
    M = np.concatenate(blocks) if blocks else np.zeros((0, layout.size), dtype=np.int32)
    return M, total
