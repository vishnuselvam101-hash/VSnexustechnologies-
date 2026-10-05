"""Soft symbols for V5 Phase 4: per-base posteriors over A, C, G, T and the byte posteriors they imply.

Representation (docs/V5_PHASE4_REPORT.md §5)
--------------------------------------------
A frame of ``n`` nucleotides is an (n, 4) float64 array ``L`` of **normalised log-probabilities**:
``L[k, b] = log P(base_k = b)`` with ``logsumexp_b L[k, b] = 0``. Log-domain throughout: no products of many small
probabilities are ever formed; normalisation uses log-sum-exp. ``-inf`` is allowed (probability exactly 0), NaN and
``+inf`` never are.

Conversion paths (each documented, none calibrated against a real sequencer):

* read base b with Phred Q:   P(b) = 1 − ε, P(other) = ε/3, ε = 10^(−Q/10) clipped to [1e−6, 0.75]
* read base without quality:   the same with ε = ``default_error`` (an assumption, recorded in the config)
* N / erased / unknown:        uniform, P = ¼ each, unless stronger evidence exists
* Phase 3 window posterior:    the window's per-position distribution (already normalised), taken as is
* consensus:                   Σ over reads of log P(observation | base) + uniform prior, then normalised

Byte posterior. A byte is four consecutive bases b0 b1 b2 b3 (most significant first, V4's 2-bit map). Bases are
treated as independent given the read evidence, so log P(byte = v) = Σ_i L[4j + i, v_i] — the exact Cartesian product
of the four base distributions in *factorised* form (16 numbers per byte instead of 256). The full 256-entry table is
available for tests and accounting (:func:`byte_log_posterior`); decoding uses the factorised form:

* hard byte = per-base argmax (the argmax of a product of independent factors);
* byte reliability = log P(best byte) − log P(second-best byte) = min over the 4 bases of (top − second) log-gap,
  because the second-best byte differs from the best in exactly one base;
* top-K bytes: a K-best merge over the four sorted base lists (:func:`byte_topk`), never 256 enumerations.

Independence between neighbouring bases is a modelling assumption (true for the V4 channel's substitutions; not for
indel windows, where the Phase 3 posterior already carries the joint structure through its candidates).
"""
from __future__ import annotations

import heapq
import math

import numpy as np

LOG_QUARTER = math.log(0.25)
_NORM_TOL = 1e-6


class SoftInputError(ValueError):
    """A soft-symbol array is malformed (NaN, +inf, negative or unnormalised probabilities, wrong shape)."""


# ---------------------------------------------------------------- validation
def validate_logp(L: np.ndarray, n: int | None = None) -> np.ndarray:
    """Check an (n, 4) log-probability array. Returns it as float64; raises SoftInputError otherwise (fail closed)."""
    if not isinstance(L, np.ndarray) or L.ndim != 2 or L.shape[1] != 4:
        raise SoftInputError("soft symbols must be an (n, 4) array")
    if n is not None and L.shape[0] != n:
        raise SoftInputError(f"soft symbols have {L.shape[0]} positions, expected {n}")
    if not np.issubdtype(L.dtype, np.floating):
        raise SoftInputError("soft symbols must be floating point")
    L = L.astype(np.float64, copy=False)
    if np.isnan(L).any() or np.isposinf(L).any():
        raise SoftInputError("soft symbols contain NaN or +inf")
    if (L > 1e-12).any():
        raise SoftInputError("a log-probability is positive (probability > 1)")
    if (L.max(axis=1) == -np.inf).any():
        raise SoftInputError("a position has probability 0 for every base")
    tot = np.logaddexp.reduce(L, axis=1)
    if (np.abs(tot) > _NORM_TOL).any():
        raise SoftInputError("a position's probabilities do not sum to 1")
    return L


def from_probabilities(P: np.ndarray) -> np.ndarray:
    """(n, 4) probabilities → validated log-probabilities. Negative, NaN, inf or unnormalised input raises."""
    if not isinstance(P, np.ndarray) or P.ndim != 2 or P.shape[1] != 4 or not np.issubdtype(P.dtype, np.floating):
        raise SoftInputError("probabilities must be an (n, 4) floating-point array")
    if not np.isfinite(P).all():
        raise SoftInputError("probabilities contain NaN or inf")
    if (P < 0).any():
        raise SoftInputError("negative probability")
    s = P.sum(axis=1)
    if (np.abs(s - 1.0) > _NORM_TOL).any():
        raise SoftInputError("probabilities do not sum to 1")
    with np.errstate(divide="ignore"):
        L = np.log(P)
    return validate_logp(L - np.logaddexp.reduce(L, axis=1, keepdims=True))


def normalise(L: np.ndarray) -> np.ndarray:
    """Normalise unnormalised log-likelihood rows (log-sum-exp). Rows that are all −inf or contain NaN raise."""
    if np.isnan(L).any() or np.isposinf(L).any():
        raise SoftInputError("log-likelihoods contain NaN or +inf")
    m = L.max(axis=1, keepdims=True)
    if (m == -np.inf).any():
        raise SoftInputError("a position has zero likelihood for every base")
    return L - (m + np.log(np.exp(L - m).sum(axis=1, keepdims=True)))


