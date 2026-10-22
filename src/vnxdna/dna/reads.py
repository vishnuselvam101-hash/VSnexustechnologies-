"""Read/strand file I/O: FASTA (single or multi-line), FASTQ, or one sequence per line.

Sequence identifiers and headers are *never* used for decoding. They are
kept only as optional labels. Every piece of placement information comes from
the DNA itself (see :mod:`vnxdna.strand`).
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from ..core.fsio import private_output
from ..errors import InvalidInputError

MAX_READ_NT = 100_000
MAX_READ_FILE_BYTES = 8 * 1024 ** 3
#: a line is cut at this many characters and the rest of it is skipped in bounded blocks (the bound of
#: ``dnaenc/strandio.py``: a cut line is longer than any read VNX-DNA accepts, so the cut never changes a decodable
#: read)
MAX_LINE_BYTES = MAX_READ_NT + 2
_SKIP_BLOCK = 1 << 20
#: the number of records one read file may hold (the list of records is returned whole, so it must be bounded)
MAX_READ_RECORDS = 1 << 24


def _skip_line(handle) -> None:
    """Skip the rest of an over-long line, in bounded blocks."""
    while True:
        block = handle.readline(_SKIP_BLOCK)
        if not block or block.endswith("\n"):
            return


def _lines(path: Path) -> Iterator[str]:
    """Lines of a read file, each cut to ``MAX_LINE_BYTES`` (memory bounded whatever the line length).

    VNX-DNA 1.0 iterated the file object directly, which reads a whole line into memory before any length check: one
    unwrapped multi-gigabyte line (or junk without newlines) could exhaust RAM.
    """
    try:
        with path.open("r", encoding="ascii", newline=None) as handle:
            while True:
                line = handle.readline(MAX_LINE_BYTES)
                if not line:
                    return
                if len(line) == MAX_LINE_BYTES and not line.endswith("\n"):
                    _skip_line(handle)        # the rest of an over-long line
                yield line.strip()
    except UnicodeDecodeError as error:
        raise InvalidInputError(f"{path}: read file must be ASCII text") from error
    except OSError as error:
        raise InvalidInputError(f"cannot read {path}: {error.strerror or error}") from error


def _check_records(count: int, path: Path) -> None:
    if count > MAX_READ_RECORDS:
        raise InvalidInputError(f"{path}: read file holds more than {MAX_READ_RECORDS} records")


def read_sequences(path: str | os.PathLike) -> list[str]:
    """Return the raw sequences in ``path`` (upper-cased, whitespace removed).

    Over-long records are kept but truncated to ``MAX_READ_NT + 1`` so that the
    decoder rejects them by length instead of holding unbounded data in memory.
    The number of records is bounded too, and a line longer than ``MAX_LINE_BYTES`` is cut.
    """
    path = Path(path)
    if not path.is_file():
        raise InvalidInputError(f"read file not found: {path}")
    if path.stat().st_size > MAX_READ_FILE_BYTES:
        raise InvalidInputError(f"read file exceeds {MAX_READ_FILE_BYTES} bytes")
    sequences: list[str] = []
    lines = _lines(path)
    first = None
    buffered: list[str] = []
    for line in lines:
        if line:
            first = line
            buffered.append(line)
            break
    if first is None:
        return sequences
    if first.startswith("@"):
        return _fastq(_chain(buffered, lines), path)
    current: list[str] | None = None
    length = 0
    fasta = first.startswith(">")
    for line in _chain(buffered, lines):
        if not line or line.startswith(";"):
            continue
        if fasta:
            if line.startswith(">"):
                if current is not None:
                    sequences.append("".join(current).upper())
                    _check_records(len(sequences), path)
                current, length = [], 0
                continue
            if length <= MAX_READ_NT:
                current.append(line)  # type: ignore[union-attr]
                length += len(line)
        else:
            sequences.append(line[: MAX_READ_NT + 1].upper())
            _check_records(len(sequences), path)
    if fasta and current is not None:
        sequences.append("".join(current)[: MAX_READ_NT + 1].upper())
        _check_records(len(sequences), path)
    return sequences


def _chain(first: Iterable[str], rest: Iterable[str]) -> Iterator[str]:
    yield from first
    yield from rest


def _fastq(lines: Iterable[str], path: Path) -> list[str]:
    sequences = []
    record: list[str] = []
    for line in lines:
        if not line:
            continue
        record.append(line)
        if len(record) < 4:
            continue
        if not record[0].startswith("@") or not record[2].startswith("+"):
            raise InvalidInputError(f"{path}: malformed FASTQ record at line {len(sequences) * 4 + 1}")
        sequences.append(record[1][: MAX_READ_NT + 1].upper())
        _check_records(len(sequences), path)
        record = []
    if record:
        raise InvalidInputError(f"{path}: FASTQ record count is not a multiple of 4 lines")
    return sequences


def write_fasta(path: Path, sequences: Iterable[str], labels: Iterable[str]) -> None:
    """Write ``sequences`` as FASTA to ``path`` through a private temporary file.

    The temporary file has a unique name and is created with ``O_EXCL`` and mode 0600: a fixed name such as
    ``<name>.tmp`` can be planted as a symlink by anyone who may write in the output directory, and the write would
    go through it. The result is published with a rename, which replaces the name that is there (a symlink included)
    instead of writing through it.
    """
    with private_output(path) as handle:
        for label, sequence in zip(labels, sequences):
            handle.write(f">{label}\n{sequence}\n".encode("ascii"))
