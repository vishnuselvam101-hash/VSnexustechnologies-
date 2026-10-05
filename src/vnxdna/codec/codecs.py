"""Comparative codec interface and the V4 outer/inner codes.

Every outer code implements :class:`OuterCodec`::

    encode(data)            (k, P) source symbols → (n, P) coded symbols (symbol index = row)
    decode(symbols, k)      {index: (P,) symbol} → (k, P) source symbols, or raises VNXDecodeError
    symbols_for(k)          number of coded symbols emitted for a block of k source symbols
    overhead(k)             (n − k) / k
    capabilities()          what the code guarantees (machine-readable)
    configuration()         parameters recorded in the superblock / benchmark output
    benchmark(...)          see vnxdna.v4.bench.codec_benchmark

Registered outer codes:

* ``cauchy-rs`` (IMPLEMENTED, default) — the V3 systematic Cauchy Reed–Solomon
  code over GF(2^8) (:class:`vnxdna.ecc.cauchy.CauchyErasureCode`, MDS: any k of
  the k + m symbols decode). Re-used unchanged.
* ``lt-fountain`` (EXPERIMENTAL) — an independent systematic LT code over GF(2)
  with a robust-soliton degree distribution, a peeling decoder and a GF(2)
  Gaussian-elimination fallback. Not MDS: decoding succeeds with high
  probability once slightly more than k symbols arrive; it is measured, not
  guaranteed (docs/ECC_ARCHITECTURE.md).

Inner code: the V3 systematic RS(n, n − r) with the vectorised bounded-distance
decoder (:mod:`vnxdna.ecc.rs_batch`), re-used unchanged.
"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol

import numpy as np

import os

from vnxdna.codec import rs_batch
from vnxdna.codec.cauchy import CauchyErasureCode
from vnxdna.codec.inner_parity import parity_matrix as _parity_matrix   # = ecc.inner_rs._parity_matrix, no reedsolo
from vnxdna.codec import gf256
from vnxdna.codec.cauchy import cauchy_parity
from vnxdna.core.errors import VNXConfigurationError, VNXDecodeError


class OuterCodec(Protocol):
    name: str
    stability: str

    def symbols_for(self, k: int) -> int: ...
    def encode(self, data: np.ndarray) -> np.ndarray: ...
    def decode(self, symbols: dict[int, np.ndarray], k: int, length: int) -> np.ndarray: ...
    def overhead(self, k: int | None = None) -> float: ...
    def capabilities(self) -> dict: ...
    def configuration(self) -> dict: ...


# ============================================================================ Cauchy RS (V3 code, wrapped)
class CauchyRSCodec:
    """Systematic MDS erasure code; a block of k ≤ K source symbols gets m = M parity symbols (shortened code)."""

    name = "cauchy-rs"
    stability = "IMPLEMENTED"
    code_id = 1

    def __init__(self, data_symbols: int, parity_symbols: int):
        if data_symbols < 1 or parity_symbols < 0 or data_symbols + parity_symbols > 256:
            raise VNXConfigurationError("cauchy-rs needs 1 <= K, 0 <= M, K + M <= 256")
        self.K, self.M = data_symbols, parity_symbols
        self._code = CauchyErasureCode(self.K, self.M)

    def symbols_for(self, k: int) -> int:
        return k + self.M

    def overhead(self, k: int | None = None) -> float:
        return self.M / (k or self.K)

    def encode(self, data: np.ndarray) -> np.ndarray:
        """(k, P) → (k + M, P). A short block (k < K) is a shortened code: missing data rows are implicit zeros."""
        return self.encode_many(data[None])[0][: data.shape[0] + self.M] if data.shape[0] == self.K else \
            self._encode_short(data)

    def _encode_short(self, data: np.ndarray) -> np.ndarray:
        k, p = data.shape
        full = np.zeros((1, self.K, p), dtype=np.uint8)
        full[0, :k] = data
        parity = cauchy_parity(self._code, full)[0]
        return np.concatenate([data, parity], axis=0)

    def encode_many(self, data: np.ndarray) -> np.ndarray:
        """(G, K, P) full blocks → (G, K + M, P)."""
        parity = cauchy_parity(self._code, np.ascontiguousarray(data, dtype=np.uint8))
        return np.concatenate([data, parity], axis=1)

    def decode(self, symbols: dict[int, np.ndarray], k: int, length: int) -> np.ndarray:
        """Symbols are indexed 0..k−1 (data) and k..k+M−1 (parity) for a block of k source symbols."""
        n = self.K + self.M
        shards = np.zeros((1, n, length), dtype=np.uint8)
        present = np.zeros((1, n), dtype=bool)
        present[0, k:self.K] = True              # shortened positions are known zeros
        for idx, sym in symbols.items():
            if 0 <= idx < k:
                shards[0, idx] = sym
                present[0, idx] = True
            elif k <= idx < k + self.M:
                shards[0, self.K + idx - k] = sym
                present[0, self.K + idx - k] = True
        if present.sum() < self.K:
            raise VNXDecodeError(f"cauchy-rs block has {int(present.sum()) - (self.K - k)} of {k} required symbols",
                                 details={"received": int(present.sum()) - (self.K - k), "required": k})
        return self._code.decode(shards, present)[0, :k]

    def capabilities(self) -> dict:
        return {"type": "erasure", "mds": True, "guarantee": f"any {self.M} of {self.K + self.M} symbols per block may be lost",
                "locates_errors": False, "field": "GF(2^8)", "max_symbols": 256}

    def configuration(self) -> dict:
        return {"name": self.name, "data_symbols": self.K, "parity_symbols": self.M}


# ============================================================================ LT fountain (independent implementation)
def _robust_soliton_cdf(k: int, c: float, delta: float) -> np.ndarray:
    """CDF over degrees 1..k of Luby's robust soliton distribution."""
    if k == 1:
        return np.array([1.0])
    d = np.arange(1, k + 1, dtype=np.float64)
    rho = np.zeros(k)
    rho[0] = 1.0 / k
    rho[1:] = 1.0 / (d[1:] * (d[1:] - 1.0))
    s = c * math.log(k / delta) * math.sqrt(k)
    tau = np.zeros(k)
    pivot = max(1, min(k, int(round(k / s)))) if s > 0 else k
    tau[: pivot - 1] = s / (k * d[: pivot - 1])
    tau[pivot - 1] = s * math.log(s / delta) / k if s > delta else 0.0
    mu = rho + np.maximum(tau, 0)
    cdf = np.cumsum(mu / mu.sum())
    cdf[-1] = 1.0
    return cdf


