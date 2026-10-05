"""Strand file records: ``vnx4|tag|kind|group|symbol`` labels and vectorised FASTA/FASTQ text (VNX4 §14).
Split from ``vnxdna.v4.encoder`` and ``vnxdna.v6.encoder`` (V6 Phase 2, M4)."""
from __future__ import annotations

import numpy as np


_ASCII = np.frombuffer(b"ACGTN", dtype=np.uint8)


def _labels(tag: int, kind: int, groups: np.ndarray, symbols: np.ndarray) -> list[bytes]:
    pre = f"vnx4|{tag:04x}|{kind}|".encode()
    return [pre + b"%d|%d" % (g, s) for g, s in zip(groups.tolist(), symbols.tolist())]


def _serialize(codes: np.ndarray, labels: list[bytes], fmt: str) -> bytes:
    """Vectorised FASTA/FASTQ text: one table lookup for all bases, one join for all records."""
    n, length = codes.shape
    seq = np.empty((n, length + 1), dtype=np.uint8)
    seq[:, :length] = _ASCII[codes]
    seq[:, length] = 10
    rows = seq.tobytes()
    w = length + 1
    if fmt == "fastq":
        q = b"+\n" + b"I" * length + b"\n"
        return b"".join(b"@" + lab + b"\n" + rows[i * w:(i + 1) * w] + q for i, lab in enumerate(labels))
    return b"".join(b">" + lab + b"\n" + rows[i * w:(i + 1) * w] for i, lab in enumerate(labels))


def _records(codes: np.ndarray, labels: list[bytes], fmt: str) -> list[bytes]:
    n, length = codes.shape
    seq = np.empty((n, length + 1), dtype=np.uint8)
    seq[:, :length] = _ASCII[codes]
    seq[:, length] = 10
    rows = seq.tobytes()
    w = length + 1
    if fmt == "fastq":
        q = b"+\n" + b"I" * length + b"\n"
        return [b"@" + lab + b"\n" + rows[i * w:(i + 1) * w] + q for i, lab in enumerate(labels)]
    return [b">" + lab + b"\n" + rows[i * w:(i + 1) * w] for i, lab in enumerate(labels)]