# ---------------------------------------------------------------- conversions
def phred_error(q: np.ndarray) -> np.ndarray:
    return np.clip(10.0 ** (-np.asarray(q, dtype=np.float64) / 10.0), 1e-6, 0.75)


def observation_loglik(bases: np.ndarray, quals: np.ndarray | None, default_error: float) -> np.ndarray:
    """(n, 4) log P(observed base | true base = b) for read bases (codes 0–3; ≥ 4 = N → flat)."""
    bases = np.asarray(bases)
    n = bases.size
    eps = np.full(n, float(default_error)) if quals is None else phred_error(quals)
    if quals is not None and np.asarray(quals).size != n:
        raise SoftInputError("qualities and bases differ in length")
    out = np.repeat(np.log(eps / 3.0)[:, None], 4, axis=1)
    known = bases < 4
    out[np.flatnonzero(known), bases[known].astype(np.int64)] = np.log1p(-eps[known])
    out[~known] = 0.0
    return out


def from_read(bases: np.ndarray, quals: np.ndarray | None, default_error: float) -> np.ndarray:
    """Posterior of each base from one read with a uniform prior (= normalised observation likelihood)."""
    return normalise(observation_loglik(bases, quals, default_error))


def uniform(n: int) -> np.ndarray:
    return np.full((n, 4), LOG_QUARTER)


def combine(*logliks: np.ndarray) -> np.ndarray:
    """Independent evidence: Σ log-likelihoods + uniform prior, normalised (log domain)."""
    if not logliks:
        raise SoftInputError("nothing to combine")
    acc = np.zeros_like(logliks[0], dtype=np.float64)
    for x in logliks:
        if x.shape != acc.shape:
            raise SoftInputError("soft arrays of different shapes")
        acc = acc + x
    return normalise(acc)


def entropy_bits(L: np.ndarray) -> np.ndarray:
    """Per-position entropy in bits (0 = certain, 2 = uniform)."""
    P = np.exp(L)
    with np.errstate(invalid="ignore"):
        h = -(np.where(P > 0, P * L, 0.0)).sum(axis=1) / math.log(2)
    return h


# ---------------------------------------------------------------- bytes
_SHIFT = np.array([6, 4, 2, 0], dtype=np.int64)


def byte_hard(L: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """→ (hard bytes (n/4,) uint8, hard bases (n,) uint8): per-base argmax (ties → lowest base code)."""
    hb = L.argmax(axis=1).astype(np.uint8)
    q = hb.reshape(-1, 4).astype(np.int64)
    return ((q << _SHIFT).sum(axis=1)).astype(np.uint8), hb


def byte_reliability(L: np.ndarray) -> np.ndarray:
    """(n/4,) log P(best byte) − log P(second-best byte) = min over its bases of the top-two log gap (≥ 0)."""
    s = np.sort(L, axis=1)
    gap = s[:, 3] - s[:, 2]
    gap = np.where(np.isinf(gap), 1e300, gap)            # second base impossible: the base is certain
    return gap.reshape(-1, 4).min(axis=1)


def byte_log_posterior(L: np.ndarray) -> np.ndarray:
    """(n/4, 256) exact log P(byte = v) (the full Cartesian product; tests and accounting)."""
    q = L.reshape(-1, 4, 4)
    v = np.arange(256)
    digits = (v[None, :] >> _SHIFT[:, None]) & 3                    # (4, 256)
    return q[:, 0, digits[0]] + q[:, 1, digits[1]] + q[:, 2, digits[2]] + q[:, 3, digits[3]]


def byte_topk(Lb: np.ndarray, k: int) -> list[tuple[float, int]]:
    """K most probable values of one byte from its (4, 4) base log-probabilities: [(log P, value)], best first.

    K-best merge over the four bases sorted by probability: a heap of index vectors, O(K log K), never 256 values.
    Ties are broken by the byte value so the order is deterministic."""
    if Lb.shape != (4, 4):
        raise SoftInputError("a byte needs (4, 4) base log-probabilities")
    order = np.argsort(-Lb, axis=1, kind="stable")
    vals = np.take_along_axis(Lb, order, axis=1)
    def score(ix):
        return float(sum(vals[i, ix[i]] for i in range(4)))
    def value(ix):
        return int(sum(int(order[i, ix[i]]) << (6 - 2 * i) for i in range(4)))
    start = (0, 0, 0, 0)
    heap = [(-score(start), value(start), start)]
    seen = {start}
    out = []
    while heap and len(out) < k:
        negs, val, ix = heapq.heappop(heap)
        if negs == math.inf:
            break                                          # remaining values have probability 0
        out.append((-negs, val))
        for i in range(4):
            if ix[i] < 3:
                nx = ix[:i] + (ix[i] + 1,) + ix[i + 1:]
                if nx not in seen:
                    seen.add(nx)
                    heapq.heappush(heap, (-score(nx), value(nx), nx))
    return out


def word_loglik(L: np.ndarray, word: np.ndarray) -> float:
    """log P(frame = word) under the factorised posterior (word: n/4 bytes)."""
    w = np.asarray(word, dtype=np.int64)
    digits = (w[:, None] >> _SHIFT[None, :]) & 3                    # (n/4, 4)
    return float(np.take_along_axis(L.reshape(-1, 4, 4), digits[:, :, None], axis=2).sum())
