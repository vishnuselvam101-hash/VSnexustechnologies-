"""Generic error models (SIMULATED): the building blocks of the channel stages.

Every model implements :class:`ErrorModel`: ``kind``, ``parameters()``, ``active`` and ``apply(pool, rng)``, where ``pool`` is
a :class:`SequencePool` (a padded batch of sequences). They can be used alone or combined:

* :class:`SubstitutionModel`  per-base substitutions; optional 4x4 matrix P(to | from) and per-from-base rate multipliers
* :class:`InsertionModel`     per-base insertions of a random base (optional base weights) before a position
* :class:`DeletionModel`      per-base deletion events; optional geometric run length (clustered deletions)
* :class:`DropoutModel`       whole sequences lost (Bernoulli per sequence)
* :class:`CoverageModel`      copies per sequence: fixed, Poisson, negative binomial (Gamma-Poisson) or lognormal-Poisson
* :class:`QualityModel`       Phred scores: fixed, informative (low score on erroneous bases), Gaussian variation, slope
* :class:`CompositeModel`     an ordered combination; adjacent substitution/insertion/deletion models are applied *jointly*
                              (competing events per position, one uniform draw per position) as in the V4 channel

The per-base kernel :func:`per_base_errors` is the V4 channel's algorithm (``vnxdna.simulation.channel.simulate_batch``) with
optional extensions. Its draws from the main generator are exactly the V4 draws, and every extension draws from a separate
auxiliary generator, so a model that uses no extension reproduces the V4 reads bit for bit.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Sequence

import numpy as np

PAD = 255                                    # code of padding positions in a SequencePool (never written)
_RC = np.array([3, 2, 1, 0, 4], dtype=np.uint8)


# ================================================================================================================ pool
@dataclass
class SequencePool:
    """A batch of sequences as a padded (m, W) uint8 matrix (A=0 C=1 G=2 T=3 N=4, padding 255) plus lengths.

    ``source`` maps each sequence to the strand it came from; ``quals`` (optional) has the same shape as ``codes``.
    """

    codes: np.ndarray
    lengths: np.ndarray
    source: np.ndarray
    quals: np.ndarray | None = None
    stats: dict = field(default_factory=dict)

    @classmethod
    def from_matrix(cls, codes: np.ndarray) -> "SequencePool":
        codes = np.asarray(codes, dtype=np.uint8)
        m = codes.shape[0]
        return cls(codes.copy(), np.full(m, codes.shape[1], dtype=np.int64), np.arange(m, dtype=np.int64))

    @classmethod
    def from_strings(cls, seqs: Sequence[str]) -> "SequencePool":
        table = np.full(256, PAD, dtype=np.uint8)
        for i, c in enumerate(b"ACGTN"):
            table[c] = i
        lengths = np.array([len(s) for s in seqs], dtype=np.int64)
        flat = table[np.frombuffer("".join(seqs).encode("ascii"), dtype=np.uint8)] if seqs else np.zeros(0, np.uint8)
        return cls(pad(flat, lengths), lengths, np.arange(len(seqs), dtype=np.int64))

    @classmethod
    def from_flat(cls, flat: np.ndarray, lengths: np.ndarray, source: np.ndarray | None = None,
                  quals: np.ndarray | None = None) -> "SequencePool":
        lengths = np.asarray(lengths, dtype=np.int64)
        return cls(pad(flat, lengths), lengths, np.arange(lengths.size, dtype=np.int64) if source is None else source,
                   None if quals is None else pad(quals, lengths, fill=0))

    @property
    def width(self) -> int:
        return int(self.codes.shape[1])

    def valid(self) -> np.ndarray:
        return np.arange(self.width)[None, :] < self.lengths[:, None]

    def flat(self) -> tuple[np.ndarray, np.ndarray]:
        return self.codes[self.valid()], self.lengths

    def strings(self) -> list[str]:
        flat, lengths = self.flat()
        text = np.frombuffer(b"ACGTN", dtype=np.uint8)[flat].tobytes().decode()
        offs = np.concatenate([[0], np.cumsum(lengths)])
        return [text[offs[i]:offs[i + 1]] for i in range(lengths.size)]

    def take(self, index: np.ndarray) -> "SequencePool":
        return SequencePool(self.codes[index], self.lengths[index], self.source[index],
                            None if self.quals is None else self.quals[index], dict(self.stats))


def pad(flat: np.ndarray, lengths: np.ndarray, fill: int = PAD) -> np.ndarray:
    """Flat concatenated sequences → (m, max length) matrix padded with ``fill``."""
    lengths = np.asarray(lengths, dtype=np.int64)
    width = int(lengths.max()) if lengths.size else 0
    out = np.full((lengths.size, max(width, 1)), fill, dtype=np.uint8)
    out[np.arange(out.shape[1])[None, :] < lengths[:, None]] = flat
    return out


def _bump(stats: dict, key: str, value: int) -> None:
    stats[key] = stats.get(key, 0) + int(value)


# ================================================================================================================ helpers
def cumulative_matrix(matrix) -> np.ndarray:
    """Row-cumulative 4x4 matrix whose last non-zero entry per row is exactly 1 (no float round-off to the diagonal)."""
    m = np.asarray(matrix, dtype=np.float64)
    cm = np.cumsum(m, axis=1)
    for r in range(4):
        last = int(np.flatnonzero(m[r] > 0)[-1])
        cm[r, last:] = 1.0
    return cm


def sample_targets(from_codes: np.ndarray, cm: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Draw substitution targets for ``from_codes`` (values 0..3) from a cumulative matrix."""
    if from_codes.size == 0:
        return from_codes.astype(np.uint8)
    u = rng.random(from_codes.size)
    return (u[:, None] < cm[np.minimum(from_codes, 3)]).argmax(axis=1).astype(np.uint8)


