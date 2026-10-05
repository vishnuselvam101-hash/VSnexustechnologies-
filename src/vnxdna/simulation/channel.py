"""Configurable stochastic DNA storage/sequencing channel (SIMULATION — not fitted to any platform).

Models (each independently configurable; all rates are per base unless noted):

* **dropout**           a strand is lost entirely (per strand), plus GC-dependent coverage bias
* **coverage**          reads per surviving strand: fixed, Poisson, or negative binomial (uneven coverage)
* **duplication**       a read is emitted again as an identical copy (per read; PCR-duplicate-like)
* **substitution**      base replaced by one of the other three, uniformly
* **insertion**         a random base inserted before a position
* **deletion**          a base removed
* **homopolymer**       rates multiplied inside homopolymer runs of length ≥ ``min_run``
* **burst**             with probability ``burst_rate`` per read, a contiguous run of 1…burst_max_len bases is deleted
* **N calls**           base reported as N
* **reverse complement** a read reported on the opposite strand
* **qualities**         Phred scores, optionally informative (low scores on erroneous bases)

Determinism: strands are processed in fixed batches of ``BATCH`` strands; batch b
uses ``numpy.random.default_rng([seed, b])``. The output is therefore a pure
function of (input strands, configuration, seed), independent of the number of
worker processes. Same input + configuration + seed ⇒ byte-identical reads.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from vnxdna.dnaenc.strandio import iter_batches
from vnxdna.core.errors import VNXConfigurationError, VNXOutputError
from vnxdna.core.util import atomic_output

BATCH = 1024
_ACGTN = np.frombuffer(b"ACGTN", dtype=np.uint8)
_RC = np.array([3, 2, 1, 0, 4], dtype=np.uint8)


@dataclass
class ChannelConfig:
    substitution_rate: float = 0.0
    insertion_rate: float = 0.0
    deletion_rate: float = 0.0
    dropout_rate: float = 0.0
    coverage: float = 1.0
    coverage_model: str = "fixed"            # fixed | poisson | negative-binomial
    coverage_dispersion: float = 5.0         # negative-binomial shape k (smaller = more uneven)
    duplication_rate: float = 0.0
    homopolymer_min_run: int = 3
    homopolymer_indel_multiplier: float = 1.0
    homopolymer_substitution_multiplier: float = 1.0
    gc_bias_strength: float = 0.0            # coverage weight exp(−s·((gc − optimum)/0.1)^2)
    gc_bias_optimum: float = 0.5
    burst_rate: float = 0.0
    burst_max_len: int = 0
    n_rate: float = 0.0
    reverse_complement_rate: float = 0.0
    quality_correct: int = 35
    quality_error: int = 12
    quality_informative: float = 0.0          # probability that an erroneous base gets the low score
    shuffle_window: int = 0                   # 0 = keep strand order; otherwise seeded shuffle within windows of reads
    seed: int = 12345

    def validate(self) -> "ChannelConfig":
        try:
            return self._validate()
        except TypeError as error:   # wrong types (e.g. a string where a number is expected) are configuration errors
            raise VNXConfigurationError(f"invalid channel configuration value: {error}") from None

    def _validate(self) -> "ChannelConfig":
        for name in ("homopolymer_min_run", "burst_max_len", "quality_correct", "quality_error", "shuffle_window", "seed"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                raise VNXConfigurationError(f"{name} must be an integer", details={name: v})
        for name in ("coverage", "coverage_dispersion", "homopolymer_indel_multiplier", "homopolymer_substitution_multiplier",
                     "gc_bias_strength", "gc_bias_optimum"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v:
                raise VNXConfigurationError(f"{name} must be a number", details={name: v})
        if not isinstance(self.coverage_model, str):
            raise VNXConfigurationError("coverage_model must be a string")
        for name in ("substitution_rate", "insertion_rate", "deletion_rate", "dropout_rate", "duplication_rate", "burst_rate",
                     "n_rate", "reverse_complement_rate", "quality_informative"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.0 <= v <= 1.0:
                raise VNXConfigurationError(f"{name} must be a probability in [0, 1]", details={name: v})
        if self.substitution_rate + self.insertion_rate + self.deletion_rate > 0.5:
            raise VNXConfigurationError("substitution + insertion + deletion rates must not exceed 0.5")
        if not isinstance(self.coverage, (int, float)) or not 0 <= self.coverage <= 10_000:
            raise VNXConfigurationError("coverage must be in [0, 10000]")
        if self.coverage_model not in ("fixed", "poisson", "negative-binomial"):
            raise VNXConfigurationError("coverage_model must be fixed, poisson or negative-binomial")
        if self.coverage_model == "fixed" and float(self.coverage) != int(self.coverage):
            raise VNXConfigurationError("fixed coverage must be an integer")
        if self.coverage_dispersion <= 0:
            raise VNXConfigurationError("coverage_dispersion must be > 0")
        if self.homopolymer_min_run < 2:
            raise VNXConfigurationError("homopolymer_min_run must be >= 2")
        for name in ("homopolymer_indel_multiplier", "homopolymer_substitution_multiplier"):
            if not 0 <= getattr(self, name) <= 100:
                raise VNXConfigurationError(f"{name} must be in [0, 100]")
        if self.gc_bias_strength < 0 or not 0 <= self.gc_bias_optimum <= 1:
            raise VNXConfigurationError("invalid GC bias parameters")
        if not 0 <= self.burst_max_len <= 1000 or (self.burst_rate and not self.burst_max_len):
            raise VNXConfigurationError("burst_max_len must be in 1..1000 when burst_rate > 0")
        if not 0 <= self.quality_error <= 93 or not 0 <= self.quality_correct <= 93:
            raise VNXConfigurationError("quality scores must be in 0..93")
        if not isinstance(self.seed, int) or not 0 <= self.seed < 2 ** 63:
            raise VNXConfigurationError("seed must be a non-negative 63-bit integer")
        if self.shuffle_window < 0:
            raise VNXConfigurationError("shuffle_window must be >= 0")
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ChannelConfig":
        if not isinstance(data, dict):
            raise VNXConfigurationError("configuration must be a JSON object")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise VNXConfigurationError(f"unknown channel configuration keys: {sorted(unknown)}")
        return cls(**data).validate()

    @classmethod
    def load(cls, path: str | os.PathLike) -> "ChannelConfig":
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError) as error:
            raise VNXConfigurationError(f"cannot read channel configuration {path}: {error}") from None
        if not isinstance(data, dict):
            raise VNXConfigurationError("channel configuration must be a JSON object")
        return cls.from_dict(data)


def _homopolymer_mask(codes: np.ndarray, min_run: int) -> np.ndarray:
    """(n, L) → bool mask of positions inside runs of ≥ min_run equal bases."""
    n, L = codes.shape
    eq = np.zeros((n, L + 1), dtype=bool)
    eq[:, 1:L] = codes[:, 1:] == codes[:, :-1]
    # run length ending at each position (forward) and starting at each position (backward)
    fwd = np.ones((n, L), dtype=np.int32)
    for j in range(1, L):
        fwd[:, j] = np.where(eq[:, j], fwd[:, j - 1] + 1, 1)
    bwd = np.ones((n, L), dtype=np.int32)
    for j in range(L - 2, -1, -1):
        bwd[:, j] = np.where(eq[:, j + 1], bwd[:, j + 1] + 1, 1)
    return fwd + bwd - 1 >= min_run


def simulate_batch(codes: np.ndarray, cfg: ChannelConfig, batch_index: int, *, truth: bool = False) -> dict:
    """Simulate reads for one batch of equal-length strands (n, L). Pure function of (codes, cfg, batch_index).

    ``truth`` (V7 diagnostics, opt-in): also return the ground truth of every read, ``source`` (index of its strand in
    the batch) and ``reverse_complement`` (bool). It draws no extra random numbers, so the reads are identical."""
    rng = np.random.default_rng([cfg.seed, batch_index])
    n, L = codes.shape
    stats = {"strands": n, "dropped": 0, "zero_coverage": 0, "reads": 0, "substitutions": 0, "insertions": 0, "deletions": 0,
             "bursts": 0, "n_calls": 0, "reverse_complement": 0, "duplicates": 0, "bases": 0}
    drop = rng.random(n) < cfg.dropout_rate
    gc = ((codes == 1) | (codes == 2)).mean(axis=1)
    weight = np.exp(-cfg.gc_bias_strength * ((gc - cfg.gc_bias_optimum) / 0.1) ** 2) if cfg.gc_bias_strength else np.ones(n)
    mean = cfg.coverage * weight
    if cfg.coverage_model == "fixed":
        reads = np.full(n, int(cfg.coverage)) if not cfg.gc_bias_strength else rng.poisson(mean)
    elif cfg.coverage_model == "poisson":
        reads = rng.poisson(mean)
    else:
        k = cfg.coverage_dispersion
        reads = rng.poisson(rng.gamma(k, mean / k))
    reads = np.where(drop, 0, reads).astype(np.int64)
    stats["dropped"] = int(np.count_nonzero(drop))
    stats["zero_coverage"] = int(((reads == 0) & ~drop).sum())
    src = np.repeat(np.arange(n), reads)
    m = src.size
    if m == 0:
        out = {"codes": np.zeros(0, dtype=np.uint8), "lengths": np.zeros(0, dtype=np.int64),
               "quals": np.zeros(0, dtype=np.uint8), "stats": stats}
        if truth:
            out.update(source=np.zeros(0, dtype=np.int64), reverse_complement=np.zeros(0, dtype=bool))
        return out
    base = codes[src]
    sub_r = np.full((m, L), cfg.substitution_rate)
    ins_r = np.full((m, L), cfg.insertion_rate)
    del_r = np.full((m, L), cfg.deletion_rate)
    if cfg.homopolymer_indel_multiplier != 1.0 or cfg.homopolymer_substitution_multiplier != 1.0:
        hp = _homopolymer_mask(codes, cfg.homopolymer_min_run)[src]
        ins_r = np.where(hp, ins_r * cfg.homopolymer_indel_multiplier, ins_r)
        del_r = np.where(hp, del_r * cfg.homopolymer_indel_multiplier, del_r)
        sub_r = np.where(hp, sub_r * cfg.homopolymer_substitution_multiplier, sub_r)
    u = rng.random((m, L))
    is_del = u < del_r
    is_ins = (u >= del_r) & (u < del_r + ins_r)
    is_sub = (u >= del_r + ins_r) & (u < del_r + ins_r + sub_r)
    if cfg.burst_rate:
        burst = np.flatnonzero(rng.random(m) < cfg.burst_rate)
        if burst.size:
            blen = rng.integers(1, cfg.burst_max_len + 1, burst.size)
            bpos = rng.integers(0, L, burst.size)
            for r, s, ln in zip(burst.tolist(), bpos.tolist(), blen.tolist()):
                is_del[r, s:s + ln] = True
            stats["bursts"] = int(burst.size)
    subbed = np.where(is_sub, (base + rng.integers(1, 4, (m, L))) % 4, base).astype(np.uint8)
    ins_base = rng.integers(0, 4, (m, L)).astype(np.uint8)
    slots = np.stack([ins_base, subbed], axis=2).reshape(m, 2 * L)
    keep = np.stack([is_ins, ~is_del], axis=2).reshape(m, 2 * L)
    err = np.stack([is_ins, is_sub], axis=2).reshape(m, 2 * L)
    lengths = keep.sum(axis=1).astype(np.int64)
    flat = slots[keep]
    flat_err = err[keep]
    if cfg.n_rate:
        nmask = rng.random(flat.size) < cfg.n_rate
        flat = np.where(nmask, 4, flat).astype(np.uint8)
        flat_err |= nmask
        stats["n_calls"] = int(nmask.sum())
    q = np.full(flat.size, cfg.quality_correct, dtype=np.uint8)
    if cfg.quality_informative:
        lowq = flat_err & (rng.random(flat.size) < cfg.quality_informative)
        q[lowq] = cfg.quality_error
    rc = rng.random(m) < cfg.reverse_complement_rate
    if rc.any():
        offs = np.concatenate([[0], np.cumsum(lengths)])
        for r in np.flatnonzero(rc).tolist():
            a, b = offs[r], offs[r + 1]
            flat[a:b] = _RC[flat[a:b][::-1]]
            q[a:b] = q[a:b][::-1]
        stats["reverse_complement"] = int(rc.sum())
    read_order = None
    stats["substitutions"] = int(is_sub.sum())
    stats["insertions"] = int(is_ins.sum())
    stats["deletions"] = int(is_del.sum())
    if cfg.duplication_rate:
        dup = rng.random(m) < cfg.duplication_rate
        if dup.any():
            offs = np.concatenate([[0], np.cumsum(lengths)])
            order = np.repeat(np.arange(m), np.where(dup, 2, 1))
            pieces = [flat[offs[r]:offs[r + 1]] for r in order]
            qp = [q[offs[r]:offs[r + 1]] for r in order]
            flat = np.concatenate(pieces)
            q = np.concatenate(qp)
            lengths = lengths[order]
            read_order = order
            stats["duplicates"] = int(dup.sum())
    stats["reads"] = int(lengths.size)
    stats["bases"] = int(flat.size)
    out = {"codes": flat, "lengths": lengths, "quals": q, "stats": stats}
    if truth:
        sel = np.arange(m) if read_order is None else read_order
        out.update(source=src[sel].astype(np.int64), reverse_complement=rc[sel].copy())
    return out


def _serialize(res: dict, fmt: str, first: int) -> bytes:
    return b"".join(_records(res, fmt, first))


def _records(res: dict, fmt: str, first: int) -> list[bytes]:
    codes, lengths, quals = res["codes"], res["lengths"], res["quals"]
    ascii_ = _ACGTN[codes].tobytes()
    qasc = (quals + 33).astype(np.uint8).tobytes()
    out = []
    pos = 0
    for i, ln in enumerate(lengths.tolist()):
        seq = ascii_[pos:pos + ln]
        if fmt == "fastq":
            out.append(b"@r%d\n%s\n+\n%s\n" % (first + i, seq, qasc[pos:pos + ln]))
        else:
            out.append(b">r%d\n%s\n" % (first + i, seq))
        pos += ln
    return out


def _strand_batches(path: str | os.PathLike):
    """Yield (batch index, (n, L) codes) of BATCH strands; strands must have equal length within a batch."""
    buf: list[np.ndarray] = []
    index = 0
    for batch in iter_batches(path, BATCH):
        offs = batch.offsets
        for i in range(batch.count):
            buf.append(batch.codes[offs[i]:offs[i + 1]])
            if len(buf) == BATCH:
                yield index, _stack(buf)
                index += 1
                buf = []
    if buf:
        yield index, _stack(buf)


def _stack(buf: list[np.ndarray]) -> np.ndarray:
    lengths = {b.size for b in buf}
    if len(lengths) != 1:
        raise VNXConfigurationError("the channel simulator needs strands of equal length")
    arr = np.stack(buf)
    if (arr > 3).any():
        raise VNXConfigurationError("strands must contain only A, C, G, T")
    return arr


_C: dict = {}


def _c_init(cfg: dict) -> None:
    _C["cfg"] = ChannelConfig.from_dict(cfg)


def _c_task(args):
    index, codes = args
    return index, simulate_batch(codes, _C["cfg"], index)


def simulate_file(strands: str | os.PathLike, output: str | os.PathLike, cfg: ChannelConfig, *, fmt: str | None = None,
                  workers: int = 1, overwrite: bool = False, progress=None) -> dict:
    cfg.validate()
    t0 = time.perf_counter()
    fmt = fmt or ("fasta" if str(output).endswith((".fa", ".fasta")) else "fastq")
    if fmt not in ("fasta", "fastq"):
        raise VNXOutputError("channel output must be fasta or fastq")
    totals: dict = {}
    count = 0
    rng_shuffle = np.random.default_rng([cfg.seed, 0x5F5F])
    with atomic_output(output, overwrite=overwrite, mode=0o644) as tmp:
        with open(tmp, "wb", buffering=1 << 20) as out:
            window: list[bytes] = []

            def flush(force: bool = False) -> None:
                nonlocal window
                if cfg.shuffle_window and (len(window) >= cfg.shuffle_window or force) and window:
                    for i in rng_shuffle.permutation(len(window)).tolist():
                        out.write(window[i])
                    window = []

            def take(res: dict) -> None:
                nonlocal count
                for k, v in res["stats"].items():
                    totals[k] = totals.get(k, 0) + v
                if cfg.shuffle_window:
                    window.extend(_records(res, fmt, count))
                    flush()
                else:
                    out.write(_serialize(res, fmt, count))
                count += int(res["lengths"].size)
                if progress:
                    progress({"stage": "channel", "reads": count, "elapsed": time.perf_counter() - t0})

            if workers == 1:
                for index, codes in _strand_batches(strands):
                    take(simulate_batch(codes, cfg, index))
            else:
                with ProcessPoolExecutor(max_workers=workers, initializer=_c_init, initargs=(cfg.to_dict(),)) as pool:
                    q: deque = deque()
                    for item in _strand_batches(strands):
                        q.append(pool.submit(_c_task, item))
                        if len(q) >= 2 * workers:
                            take(q.popleft().result()[1])
                    while q:
                        take(q.popleft().result()[1])
            flush(force=True)
    seconds = time.perf_counter() - t0
    totals.update({"output": str(output), "format": fmt, "seconds": seconds, "config": cfg.to_dict(), "workers": workers,
                   "reads_per_second": round(count / seconds, 1) if seconds else None,
                   "bases_per_second": round(totals.get("bases", 0) / seconds, 1) if seconds else None,
                   "simulation": "SIMULATED channel; parameters are stress settings, not fitted to a sequencing platform"})
    return totals
