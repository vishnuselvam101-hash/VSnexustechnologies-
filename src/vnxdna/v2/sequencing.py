"""Simulated DNA storage and sequencing channel (software only).

Nothing here is a model fitted to a real synthesis or sequencing platform. It
is a configurable, reproducible stress generator for the decoder. Results
obtained with it are **software simulation** results, never evidence of
physical DNA-storage performance.

Model, per strand of the input pool (a designed strand = one molecule species):

1. **Dropout**: the species is lost with probability ``dropout_rate``.
2. **Abundance and coverage**: the number of reads is ``coverage`` (``fixed``),
   Poisson(``coverage``) (``poisson``), or Poisson(``coverage × w``) with a
   per-species weight ``w ~ LogNormal(−σ²/2, σ)`` of mean 1 (``lognormal``:
   uneven abundance, as after PCR). Species can end up with zero reads.
3. Per read, **synthesis errors**: substitution/insertion/deletion per base.
   Each read comes from its own molecule, so synthesis errors are independent
   between reads. They are not reflected in quality scores.
4. Per read, **sequencing errors**: substitution, then deletion, then
   insertion of a uniform random base after the position, per base.
5. **Burst errors** (VNX-DNA 3): with probability ``burst_rate`` a read
   carries one burst: a contiguous run of Geometric(1/``burst_length_mean``)
   bases (mean ``burst_length_mean``, at least 1) at a uniform position that is
   substituted (each base by a different base), deleted, or preceded by an
   inserted run of random bases (``burst_kind`` substitution | deletion |
   insertion | mixed, mixed choosing uniformly per burst). Bursts model
   localised molecule damage; they are not reflected in quality scores.
   A substitution or deletion burst is clipped to the read length.
5b. **Truncation**: with probability ``truncation_rate`` a read keeps only a
   uniform prefix of 50–99 % of its length (never longer than the read).
6. **Unreadable calls**: each base becomes ``N`` with probability ``n_rate``.
7. **Orientation**: reverse-complemented with probability ``reverse_complement_rate``.
8. **Duplication**: each read gets an identical extra copy (a PCR/optical
   duplicate, errors included) with probability ``duplication_rate``.
9. **Invalid reads and contamination**: ``invalid_read_rate`` × reads extra
   junk reads (random length 20–400 with random bases including ``N``), and
   ``contamination_rate`` × reads foreign reads (random A/C/G/T sequences of
   the strand length, from no archive).
10. **Order**: a uniform random permutation of all reads (``shuffle``),
    computed out of core: every read goes to a random bucket file, then each
    bucket is permuted in memory.

Quality scores (FASTQ): ``quality_model="informative"`` gives correct bases
Phred 30–40 and gives bases touched by a *sequencing* substitution or
insertion Phred 2–20 with probability ``quality_informativeness``. ``flat``
gives Phred 30 everywhere.

All randomness comes from ``numpy.random.Generator(PCG64)`` seeded with
``(seed, batch index)``, so equal inputs, configuration and seed give
byte-identical output. The report counts events that were applied to the
simulated molecules (an error on a base that is later deleted or truncated
away still counts; duplicates copy their original's errors and are not
counted again).

Batches: at most ``batch_strands`` (8192) strands per batch, fewer when
coverage is high, so that one batch holds at most about ``BATCH_TARGET_BASES``
simulated bases (VNX-DNA 3; 2.0 used 8192 strands whatever the coverage, so
memory grew linearly with coverage). Channels with ``mean strand_nt ×
max(coverage, 1) × (1 + duplication_rate) × (1 + invalid_read_rate +
contamination_rate) ≤ 4096`` (for example 276-nt strands at coverage ≤ 14)
keep 8192-strand batches and therefore produce exactly the 2.0 output for the
same seed.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..dna.mapping import _COMPLEMENT
from ..errors import ConfigurationError, InvalidInputError, OutputError
from .paths import atomic_write_text, check_output_file, check_temp_dir
from .strandio import ReadBatch, StrandWriter, format_for_output, iter_batches

BUCKET_TARGET_BYTES = 64 << 20
BATCH_TARGET_BASES = 8192 * 4096  # simulated bases per batch (see the module docstring)
BURST_KINDS = ("substitution", "deletion", "insertion", "mixed")
_RATE_FIELDS = ("dropout_rate", "synthesis_substitution_rate", "synthesis_insertion_rate", "synthesis_deletion_rate",
                "substitution_rate", "insertion_rate", "deletion_rate", "duplication_rate", "truncation_rate", "n_rate",
                "invalid_read_rate", "contamination_rate", "reverse_complement_rate", "quality_informativeness",
                "burst_rate")


@dataclass(frozen=True)
class SequencingConfig:
    seed: int = 0
    coverage: float = 10.0
    coverage_model: str = "poisson"   # fixed | poisson | lognormal
    abundance_sigma: float = 0.0      # lognormal only
    dropout_rate: float = 0.0
    synthesis_substitution_rate: float = 0.0
    synthesis_insertion_rate: float = 0.0
    synthesis_deletion_rate: float = 0.0
    substitution_rate: float = 0.0
    insertion_rate: float = 0.0
    deletion_rate: float = 0.0
    duplication_rate: float = 0.0
    truncation_rate: float = 0.0
    n_rate: float = 0.0
    invalid_read_rate: float = 0.0
    contamination_rate: float = 0.0
    reverse_complement_rate: float = 0.0
    shuffle: bool = True
    quality_model: str = "informative"  # informative | flat
    quality_informativeness: float = 0.8
    burst_rate: float = 0.0             # per-read probability of one burst (VNX-DNA 3)
    burst_length_mean: float = 4.0      # mean burst length in nt (geometric, >= 1)
    burst_kind: str = "substitution"    # substitution | deletion | insertion | mixed

    def __post_init__(self) -> None:
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            raise ConfigurationError("seed must be a non-negative integer")
        for name in _RATE_FIELDS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                raise ConfigurationError(f"{name} must be a probability in [0, 1]")
        if self.coverage_model not in ("fixed", "poisson", "lognormal"):
            raise ConfigurationError("coverage_model must be fixed, poisson or lognormal")
        if not isinstance(self.coverage, (int, float)) or isinstance(self.coverage, bool) or not 0 < self.coverage <= 1000:
            raise ConfigurationError("coverage must be in (0, 1000]")
        if self.coverage_model == "fixed" and float(self.coverage) != int(self.coverage):
            raise ConfigurationError("fixed coverage must be an integer")
        if not 0.0 <= float(self.abundance_sigma) <= 3.0:
            raise ConfigurationError("abundance_sigma must be in [0, 3]")
        if self.quality_model not in ("informative", "flat"):
            raise ConfigurationError("quality_model must be informative or flat")
        if isinstance(self.burst_length_mean, bool) or not isinstance(self.burst_length_mean, (int, float)) \
                or not 1.0 <= float(self.burst_length_mean) <= 64.0:
            raise ConfigurationError("burst_length_mean must be in [1, 64]")
        if self.burst_kind not in BURST_KINDS:
            raise ConfigurationError(f"burst_kind must be one of {', '.join(BURST_KINDS)}")

    @property
    def changes_length(self) -> bool:
        return any((self.synthesis_insertion_rate, self.synthesis_deletion_rate, self.insertion_rate, self.deletion_rate,
                    self.truncation_rate, self.invalid_read_rate,
                    self.burst_rate if self.burst_kind != "substitution" else 0.0))

    @property
    def expected_reads_per_strand(self) -> float:
        """Upper-bound estimate of reads per designed strand (for batch and bucket sizing only)."""
        return max(float(self.coverage), 1.0) * (1.0 + float(self.duplication_rate)) \
            * (1.0 + float(self.invalid_read_rate) + float(self.contamination_rate))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ragged_index(starts: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    """Flat indices of the segments [starts[i], starts[i] + lengths[i]) concatenated, without a Python loop."""
    lengths = np.asarray(lengths, dtype=np.int64)
    total = int(lengths.sum())
    if total == 0:
        return np.zeros(0, dtype=np.int64)
    seg_start = np.cumsum(lengths) - lengths
    return np.arange(total, dtype=np.int64) - np.repeat(seg_start, lengths) + np.repeat(np.asarray(starts, dtype=np.int64), lengths)


def bernoulli_positions(rng: np.random.Generator, size: int, p: float) -> np.ndarray:
    """Sorted positions of an i.i.d. Bernoulli(p) process on ``range(size)``.

    Gaps between successes of a Bernoulli process are Geometric(p), so drawing
    the gaps gives exactly the same distribution as one uniform draw per base,
    at a cost proportional to the number of events instead of the number of bases.
    """
    if p <= 0 or size <= 0:
        return np.zeros(0, dtype=np.int64)
    if p >= 1:
        return np.arange(size, dtype=np.int64)
    parts = []
    position = -1
    expected = int(size * p * 1.1) + 16
    while True:
        gaps = rng.geometric(p, expected).astype(np.int64)
        pos = position + np.cumsum(gaps)
        parts.append(pos[pos < size])
        if pos[-1] >= size:
            break
        position = int(pos[-1])
    return np.concatenate(parts)


def _per_read(positions: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    ends = np.cumsum(lengths)
    return np.bincount(np.searchsorted(ends, positions, side="right"), minlength=lengths.size).astype(np.int64)


def _edit(flat: np.ndarray, lengths: np.ndarray, sub: float, ins: float, dele: float, rng: np.random.Generator,
          counts: Counter, prefix: str, flags: np.ndarray | None) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Per-base substitution → deletion → insertion (after the base) on a flat read array.

    ``flags`` (optional) marks bases produced by an error, for quality scores.
    """
    size = flat.size
    if sub:
        hit = bernoulli_positions(rng, size, sub)
        if hit.size:
            flat = flat.copy()
            flat[hit] = (flat[hit] + rng.integers(1, 4, hit.size)) % 4
            if flags is not None:
                flags = flags.copy()
                flags[hit] = True
        counts[prefix + "substitutions"] += int(hit.size)
    if not (ins or dele):
        return flat, lengths, flags
    deleted = bernoulli_positions(rng, size, dele)
    inserted_after = bernoulli_positions(rng, size, ins)
    counts[prefix + "deletions"] += int(deleted.size)
    counts[prefix + "insertions"] += int(inserted_after.size)
    new_lengths = lengths - _per_read(deleted, lengths) + _per_read(inserted_after, lengths)
    bases = rng.integers(0, 4, inserted_after.size).astype(np.uint8)
    # insert first (positions refer to the original array), then delete the original bases
    out = np.insert(flat, inserted_after + 1, bases)
    shift = np.searchsorted(inserted_after + 1, deleted, side="right")  # insertions before each deleted base
    out = np.delete(out, deleted + shift)
    if flags is not None:
        f = np.insert(flags, inserted_after + 1, True)
        flags = np.delete(f, deleted + shift)
    return out, new_lengths, flags