@dataclass(frozen=True)
class PositionProfile:
    """Per-position multipliers of the substitution, insertion and deletion rates.

    ``absolute``: entry i applies to position i of the sequence (the last entry applies beyond the list).
    ``relative``: the B entries are equal-width bins over each sequence's own length (position p of a length-n
    sequence uses bin floor(p * B / n)).
    """

    basis: str = "absolute"
    substitution: tuple | None = None
    insertion: tuple | None = None
    deletion: tuple | None = None

    @classmethod
    def from_json(cls, doc: dict | None) -> "PositionProfile | None":
        if doc is None:
            return None
        conv = (lambda v: None if v is None else tuple(float(x) for x in v))
        return cls(doc.get("basis", "absolute"), conv(doc.get("substitution")), conv(doc.get("insertion")),
                   conv(doc.get("deletion")))

    def to_json(self) -> dict:
        lst = (lambda v: None if v is None else list(v))
        return {"basis": self.basis, "substitution": lst(self.substitution), "insertion": lst(self.insertion),
                "deletion": lst(self.deletion)}

    def multipliers(self, kind: str, width: int, lengths: np.ndarray | None) -> np.ndarray | None:
        vec = getattr(self, kind)
        if vec is None:
            return None
        v = np.asarray(vec, dtype=np.float64)
        pos = np.arange(width)
        if self.basis == "absolute":
            return v[np.minimum(pos, v.size - 1)][None, :]
        if lengths is None:
            lens = np.full(1, width, dtype=np.int64)
        else:
            lens = np.maximum(lengths, 1)
        idx = (pos[None, :] * v.size) // lens[:, None]
        return v[np.minimum(idx, v.size - 1)]


# ================================================================================================================ interface
class ErrorModel(ABC):
    """One error mechanism. ``apply`` returns a new pool and adds its event counts to ``pool.stats``."""

    kind: ClassVar[str] = "error-model"

    @abstractmethod
    def parameters(self) -> dict:
        ...

    @property
    @abstractmethod
    def active(self) -> bool:
        ...

    @abstractmethod
    def apply(self, pool: SequencePool, rng: np.random.Generator) -> SequencePool:
        ...

    def describe(self) -> dict:
        return {"kind": self.kind, "parameters": self.parameters()}


