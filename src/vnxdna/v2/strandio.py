"""Streaming strand and read files: FASTA, FASTQ, plain text and packed VXS.

Nothing here loads a whole file. Readers yield :class:`ReadBatch` objects of
at most ``batch_reads`` reads, and writers append batches. Headers and read
names are never used to decode. Every piece of identity is inside the DNA.

Packed strand file ``.vxs`` (version 1)
    Text FASTA spends one byte per nucleotide plus headers: a 10 GB input
    becomes about 45 GB of FASTA. VXS stores each strand as 2-bit packed
    nucleotides (A=0, C=1, G=2, T=3, most significant bits first), one
    fixed-size record per strand, so the same strands take about 10 GB. VXS
    holds equal-length A/C/G/T sequences only (no ``N``, no quality, no
    indels). It is the large-file strand format. FASTA/FASTQ remain the
    interchange formats.

    ::

        offset  size  field
        0       8     magic 89 56 58 53 54 52 44 0A ("\\x89VXSTRD\\n")
        8       2     version = 1
        10      2     flags = 0
        12      4     strand_nt
        16      4     record_bytes = ceil(strand_nt / 4)
        20      12    reserved = 0
        32      R·n   records
        end-56  8     record count n
        end-48  8     reserved = 0
        end-40  8     trailer magic "VXSEND\\0\\0"
        end-32  32    SHA-256 of the record bytes

    Files are written to a temporary name and renamed on completion, so a
    killed writer never leaves a VXS file that looks complete.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..dna.mapping import INVALID, _ASCII_TO_CODE, _CODE_TO_ASCII
from vnxdna.core.taxonomy import ConfigurationError, InvalidDNAError, InvalidInputError, OutputError, UnsupportedFormatError
from .container import fsync_dir

VXS_MAGIC = b"\x89VXSTRD\n"
VXS_VERSION = 1
VXS_HEADER = 32
VXS_TRAILER = 56
VXS_END = b"VXSEND\x00\x00"
MAX_READ_NT = 100_000
_QUAL_OFFSET = 33


@dataclass
class ReadBatch:
    """A batch of reads as one flat code array. ``codes`` uses A=0 C=1 G=2 T=3 N=4."""

    codes: np.ndarray               # (total,) uint8
    lengths: np.ndarray             # (n,) int64
    quals: np.ndarray | None = None  # (total,) uint8 Phred scores, or None
    invalid: np.ndarray | None = None  # (n,) bool: read contained symbols other than ACGTN
    names: list[str] | None = None

    @property
    def count(self) -> int:
        return int(self.lengths.size)

    @property
    def offsets(self) -> np.ndarray:
        return np.concatenate([[0], np.cumsum(self.lengths)]).astype(np.int64)

    @classmethod
    def from_matrix(cls, codes: np.ndarray, quals: np.ndarray | None = None) -> "ReadBatch":
        n, length = codes.shape
        return cls(np.ascontiguousarray(codes).reshape(-1), np.full(n, length, dtype=np.int64),
                   None if quals is None else np.ascontiguousarray(quals).reshape(-1))

    def read(self, i: int) -> np.ndarray:
        o = self.offsets
        return self.codes[o[i]:o[i + 1]]


def detect_format(path: str | os.PathLike) -> str:
    """``vxs``, ``fasta``, ``fastq`` or ``plain`` from the file's first bytes."""
    p = Path(path)
    try:
        with p.open("rb") as handle:
            head = handle.read(4096)
    except OSError as error:
        raise InvalidInputError(f"cannot read {p}: {error.strerror or error}") from None
    if head.startswith(VXS_MAGIC):
        return "vxs"
    text = head.lstrip()
    if text.startswith(b">"):
        return "fasta"
    if text.startswith(b"@"):
        return "fastq"
    if text and all(c in b"ACGTNacgtn\r\n\t " for c in text):
        return "plain"
    raise InvalidInputError(f"{p} is not a DNA strand/read file (FASTA, FASTQ, plain or VXS)")


def format_for_output(path: str | os.PathLike, explicit: str | None = None) -> str:
    if explicit:
        if explicit not in ("fasta", "fastq", "vxs"):
            raise InvalidInputError(f"unknown strand format {explicit!r}; use fasta, fastq or vxs")
        return explicit
    suffix = Path(path).suffix.lower()
    if suffix in _UNWRITABLE_SUFFIXES:
        # VNX-DNA 2.0 silently wrote plain FASTA to e.g. reads.fastq.gz or reads.bam
        raise ConfigurationError(f"cannot write {Path(path).name}: {suffix} output (compressed or alignment formats) is not "
                                 "supported; use .fasta, .fastq or .vxs, or pass --format")
    return {".vxs": "vxs", ".fastq": "fastq", ".fq": "fastq"}.get(suffix, "fasta")