class LTFountainCodec:
    """Systematic fountain code over GF(2): symbols 0..k−1 are the source symbols, k.. are XOR droplets.

    Two droplet distributions (benchmarked against each other, docs/ECC_ARCHITECTURE.md):

    * ``dense`` (default): every source symbol joins a droplet with probability 1/2 — a
      random linear fountain decoded by Gaussian elimination; with e extra symbols
      beyond what is needed the residual system is full-rank with probability ≈ 1 − 2^−e.
    * ``robust-soliton``: Luby's LT distribution with peeling. Kept for comparison: in the
      systematic setting its low-degree droplets rarely touch the few missing sources,
      and it failed at 6 % reception overhead in our measurements (docs/V4_ENGINEERING_LOG.md).

    Droplet j of a block draws its degree and neighbours from SHA-256(seed ‖ block ‖ j),
    so encoder and decoder agree without storing the graph, and droplets can be
    generated in any number (rateless). ``repair_symbols`` is the number of
    droplets per *full* block of K source symbols; a short last block gets a
    proportional share (at least 1).
    """

    name = "lt-fountain"
    stability = "EXPERIMENTAL"
    code_id = 2

    def __init__(self, data_symbols: int, repair_symbols: int, *, seed: int = 0, c: float = 0.05, delta: float = 0.5,
                 distribution: str = "dense"):
        if data_symbols < 1 or repair_symbols < 0 or data_symbols + repair_symbols > 65535:
            raise VNXConfigurationError("lt-fountain needs 1 <= K, 0 <= M, K + M <= 65535")
        if distribution not in ("dense", "robust-soliton"):
            raise VNXConfigurationError("lt-fountain distribution must be 'dense' or 'robust-soliton'")
        self.K, self.M, self.seed, self.c, self.delta = data_symbols, repair_symbols, seed, c, delta
        self.distribution = distribution
        self._cdf: dict[int, np.ndarray] = {}
        self.block = 0  # set by callers per block (part of the droplet seed)

    def symbols_for(self, k: int) -> int:
        return k + (self.M if k == self.K else max(1 if self.M else 0, -(-self.M * k // self.K)))

    def overhead(self, k: int | None = None) -> float:
        k = k or self.K
        return (self.symbols_for(k) - k) / k

    def neighbours(self, block: int, j: int, k: int) -> np.ndarray:
        if j < k:
            return np.array([j])
        tag = b"VNX4 LT\x00" + self.seed.to_bytes(4, "big") + block.to_bytes(4, "big") + j.to_bytes(4, "big")
        if self.distribution == "dense":
            # random linear fountain over GF(2): each source symbol joins with probability 1/2 (SHAKE-128 bits)
            bits = np.unpackbits(np.frombuffer(hashlib.shake_128(tag).digest(-(-k // 8)), dtype=np.uint8))[:k]
            nb = np.flatnonzero(bits)
            return nb if nb.size else np.array([j % k])
        cdf = self._cdf.get(k)
        if cdf is None:
            cdf = self._cdf[k] = _robust_soliton_cdf(k, self.c, self.delta)
        h = hashlib.sha256(tag).digest()
        u = int.from_bytes(h[:8], "big") / 2.0 ** 64
        degree = int(np.searchsorted(cdf, u, side="right")) + 1
        degree = min(max(degree, 1), k)
        state = int.from_bytes(h[8:16], "big") | 1
        chosen: list[int] = []
        seen = set()
        while len(chosen) < degree:   # xorshift64* stream; rejection keeps neighbours distinct and uniform
            state ^= (state >> 12) & 0xFFFFFFFFFFFFFFFF
            state ^= (state << 25) & 0xFFFFFFFFFFFFFFFF
            state ^= (state >> 27) & 0xFFFFFFFFFFFFFFFF
            v = ((state * 0x2545F4914F6CDD1D) & 0xFFFFFFFFFFFFFFFF) % k
            if v not in seen:
                seen.add(v)
                chosen.append(v)
        return np.array(sorted(chosen))

    def encode_block(self, data: np.ndarray, block: int) -> np.ndarray:
        k, p = data.shape
        n = self.symbols_for(k)
        out = np.zeros((n, p), dtype=np.uint8)
        out[:k] = data
        for j in range(k, n):
            out[j] = np.bitwise_xor.reduce(data[self.neighbours(block, j, k)], axis=0)
        return out

    def encode(self, data: np.ndarray) -> np.ndarray:
        return self.encode_block(data, self.block)

    def decode_block(self, symbols: dict[int, np.ndarray], k: int, length: int, block: int) -> np.ndarray:
        n = self.symbols_for(k)
        src = np.zeros((k, length), dtype=np.uint8)
        known = np.zeros(k, dtype=bool)
        eqs: list[tuple[set[int], np.ndarray]] = []
        for j, sym in symbols.items():
            if not 0 <= j < n:
                continue
            if j < k:
                src[j] = sym
                known[j] = True
            else:
                eqs.append((set(self.neighbours(block, j, k).tolist()), np.array(sym, dtype=np.uint8)))
        if known.all():
            return src
        if len(eqs) + int(known.sum()) < k:
            raise VNXDecodeError(f"lt-fountain block {block}: {len(eqs) + int(known.sum())} symbols < k = {k}",
                                 details={"received": len(eqs) + int(known.sum()), "required": k})
        # peeling
        for nb, val in eqs:
            for i in [i for i in nb if known[i]]:
                nb.discard(i)
                val ^= src[i]
        progress = True
        while progress:
            progress = False
            for nb, val in eqs:
                if len(nb) == 1:
                    i = next(iter(nb))
                    nb.clear()
                    if not known[i]:
                        src[i] = val
                        known[i] = True
                        progress = True
                        for nb2, val2 in eqs:
                            if i in nb2:
                                nb2.discard(i)
                                val2 ^= src[i]
        if known.all():
            return src
        # Gaussian elimination over GF(2) on the residual system
        unknown = np.flatnonzero(~known)
        col = {int(u): c for c, u in enumerate(unknown)}
        rows = [(nb, val) for nb, val in eqs if nb]
        if len(rows) < unknown.size:
            raise VNXDecodeError(f"lt-fountain block {block}: peeling stalled with {unknown.size} unknowns and {len(rows)} equations")
        a = np.zeros((len(rows), unknown.size), dtype=bool)
        b = np.zeros((len(rows), length), dtype=np.uint8)
        for r, (nb, val) in enumerate(rows):
            a[r, [col[i] for i in nb]] = True
            b[r] = val
        rank = 0
        pivots = []
        for c in range(unknown.size):
            piv = np.flatnonzero(a[rank:, c])
            if piv.size == 0:
                raise VNXDecodeError(f"lt-fountain block {block}: residual system is rank-deficient ({unknown.size} unknowns)",
                                     details={"unknowns": int(unknown.size), "equations": len(rows)})
            p = rank + int(piv[0])
            if p != rank:
                a[[rank, p]] = a[[p, rank]]
                b[[rank, p]] = b[[p, rank]]
            hit = np.flatnonzero(a[:, c])
            hit = hit[hit != rank]
            a[hit] ^= a[rank]
            b[hit] ^= b[rank]
            pivots.append(rank)
            rank += 1
        for c, u in enumerate(unknown):
            src[u] = b[c]
        return src

    def decode(self, symbols: dict[int, np.ndarray], k: int, length: int) -> np.ndarray:
        return self.decode_block(symbols, k, length, self.block)

    def capabilities(self) -> dict:
        return {"type": "erasure", "mds": False, "rateless": True,
                "guarantee": "none deterministic; success probability rises with received symbols above k (measured)",
                "locates_errors": False, "field": "GF(2)", "max_symbols": 65535}

    def configuration(self) -> dict:
        out = {"name": self.name, "data_symbols": self.K, "repair_symbols": self.M, "seed": self.seed,
               "distribution": self.distribution}
        if self.distribution == "robust-soliton":
            out["robust_soliton"] = {"c": self.c, "delta": self.delta}
        return out


OUTER_CODECS = {"cauchy-rs": CauchyRSCodec, "lt-fountain": LTFountainCodec}
CODE_IDS = {1: "cauchy-rs", 2: "lt-fountain"}


def make_outer(name: str, k: int, m: int, seed: int = 0, distribution: str = "dense") -> CauchyRSCodec | LTFountainCodec:
    if name == "cauchy-rs":
        return CauchyRSCodec(k, m)
    if name == "lt-fountain":
        return LTFountainCodec(k, m, seed=seed, distribution=distribution)
    raise VNXConfigurationError(f"unknown outer code {name!r}; available: {sorted(OUTER_CODECS)}")


# ============================================================================ inner RS (V3 code, wrapped)
_TABLES: dict[tuple[int, int], np.ndarray] = {}
_REFERENCE_RS = os.environ.get("VNX_RS_REFERENCE") == "1"


class InnerRS:
    """Systematic RS(n, n − r) over GF(2^8)/0x11D (fcr 0, generator 2), identical to V3 and reedsolo."""

    name = "reed-solomon-gf256"
    stability = "IMPLEMENTED"

    def __init__(self, parity_bytes: int):
        if not 0 <= parity_bytes <= 64:
            raise VNXConfigurationError("inner parity must be 0..64 bytes")
        self.r = parity_bytes

    def parity(self, messages: np.ndarray) -> np.ndarray:
        n, k = messages.shape
        out = np.zeros((n, self.r), dtype=np.uint8)
        if self.r == 0 or n == 0:
            return out
        key = (k, self.r)
        if key not in _TABLES:
            matrix = _parity_matrix(k, self.r)
            _TABLES[key] = np.ascontiguousarray(gf256.MUL[:, matrix].transpose(1, 0, 2))
        tables = _TABLES[key]
        for i in range(k):
            out ^= tables[i][messages[:, i]]
        return out

    def decode(self, codewords: np.ndarray, erasures: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """→ (corrected, ok, errata). Bounded distance: corrects iff 2e + f ≤ r; callers must re-check the CRC."""
        if _REFERENCE_RS:
            return rs_batch.decode_batch(codewords, self.r, erasures)
        from vnxdna.native import rs as native_rs  # V6: native kernel (AVX2/scalar), bit-exact with rs_fast, which it falls back to
        return native_rs.decode_batch(codewords, self.r, erasures)

    def capabilities(self) -> dict:
        return {"type": "error-and-erasure", "guarantee": f"2e + f <= {self.r} byte errors e / erasures f per strand",
                "handles_indels": False}