@dataclass(frozen=True)
class SubstitutionModel(ErrorModel):
    rate: float = 0.0
    matrix: tuple | None = None                  # 4x4, rows from A,C,G,T: P(to | from, substitution); diagonal 0
    from_multipliers: tuple | None = None        # rate multiplier by the original base (A, C, G, T)
    profile: PositionProfile | None = None
    kind: ClassVar[str] = "substitution"

    @classmethod
    def from_json(cls, doc: dict, profile: PositionProfile | None = None) -> "SubstitutionModel":
        m = doc.get("matrix")
        fm = doc.get("from_multipliers")
        return cls(doc["rate"], None if m is None else tuple(tuple(r) for r in m), None if fm is None else tuple(fm), profile)

    def parameters(self) -> dict:
        return {"rate": self.rate, "matrix": None if self.matrix is None else [list(r) for r in self.matrix],
                "from_multipliers": None if self.from_multipliers is None else list(self.from_multipliers)}

    @property
    def active(self) -> bool:
        return self.rate > 0

    @property
    def cm(self) -> np.ndarray | None:
        return None if self.matrix is None else cumulative_matrix(self.matrix)

    def site_rates(self, base: np.ndarray, rates: np.ndarray) -> np.ndarray:
        if self.from_multipliers is not None:
            rates = rates * np.asarray(self.from_multipliers, dtype=np.float64)[np.minimum(base, 3)]
        return rates

    def apply(self, pool: SequencePool, rng: np.random.Generator) -> SequencePool:
        return CompositeModel((self,)).apply(pool, rng)

    def mutate(self, base: np.ndarray, hit: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Substitute ``base`` at ``hit`` (a standalone substitution draw, used for damage and PCR errors)."""
        out = base.copy()
        idx = np.nonzero(hit)
        if self.matrix is not None:
            out[idx] = sample_targets(base[idx], self.cm, rng)
        else:
            out[idx] = ((base[idx].astype(np.int64) + rng.integers(1, 4, idx[0].size)) % 4).astype(np.uint8)
        return out

    def hits(self, base: np.ndarray, valid: np.ndarray, rng: np.random.Generator, scale: float = 1.0) -> np.ndarray:
        rates = self.site_rates(base, np.full(base.shape, self.rate * scale))
        return (rng.random(base.shape) < rates) & valid


@dataclass(frozen=True)
class InsertionModel(ErrorModel):
    rate: float = 0.0
    base_weights: tuple | None = None            # P(inserted base = A, C, G, T); None = uniform
    profile: PositionProfile | None = None
    kind: ClassVar[str] = "insertion"

    @classmethod
    def from_json(cls, doc: dict, profile: PositionProfile | None = None) -> "InsertionModel":
        w = doc.get("base_weights")
        return cls(doc["rate"], None if w is None else tuple(w), profile)

    def parameters(self) -> dict:
        return {"rate": self.rate, "base_weights": None if self.base_weights is None else list(self.base_weights)}

    @property
    def active(self) -> bool:
        return self.rate > 0

    def apply(self, pool: SequencePool, rng: np.random.Generator) -> SequencePool:
        return CompositeModel((self,)).apply(pool, rng)


@dataclass(frozen=True)
class DeletionModel(ErrorModel):
    rate: float = 0.0                            # per-base rate of deletion events (run starts)
    run_distribution: str = "single"             # single | geometric
    run_mean: float = 1.0                        # mean run length (geometric, >= 1)
    profile: PositionProfile | None = None
    kind: ClassVar[str] = "deletion"

    @classmethod
    def from_json(cls, doc: dict, profile: PositionProfile | None = None) -> "DeletionModel":
        rl = doc["run_length"]
        return cls(doc["rate"], rl["distribution"], rl["mean"], profile)

    def parameters(self) -> dict:
        return {"rate": self.rate, "run_length": {"distribution": self.run_distribution, "mean": self.run_mean}}

    @property
    def active(self) -> bool:
        return self.rate > 0

    @property
    def clustered(self) -> bool:
        return self.run_distribution == "geometric" and self.run_mean > 1.0

    def apply(self, pool: SequencePool, rng: np.random.Generator) -> SequencePool:
        return CompositeModel((self,)).apply(pool, rng)


@dataclass(frozen=True)
class DropoutModel(ErrorModel):
    rate: float = 0.0
    kind: ClassVar[str] = "dropout"

    def parameters(self) -> dict:
        return {"rate": self.rate}

    @property
    def active(self) -> bool:
        return self.rate > 0

    def lost(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.random(n) < self.rate

    def apply(self, pool: SequencePool, rng: np.random.Generator) -> SequencePool:
        lost = self.lost(pool.lengths.size, rng)
        out = pool.take(np.flatnonzero(~lost))
        _bump(out.stats, "dropped", lost.sum())
        return out


@dataclass(frozen=True)
class CoverageModel(ErrorModel):
    model: str = "fixed"                         # fixed | poisson | negative-binomial | lognormal
    mean: float = 1.0
    dispersion: float = 5.0                      # negative binomial shape k (smaller = more uneven)
    sigma: float = 0.0                           # lognormal: per-strand abundance LogNormal(-sigma^2/2, sigma), mean 1
    kind: ClassVar[str] = "coverage"

    @classmethod
    def from_json(cls, doc: dict) -> "CoverageModel":
        return cls(doc["model"], doc["mean"], doc["dispersion"], doc["sigma"])

    def parameters(self) -> dict:
        return {"model": self.model, "mean": self.mean, "dispersion": self.dispersion, "sigma": self.sigma}

    @property
    def active(self) -> bool:
        return not (self.model == "fixed" and self.mean == 1)

    def sample(self, mean: np.ndarray, rng: np.random.Generator, weighted: bool) -> np.ndarray:
        """Reads per strand for per-strand expected coverage ``mean`` (V4 draws for fixed/Poisson/NB)."""
        n = mean.size
        if self.model == "fixed":
            return np.full(n, int(self.mean)) if not weighted else rng.poisson(mean)
        if self.model == "poisson":
            return rng.poisson(mean)
        if self.model == "negative-binomial":
            k = self.dispersion
            return rng.poisson(rng.gamma(k, mean / k))
        s = self.sigma
        return rng.poisson(mean * rng.lognormal(-s * s / 2.0, s, n))

    def apply(self, pool: SequencePool, rng: np.random.Generator) -> SequencePool:
        reads = self.sample(np.full(pool.lengths.size, float(self.mean)), rng, weighted=False).astype(np.int64)
        out = pool.take(np.repeat(np.arange(pool.lengths.size), reads))
        _bump(out.stats, "zero_coverage", (reads == 0).sum())
        return out


@dataclass(frozen=True)
class QualityModel(ErrorModel):
    correct: int = 35
    error: int = 12
    informative: float = 0.0                     # P(an erroneous base gets ``error``)
    sd: float = 0.0                              # Gaussian variation of every score (Phred units)
    position_slope: float = 0.0                  # Phred units lost per base along the read as reported
    kind: ClassVar[str] = "quality"

    @classmethod
    def from_json(cls, doc: dict) -> "QualityModel":
        return cls(doc["correct"], doc["error"], doc["informative"], doc["sd"], doc["position_slope"])

    def parameters(self) -> dict:
        return {"correct": self.correct, "error": self.error, "informative": self.informative, "sd": self.sd,
                "position_slope": self.position_slope}

    @property
    def active(self) -> bool:
        return True

    @property
    def varies(self) -> bool:
        return bool(self.sd or self.position_slope)

    def base_scores(self, flat_err: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """V4 scores: ``correct`` everywhere; ``error`` on erroneous bases with probability ``informative``."""
        q = np.full(flat_err.size, self.correct, dtype=np.uint8)
        if self.informative:
            lowq = flat_err & (rng.random(flat_err.size) < self.informative)
            q[lowq] = self.error
        return q

    def vary(self, q: np.ndarray, lengths: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Quality variation: slope along each read plus Gaussian noise, rounded and clipped to 0..93."""
        if not self.varies or q.size == 0:
            return q
        qf = q.astype(np.float64)
        if self.position_slope:
            starts = np.concatenate([[0], np.cumsum(lengths)[:-1]])
            pos = np.arange(q.size) - np.repeat(starts, lengths)
            qf -= self.position_slope * pos
        if self.sd:
            qf += rng.normal(0.0, self.sd, q.size)
        return np.clip(np.rint(qf), 0, 93).astype(np.uint8)

    def apply(self, pool: SequencePool, rng: np.random.Generator) -> SequencePool:
        flat, lengths = pool.flat()
        q = self.vary(self.base_scores(np.zeros(flat.size, dtype=bool), rng), lengths, rng)
        out = pool.take(np.arange(lengths.size))
        out.quals = pad(q, lengths, fill=0)
        return out


_PER_BASE = (SubstitutionModel, InsertionModel, DeletionModel)


@dataclass(frozen=True)
class CompositeModel(ErrorModel):
    """Models applied in order. Adjacent substitution/insertion/deletion models form one joint per-base step."""

    models: tuple = ()
    kind: ClassVar[str] = "composite"

    def parameters(self) -> dict:
        return {"models": [m.describe() for m in self.models]}

    @property
    def active(self) -> bool:
        return any(m.active for m in self.models)

    def apply(self, pool: SequencePool, rng: np.random.Generator) -> SequencePool:
        i = 0
        while i < len(self.models):
            if isinstance(self.models[i], _PER_BASE):
                group: dict[str, Any] = {}
                while i < len(self.models) and isinstance(self.models[i], _PER_BASE):
                    group[self.models[i].kind] = self.models[i]
                    i += 1
                pool = apply_per_base(pool, rng, group.get("substitution"), group.get("insertion"), group.get("deletion"))
            else:
                pool = self.models[i].apply(pool, rng)
                i += 1
        return pool


# ================================================================================================================ kernel
def rate_arrays(base: np.ndarray, lengths: np.ndarray | None, sub: SubstitutionModel | None, ins: InsertionModel | None,
                dele: DeletionModel | None, profile: PositionProfile | None = None, hp: np.ndarray | None = None,
                hp_indel: float = 1.0, hp_sub: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-site (m, W) substitution, insertion and deletion probabilities, in the V4 order of float operations."""
    m, w = base.shape
    sub_r = np.full((m, w), sub.rate if sub else 0.0)
    ins_r = np.full((m, w), ins.rate if ins else 0.0)
    del_r = np.full((m, w), dele.rate if dele else 0.0)
    if hp is not None:
        ins_r = np.where(hp, ins_r * hp_indel, ins_r)
        del_r = np.where(hp, del_r * hp_indel, del_r)
        sub_r = np.where(hp, sub_r * hp_sub, sub_r)
    if profile is not None:
        for kind, arr in (("substitution", "s"), ("insertion", "i"), ("deletion", "d")):
            mult = profile.multipliers(kind, w, lengths)
            if mult is None:
                continue
            if arr == "s":
                sub_r = sub_r * mult
            elif arr == "i":
                ins_r = ins_r * mult
            else:
                del_r = del_r * mult
    if sub is not None:
        sub_r = sub.site_rates(base, sub_r)
    return sub_r, ins_r, del_r


def per_base_errors(base: np.ndarray, lengths: np.ndarray | None, rates: tuple, rng: np.random.Generator,
                    aux: np.random.Generator | None, *, sub: SubstitutionModel | None = None,
                    ins: InsertionModel | None = None, dele: DeletionModel | None = None,
                    burst_rate: float = 0.0, burst_max_len: int = 0) -> dict:
    """Joint substitution / insertion / deletion (+ V4 per-read deletion bursts) on a padded batch.

    Per position, one uniform u decides: deletion if u < d; insertion (of a random base *before* the position) if
    d <= u < d + i; substitution if d + i <= u < d + i + s. ``rng`` provides exactly the V4 draws; ``aux`` (needed only
    for a substitution matrix, insertion base weights or clustered deletions) provides the rest. ``lengths`` None means
    every row has the full width (the V4 case). Returns flat codes, lengths, per-base error flags and event counts.
    """
    m, w = base.shape
    sub_r, ins_r, del_r = rates
    u = rng.random((m, w))
    is_del = u < del_r
    is_ins = (u >= del_r) & (u < del_r + ins_r)
    is_sub = (u >= del_r + ins_r) & (u < del_r + ins_r + sub_r)
    valid = None
    if lengths is not None:
        valid = np.arange(w)[None, :] < lengths[:, None]
        is_del &= valid
        is_ins &= valid
        is_sub &= valid
    events = is_del.copy() if dele is not None and dele.clustered else None
    bursts = 0
    if burst_rate:
        burst = np.flatnonzero(rng.random(m) < burst_rate)
        if burst.size:
            blen = rng.integers(1, burst_max_len + 1, burst.size)
            high = w if lengths is None else np.maximum(lengths[burst], 1)
            bpos = rng.integers(0, high, burst.size)
            for r, s, ln in zip(burst.tolist(), bpos.tolist(), blen.tolist()):
                is_del[r, s:s + ln] = True
            bursts = int(burst.size)
    if events is not None and events.any():
        rows, cols = np.nonzero(events)
        runs = aux.geometric(1.0 / dele.run_mean, rows.size)
        starts = np.concatenate([[0], np.cumsum(runs)[:-1]])
        rr = np.repeat(rows, runs)
        cc = np.repeat(cols, runs) + (np.arange(int(runs.sum())) - np.repeat(starts, runs))
        ok = cc < w
        is_del[rr[ok], cc[ok]] = True
    if valid is not None:
        is_del &= valid
    subbed = np.where(is_sub, (base + rng.integers(1, 4, (m, w))) % 4, base).astype(np.uint8)
    if sub is not None and sub.matrix is not None and is_sub.any():
        idx = np.nonzero(is_sub)
        subbed[idx] = sample_targets(base[idx], sub.cm, aux)
    ins_base = rng.integers(0, 4, (m, w)).astype(np.uint8)
    if ins is not None and ins.base_weights is not None and is_ins.any():
        idx = np.nonzero(is_ins)
        ins_base[idx] = aux.choice(4, size=idx[0].size, p=np.asarray(ins.base_weights, dtype=np.float64)).astype(np.uint8)
    slots = np.stack([ins_base, subbed], axis=2).reshape(m, 2 * w)
    keep_base = ~is_del if valid is None else (~is_del & valid)
    keep = np.stack([is_ins, keep_base], axis=2).reshape(m, 2 * w)
    err = np.stack([is_ins, is_sub], axis=2).reshape(m, 2 * w)
    return {"codes": slots[keep], "lengths": keep.sum(axis=1).astype(np.int64), "err": err[keep],
            "substitutions": int(is_sub.sum()), "insertions": int(is_ins.sum()), "deletions": int(is_del.sum()),
            "bursts": bursts}


def apply_per_base(pool: SequencePool, rng: np.random.Generator, sub: SubstitutionModel | None,
                   ins: InsertionModel | None, dele: DeletionModel | None) -> SequencePool:
    """Standalone joint per-base step on a pool (one generator for everything)."""
    profile = next((m.profile for m in (sub, ins, dele) if m is not None and m.profile is not None), None)
    rates = rate_arrays(pool.codes, pool.lengths, sub, ins, dele, profile)
    res = per_base_errors(pool.codes, pool.lengths, rates, rng, rng, sub=sub, ins=ins, dele=dele)
    out = SequencePool.from_flat(res["codes"], res["lengths"], pool.source.copy())
    out.stats = dict(pool.stats)
    for k in ("substitutions", "insertions", "deletions"):
        _bump(out.stats, k, res[k])
    return out


def reverse_complement_flat(flat: np.ndarray, q: np.ndarray, lengths: np.ndarray, rows: np.ndarray) -> None:
    """In place: reverse-complement the reads ``rows`` of a flat batch (V4 loop)."""
    offs = np.concatenate([[0], np.cumsum(lengths)])
    for r in rows.tolist():
        a, b = offs[r], offs[r + 1]
        flat[a:b] = _RC[flat[a:b][::-1]]
        q[a:b] = q[a:b][::-1]


def lognormal_weights(sigma: float, n: int, rng: np.random.Generator) -> np.ndarray:
    """Per-strand weights LogNormal(-sigma^2/2, sigma): mean 1."""
    return rng.lognormal(-sigma * sigma / 2.0, sigma, n)


def truncation_lengths(lengths: np.ndarray, rate: float, min_fraction: float, rng: np.random.Generator) -> np.ndarray:
    """New lengths: with probability ``rate`` a sequence keeps a uniform length in [ceil(min_fraction*n), n-1]."""
    t = rng.random(lengths.size) < rate
    lo = np.ceil(min_fraction * lengths).astype(np.int64)
    cut = lo + np.floor(rng.random(lengths.size) * (lengths - lo)).astype(np.int64)
    return np.where(t & (lengths > lo), cut, lengths)


__all__ = ["ErrorModel", "SequencePool", "SubstitutionModel", "InsertionModel", "DeletionModel", "DropoutModel",
           "CoverageModel", "QualityModel", "CompositeModel", "PositionProfile", "per_base_errors", "rate_arrays",
           "apply_per_base", "pad", "PAD", "sample_targets", "cumulative_matrix"]
