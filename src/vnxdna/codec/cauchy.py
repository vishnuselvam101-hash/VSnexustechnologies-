"""Systematic Cauchy Reed–Solomon erasure code over GF(256) (outer code).

Construction
------------
For K data shards and M parity shards (K ≥ 1, M ≥ 0, K + M ≤ 256) the
generator matrix is ``G = [I_K ; C]`` where ``C`` is the M×K Cauchy matrix

    C[i][j] = 1 / (x_i + y_j),   x_i = K + i,   y_j = j     (arithmetic in GF(256))

The sets {x_i} and {y_j} are disjoint, so x_i + y_j = x_i XOR y_j ≠ 0.

MDS guarantee (proof sketch)
----------------------------
Every square submatrix of a Cauchy matrix is itself a Cauchy matrix and is
therefore nonsingular (its determinant has the closed form
∏(x_i−x_k)(y_j−y_l) / ∏(x_i+y_j) with distinct x's and distinct y's).
Take any K surviving shards: r of them data rows, K−r parity rows. After
permuting, the K×K submatrix of G is block-triangular with an r×r identity
block and a (K−r)×(K−r) block equal to the Cauchy submatrix formed by the
surviving parity rows and the *missing* data columns. Its determinant is ±
that Cauchy minor, which is nonzero. Hence **any K of the K+M shards
reconstruct the data, i.e. any M erasures are recoverable**.

This is erasure recovery only: the decoder must be told which shards are
missing or invalid (VNX-DNA uses per-strand CRC-32 plus the inner code for
that). It does not locate errors and does not correct insertions/deletions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from vnxdna.core.taxonomy import ConfigurationError, InsufficientRedundancyError
from vnxdna.codec import gf256

MAX_TOTAL_SHARDS = 256


@dataclass(frozen=True)
class CauchyErasureCode:
    data_shards: int
    parity_shards: int
    _parity_matrix: tuple[tuple[int, ...], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        k, m = self.data_shards, self.parity_shards
        if not isinstance(k, int) or not isinstance(m, int) or isinstance(k, bool) or isinstance(m, bool):
            raise ConfigurationError("data_shards and parity_shards must be integers")
        if k < 1 or m < 0 or k + m > MAX_TOTAL_SHARDS:
            raise ConfigurationError(f"require data_shards >= 1, parity_shards >= 0 and data_shards + parity_shards <= {MAX_TOTAL_SHARDS}")
        matrix = tuple(tuple(gf256.inv((k + i) ^ j) for j in range(k)) for i in range(m))
        object.__setattr__(self, "_parity_matrix", matrix)

    @property
    def total_shards(self) -> int:
        return self.data_shards + self.parity_shards

    @property
    def name(self) -> str:
        return "cauchy-rs-gf256"

    def generator_row(self, index: int) -> list[int]:
        """Row ``index`` of the (K+M)×K generator matrix ``[I; C]``."""
        if index < self.data_shards:
            return [int(index == j) for j in range(self.data_shards)]
        return list(self._parity_matrix[index - self.data_shards])

    # ------------------------------------------------------------------ encode
    def encode(self, data: np.ndarray) -> np.ndarray:
        """Compute parity for ``data`` of shape (stripes, K, L) → (stripes, M, L)."""
        data = np.ascontiguousarray(data, dtype=np.uint8)
        if data.ndim != 3 or data.shape[1] != self.data_shards:
            raise ValueError(f"expected data of shape (stripes, {self.data_shards}, length)")
        if self.parity_shards == 0:
            return np.zeros((data.shape[0], 0, data.shape[2]), dtype=np.uint8)
        return gf256.matmul_rows([list(r) for r in self._parity_matrix], data)

    # ------------------------------------------------------------------ decode
    def decode(self, shards: np.ndarray, present: np.ndarray) -> np.ndarray:
        """Reconstruct data shards.

        ``shards``: (stripes, K+M, L) uint8, contents of absent shards ignored.
        ``present``: (stripes, K+M) bool, True where the shard is known-good.
        Returns (stripes, K, L). Raises :class:`InsufficientRedundancyError`
        listing every stripe with fewer than K present shards.

        For a stripe missing the data shards E (|E| = e) the decoder picks e
        present parity rows P and solves the e×e system
        ``C[P,E] · d_E = p_P ⊕ C[P,Ē] · d_Ē``. ``C[P,E]`` is a square Cauchy
        submatrix, hence invertible (see module docstring).
        """
        k, n = self.data_shards, self.total_shards
        shards = np.asarray(shards, dtype=np.uint8)
        present = np.asarray(present, dtype=bool)
        if shards.ndim != 3 or shards.shape[1] != n or present.shape != shards.shape[:2]:
            raise ValueError("shards/present shape mismatch")
        counts = present.sum(axis=1)
        short = np.flatnonzero(counts < k)
        if short.size:
            raise InsufficientRedundancyError(
                f"{short.size} stripe(s) have fewer than {k} valid shards",
                details={"unrecoverable_stripes": short[:50].tolist(), "available": counts[short[:50]].tolist(), "required": k,
                         "unrecoverable_count": int(short.size)},  # capped: the list can be as long as the input
            )
        out = np.array(shards[:, :k, :], copy=True)
        needs = np.flatnonzero((~present[:, :k]).any(axis=1))
        if needs.size == 0:
            return out
        patterns: dict[bytes, list[int]] = {}
        for s in needs.tolist():
            patterns.setdefault(present[s].tobytes(), []).append(s)
        parity = np.asarray(self._parity_matrix, dtype=np.uint8).reshape(self.parity_shards, k)
        for key, stripe_ids in patterns.items():
            mask = np.frombuffer(key, dtype=bool)
            missing = np.flatnonzero(~mask[:k])
            known = np.flatnonzero(mask[:k])
            rows = np.flatnonzero(mask[k:])[: missing.size]
            idx = np.asarray(stripe_ids)
            inverse = gf256.matrix_invert_np(parity[np.ix_(rows, missing)])
            syndrome = shards[idx][:, k + rows, :]
            if known.size:
                syndrome = syndrome ^ gf256.matmul_rows(parity[np.ix_(rows, known)], out[idx][:, known, :])
            recovered = gf256.matmul_rows(inverse, syndrome)
            for pos, j in enumerate(missing.tolist()):
                out[idx, j, :] = recovered[:, pos, :]
        return out


# ---------------------------------------------------------------- reference
def reference_encode(code: CauchyErasureCode, data: list[bytes]) -> list[bytes]:
    """Scalar reference encoder (pure Python) used to validate the vectorized path."""
    length = len(data[0])
    parity = []
    for i in range(code.parity_shards):
        row = code.generator_row(code.data_shards + i)
        out = bytearray(length)
        for coefficient, shard in zip(row, data):
            for pos, value in enumerate(shard):
                out[pos] ^= gf256.mul(coefficient, value)
        parity.append(bytes(out))
    return list(data) + parity


# Formerly vnxdna.v2.encoder.cauchy_parity (V2 encoder); used by the V4-V6 outer codes. Moved verbatim (V6 Phase 2, M2).
_PARITY_TABLES: dict[tuple[int, int], np.ndarray] = {}


def cauchy_parity(code: CauchyErasureCode, data: np.ndarray) -> np.ndarray:
    """Outer parity (S, M, L) for data (S, K, L); equal to ``CauchyErasureCode.encode`` (tested).

    Loops over the K data columns with one (M, 256) table gather each, which is
    several times faster than the generic GF(256) matrix product for K ≥ 32.
    """
    s, k, length = data.shape
    m = code.parity_shards
    if m == 0:
        return np.zeros((s, 0, length), dtype=np.uint8)
    key = (k, m)
    if key not in _PARITY_TABLES:
        coeff = np.asarray([code.generator_row(k + i) for i in range(m)], dtype=np.uint8)  # (M, K)
        _PARITY_TABLES[key] = np.ascontiguousarray(gf256.MUL[coeff.T])  # (K, M, 256)
    tables = _PARITY_TABLES[key]
    out = np.zeros((m, s, length), dtype=np.uint8)
    for c in range(k):
        out ^= tables[c][:, data[:, c, :]]
    return out.transpose(1, 0, 2)