_UNWRITABLE_SUFFIXES = {".gz", ".bgz", ".bz2", ".xz", ".zst", ".zstd", ".lz4", ".zip", ".bam", ".sam", ".cram", ".sra"}


# ======================================================================= codes <-> text
def text_to_batch(sequences: list[bytes], quals: list[bytes] | None = None, names: list[str] | None = None) -> ReadBatch:
    lengths = np.fromiter((len(s) for s in sequences), dtype=np.int64, count=len(sequences))
    raw = np.frombuffer(b"".join(sequences), dtype=np.uint8)
    codes = _ASCII_TO_CODE[raw]
    bad = codes == 255
    invalid = None
    if bad.any():
        read_of = np.repeat(np.arange(len(sequences)), lengths)
        invalid = np.zeros(len(sequences), dtype=bool)
        invalid[np.unique(read_of[bad])] = True
        codes = np.where(bad, INVALID, codes).astype(np.uint8)
    q = None
    if quals is not None:
        q = np.frombuffer(b"".join(quals), dtype=np.uint8).astype(np.int16) - _QUAL_OFFSET
        if q.size != codes.size:
            raise InvalidInputError("FASTQ quality string length differs from its sequence length")
        q = np.clip(q, 0, 93).astype(np.uint8)
    return ReadBatch(codes, lengths, q, invalid, names)


def codes_to_ascii(codes: np.ndarray) -> bytes:
    return _CODE_TO_ASCII[codes].tobytes()


