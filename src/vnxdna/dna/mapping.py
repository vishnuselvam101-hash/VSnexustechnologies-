"""Logical binary↔DNA mappings (no biological constraints are enforced here).

Nucleotides are handled as uint8 codes A=0, C=1, G=2, T=3. Code 4 marks an
unreadable symbol (e.g. ``N``). Every mapping is deterministic and
format-defined, so its tables never depend on user settings. Constraint
satisfaction is a separate layer (``vnxdna.dna.constraints`` plus strand
screening).

``decode`` returns bytes and an *erasure mask*. A byte is flagged as an
erasure when its nucleotides cannot be a valid encoding (unreadable symbol,
unknown codeword, repeated base in the rotation code). The inner RS decoder
can correct twice as many flagged erasures as unknown errors.

| name              | nt/byte | bits/nt | homopolymer guarantee          |
|-------------------|---------|---------|--------------------------------|
| ``2bit``          | 4       | 2.00    | none (screening required)      |
| ``rotation3``     | 6       | 1.33    | no two equal adjacent bases    |
| ``codebook8``     | 8       | 1.00    | ≤3, and every word has 50 % GC |
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache
from itertools import product

import numpy as np

from ..errors import ConfigurationError, InvalidDNAError

# the base tables moved verbatim to vnxdna.dnaenc.mapping (V6 Phase 2, M3); same objects
from ..dnaenc.mapping import _ASCII_TO_CODE, _CODE_TO_ASCII, _COMPLEMENT, BASES, INVALID  # noqa: E402,F401


def to_codes(sequence: str) -> np.ndarray:
    """Convert a nucleotide string to codes. Raises on symbols other than ACGTN."""
    try:
        raw = np.frombuffer(sequence.encode("ascii"), dtype=np.uint8)
    except UnicodeEncodeError as error:
        raise InvalidDNAError("sequence contains non-ASCII symbols") from error
    codes = _ASCII_TO_CODE[raw]
    if (codes == 255).any():
        bad = sorted({chr(c) for c in raw[codes == 255]})
        raise InvalidDNAError(f"sequence contains invalid symbols: {''.join(bad)!r}")
    return codes


def to_string(codes: np.ndarray) -> str:
    return _CODE_TO_ASCII[codes].tobytes().decode("ascii")


def reverse_complement_codes(codes: np.ndarray) -> np.ndarray:
    return _COMPLEMENT[codes[..., ::-1]]


def reverse_complement(sequence: str) -> str:
    return to_string(reverse_complement_codes(to_codes(sequence)))


class Mapping(ABC):
    """A format-defined bijection between bytes and nucleotide codes."""

    name: str
    nt_per_byte: int

    @abstractmethod
    def encode(self, frames: np.ndarray) -> np.ndarray:
        """(N, B) uint8 bytes → (N, B·nt_per_byte) nucleotide codes."""

    @abstractmethod
    def decode(self, codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(N, L) codes → (bytes (N, B), erasure mask (N, B))."""

    def _check_shape(self, codes: np.ndarray) -> int:
        if codes.ndim != 2 or codes.shape[1] % self.nt_per_byte:
            raise InvalidDNAError(f"{self.name} sequences must be a multiple of {self.nt_per_byte} nt")
        return codes.shape[1] // self.nt_per_byte


