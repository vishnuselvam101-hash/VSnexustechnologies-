"""Fast, bounded FASTA / FASTQ / plain-sequence reader (input adapters for read reconstruction).

Reads the file in large blocks and splits lines with ``bytes.split`` instead of
a Python ``readline`` loop (measured ~3× faster than the V3 reader on FASTA;
docs/PERFORMANCE.md). Records are bounded (``MAX_READ_NT``) so a malformed or
hostile file cannot make one record consume unbounded memory.

Supported and tested: FASTA (single- or multi-line), FASTQ (4-line records,
Phred+33), plain one-sequence-per-line. **BAM is not supported** (no parser is
shipped; convert with external tools). Symbols other than ACGTN become N
(code 4) and the read is flagged in ``invalid``.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from vnxdna.core.errors import VNXFormatError, VNXResourceError

MAX_READ_NT = 100_000
BLOCK = 8 << 20
_LUT = np.full(256, 4, dtype=np.uint8)
for _i, _c in enumerate(b"ACGT"):
    _LUT[_c] = _i
    _LUT[_c + 32] = _i
_VALID = np.zeros(256, dtype=bool)
_VALID[list(b"ACGTNacgtn")] = True


@dataclass
class Reads:
    codes: np.ndarray          # flat uint8 A0 C1 G2 T3 N4
    lengths: np.ndarray        # int64
    quals: np.ndarray | None   # flat uint8 Phred, or None
    invalid: np.ndarray        # bool per read

    @property
    def count(self) -> int:
        return int(self.lengths.size)


def detect(path: str | os.PathLike) -> str:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError as error:
        raise VNXFormatError(f"cannot read {path}: {error.strerror or error}", stage="input") from None
    text = head.lstrip()
    if text.startswith(b">"):
        return "fasta"
    if text.startswith(b"@"):
        return "fastq"
    if text.startswith(b"\x89VXSTRD"):
        return "vxs"
    if text and all(c in b"ACGTNacgtn\r\n\t " for c in text[:1024]):
        return "plain"
    if head.startswith(b"BAM\x01") or head[:2] == b"\x1f\x8b":
        raise VNXFormatError(f"{path}: BAM / gzip input is not supported; convert to FASTA or FASTQ", stage="input")
    raise VNXFormatError(f"{path} is not FASTA, FASTQ or plain sequence text", stage="input")


def _lines(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as f:
        rest = b""
        while True:
            block = f.read(BLOCK)
            if not block:
                break
            data = rest + block
            parts = data.split(b"\n")
            rest = parts.pop()
            if len(rest) > MAX_READ_NT * 2 + 4096:
                raise VNXResourceError(f"{path}: a line is longer than {MAX_READ_NT} bases", stage="input")
            yield from parts
        if rest:
            yield rest


def _batch(seqs: list[bytes], quals: list[bytes] | None) -> Reads:
    lengths = np.fromiter((len(s) for s in seqs), dtype=np.int64, count=len(seqs))
    raw = np.frombuffer(b"".join(seqs), dtype=np.uint8)
    codes = _LUT[raw]
    bad = ~_VALID[raw]
    invalid = np.zeros(len(seqs), dtype=bool)
    if bad.any():
        owner = np.repeat(np.arange(len(seqs)), lengths)
        invalid[np.unique(owner[bad])] = True
    q = None
    if quals is not None:
        qa = np.frombuffer(b"".join(quals), dtype=np.uint8)
        if qa.size != raw.size:
            raise VNXFormatError("FASTQ quality string length differs from its sequence length", stage="input")
        q = np.clip(qa.astype(np.int16) - 33, 0, 93).astype(np.uint8)
    return Reads(codes, lengths, q, invalid)


def iter_reads(path: str | os.PathLike, batch: int = 8192, *, max_reads: int | None = None) -> Iterator[Reads]:
    p = Path(path)
    fmt = detect(p)
    if fmt == "vxs":
        from vnxdna.dnaenc.strandio import iter_batches
        for b in iter_batches(p, batch):
            yield Reads(b.codes, b.lengths, b.quals, b.invalid if b.invalid is not None else np.zeros(b.count, dtype=bool))
        return
    seqs: list[bytes] = []
    quals: list[bytes] | None = [] if fmt == "fastq" else None
    total = 0
    lines = _lines(p)
    if fmt == "fastq":
        assert quals is not None
        for header in lines:
            header = header.rstrip(b"\r")
            if not header:
                continue
            if not header.startswith(b"@"):
                raise VNXFormatError(f"{p}: malformed FASTQ record (expected '@', got {header[:20]!r})", stage="input")
            try:
                seq = next(lines).rstrip(b"\r")
                plus = next(lines).rstrip(b"\r")
                qual = next(lines).rstrip(b"\r")
            except StopIteration:
                raise VNXFormatError(f"{p}: truncated FASTQ record", stage="input") from None
            if not plus.startswith(b"+") or len(qual) != len(seq):
                raise VNXFormatError(f"{p}: malformed FASTQ record near {header[:40]!r}", stage="input")
            if len(seq) > MAX_READ_NT:
                raise VNXResourceError(f"{p}: read longer than {MAX_READ_NT} nt", stage="input")
            seqs.append(seq)
            quals.append(qual)
            if len(seqs) >= batch:
                total += len(seqs)
                _cap(total, max_reads)
                yield _batch(seqs, quals)
                seqs, quals = [], []
    elif fmt == "fasta":
        cur: list[bytes] | None = None
        size = 0
        for line in lines:
            line = line.rstrip(b"\r")
            if line.startswith(b">"):
                if cur is not None:
                    seqs.append(b"".join(cur))
                    if len(seqs) >= batch:
                        total += len(seqs)
                        _cap(total, max_reads)
                        yield _batch(seqs, None)
                        seqs = []
                cur, size = [], 0
            elif line:
                if cur is None:
                    raise VNXFormatError(f"{p}: sequence data before the first FASTA header", stage="input")
                cur.append(line.strip())
                size += len(line)
                if size > MAX_READ_NT:
                    raise VNXResourceError(f"{p}: record longer than {MAX_READ_NT} nt", stage="input")
        if cur is not None:
            seqs.append(b"".join(cur))
    else:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if len(line) > MAX_READ_NT:
                raise VNXResourceError(f"{p}: read longer than {MAX_READ_NT} nt", stage="input")
            seqs.append(line)
            if len(seqs) >= batch:
                total += len(seqs)
                _cap(total, max_reads)
                yield _batch(seqs, None)
                seqs = []
    if seqs:
        total += len(seqs)
        _cap(total, max_reads)
        yield _batch(seqs, quals)


def _cap(total: int, max_reads: int | None) -> None:
    if max_reads is not None and total > max_reads:
        raise VNXResourceError(f"more than {max_reads} reads", stage="input")
