"""Per-strand (inner) Reed–Solomon code over GF(256).

Each strand frame is protected by a systematic RS(n, n−nsym) codeword with
``nsym`` parity bytes, so it corrects ``e`` byte errors and ``f`` byte
erasures whenever ``2e + f ≤ nsym``. In the 2-bit mapping, one nucleotide
substitution corrupts exactly one byte.

* **Encoding** is vectorized. RS parity is a linear function of the message
  over GF(256), so parity = message · P for a k×nsym matrix P. P is derived
  column by column by encoding unit vectors with ``reedsolo``, which keeps
  the output bit-identical to ``reedsolo`` (checked in tests).
* **Decoding** uses the established ``reedsolo`` errors-and-erasures decoder
  (Berlekamp–Massey / Forney). It runs only for strands whose CRC fails.

This code corrects substitutions (and known erasures) inside one strand. It
cannot correct insertions or deletions, which shift every later symbol, and it
cannot recover strands that are missing entirely (the outer code handles those).
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import reedsolo

from ..errors import ConfigurationError
from . import gf256

MAX_CODEWORD = 255


@lru_cache(maxsize=32)
def _codec(nsym: int) -> reedsolo.RSCodec:
    return reedsolo.RSCodec(nsym, nsize=MAX_CODEWORD, fcr=0, prim=gf256.PRIMITIVE_POLY, generator=gf256.GENERATOR)


@lru_cache(maxsize=64)
def _parity_matrix(k: int, nsym: int) -> np.ndarray:
    codec = _codec(nsym)
    matrix = np.zeros((k, nsym), dtype=np.uint8)
    for i in range(k):
        unit = bytearray(k)
        unit[i] = 1
        matrix[i] = np.frombuffer(bytes(codec.encode(bytes(unit))[k:]), dtype=np.uint8)
    return matrix


class InnerReedSolomon:
    def __init__(self, nsym: int):
        if not isinstance(nsym, int) or isinstance(nsym, bool) or not 0 <= nsym <= 64 or nsym % 2:
            raise ConfigurationError("inner_parity_bytes must be an even integer in 0..64")
        self.nsym = nsym

    def encode_batch(self, messages: np.ndarray) -> np.ndarray:
        """Return parity (N, nsym) for messages (N, k)."""
        messages = np.ascontiguousarray(messages, dtype=np.uint8)
        n, k = messages.shape
        if k + self.nsym > MAX_CODEWORD:
            raise ConfigurationError(f"frame of {k} bytes plus {self.nsym} parity exceeds {MAX_CODEWORD}")
        parity = np.zeros((n, self.nsym), dtype=np.uint8)
        if self.nsym == 0 or n == 0:
            return parity
        matrix = _parity_matrix(k, self.nsym)
        for i in range(k):
            column = messages[:, i]
            for j in range(self.nsym):
                coefficient = int(matrix[i, j])
                if coefficient:
                    parity[:, j] ^= gf256.MUL[coefficient][column]
        return parity

    def correct_codeword(self, codeword: bytes, erasures: list[int] | None = None) -> tuple[bytes, int] | None:
        """Errors-and-erasures decode of a full codeword.

        Returns ``(corrected codeword, symbols corrected)`` or ``None`` when
        the decoder reports failure. A returned codeword may still be a
        miscorrection if more than ``nsym/2`` errors occurred; callers must
        re-verify with an independent check (the frame CRC-32).
        """
        if self.nsym == 0:
            return None
        erasures = sorted(set(erasures or []))
        if len(erasures) > self.nsym:
            return None
        try:
            _, full, errata = _codec(self.nsym).decode(codeword, erase_pos=erasures or None)
        except (reedsolo.ReedSolomonError, ValueError, IndexError, ZeroDivisionError):
            return None
        return bytes(full), len(errata)

    def correct(self, codeword: bytes, erasures: list[int] | None = None) -> tuple[bytes, int] | None:
        """Like :meth:`correct_codeword` but returns only the message part."""
        result = self.correct_codeword(codeword, erasures)
        if result is None:
            return None
        return result[0][: len(codeword) - self.nsym], result[1]