class TwoBit(Mapping):
    """A=00 C=01 G=10 T=11, most significant bits first (same as V0.1)."""

    name = "2bit"
    nt_per_byte = 4
    _TABLE = np.array([[(v >> s) & 3 for s in (6, 4, 2, 0)] for v in range(256)], dtype=np.uint8)

    def encode(self, frames: np.ndarray) -> np.ndarray:
        return self._TABLE[frames].reshape(frames.shape[0], -1)

    def decode(self, codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n_bytes = self._check_shape(codes)
        grouped = codes.reshape(codes.shape[0], n_bytes, 4)
        erasures = (grouped == INVALID).any(axis=2)
        clean = np.where(grouped == INVALID, 0, grouped).astype(np.uint16)
        values = (clean[..., 0] << 6) | (clean[..., 1] << 4) | (clean[..., 2] << 2) | clean[..., 3]
        return values.astype(np.uint8), erasures


@lru_cache(maxsize=1)
def codebook8_words() -> tuple[str, ...]:
    """Format-defined 256-word codebook: 'A'+6 bases+'T', exactly 4 G/C, runs ≤ 3.

    Words are taken in ``itertools.product`` order. Because every word starts
    with A and ends with T, runs never extend across word boundaries.
    """
    words = []
    for middle in product(BASES, repeat=6):
        word = "A" + "".join(middle) + "T"
        if sum(b in "GC" for b in word) != 4:
            continue
        run = best = 1
        for a, b in zip(word, word[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        if best <= 3:
            words.append(word)
        if len(words) == 256:
            return tuple(words)
    raise ConfigurationError("codebook8 construction failed")  # pragma: no cover - fixed definition


class Codebook8(Mapping):
    name = "codebook8"
    nt_per_byte = 8

    def __init__(self) -> None:
        words = codebook8_words()
        self._table = np.array([[BASES.index(b) for b in w] for w in words], dtype=np.uint8)
        # index words by their 16-bit packed value
        packed = np.zeros(256, dtype=np.int64)
        for i, row in enumerate(self._table):
            value = 0
            for c in row:
                value = value * 4 + int(c)
            packed[i] = value
        self._lookup = np.full(4 ** 8, -1, dtype=np.int16)
        self._lookup[packed] = np.arange(256, dtype=np.int16)

    def encode(self, frames: np.ndarray) -> np.ndarray:
        return self._table[frames].reshape(frames.shape[0], -1)

    def decode(self, codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n_bytes = self._check_shape(codes)
        grouped = codes.reshape(codes.shape[0], n_bytes, 8)
        bad_symbol = (grouped == INVALID).any(axis=2)
        clean = np.where(grouped == INVALID, 0, grouped).astype(np.int64)
        packed = np.zeros(clean.shape[:2], dtype=np.int64)
        for i in range(8):
            packed = packed * 4 + clean[..., i]
        values = self._lookup[packed]
        erasures = bad_symbol | (values < 0)
        return np.where(erasures, 0, values).astype(np.uint8), erasures


def _rotation_tables() -> tuple[np.ndarray, np.ndarray]:
    nxt = np.array([[b for b in range(4) if b != p] for p in range(4)], dtype=np.uint8)  # [prev][digit] -> base
    digit = np.full((5, 5), 255, dtype=np.uint8)  # [prev][base] -> digit, 255 = impossible
    for p in range(4):
        for d, b in enumerate(nxt[p]):
            digit[p, b] = d
    return nxt, digit


_ROTATION_NEXT, _ROTATION_DIGIT = _rotation_tables()


class Rotation3(Mapping):
    """Rotating ternary code: each byte → 6 base-3 digits (3^6 = 729 ≥ 256).

    Digit d is written as the d-th base (in ACGT order) that differs from
    the previous base, so equal adjacent bases never occur. The previous base
    at strand start is defined as ``A``. A substitution affects at most the
    digit it replaces and the following digit, so at most two bytes.
    """

    name = "rotation3"
    nt_per_byte = 6
    _NEXT = _ROTATION_NEXT
    _DIGIT = _ROTATION_DIGIT
    _POW = np.array([3 ** (5 - i) for i in range(6)], dtype=np.int32)

    def encode(self, frames: np.ndarray) -> np.ndarray:
        n, width = frames.shape
        digits = (frames.astype(np.int32)[..., None] // self._POW) % 3  # (N, B, 6)
        digits = digits.reshape(n, width * 6).astype(np.uint8)
        out = np.empty_like(digits)
        prev = np.zeros(n, dtype=np.uint8)
        for i in range(digits.shape[1]):
            prev = self._NEXT[prev, digits[:, i]]
            out[:, i] = prev
        return out

    def decode(self, codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n_bytes = self._check_shape(codes)
        prev = np.concatenate([np.zeros((codes.shape[0], 1), dtype=np.uint8), codes[:, :-1]], axis=1)
        prev = np.where(prev == INVALID, 4, prev)
        digits = self._DIGIT[prev, codes].reshape(codes.shape[0], n_bytes, 6)
        bad = (digits == 255).any(axis=2)
        values = (np.where(digits == 255, 0, digits).astype(np.int32) * self._POW).sum(axis=2)
        erasures = bad | (values > 255)
        return np.where(erasures, 0, values).astype(np.uint8), erasures


MAPPINGS: dict[str, type[Mapping]] = {"2bit": TwoBit, "rotation3": Rotation3, "codebook8": Codebook8}


@lru_cache(maxsize=None)
def get_mapping(name: str) -> Mapping:
    try:
        return MAPPINGS[name]()
    except KeyError:
        raise ConfigurationError(f"unknown DNA mapping {name!r}; supported: {sorted(MAPPINGS)}") from None