def pack_codes(codes: np.ndarray) -> np.ndarray:
    """(N, L) codes in 0..3 → (N, ceil(L/4)) packed bytes."""
    n, length = codes.shape
    if (codes > 3).any():
        raise InvalidDNAError("VXS can store only A/C/G/T (no N or other symbols)")
    width = -(-length // 4)
    padded = np.zeros((n, width * 4), dtype=np.uint8)
    padded[:, :length] = codes
    g = padded.reshape(n, width, 4)
    return ((g[..., 0] << 6) | (g[..., 1] << 4) | (g[..., 2] << 2) | g[..., 3]).astype(np.uint8)


_UNPACK = np.array([[(v >> s) & 3 for s in (6, 4, 2, 0)] for v in range(256)], dtype=np.uint8)


def unpack_codes(packed: np.ndarray, length: int) -> np.ndarray:
    return _UNPACK[packed].reshape(packed.shape[0], -1)[:, :length]


# ======================================================================= writers
class StrandWriter:
    """Atomic, streaming writer for FASTA, FASTQ or VXS. Use as a context manager."""

    def __init__(self, target: str | os.PathLike, fmt: str, *, strand_nt: int | None = None, overwrite: bool = False):
        self.target = Path(target)
        self.fmt = fmt
        if self.target.exists() and not overwrite:
            raise OutputError(f"output already exists: {self.target} (use --force to overwrite)")
        if self.target.exists() and self.target.is_dir():
            raise OutputError(f"output is a directory: {self.target}")
        if fmt == "vxs" and not strand_nt:
            raise OutputError("VXS output needs a fixed strand length")
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="." + self.target.name + ".", suffix=".partial", dir=self.target.parent)
        except OSError as error:
            raise OutputError(f"cannot write {self.target}: {error.strerror or error}") from None
        self.tmp = Path(tmp)
        self.overwrite = overwrite
        self.handle = os.fdopen(fd, "wb", buffering=1 << 20)
        self.count = 0
        self.bases = 0
        self.bytes_written = 0
        self.file_hash = hashlib.sha256()
        self.strand_nt = strand_nt
        if fmt == "vxs":
            self.record_bytes = -(-strand_nt // 4)
            self.record_hash = hashlib.sha256()
            self._raw(VXS_MAGIC + VXS_VERSION.to_bytes(2, "big") + (0).to_bytes(2, "big") + strand_nt.to_bytes(4, "big")
                      + self.record_bytes.to_bytes(4, "big") + bytes(12))

    def _raw(self, data: bytes) -> None:
        self.handle.write(data)
        self.file_hash.update(data)
        self.bytes_written += len(data)

    def write_bytes(self, data: bytes, count: int, bases: int) -> None:
        """Append pre-serialised records (used by encoder workers)."""
        if self.fmt == "vxs":
            self.record_hash.update(data)
        self._raw(data)
        self.count += count
        self.bases += bases

    def write_batch(self, batch: ReadBatch, labels: list[str] | None = None) -> None:
        self.write_bytes(*serialize_batch(batch, self.fmt, labels, self.count, self.strand_nt))

    def commit(self) -> dict:
        if self.fmt == "vxs":
            self._raw(self.count.to_bytes(8, "big") + bytes(8) + VXS_END)
            digest = self.record_hash.digest()
            self._raw(digest)
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        from .container import publish
        publish(self.tmp, self.target, overwrite=self.overwrite)
        fsync_dir(self.target.parent)
        return {"output": str(self.target), "format": self.fmt, "records": self.count, "bases": self.bases,
                "bytes": self.bytes_written, "file_sha256": self.file_hash.hexdigest()}

    def abort(self) -> None:
        try:
            self.handle.close()
        finally:
            self.tmp.unlink(missing_ok=True)

    def __enter__(self) -> "StrandWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.abort()


def serialize_batch(batch: ReadBatch, fmt: str, labels: list[str] | None, first_index: int = 0,
                    strand_nt: int | None = None) -> tuple[bytes, int, int]:
    """Serialise a batch to FASTA/FASTQ/VXS bytes. Returns (bytes, records, bases)."""
    n = batch.count
    total = int(batch.lengths.sum())
    if fmt == "vxs":
        if n and ((batch.lengths != strand_nt).any()):
            raise OutputError("VXS holds equal-length strands only; this batch has reads of other lengths (use FASTQ)")
        return pack_codes(batch.codes.reshape(n, strand_nt)).tobytes() if n else b"", n, total
    ascii_all = _CODE_TO_ASCII[batch.codes].tobytes()
    offsets = batch.offsets.tolist()
    names = labels or [f"r{first_index + i}" for i in range(n)]
    parts: list[bytes] = []
    if fmt == "fasta":
        for i in range(n):
            parts.append(b">" + names[i].encode("ascii") + b"\n" + ascii_all[offsets[i]:offsets[i + 1]] + b"\n")
    elif fmt == "fastq":
        if batch.quals is None:
            q_all = bytes([_QUAL_OFFSET + 40]) * total
        else:
            q_all = (np.minimum(batch.quals, 93) + _QUAL_OFFSET).astype(np.uint8).tobytes()
        for i in range(n):
            a, b = offsets[i], offsets[i + 1]
            parts.append(b"@" + names[i].encode("ascii") + b"\n" + ascii_all[a:b] + b"\n+\n" + q_all[a:b] + b"\n")
    else:
        raise OutputError(f"unknown output format {fmt!r}")
    return b"".join(parts), n, total


# ======================================================================= readers
@dataclass
class VxsInfo:
    strand_nt: int
    record_bytes: int
    count: int
    records_sha256: bytes
    size: int


def vxs_info(path: str | os.PathLike) -> VxsInfo:
    p = Path(path)
    size = p.stat().st_size
    with p.open("rb") as handle:
        head = handle.read(VXS_HEADER)
        if len(head) < VXS_HEADER or head[:8] != VXS_MAGIC:
            raise InvalidInputError(f"{p} is not a VXS strand file")
        if int.from_bytes(head[8:10], "big") != VXS_VERSION or head[10:12] != b"\0\0" or head[20:32] != bytes(12):
            raise UnsupportedFormatError(f"{p}: unsupported VXS version or flags")
        strand_nt = int.from_bytes(head[12:16], "big")
        record_bytes = int.from_bytes(head[16:20], "big")
        if not 1 <= strand_nt <= MAX_READ_NT or record_bytes != -(-strand_nt // 4):
            raise InvalidInputError(f"{p}: VXS header is inconsistent")
        if size < VXS_HEADER + VXS_TRAILER:
            raise InvalidInputError(f"{p}: VXS file is truncated")
        handle.seek(size - VXS_TRAILER)
        tail = handle.read(VXS_TRAILER)
    if tail[16:24] != VXS_END:
        raise InvalidInputError(f"{p}: VXS file has no valid trailer (truncated or unfinished)")
    count = int.from_bytes(tail[:8], "big")
    if VXS_HEADER + count * record_bytes + VXS_TRAILER != size:
        raise InvalidInputError(f"{p}: VXS record count does not match the file size")
    return VxsInfo(strand_nt, record_bytes, count, tail[24:], size)


def read_vxs_range(path: str | os.PathLike, info: VxsInfo, first: int, count: int) -> np.ndarray:
    """Codes (count, strand_nt) for records [first, first+count)."""
    with Path(path).open("rb") as handle:
        handle.seek(VXS_HEADER + first * info.record_bytes)
        data = handle.read(count * info.record_bytes)
    if len(data) != count * info.record_bytes:
        raise InvalidInputError(f"{path}: VXS file ended early")
    return unpack_codes(np.frombuffer(data, dtype=np.uint8).reshape(count, info.record_bytes), info.strand_nt)


def verify_vxs(path: str | os.PathLike) -> bool:
    info = vxs_info(path)
    h = hashlib.sha256()
    remaining = info.count * info.record_bytes
    with Path(path).open("rb") as handle:
        handle.seek(VXS_HEADER)
        while remaining:
            block = handle.read(min(4 << 20, remaining))
            if not block:
                return False
            h.update(block)
            remaining -= len(block)
    return h.digest() == info.records_sha256


def iter_batches(path: str | os.PathLike, batch_reads: int = 65536, *, byte_range: tuple[int, int] | None = None) -> Iterator[ReadBatch]:
    """Stream a strand/read file as batches. ``byte_range`` limits text formats to a slice (DNA index)."""
    p = Path(path)
    if not p.is_file():
        raise InvalidInputError(f"read file not found: {p}")
    fmt = detect_format(p)
    if fmt == "vxs":
        info = vxs_info(p)
        for first in range(0, info.count, batch_reads):
            yield ReadBatch.from_matrix(read_vxs_range(p, info, first, min(batch_reads, info.count - first)))
        return
    yield from _iter_text(p, fmt, batch_reads, byte_range)


def _iter_text(p: Path, fmt: str, batch_reads: int, byte_range: tuple[int, int] | None) -> Iterator[ReadBatch]:
    seqs: list[bytes] = []
    quals: list[bytes] | None = [] if fmt == "fastq" else None
    with p.open("rb") as handle:
        if byte_range is not None:
            handle.seek(byte_range[0])
            lines = _bounded_lines(handle, byte_range[1])
        else:
            lines = _bounded_lines(handle)
        if fmt == "fastq":
            line_no = 0
            while True:
                header = next(lines, None)
                line_no += 1
                if header is None:
                    break
                header = header.strip()
                if not header:
                    continue
                seq = next(lines, b"").strip()
                plus = next(lines, b"").strip()
                qual = next(lines, None)
                line_no += 3
                if not header.startswith(b"@") or not plus.startswith(b"+") or qual is None:
                    raise InvalidInputError(f"{p}: malformed FASTQ record near line {line_no - 3}")
                qual = qual.strip()
                if len(qual) != len(seq):
                    raise InvalidInputError(f"{p}: FASTQ quality length differs from sequence length near line {line_no - 3}")
                seqs.append(seq[: MAX_READ_NT + 1].upper())
                quals.append(qual[: MAX_READ_NT + 1])  # type: ignore[union-attr]
                if len(seqs) >= batch_reads:
                    yield text_to_batch(seqs, quals)
                    seqs, quals = [], []
        elif fmt == "fasta":
            current: list[bytes] | None = None
            length = 0
            for line in lines:
                line = line.strip()
                if not line or line.startswith(b";"):
                    continue
                if line.startswith(b">"):
                    if current is not None:
                        seqs.append(b"".join(current)[: MAX_READ_NT + 1].upper())
                        if len(seqs) >= batch_reads:
                            yield text_to_batch(seqs)
                            seqs = []
                    current, length = [], 0
                    continue
                if current is None:
                    raise InvalidInputError(f"{p}: FASTA sequence data before the first '>' header")
                if length <= MAX_READ_NT:
                    current.append(line)
                    length += len(line)
            if current is not None:
                seqs.append(b"".join(current)[: MAX_READ_NT + 1].upper())
        else:
            for line in lines:
                line = line.strip()
                if line:
                    seqs.append(line[: MAX_READ_NT + 1].upper())
                    if len(seqs) >= batch_reads:
                        yield text_to_batch(seqs)
                        seqs = []
    if seqs:
        yield text_to_batch(seqs, quals)


MAX_LINE_BYTES = MAX_READ_NT + 2   # longer lines are cut here; the rest of the line is skipped in bounded blocks
_SKIP_BLOCK = 1 << 20


def _bounded_lines(handle, length: int | None = None) -> Iterator[bytes]:
    """Lines of a text file, each cut to ``MAX_LINE_BYTES`` (memory bounded whatever the line length).

    VNX-DNA 2.0 iterated the file object directly, which reads a whole line
    into memory before any length check: one unwrapped multi-gigabyte FASTA
    line (or junk without newlines) could exhaust RAM. A cut line is longer
    than any read VNX-DNA accepts, so the cut never changes a decodable read.
    ``length`` limits reading to that many bytes (a DNA-index byte range).
    """
    remaining = length
    while remaining is None or remaining > 0:
        want = MAX_LINE_BYTES if remaining is None else min(MAX_LINE_BYTES, remaining)
        line = handle.readline(want)
        if not line:
            return
        if remaining is not None:
            remaining -= len(line)
        if len(line) == want and not line.endswith(b"\n") and (remaining is None or remaining > 0):
            while remaining is None or remaining > 0:  # skip the rest of an over-long line
                step = _SKIP_BLOCK if remaining is None else min(_SKIP_BLOCK, remaining)
                rest = handle.readline(step)
                if remaining is not None:
                    remaining -= len(rest)
                if not rest or rest.endswith(b"\n"):
                    break
        yield line
