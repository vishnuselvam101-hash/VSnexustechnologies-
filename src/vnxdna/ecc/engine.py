"""ECC engine (V3): the two coding layers behind a small, explicit interface.

VNX-DNA protects data with two independent codes:

* an **outer erasure code** across the strands of one ECC group (it fills in
  strands that are missing or were rejected), and
* an **inner code** inside every strand (it corrects substitutions and flagged
  erasures before the frame CRC-32 decides whether the strand is accepted).

Each layer is named in the authenticated manifest (``erasure_code.algorithm``
and ``strand.inner_ecc``). The encoder and decoder obtain their codes from this
registry by that name, so a decoder can never silently apply a different code
than the one an archive declares, and an additional code needs exactly one
registration here plus a manifest name and a ``required_features`` entry (so
older readers refuse it cleanly).

Registered implementations (both tested exhaustively or against a reference):

========================  =======================================================  ==========================================
manifest name             implementation                                           guarantee
========================  =======================================================  ==========================================
``cauchy-rs-gf256``       :class:`vnxdna.ecc.cauchy.CauchyErasureCode` (MDS)       any M of the K + M shards of a group may be
                                                                                   lost (erasures only; no error location)
``reed-solomon-gf256``    :class:`ReedSolomonInner` (systematic RS, GF(2^8)/0x11D,  ``2e + f ≤ r`` byte errors e and flagged
                          fcr 0, generator 2; vectorised decoder                   erasures f per strand; beyond that the
                          :mod:`vnxdna.ecc.rs_batch`)                              decoder refuses or miscorrects, and the
                                                                                   frame CRC-32 rejects miscorrections
========================  =======================================================  ==========================================

Fountain (LT/Raptor) outer codes are a research direction (``docs/ROADMAP.md``),
not an implementation: nothing is registered for them, because nothing untested
is shipped.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from ..errors import UnsupportedFormatError
from .cauchy import CauchyErasureCode
from .inner_rs import InnerReedSolomon
from .rs_batch import decode_batch


@runtime_checkable
class OuterCode(Protocol):
    """Systematic erasure code over groups of equal-length shards."""

    data_shards: int
    parity_shards: int

    def encode(self, data: np.ndarray) -> np.ndarray:
        """(S, K, L) data shards → (S, M, L) parity shards."""

    def decode(self, shards: np.ndarray, present: np.ndarray) -> np.ndarray:
        """(S, K + M, L) shards and (S, K + M) presence → (S, K, L) data; raises InsufficientRedundancyError."""


@runtime_checkable
class InnerCode(Protocol):
    """Systematic per-strand code with a batch decoder."""

    nsym: int

    def encode_batch(self, messages: np.ndarray) -> np.ndarray:
        """(N, k) messages → (N, nsym) parity."""

    def decode_batch(self, codewords: np.ndarray, erasures: np.ndarray | None = None
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(N, n) codewords → (corrected, ok, symbols corrected); ``ok`` False rows are returned unchanged."""


class ReedSolomonInner(InnerReedSolomon):
    """The inner RS code with the V3 vectorised bounded-distance decoder."""

    def decode_batch(self, codewords: np.ndarray, erasures: np.ndarray | None = None
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return decode_batch(codewords, self.nsym, erasures)


OUTER_CODES = {"cauchy-rs-gf256": CauchyErasureCode}
INNER_CODES = {"reed-solomon-gf256": ReedSolomonInner}


def outer_code(algorithm: str, data_shards: int, parity_shards: int) -> OuterCode:
    """The outer code an archive declares (``erasure_code.algorithm``)."""
    try:
        cls = OUTER_CODES[algorithm]
    except KeyError:
        raise UnsupportedFormatError(f"unsupported outer erasure code {algorithm!r}; supported: {sorted(OUTER_CODES)}") from None
    return cls(data_shards, parity_shards)


def inner_code(algorithm: str, parity_bytes: int) -> InnerCode:
    """The inner code an archive declares (``strand.inner_ecc``)."""
    try:
        cls = INNER_CODES[algorithm]
    except KeyError:
        raise UnsupportedFormatError(f"unsupported inner code {algorithm!r}; supported: {sorted(INNER_CODES)}") from None
    return cls(parity_bytes)
