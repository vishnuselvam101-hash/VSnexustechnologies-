"""Canonical k-mer MinHash sketches (V7_ARCHITECTURE §5.2 step 1; NumPy reference).

For each read: every k-mer without N (2 bits per base, A=0 C=1 G=2 T=3, most significant first) and its reverse
complement; the canonical k-mer is the smaller of the two, and its orientation bit is 1 when the canonical form is the
reverse complement. For each of ``s`` fixed hash functions h_i(x) = splitmix64(x XOR c_i) the sketch keeps the
minimum over the read's canonical k-mers and the orientation bit of the k-mer that attains it. A read and its reverse
complement have the same canonical k-mers, so the same hashes with every orientation bit flipped.

The constants c_i are fixed here (``SEEDS``): c_i = splitmix64(0x56584E37 + i). The sketch is a pure function of the
read, so it does not depend on read order, batch size or worker count.

:func:`sketch_reads` runs the native kernel (``vnxdna.native.cluster``) when it is available and selected, else
:func:`sketch_reads_reference`; both return identical arrays.
"""
from __future__ import annotations

import numpy as np

from vnxdna.native import cluster as _nc

_M64 = (1 << 64) - 1
NO_KMER = np.uint64(_M64)


def splitmix64_int(x: int) -> int:
    z = (x + 0x9E3779B97F4A7C15) & _M64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _M64
    return z ^ (z >> 31)


SEEDS = tuple(splitmix64_int(0x56584E37 + i) for i in range(256))


def splitmix64(x: np.ndarray) -> np.ndarray:
    """Vectorised splitmix64 finaliser on uint64 (wrapping arithmetic)."""
    with np.errstate(over="ignore"):
        z = x + np.uint64(0x9E3779B97F4A7C15)
        z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        return z ^ (z >> np.uint64(31))


def canonical_kmers(raw: np.ndarray, lengths: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(m, Lw) canonical k-mer values, orientation bits and validity (no N, inside the read) of padded reads."""
    m, width = raw.shape
    lw = max(0, width - k + 1)
    fwd = np.zeros((m, lw), dtype=np.uint64)
    rev = np.zeros((m, lw), dtype=np.uint64)
    bad = np.zeros((m, lw), dtype=bool)
    codes = raw.astype(np.uint64)
    for q in range(k):
        col = codes[:, q:q + lw]
        bad |= raw[:, q:q + lw] > 3
        fwd |= (col & np.uint64(3)) << np.uint64(2 * (k - 1 - q))
        rev |= (np.uint64(3) - (col & np.uint64(3))) << np.uint64(2 * q)
    valid = ~bad & (np.arange(lw)[None, :] + k <= lengths[:, None])
    orient = rev < fwd
    canon = np.where(orient, rev, fwd)
    return canon, orient, valid


def sketch_reads(raw: np.ndarray, lengths: np.ndarray, k: int, s: int, chunk: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """Native or reference :func:`sketch_reads_reference` (identical results)."""
    raw = np.asarray(raw)
    if raw.ndim == 2 and 1 <= k <= 31 and 1 <= s <= 256 and _nc.resolve_backend() == "native":
        return _nc.sketch(raw, np.asarray(lengths, dtype=np.int64), k, s)
    return sketch_reads_reference(raw, lengths, k, s, chunk)


def sketch_reads_reference(raw: np.ndarray, lengths: np.ndarray, k: int, s: int,
                           chunk: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """(n, s) uint32 MinHash values (the top 32 bits; ``0xFFFFFFFF`` with orientation 2 = no valid k-mer) and (n, s)
    uint8 orientation bits of the minimising k-mers."""
    n = raw.shape[0]
    hashes = np.full((n, s), 0xFFFFFFFF, dtype=np.uint32)
    orient = np.full((n, s), 2, dtype=np.uint8)
    lengths = np.asarray(lengths, dtype=np.int64)
    for c0 in range(0, n, chunk):
        sl = slice(c0, min(n, c0 + chunk))
        canon, ori, valid = canonical_kmers(np.asarray(raw[sl]), lengths[sl], k)
        if canon.shape[1] == 0:
            continue
        has = valid.any(axis=1)
        obit = ori.astype(np.uint64)
        for i in range(s):
            h = splitmix64(canon ^ np.uint64(SEEDS[i]))
            key = (h & ~np.uint64(1)) | obit
            key[~valid] = NO_KMER
            best = key.min(axis=1)
            hashes[sl, i] = np.where(has, (best >> np.uint64(32)).astype(np.uint32), np.uint32(0xFFFFFFFF))
            orient[sl, i] = np.where(has, (best & np.uint64(1)).astype(np.uint8), np.uint8(2))
    return hashes, orient
