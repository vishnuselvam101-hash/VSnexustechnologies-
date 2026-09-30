"""Read/strand file I/O: FASTA (single or multi-line), FASTQ, or one sequence per line.

Sequence identifiers and headers are *never* used for decoding. They are
kept only as optional labels. Every piece of placement information comes from
the DNA itself (see :mod:`vnxdna.strand`).
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from ..errors import InvalidInputError

MAX_READ_NT = 100_000
MAX_READ_FILE_BYTES = 8 * 1024 ** 3


def _lines(path: Path) -> Iterator[str]:
    try:
        with path.open("r", encoding="ascii", newline=None) as handle:
            for line in handle:
                yield line.strip()
    except UnicodeDecodeError as error:
        raise InvalidInputError(f"{path}: read file must be ASCII text") from error
    except OSError as error:
        raise InvalidInputError(f"cannot read {path}: {error.strerror or error}") from error


def read_sequences(path: str | os.PathLike) -> list[str]:
    """Return the raw sequences in ``path`` (upper-cased, whitespace removed).

    Over-long records are kept but truncated to ``MAX_READ_NT + 1`` so that the
    decoder rejects them by length instead of holding unbounded data in memory.
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
        return _fastq(buffered + list(lines), path)
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
                current, length = [], 0
                continue
            if length <= MAX_READ_NT:
                current.append(line)  # type: ignore[union-attr]
                length += len(line)
        else:
            sequences.append(line[: MAX_READ_NT + 1].upper())
    if fasta and current is not None:
        sequences.append("".join(current)[: MAX_READ_NT + 1].upper())
    return sequences


def _chain(first: Iterable[str], rest: Iterable[str]) -> Iterator[str]:
    yield from first
    yield from rest


def _fastq(lines: list[str], path: Path) -> list[str]:
    records = [line for line in lines if line != ""]
    if len(records) % 4:
        raise InvalidInputError(f"{path}: FASTQ record count is not a multiple of 4 lines")
    sequences = []
    for i in range(0, len(records), 4):
        if not records[i].startswith("@") or not records[i + 2].startswith("+"):
            raise InvalidInputError(f"{path}: malformed FASTQ record at line {i + 1}")
        sequences.append(records[i + 1][: MAX_READ_NT + 1].upper())
    return sequences


def write_fasta(path: Path, sequences: Iterable[str], labels: Iterable[str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="ascii", newline="\n") as handle:
        for label, sequence in zip(labels, sequences):
            handle.write(f">{label}\n{sequence}\n")
    os.replace(tmp, path)
