"""Unplaced-read store (V7_ARCHITECTURE §5.1; opt-in).

Pass 1 keeps one fixed-width record per read that yields no verified frame — including reads beyond the alignment band
and reads whose header is unreadable, which the 6.0 path drops — as read (not re-oriented), with its length and its
qualities when the parser delivered them. Width W = ceil(1.25 · strand_nt); reads shorter than floor(0.5 · strand_nt)
or longer than W are counted and not stored. One file ``unpl.bin`` in the spill directory, read back by memory map.

Budget (FC-5, protocol §11): at most ``max_unplaced_reads`` records. When a read would exceed it the store stops
accepting reads (the records kept are the first ones in input order, so the cut is worker-count independent), the
budget is recorded, and the clustering stage does not run; the decode continues on the 6.0 path.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import numpy as np


def store_width(strand_nt: int) -> int:
    return -(-5 * strand_nt // 4)


def min_store_len(strand_nt: int) -> int:
    return strand_nt // 2


def store_dtype(width: int) -> np.dtype:
    return np.dtype([("raw", "u1", (width,)), ("q", "u1", (width,)), ("len", ">u2"), ("hasq", "u1")])


def select_unplaced(batch_codes: np.ndarray, lengths: np.ndarray, quals: np.ndarray | None, acc: np.ndarray,
                    strand_nt: int) -> dict:
    """Pass-1 worker side: the records of one batch's unverified reads, in batch order (raw, not re-oriented)."""
    width = store_width(strand_nt)
    lo = min_store_len(strand_nt)
    fail = np.flatnonzero(~acc)
    ln = lengths[fail]
    short = ln < lo
    long_ = ln > width
    keep = fail[~short & ~long_]
    offs = np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64)
    raw = np.zeros((keep.size, width), dtype=np.uint8)
    q = np.zeros((keep.size, width), dtype=np.uint8)
    if keep.size:
        kl = lengths[keep].astype(np.int64)
        col = np.arange(width)[None, :]
        inside = col < kl[:, None]
        src = offs[keep][:, None] + col
        raw[inside] = batch_codes[src[inside]]
        if quals is not None:
            q[inside] = quals[src[inside]]
    return {"raw": raw, "q": q, "len": lengths[keep].astype(np.int64),
            "hasq": np.full(keep.size, int(quals is not None), dtype=np.uint8),
            "too_short": int(short.sum()), "too_long": int(long_.sum())}


class UnplacedStore:
    """Parent side: appends the workers' records in batch order and enforces the budget."""

    def __init__(self, workdir: Path, strand_nt: int, max_reads: int):
        self.path = Path(workdir) / "unpl.bin"
        self.width = store_width(strand_nt)
        self.dtype = store_dtype(self.width)
        self.max_reads = int(max_reads)
        self.f = open(self.path, "wb")
        self.counts: Counter = Counter()
        self.stored = 0
        self.exceeded = False

    def write(self, rec: dict) -> None:
        self.counts["unplaced_too_short"] += rec["too_short"]
        self.counts["unplaced_too_long"] += rec["too_long"]
        n = len(rec["len"])
        self.counts["unplaced_offered"] += n
        if self.exceeded or not n:
            if self.exceeded:
                self.counts["unplaced_over_budget"] += n
            return
        room = self.max_reads - self.stored
        take = min(n, room)
        if take < n:
            self.exceeded = True
            self.counts["unplaced_over_budget"] += n - take
        if take:
            out = np.zeros(take, dtype=self.dtype)
            out["raw"], out["q"], out["len"], out["hasq"] = (rec["raw"][:take], rec["q"][:take], rec["len"][:take],
                                                            rec["hasq"][:take])
            self.f.write(out.tobytes())
            self.stored += take

    def close(self) -> None:
        if not self.f.closed:
            self.f.close()
        self.counts["unplaced_stored"] = self.stored

    def load(self) -> np.ndarray:
        """Read-only memory map of the stored records (pages are read on demand)."""
        self.close()
        if not self.path.exists() or self.path.stat().st_size < self.dtype.itemsize:
            return np.zeros(0, dtype=self.dtype)
        return np.memmap(self.path, dtype=self.dtype, mode="r")
