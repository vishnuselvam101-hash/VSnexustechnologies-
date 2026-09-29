"""Deterministic synthetic DNA channel (synthesis → storage → sequencing), in software.

Model, applied independently to every strand in a pool:

1. **Dropout** (molecule loss): each strand is lost with probability
   ``dropout_rate``. With ``exact_dropout`` exactly ``round(rate·N)`` strands are
   removed instead, for controlled experiments.
2. **Coverage** (sequencing depth): each surviving strand yields ``coverage``
   reads (``fixed``, integer) or Poisson(``coverage``) reads (``poisson``, which
   can yield 0 reads, i.e. extra read loss).
3. Per read, per base, in this order: **substitution** (the base changes to one of
   the three other bases, uniformly), then **deletion**, then **insertion** of one
   uniform random base after the position. Rates are independent.
4. **Burst** (per read, probability ``burst_rate``): one contiguous run of length
   U[min, max], applied before the per-base errors. It is ``substitution`` (every
   base replaced), ``deletion`` (run removed) or ``mixed`` (each burst base
   substituted or deleted with probability 1/2).
5. **Orientation**: each read is reverse-complemented with probability
   ``reverse_complement_rate``.
6. **Order**: reads are shuffled if ``shuffle``.

All randomness comes from ``numpy.random.Generator(PCG64(seed))``, so equal inputs,
config and seed give identical output. The report counts events that
*actually happened*, not configured rates.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .dna.mapping import INVALID, _ASCII_TO_CODE, _CODE_TO_ASCII, _COMPLEMENT
from .errors import ConfigurationError, InvalidDNAError

BLOCK_BASES = 1 << 20


@dataclass(frozen=True)
class ChannelConfig:
    seed: int = 0
    substitution_rate: float = 0.0
    insertion_rate: float = 0.0
    deletion_rate: float = 0.0
    dropout_rate: float = 0.0
    exact_dropout: bool = False
    burst_rate: float = 0.0
    burst_length_min: int = 2
    burst_length_max: int = 8
    burst_kind: str = "substitution"
    coverage: float = 1.0
    coverage_model: str = "fixed"
    reverse_complement_rate: float = 0.0
    shuffle: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            raise ConfigurationError("seed must be a non-negative integer")
        for name in ("substitution_rate", "insertion_rate", "deletion_rate", "dropout_rate", "burst_rate", "reverse_complement_rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                raise ConfigurationError(f"{name} must be a probability in [0, 1]")
        if not 1 <= self.burst_length_min <= self.burst_length_max <= 10_000:
            raise ConfigurationError("require 1 <= burst_length_min <= burst_length_max <= 10000")
        if self.burst_kind not in ("substitution", "deletion", "mixed"):
            raise ConfigurationError("burst_kind must be substitution, deletion or mixed")
        if self.coverage_model not in ("fixed", "poisson"):
            raise ConfigurationError("coverage_model must be fixed or poisson")
        if self.coverage_model == "fixed" and (float(self.coverage) != int(self.coverage) or not 1 <= self.coverage <= 1000):
            raise ConfigurationError("fixed coverage must be an integer in 1..1000")
        if self.coverage_model == "poisson" and not 0 < self.coverage <= 1000:
            raise ConfigurationError("poisson coverage must be in (0, 1000]")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _codes(sequence: str) -> np.ndarray:
    raw = np.frombuffer(sequence.encode("ascii", errors="replace"), dtype=np.uint8)
    codes = _ASCII_TO_CODE[raw]
    if (codes == 255).any():
        raise InvalidDNAError("channel input contains non-nucleotide symbols")
    return codes


def simulate(sequences: list[str], config: ChannelConfig, *, record_events: bool = False) -> tuple[list[str], dict[str, Any]]:
    """Pass a strand pool through the channel. Returns (reads, report).

    With ``record_events`` the report gains an ``events`` list with one entry
    per injected event: dropout (strand index), burst, substitution
    (position, original and replacement base), deletion, insertion and
    reverse complement. Positions are 0-based offsets into the read *before*
    per-base errors are applied. ``read`` is the read's index before
    shuffling, and ``output_read`` is its index in the output file.
    """
    events: list[dict[str, Any]] = []
    rng = np.random.default_rng(config.seed)
    n = len(sequences)
    # 1. dropout
    if config.exact_dropout:
        k = int(round(config.dropout_rate * n))
        dropped = np.zeros(n, dtype=bool)
        dropped[rng.choice(n, size=k, replace=False)] = True
    else:
        dropped = rng.random(n) < config.dropout_rate
    survivors = np.flatnonzero(~dropped)
    if record_events:
        events.extend({"type": "dropout", "strand": int(i)} for i in np.flatnonzero(dropped).tolist())
    # 2. coverage
    if config.coverage_model == "fixed":
        copies = np.full(survivors.size, int(config.coverage), dtype=np.int64)
    else:
        copies = rng.poisson(config.coverage, survivors.size).astype(np.int64)
    source = np.repeat(survivors, copies)
    reads_codes = [_codes(sequences[i]).copy() for i in source]
    counts = {"substitutions": 0, "insertions": 0, "deletions": 0, "bursts": 0, "burst_bases": 0}
    # 4. bursts (before per-base errors)
    if config.burst_rate and reads_codes:
        hit = np.flatnonzero(rng.random(len(reads_codes)) < config.burst_rate)
        for r in hit.tolist():
            seq = reads_codes[r]
            if seq.size == 0:
                continue
            length = int(rng.integers(config.burst_length_min, config.burst_length_max + 1))
            length = min(length, seq.size)
            start = int(rng.integers(0, seq.size - length + 1))
            counts["bursts"] += 1
            counts["burst_bases"] += length
            if record_events:
                events.append({"type": "burst", "read": r, "source_strand": int(source[r]), "start": start, "length": length,
                               "kind": config.burst_kind})
            if config.burst_kind == "substitution":
                seq[start:start + length] = (seq[start:start + length] + rng.integers(1, 4, length)) % 4
                counts["substitutions"] += length
            elif config.burst_kind == "deletion":
                reads_codes[r] = np.delete(seq, np.arange(start, start + length))
                counts["deletions"] += length
            else:
                kinds = rng.random(length) < 0.5
                region = seq[start:start + length].copy()
                region[~kinds] = (region[~kinds] + rng.integers(1, 4, int((~kinds).sum()))) % 4
                seq[start:start + length] = region
                reads_codes[r] = np.delete(seq, start + np.flatnonzero(kinds))
                counts["substitutions"] += int((~kinds).sum())
                counts["deletions"] += int(kinds.sum())
    # 3. per-base substitution → deletion → insertion, vectorized within blocks of reads (bounded memory).
    #    Blocks are consecutive reads totalling at most BLOCK_BASES bases; randomness is drawn block by block
    #    in a fixed order, so results depend only on (input, config, seed).
    bases = "ACGTN"
    lengths_all = np.array([c.size for c in reads_codes], dtype=np.int64)
    total = int(lengths_all.sum())
    ends = np.cumsum(lengths_all)
    pieces: list[np.ndarray] = []
    first = 0
    while first < len(reads_codes):
        base = int(ends[first - 1]) if first else 0
        last = max(first + 1, int(np.searchsorted(ends, base + BLOCK_BASES, side="right")))
        block = reads_codes[first:last]
        lengths = lengths_all[first:last]
        flat = np.concatenate(block) if block else np.zeros(0, dtype=np.uint8)
        size = flat.size
        read_id = np.repeat(np.arange(len(block)), lengths)
        starts = np.cumsum(lengths) - lengths
        if config.substitution_rate:
            sub = rng.random(size) < config.substitution_rate
            flat = flat.copy()
            before = flat[sub].copy()
            flat[sub] = (flat[sub] + rng.integers(1, 4, int(sub.sum()))) % 4
            counts["substitutions"] += int(sub.sum())
            if record_events:
                for pos, old, new in zip(np.flatnonzero(sub).tolist(), before.tolist(), flat[sub].tolist()):
                    r = int(read_id[pos])
                    events.append({"type": "substitution", "read": first + r, "source_strand": int(source[first + r]),
                                   "position": pos - int(starts[r]), "original": bases[old], "replacement": bases[new]})
        keep = rng.random(size) >= config.deletion_rate if config.deletion_rate else np.ones(size, dtype=bool)
        counts["deletions"] += int((~keep).sum())
        ins = rng.random(size) < config.insertion_rate if config.insertion_rate else np.zeros(size, dtype=bool)
        ins_bases = rng.integers(0, 4, size).astype(np.uint8) if config.insertion_rate else np.zeros(size, dtype=np.uint8)
        counts["insertions"] += int(ins.sum())
        if record_events:
            for pos in np.flatnonzero(~keep).tolist():
                r = int(read_id[pos])
                events.append({"type": "deletion", "read": first + r, "source_strand": int(source[first + r]),
                               "position": pos - int(starts[r]), "original": bases[int(flat[pos])]})
            for pos in np.flatnonzero(ins).tolist():
                r = int(read_id[pos])
                events.append({"type": "insertion", "read": first + r, "source_strand": int(source[first + r]),
                               "after_position": pos - int(starts[r]), "inserted": bases[int(ins_bases[pos])]})
        pairs = np.stack([np.where(keep, flat, 255), np.where(ins, ins_bases, 255)], axis=1).reshape(-1)
        pair_ids = np.repeat(read_id, 2)
        mask = pairs != 255
        out_codes, out_ids = pairs[mask].astype(np.uint8), pair_ids[mask]
        out_lengths = np.bincount(out_ids, minlength=len(block))
        pieces.extend(np.split(out_codes, np.cumsum(out_lengths)[:-1]))
        first = last
    # 5. orientation
    rc = rng.random(len(pieces)) < config.reverse_complement_rate if config.reverse_complement_rate else np.zeros(len(pieces), dtype=bool)
    reads = []
    for piece, flip in zip(pieces, rc):
        if flip:
            piece = _COMPLEMENT[piece[::-1]]
        reads.append(_CODE_TO_ASCII[piece].tobytes().decode("ascii"))
    # 6. order
    order = rng.permutation(len(reads)) if config.shuffle else np.arange(len(reads))
    if record_events:
        events.extend({"type": "reverse_complement", "read": int(r)} for r in np.flatnonzero(rc).tolist())
        position = np.empty(len(order), dtype=np.int64)
        position[order] = np.arange(len(order))
        for event in events:
            if "read" in event:
                event["output_read"] = int(position[event["read"]])
    reads = [reads[i] for i in order]
    changed = sum(1 for r, i in zip(reads, source[order]) if r != sequences[i]) if reads else 0
    report = {
        "config": config.to_dict(),
        "strands_in": n,
        "strands_dropped": int(dropped.sum()),
        "strands_surviving": int(survivors.size),
        "strands_with_zero_reads": int((copies == 0).sum()),
        "reads_out": len(reads),
        "reads_reverse_complemented": int(rc.sum()),
        "reads_altered": changed,
        "bases_in_reads_before_errors": int(total),
        **counts,
        "observed_substitution_rate": counts["substitutions"] / total if total else 0.0,
        "observed_insertion_rate": counts["insertions"] / total if total else 0.0,
        "observed_deletion_rate": counts["deletions"] / total if total else 0.0,
        "observed_dropout_rate": float(dropped.mean()) if n else 0.0,
        "dropped_strand_indices_sha256": hashlib.sha256(np.flatnonzero(dropped).astype(np.int64).tobytes()).hexdigest(),
    }
    if record_events:
        report["events"] = events
    return reads, report
