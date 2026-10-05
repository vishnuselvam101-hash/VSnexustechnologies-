"""CRC-32 (zlib/IEEE 802.3, reflected polynomial 0xEDB88320) over many rows at once.

``zlib.crc32`` works on one buffer per call. Frame validation needs one CRC per
strand (hundreds of millions for a multi-gigabyte archive), so a per-strand
Python loop dominates the run time. :func:`crc32_rows` runs the standard
table-driven algorithm on all rows together, one byte column per numpy step.
Tests check it against ``zlib.crc32`` bit for bit.
"""
from __future__ import annotations

import numpy as np


def _table() -> np.ndarray:
    table = np.zeros(256, dtype=np.uint32)
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table[n] = c
    return table


TABLE = _table()


def crc32_rows(rows: np.ndarray) -> np.ndarray:
    """CRC-32 of every row of a (N, W) uint8 array. Returns (N,) uint32."""
    rows = np.asarray(rows, dtype=np.uint8)
    if rows.ndim != 2:
        raise ValueError("crc32_rows expects a 2-D array")
    crc = np.full(rows.shape[0], 0xFFFFFFFF, dtype=np.uint32)
    for j in range(rows.shape[1]):
        crc = TABLE[(crc ^ rows[:, j]) & 0xFF] ^ (crc >> 8)
    return crc ^ np.uint32(0xFFFFFFFF)


def crc32_bytes_be(crc: np.ndarray) -> np.ndarray:
    """(N,) uint32 → (N, 4) big-endian bytes."""
    return crc.astype(">u4").view(np.uint8).reshape(-1, 4)