def _bursts(flat: np.ndarray, lengths: np.ndarray, config: SequencingConfig, rng: np.random.Generator,
            counts: Counter, flags: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """At most one burst per read (see the module docstring). Draws nothing when ``burst_rate`` is 0."""
    reads = lengths.size
    hit = np.flatnonzero(rng.random(reads) < config.burst_rate)
    if not hit.size:
        return flat, lengths, flags
    run = rng.geometric(1.0 / float(config.burst_length_mean), hit.size).astype(np.int64)
    if config.burst_kind == "mixed":
        kinds = rng.integers(0, 3, hit.size)
    else:
        kinds = np.full(hit.size, ("substitution", "deletion", "insertion").index(config.burst_kind))
    lens = lengths[hit]
    clipped = np.where(kinds == 2, run, np.minimum(run, lens))
    starts_in_read = (rng.random(hit.size) * (np.where(kinds == 2, lens, lens - clipped) + 1)).astype(np.int64)
    starts_in_read = np.minimum(starts_in_read, np.where(kinds == 2, lens, lens - clipped))
    keep = clipped > 0
    hit, kinds, clipped, starts_in_read = hit[keep], kinds[keep], clipped[keep], starts_in_read[keep]
    read_start = np.cumsum(lengths) - lengths
    flat = flat.copy()
    flags = flags.copy()
    lengths = lengths.copy()
    sub = kinds == 0
    if sub.any():
        pos = ragged_index(read_start[hit[sub]] + starts_in_read[sub], clipped[sub])
        flat[pos] = (flat[pos] + rng.integers(1, 4, pos.size)) % 4
    ins = kinds == 2
    inserted_at = np.zeros(0, dtype=np.int64)
    if ins.any():
        at = np.repeat(read_start[hit[ins]] + starts_in_read[ins], clipped[ins])
        bases = rng.integers(0, 4, at.size).astype(np.uint8)
        order = np.argsort(at, kind="stable")
        at, bases = at[order], bases[order]
        flat = np.insert(flat, at, bases)
        flags = np.insert(flags, at, False)
        inserted_at = at
        np.add.at(lengths, hit[ins], clipped[ins])
    dele = kinds == 1
    if dele.any():
        pos = ragged_index(read_start[hit[dele]] + starts_in_read[dele], clipped[dele])
        pos = pos + np.searchsorted(inserted_at, pos, side="right")  # shift by insertions placed before each base
        mask = np.ones(flat.size, dtype=bool)
        mask[pos] = False
        flat, flags = flat[mask], flags[mask]
        np.subtract.at(lengths, hit[dele], clipped[dele])
    for code, name in enumerate(("substitution", "deletion", "insertion")):
        chosen = kinds == code
        counts[f"bursts_{name}"] += int(chosen.sum())
        counts[f"burst_bases_{name}"] += int(clipped[chosen].sum())
    return flat, lengths, flags


def simulate_batch(codes: np.ndarray, lengths: np.ndarray, config: SequencingConfig, batch_index: int,
                   strand_nt: int) -> tuple[ReadBatch, np.ndarray, Counter]:
    """Generate the reads of one batch of strands. Returns (reads, source strand per read (-1 = junk), counts)."""
    rng = np.random.default_rng([config.seed, batch_index])
    counts: Counter = Counter()
    n = lengths.size
    counts["strands_in"] += n
    offsets = np.concatenate([[0], np.cumsum(lengths)])
    dropped = rng.random(n) < config.dropout_rate if config.dropout_rate else np.zeros(n, dtype=bool)
    counts["strands_dropped"] += int(dropped.sum())
    if config.coverage_model == "fixed":
        copies = np.full(n, int(config.coverage), dtype=np.int64)
    elif config.coverage_model == "poisson":
        copies = rng.poisson(config.coverage, n).astype(np.int64)
    else:
        sigma = float(config.abundance_sigma)
        weights = rng.lognormal(-sigma * sigma / 2, sigma, n) if sigma else np.ones(n)
        copies = rng.poisson(config.coverage * weights).astype(np.int64)
    copies[dropped] = 0
    counts["strands_with_zero_reads"] += int((copies == 0).sum())
    source = np.repeat(np.arange(n), copies)
    read_lengths = lengths[source]
    flat = codes[ragged_index(offsets[source], read_lengths)]
    flat, read_lengths, _ = _edit(flat, read_lengths, config.synthesis_substitution_rate, config.synthesis_insertion_rate,
                                  config.synthesis_deletion_rate, rng, counts, "synthesis_", None)
    flags = np.zeros(flat.size, dtype=bool)
    flat, read_lengths, edited_flags = _edit(flat, read_lengths, config.substitution_rate, config.insertion_rate,
                                             config.deletion_rate, rng, counts, "sequencing_", flags)
    assert edited_flags is not None  # _edit returns flags whenever it is given flags
    flags = edited_flags
    reads = source.size
    if config.burst_rate and reads:
        flat, read_lengths, flags = _bursts(flat, read_lengths, config, rng, counts, flags)
    if config.truncation_rate and reads:
        cut = np.flatnonzero(rng.random(reads) < config.truncation_rate)
        if cut.size:
            keep_len = read_lengths.copy()
            # never longer than the read: a read that indels shortened to 0 bases stays empty (VNX-DNA 2.0 kept 1
            # base, reading past the read into the next one, and could index past the end of the batch)
            keep_len[cut] = np.minimum(read_lengths[cut],
                                       np.maximum(1, (read_lengths[cut] * rng.uniform(0.5, 0.99, cut.size)).astype(np.int64)))
            starts = np.cumsum(read_lengths) - read_lengths
            index = ragged_index(starts, keep_len)
            flat, flags = flat[index], flags[index]
            counts["reads_truncated"] += int(cut.size)
            read_lengths = keep_len
    if config.n_rate and flat.size:
        hit = bernoulli_positions(rng, flat.size, config.n_rate)
        flat = flat.copy()
        flat[hit] = 4
        counts["n_calls"] += int(hit.size)
    if config.reverse_complement_rate and reads:
        flip = rng.random(reads) < config.reverse_complement_rate
        if flip.any():
            starts = np.cumsum(read_lengths) - read_lengths
            forward = ragged_index(starts[flip], read_lengths[flip])
            backward = ragged_index(starts[flip] + read_lengths[flip] - 1, read_lengths[flip])
            backward = 2 * np.repeat(starts[flip] + read_lengths[flip] - 1, read_lengths[flip]) - backward
            flat = flat.copy()
            flags = flags.copy()
            flat[forward] = _COMPLEMENT[flat[backward]]
            flags[forward] = flags[backward]
            counts["reads_reverse_complemented"] += int(flip.sum())
    if config.quality_model == "flat":
        quals = np.full(flat.size, 30, dtype=np.uint8)
    else:
        quals = rng.integers(30, 41, flat.size, dtype=np.uint8)
        error_bases = np.flatnonzero(flags)
        informative = error_bases[rng.random(error_bases.size) < config.quality_informativeness]
        quals[informative] = rng.integers(2, 21, informative.size, dtype=np.uint8)
    quals[flat == 4] = 2
    # designed bases of the reads that carry independent errors (duplicates copy their original's errors)
    counts["bases_designed_independent"] += int(lengths[source].sum()) if source.size else 0
    if config.duplication_rate and reads:
        dup = np.flatnonzero(rng.random(reads) < config.duplication_rate)
        if dup.size:
            starts = np.cumsum(read_lengths) - read_lengths
            pick = ragged_index(starts[dup], read_lengths[dup])
            flat = np.concatenate([flat, flat[pick]])
            quals = np.concatenate([quals, quals[pick]])
            read_lengths = np.concatenate([read_lengths, read_lengths[dup]])
            source = np.concatenate([source, source[dup]])
            counts["duplicate_reads"] += int(dup.size)
    extra_flat, extra_len, extra_q = [], [], []
    for rate, kind in ((config.invalid_read_rate, "invalid"), (config.contamination_rate, "contamination")):
        if not rate or not reads:
            continue
        k = int(rng.binomial(reads, rate))
        for _ in range(k):
            if kind == "invalid":
                length = int(rng.integers(20, 401))
                seq = rng.integers(0, 5, length).astype(np.uint8)
            else:
                length = strand_nt
                seq = rng.integers(0, 4, length).astype(np.uint8)
            extra_flat.append(seq)
            extra_len.append(length)
            extra_q.append(rng.integers(2, 41, length).astype(np.uint8))
        counts["invalid_reads_added" if kind == "invalid" else "contamination_reads_added"] += k
    if extra_flat:
        flat = np.concatenate([flat] + extra_flat)
        quals = np.concatenate([quals] + extra_q)
        read_lengths = np.concatenate([read_lengths, np.array(extra_len, dtype=np.int64)])
        source = np.concatenate([source, np.full(len(extra_len), -1, dtype=np.int64)])
    counts["reads_out"] += int(read_lengths.size)
    counts["bases_out"] += int(flat.size)
    counts["bases_designed_sequenced"] += int(lengths[source[source >= 0]].sum()) if source.size else 0
    per_strand = np.bincount(source[source >= 0], minlength=n) if source.size else np.zeros(n, np.int64)
    return ReadBatch(flat.astype(np.uint8), read_lengths.astype(np.int64), quals), per_strand, counts


def _write_bucket_record(handle, batch: ReadBatch) -> None:
    assert batch.quals is not None  # simulated reads always carry qualities
    header = np.stack([batch.lengths.astype(np.int64)], axis=1).astype("<i8").tobytes()
    handle.write(len(batch.lengths).to_bytes(8, "little") + header + batch.codes.tobytes() + batch.quals.tobytes())


def _read_bucket(path: Path) -> ReadBatch:
    data = path.read_bytes()
    lengths, codes, quals = [], [], []
    pos = 0
    while pos < len(data):
        n = int.from_bytes(data[pos:pos + 8], "little")
        pos += 8
        lens = np.frombuffer(data[pos:pos + 8 * n], dtype="<i8").astype(np.int64)
        pos += 8 * n
        total = int(lens.sum())
        codes.append(np.frombuffer(data[pos:pos + total], dtype=np.uint8))
        pos += total
        quals.append(np.frombuffer(data[pos:pos + total], dtype=np.uint8))
        pos += total
        lengths.append(lens)
    if not lengths:
        return ReadBatch(np.zeros(0, np.uint8), np.zeros(0, np.int64), np.zeros(0, np.uint8))
    return ReadBatch(np.concatenate(codes), np.concatenate(lengths), np.concatenate(quals))


def _take(batch: ReadBatch, order: np.ndarray) -> ReadBatch:
    offsets = batch.offsets
    if not order.size:
        return ReadBatch(np.zeros(0, np.uint8), np.zeros(0, np.int64), np.zeros(0, np.uint8))
    gather = ragged_index(offsets[order], batch.lengths[order])
    return ReadBatch(batch.codes[gather], batch.lengths[order], batch.quals[gather] if batch.quals is not None else None)


def sequence_file(strands_path: str | os.PathLike, output_path: str | os.PathLike, config: SequencingConfig, *,
                  fmt: str | None = None, overwrite: bool = False, temp_dir: str | os.PathLike | None = None,
                  batch_strands: int = 8192, report_path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Strand pool → simulated reads (FASTQ, FASTA or VXS), streaming, deterministic."""
    started = time.perf_counter()
    fmt = format_for_output(output_path, fmt)
    src = Path(strands_path)
    if not src.is_file():
        raise InvalidInputError(f"strand file not found: {src}")
    if fmt == "vxs" and (config.changes_length or config.n_rate or config.contamination_rate):
        raise ConfigurationError("VXS output holds only equal-length A/C/G/T reads; use FASTQ for indels, truncation, N calls, "
                                 "junk or contamination")
    check_temp_dir(temp_dir)
    check_output_file(output_path, overwrite=overwrite)
    counts: Counter = Counter()
    coverage_hist: Counter = Counter()
    strand_nt = None
    # Pool size in strands and bases, independent of the input file format (FASTA, FASTQ, plain or VXS), so the
    # batch size and bucket count (and hence the output) depend only on the strands, the configuration and the seed.
    pool_strands = pool_bases = 0
    for batch in iter_batches(src, 65536):
        pool_strands += batch.count
        pool_bases += int(batch.lengths.sum())
    if pool_strands == 0:
        raise InvalidInputError(f"{src} contains no strands")
    reads_per_strand = config.expected_reads_per_strand
    mean_nt = max(1, pool_bases // pool_strands)
    batch_size = max(1, min(batch_strands, int(BATCH_TARGET_BASES // (mean_nt * reads_per_strand))))
    workdir = Path(tempfile.mkdtemp(prefix="vnxdna-sequence-", dir=temp_dir))
    writer = None
    handles: list = []
    try:
        estimated = max(1, int(pool_bases * reads_per_strand * 2))  # bucket records hold 1 code + 1 quality byte per base
        buckets = max(1, min(4096, -(-estimated // BUCKET_TARGET_BYTES))) if config.shuffle else 1
        shuffle_rng = np.random.default_rng([config.seed, 1 << 30])
        handles = [(workdir / f"b{b:05d}").open("wb", buffering=1 << 16) for b in range(buckets)] if config.shuffle else []
        for index, batch in enumerate(iter_batches(src, batch_size)):
            if strand_nt is None:
                strand_nt = int(np.bincount(batch.lengths).argmax())
                writer = StrandWriter(output_path, fmt, strand_nt=strand_nt, overwrite=overwrite)
            if batch.invalid is not None and batch.invalid.any() or (batch.codes > 3).any():
                raise InvalidInputError(f"{src} contains symbols other than A/C/G/T; the channel needs designed strands")
            reads, per_strand, batch_counts = simulate_batch(batch.codes, batch.lengths, config, index, strand_nt)
            counts.update(batch_counts)
            coverage_hist.update(dict(enumerate(np.bincount(per_strand).tolist())))
            if config.shuffle:
                target = shuffle_rng.integers(0, buckets, reads.count)
                for b in np.unique(target).tolist():
                    _write_bucket_record(handles[b], _take(reads, np.flatnonzero(target == b)))
            else:
                assert writer is not None  # opened on the first batch
                writer.write_batch(reads)
        if writer is None:
            raise InvalidInputError(f"{src} contains no strands")
        for handle in handles:
            handle.close()
        for b in range(buckets if config.shuffle else 0):
            path = workdir / f"b{b:05d}"
            bucket = _read_bucket(path)
            path.unlink()
            if bucket.count:
                writer.write_batch(_take(bucket, shuffle_rng.permutation(bucket.count)))
        written = writer.commit()
    except BaseException:
        # no .partial output is left behind on any failure or interruption (VNX-DNA 2.0 leaked it from the batch loop)
        for handle in handles:
            handle.close()
        if writer is not None:
            writer.abort()
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    hist = {int(k): int(v) for k, v in sorted(coverage_hist.items()) if v}
    strands = counts["strands_in"]
    reads_from_strands = sum(k * v for k, v in hist.items())
    designed_bases = counts["bases_designed_independent"]
    report = {"status": "SUCCESS", "operation": "sequence", "simulation": "SOFTWARE SIMULATION (not a physical experiment)",
              "input": str(src), "output": str(output_path), "output_format": fmt, "config": config.to_dict(),
              "strands": strands, "reads": counts["reads_out"],
              "reads_per_strand_mean": reads_from_strands / strands if strands else 0.0,
              "coverage_distribution": hist,
              "strands_dropped": counts["strands_dropped"], "strands_with_zero_reads": counts["strands_with_zero_reads"],
              "duplicate_reads": counts["duplicate_reads"], "reads_truncated": counts["reads_truncated"],
              "reads_reverse_complemented": counts["reads_reverse_complemented"],
              "invalid_reads_added": counts["invalid_reads_added"], "contamination_reads_added": counts["contamination_reads_added"],
              "errors": {k: counts[k] for k in ("synthesis_substitutions", "synthesis_insertions", "synthesis_deletions",
                                                "sequencing_substitutions", "sequencing_insertions", "sequencing_deletions", "n_calls",
                                                "bursts_substitution", "bursts_deletion", "bursts_insertion",
                                                "burst_bases_substitution", "burst_bases_deletion", "burst_bases_insertion")},
              "observed_rates_per_designed_base": {
                  k: (counts[k] / designed_bases if designed_bases else 0.0)
                  for k in ("synthesis_substitutions", "synthesis_insertions", "synthesis_deletions", "sequencing_substitutions",
                            "sequencing_insertions", "sequencing_deletions")},
              "batch_strands": batch_size,
              "bases_out": counts["bases_out"], "output_bytes": written["bytes"], "output_sha256": written["file_sha256"],
              "elapsed_s": time.perf_counter() - started}
    if report_path is not None:
        _write_report(Path(report_path), report)
    return report


def _write_report(path: Path, report: dict[str, Any]) -> None:
    try:
        atomic_write_text(path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    except OSError as error:
        raise OutputError(f"cannot write report {path}: {error.strerror or error}") from None
