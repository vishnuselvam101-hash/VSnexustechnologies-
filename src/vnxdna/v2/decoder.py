"""DNA reads → stored chunks, in two passes with bounded memory (format 5).

Pass 1: scan (parallel)
    Every read is validated on its own: forward CRC, then reverse complement,
    then inner-RS correction in both orientations, then (opt-in) single-read
    indel realignment. Low-quality bases can be flagged as erasures first
    (``quality_erasure_below``). Reads whose CRC never verifies are discarded.
    Each validated shard becomes a fixed-size record ``(tag, stripe, shard,
    kind, payload)`` appended to a spill file on disk. Metadata records stay in
    memory: they are small and needed first.

Metadata
    The manifest and both index tables are rebuilt from the metadata strands
    (Cauchy 8+8) and authenticated like any manifest.

Pass 2: assemble (streaming)
    If the validated records arrived in non-decreasing stripe order (the order
    ``encode`` and ``sequence`` produce), the spill file is read once, front to
    back. Otherwise it is first partitioned into stripe-range buckets and each
    bucket is sorted in memory, an external sort bounded by the bucket size.
    Chunks are then rebuilt one at a time. Copies of each shard are merged
    (identical copies agree; a strict majority wins; a tie becomes an
    erasure), the outer Cauchy code fills in erasures, and the stored chunk is
    checked against its SHA-256 before it is handed on.

Peak memory: one read batch per worker in pass 1, one bucket (≤ 64 MiB by
default) plus one chunk's records in pass 2. Temporary disk use is about
``(10 + P) × validated reads`` bytes, twice that while bucketing.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..dna.mapping import get_mapping, reverse_complement_codes
from ..ecc.cauchy import CauchyErasureCode
from ..errors import (ConfigurationError, InsufficientRedundancyError, IntegrityError, InvalidInputError, MetadataError,
                      UnrecoverableCorruptionError, VNXDNAError)
from . import manifest as mf
from .archive import LoadedV2, check_stored, default_workers, load
from .encoder import META_DATA_SHARDS, META_MAGIC, META_PARITY_SHARDS, geometry_of, used_data_shards
from .frame import FRAME_FORMAT, KIND_DATA, KIND_META, FrameGeometry, parse_batch, parse_one_corrected
from .strandio import ReadBatch, detect_format, iter_batches, read_vxs_range, vxs_info

BUCKET_TARGET_BYTES = 64 << 20
SPILL_BLOCK_RECORDS = 1 << 18


@dataclass(frozen=True)
class DecodeOptionsV2:
    reverse_complement: bool = True
    indel_repair: bool = False      # single-read RS-assisted realignment (see vnxdna.v2.sync)
    max_indel: int = 1
    max_indel_candidates: int = 4096
    quality_erasure_below: int = 0  # FASTQ bases with Phred < this are treated as erasures (0 = off)
    inner_correction: bool = True   # run the inner RS decoder on reads whose CRC fails

    def __post_init__(self) -> None:
        if isinstance(self.max_indel, bool) or not isinstance(self.max_indel, int) or not 0 <= self.max_indel <= 3:
            raise ConfigurationError("max_indel must be an integer in 0..3")
        if not isinstance(self.quality_erasure_below, int) or not 0 <= self.quality_erasure_below <= 60:
            raise ConfigurationError("quality_erasure_below must be an integer in 0..60")


def record_dtype(p: int) -> np.dtype:
    return np.dtype([("tag", ">u4"), ("stripe", ">u4"), ("shard", "u1"), ("kind", "u1"), ("payload", "u1", (p,))])


def concat_records(parts: list[np.ndarray], rec: np.ndarray | np.dtype) -> np.ndarray:
    """Concatenate record arrays keeping the on-disk (big-endian) layout.

    NumPy 2 normalises structured dtypes to native byte order when
    concatenating, which would silently change the bytes written to the spill
    file. Casting back to the record dtype restores the layout.
    """
    rec = np.dtype(rec)
    if not parts:
        return np.zeros(0, dtype=rec)
    joined = parts[0] if len(parts) == 1 else np.concatenate(parts)
    return joined.astype(rec, copy=False)


# ======================================================================= geometry discovery
def discover(path: str | os.PathLike, sample: int = 2000, quality_erasure_below: int = 0) -> tuple[int, FrameGeometry | None]:
    """Return (frame format, geometry). Format 4 means V1 reads (geometry None: the V1 decoder discovers its own)."""
    batch = next(iter_batches(path, sample), None)
    if batch is None or batch.count == 0:
        raise InvalidInputError(f"{path} contains no sequences")
    if quality_erasure_below and batch.quals is not None:
        batch.codes = np.where(batch.quals < quality_erasure_below, 4, batch.codes).astype(np.uint8)
    lengths = Counter(batch.lengths.tolist())
    offsets = batch.offsets
    votes: Counter = Counter()
    for length, _ in lengths.most_common(3):
        rows = [batch.codes[offsets[i]:offsets[i + 1]] for i in range(batch.count) if batch.lengths[i] == length][:400]
        codes = np.stack(rows)
        codes = codes[(codes < 4).all(axis=1)]
        if not codes.shape[0]:
            continue
        for name in ("2bit", "rotation3", "codebook8"):
            mapping = get_mapping(name)
            if length % mapping.nt_per_byte:
                continue
            frame_len = length // mapping.nt_per_byte
            for oriented in (codes, reverse_complement_codes(codes)):
                frames, erasures = mapping.decode(oriented)
                frames = frames[~erasures.any(axis=1)][:100]
                if not frames.shape[0]:
                    continue
                for r in range(0, min(64, frame_len - 16) + 1, 2):
                    p = frame_len - 15 - r
                    if p < 1:
                        continue
                    hits = int(parse_batch(FrameGeometry(name, p, r), frames).ok.sum())
                    if hits:
                        votes[(name, p, r)] += hits
        if votes:
            break
    if not votes:
        votes = _discover_with_correction(batch, lengths)
    if votes:
        (name, p, r), _ = votes.most_common(1)[0]
        return FRAME_FORMAT, FrameGeometry(name, p, r)
    from ..storage.decoder import discover_geometry  # V1 frame format 4
    from ..dna.reads import read_sequences  # noqa: F401 (V1 path reads the file itself)
    try:
        seqs = ["".join("ACGTN"[c] for c in batch.read(i)) for i in range(min(batch.count, 400))]
        discover_geometry(seqs)
        return 4, None
    except VNXDNAError:
        pass
    raise UnrecoverableCorruptionError("no VNX-DNA strands could be identified in the reads "
                                       "(no read passed a frame CRC under any supported geometry)")


def _discover_with_correction(batch: ReadBatch, lengths: Counter, sample: int = 12) -> Counter:
    """Fallback when no read is error-free: accept a geometry if at least two sampled reads pass the CRC after inner RS."""
    votes: Counter = Counter()
    offsets = batch.offsets
    for length, _ in lengths.most_common(2):
        rows = [batch.codes[offsets[i]:offsets[i + 1]] for i in range(batch.count) if batch.lengths[i] == length]
        rows = rows[:sample]
        if len(rows) < 2:
            continue
        codes = np.stack(rows)
        for name in ("2bit", "rotation3", "codebook8"):
            mapping = get_mapping(name)
            if length % mapping.nt_per_byte:
                continue
            frame_len = length // mapping.nt_per_byte
            for oriented in (codes, reverse_complement_codes(codes)):
                frames, erasures = mapping.decode(oriented)
                for r in range(2, min(64, frame_len - 16) + 1, 2):
                    p = frame_len - 15 - r
                    if p < 1:
                        continue
                    geometry = FrameGeometry(name, p, r)
                    hits = sum(1 for i in range(frames.shape[0]) if parse_one_corrected(geometry, frames[i], erasures[i]) is not None)
                    if hits >= 2:
                        votes[(name, p, r)] += hits
        if votes:
            break
    return votes


# ======================================================================= pass 1: scan
_SCAN: dict[str, Any] = {}


def _scan_setup(geometry: tuple, options: dict, vxs_path: str | None) -> None:
    _SCAN.clear()
    _SCAN.update(geometry=FrameGeometry(*geometry), options=DecodeOptionsV2(**options), vxs_path=vxs_path,
                 vxs_info=vxs_info(vxs_path) if vxs_path else None)


def scan_batch(batch: ReadBatch, geometry: FrameGeometry, options: DecodeOptionsV2) -> tuple[np.ndarray, Counter]:
    """Validate a batch of reads. Returns (records, stats); records use :func:`record_dtype`."""
    stats: Counter = Counter()
    stats["reads_total"] += batch.count
    rec = record_dtype(geometry.payload_bytes)
    length = geometry.strand_nt
    mapping = get_mapping(geometry.mapping)
    invalid = batch.invalid if batch.invalid is not None else np.zeros(batch.count, dtype=bool)
    stats["reads_invalid_symbols"] += int(invalid.sum())
    exact = (batch.lengths == length) & ~invalid
    offsets = batch.offsets
    out_parts: list[np.ndarray] = []

    def emit(kind, tag, stripe, shard, payload) -> None:
        r = np.empty(len(tag), dtype=rec)
        r["tag"], r["stripe"], r["shard"], r["kind"], r["payload"] = tag, stripe, shard, kind, payload
        out_parts.append(r)

    if exact.any():
        if exact.all() and (batch.lengths == length).all():
            codes = batch.codes.reshape(batch.count, length)
            quals = batch.quals.reshape(batch.count, length) if batch.quals is not None else None
        else:
            idx = np.flatnonzero(exact)
            gather = (offsets[idx][:, None] + np.arange(length)[None, :])
            codes = batch.codes[gather]
            quals = batch.quals[gather] if batch.quals is not None else None
        if options.quality_erasure_below and quals is not None:
            codes = np.where(quals < options.quality_erasure_below, 4, codes).astype(np.uint8)
            stats["bases_flagged_low_quality"] += int((quals < options.quality_erasure_below).sum())
        frames, erasures = mapping.decode(codes)
        pb = parse_batch(geometry, frames, erasures)
        ok = pb.ok
        emit(pb.kind[ok], pb.tag[ok], pb.stripe[ok], pb.shard[ok], pb.payload[ok])
        stats["reads_valid"] += int(ok.sum())
        rest = np.flatnonzero(~ok)
        rc_frames = rc_erasures = None
        if rest.size and options.reverse_complement:
            rc_frames, rc_erasures = mapping.decode(reverse_complement_codes(codes[rest]))
            pr = parse_batch(geometry, rc_frames, rc_erasures)
            emit(pr.kind[pr.ok], pr.tag[pr.ok], pr.stripe[pr.ok], pr.shard[pr.ok], pr.payload[pr.ok])
            stats["reads_valid"] += int(pr.ok.sum())
            stats["reads_reverse_complement"] += int(pr.ok.sum())
            keep = ~pr.ok
            rest, rc_frames, rc_erasures = rest[keep], rc_frames[keep], rc_erasures[keep]
        slow_rows = []
        for j, i in enumerate(rest.tolist()):
            parsed = None
            orientation = "forward"
            if options.inner_correction:
                parsed = parse_one_corrected(geometry, frames[i], erasures[i])
                if parsed is None and rc_frames is not None:
                    parsed = parse_one_corrected(geometry, rc_frames[j], rc_erasures[j])
                    orientation = "reverse_complement"
            if parsed is None:
                stats["reads_rejected"] += 1
                continue
            (kind, tag, stripe, shard, payload), count = parsed
            slow_rows.append((kind, tag, stripe, shard, np.frombuffer(payload, dtype=np.uint8)))
            stats["reads_valid"] += 1
            stats["reads_inner_corrected"] += 1
            stats["inner_symbols_corrected"] += count
            if orientation == "reverse_complement":
                stats["reads_reverse_complement"] += 1
        if slow_rows:
            emit(*[np.array([r[f] for r in slow_rows]) for f in range(4)], np.stack([r[4] for r in slow_rows]))
    other = np.flatnonzero(~exact & ~invalid)
    repaired = []
    for i in other.tolist():
        read = batch.codes[offsets[i]:offsets[i + 1]]
        if options.indel_repair and 0 < abs(read.size - length) <= options.max_indel:
            from .sync import repair_read
            result = repair_read(read, geometry, max_indel=options.max_indel, max_candidates=options.max_indel_candidates,
                                 reverse_complement=options.reverse_complement)
            if result is not None:
                (kind, tag, stripe, shard, payload), count, orientation = result
                repaired.append((kind, tag, stripe, shard, np.frombuffer(payload, dtype=np.uint8)))
                stats["reads_valid"] += 1
                stats["reads_indel_repaired"] += 1
                continue
        stats["reads_length_mismatch"] += 1
    if repaired:
        emit(*[np.array([r[f] for r in repaired]) for f in range(4)], np.stack([r[4] for r in repaired]))
    return concat_records(out_parts, rec), stats


def _scan_task(task) -> tuple[bytes, dict]:
    if task[0] == "vxs":
        _, first, count = task
        codes = read_vxs_range(_SCAN["vxs_path"], _SCAN["vxs_info"], first, count)
        batch = ReadBatch.from_matrix(codes)
    else:
        batch = task[1]
    records, stats = scan_batch(batch, _SCAN["geometry"], _SCAN["options"])
    return records.tobytes(), dict(stats)


@dataclass
class ScanResult:
    geometry: FrameGeometry
    spill: Path
    records: int
    sorted_by_stripe: bool
    meta: np.ndarray
    stats: Counter
    tags: Counter


def scan_file(path: str | os.PathLike, geometry: FrameGeometry, options: DecodeOptionsV2, workdir: Path, *, workers: int,
              batch_reads: int = 32768, record_filter: Callable[[np.ndarray], np.ndarray] | None = None,
              byte_ranges: list[tuple[int, int]] | None = None, vxs_ranges: list[tuple[int, int]] | None = None,
              progress: Callable[[int], None] | None = None) -> ScanResult:
    """Pass 1. Streams the reads through ``workers`` processes and spills validated data records to disk."""
    rec = record_dtype(geometry.payload_bytes)
    spill = workdir / "records.spill"
    stats: Counter = Counter()
    tags: Counter = Counter()
    meta_parts: list[np.ndarray] = []
    sorted_flag = True
    last_stripe = -1
    written = 0
    fmt = detect_format(path)
    vxs_path = str(path) if fmt == "vxs" else None

    def tasks() -> Iterator:
        if fmt == "vxs":
            info = vxs_info(path)
            for first, count in (vxs_ranges or [(0, info.count)]):
                for start in range(first, first + count, batch_reads):
                    yield ("vxs", start, min(batch_reads, first + count - start))
        else:
            for byte_range in (byte_ranges or [None]):
                for batch in iter_batches(path, batch_reads, byte_range=byte_range):
                    yield ("batch", batch)

    with spill.open("wb", buffering=1 << 20) as handle:
        def consume(result: tuple[bytes, dict]) -> None:
            nonlocal sorted_flag, last_stripe, written
            data, batch_stats = result
            stats.update(batch_stats)
            records = np.frombuffer(data, dtype=rec)
            if progress is not None:
                progress(batch_stats.get("reads_total", 0))
            if not records.size:
                return
            tags.update(dict(zip(*[x.tolist() for x in np.unique(records["tag"], return_counts=True)])))
            is_meta = records["kind"] == KIND_META
            if is_meta.any():
                meta_parts.append(records[is_meta].copy())
            data_recs = records[~is_meta]
            if record_filter is not None and data_recs.size:
                data_recs = data_recs[record_filter(data_recs)]
            if not data_recs.size:
                return
            stripes = data_recs["stripe"].astype(np.int64)
            if sorted_flag and (stripes[0] < last_stripe or (np.diff(stripes) < 0).any()):
                sorted_flag = False
            last_stripe = max(last_stripe, int(stripes[-1]))
            handle.write(data_recs.tobytes())
            written += data_recs.size

        init = ((geometry.mapping, geometry.payload_bytes, geometry.inner_parity_bytes), asdict(options), vxs_path)
        if workers <= 1:
            _scan_setup(*init)
            for task in tasks():
                consume(_scan_task(task))
        else:
            with ProcessPoolExecutor(workers, initializer=_scan_setup, initargs=init) as pool:
                pending: deque = deque()
                for task in tasks():
                    pending.append(pool.submit(_scan_task, task))
                    if len(pending) >= 2 * workers:
                        consume(pending.popleft().result())
                while pending:
                    consume(pending.popleft().result())
    meta = concat_records(meta_parts, rec)
    return ScanResult(geometry, spill, written, sorted_flag, meta, stats, tags)


# ======================================================================= duplicates and metadata
def resolve_copies(records: np.ndarray, p: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, Counter]:
    """Merge validated copies per (stripe, shard).

    Returns (stripes, shards, payloads, stats) with one payload per address.
    Identical copies merge. Among disagreeing copies the strictly most frequent
    wins; a tie drops the address, so the outer code (not a guess) supplies it.
    """
    stats: Counter = Counter()
    if not records.size:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, p), np.uint8), stats
    records = np.ascontiguousarray(records.astype(record_dtype(p), copy=False))
    raw = records.view(np.uint8).reshape(records.size, 10 + p)
    key = np.ascontiguousarray(np.concatenate([raw[:, 4:9], raw[:, 10:]], axis=1))  # stripe (BE), shard, payload
    rows = key.view(np.dtype((np.void, 5 + p))).reshape(-1)
    unique, counts = np.unique(rows, return_counts=True)
    u = np.frombuffer(unique.tobytes(), dtype=np.uint8).reshape(-1, 5 + p)
    stripe = u[:, :4].copy().view(">u4").reshape(-1).astype(np.int64)
    shard = u[:, 4].astype(np.int64)
    address = stripe * 256 + shard
    starts = np.flatnonzero(np.concatenate([[True], address[1:] != address[:-1]]))
    group_max = np.maximum.reduceat(counts, starts)
    group_id = np.repeat(np.arange(starts.size), np.diff(np.append(starts, address.size)))
    is_max = counts == group_max[group_id]
    ties = np.add.reduceat(is_max.astype(np.int64), starts) > 1
    variants = np.diff(np.append(starts, address.size))
    total = np.add.reduceat(counts, starts)
    stats["shards_unique"] += int(((variants == 1) & (total == 1)).sum())
    stats["shards_duplicates_consistent"] += int(((variants == 1) & (total > 1)).sum())
    stats["shards_conflict_majority"] += int(((variants > 1) & ~ties).sum())
    stats["shards_conflict_tie"] += int(ties.sum())
    winner = np.flatnonzero(is_max & ~ties[group_id])
    first_winner = winner[np.concatenate([[True], group_id[winner][1:] != group_id[winner][:-1]])] if winner.size else winner
    return stripe[first_winner], shard[first_winner], u[first_winner, 5:], stats


def recover_metadata(meta: np.ndarray, geometry: FrameGeometry) -> tuple[bytes, bytes, bytes, int]:
    """Rebuild (manifest, chunk index, plain index, tag) from metadata records."""
    if not meta.size:
        raise UnrecoverableCorruptionError("no metadata strands survived; the manifest cannot be recovered from DNA")
    tags = np.unique(meta["tag"])
    if tags.size > 1:
        counts = {int(t): int((meta["tag"] == t).sum()) for t in tags}
        raise MetadataError(f"reads contain metadata for several archives (tags {[f'{t:08x}' for t in counts]}); separate the pools first",
                            details={"archive_tags": [f"{t:08x}" for t in counts]})
    tag = int(tags[0])
    p = geometry.payload_bytes
    stripes, shards, payloads, _ = resolve_copies(meta, p)
    code = CauchyErasureCode(META_DATA_SHARDS, META_PARITY_SHARDS)
    lookup = {(int(s), int(h)): payloads[i] for i, (s, h) in enumerate(zip(stripes.tolist(), shards.tolist()))}

    def decode(ids: list[int]) -> bytes:
        shards_arr = np.zeros((len(ids), code.total_shards, p), dtype=np.uint8)
        present = np.zeros((len(ids), code.total_shards), dtype=bool)
        for row, stripe in enumerate(ids):
            for shard in range(code.total_shards):
                value = lookup.get((stripe, shard))
                if value is not None:
                    shards_arr[row, shard] = value
                    present[row, shard] = True
        try:
            return code.decode(shards_arr, present).tobytes()
        except InsufficientRedundancyError as error:
            raise InsufficientRedundancyError("the DNA copy of the manifest is unrecoverable: " + str(error), details=error.details) from None

    head = decode([0])
    if head[:4] != META_MAGIC:
        raise MetadataError("DNA metadata stream has an invalid header")
    m_len, i_len, j_len = (int.from_bytes(head[o:o + 4], "big") for o in (4, 8, 12))
    if m_len > 1 << 20 or i_len > mf.MAX_CHUNKS * 56 or j_len > mf.MAX_CHUNKS * 36 + 16:
        raise MetadataError("DNA metadata stream declares implausible lengths")
    total = 16 + m_len + i_len + j_len
    stripes_needed = -(-total // (META_DATA_SHARDS * p))
    stream = head + (decode(list(range(1, stripes_needed))) if stripes_needed > 1 else b"")
    body = stream[16:total]
    return body[:m_len], body[m_len:m_len + i_len], body[m_len + i_len:], tag


# ======================================================================= pass 2: assemble
def _sorted_blocks(scan: ScanResult, loaded: LoadedV2, workdir: Path, stats: Counter) -> Iterator[np.ndarray]:
    """Yield record blocks in globally non-decreasing stripe order (external bucket sort if needed)."""
    rec = record_dtype(scan.geometry.payload_bytes)
    tag = loaded.manifest.archive_tag
    stripe_count = loaded.manifest.erasure_code.stripe_count

    def filtered(block: np.ndarray) -> np.ndarray:
        keep = (block["tag"] == tag) & (block["stripe"].astype(np.int64) < stripe_count)
        stats["records_foreign_or_out_of_range"] += int((~keep).sum())
        return block[keep]

    def read_blocks(path: Path) -> Iterator[np.ndarray]:
        with path.open("rb") as handle:
            while True:
                data = handle.read(SPILL_BLOCK_RECORDS * rec.itemsize)
                if not data:
                    return
                yield np.frombuffer(data, dtype=rec)

    if scan.sorted_by_stripe:
        stats["pass2_mode"] = "sequential"
        for block in read_blocks(scan.spill):
            yield filtered(block)
        return
    buckets = max(1, min(4096, -(-scan.records * rec.itemsize // BUCKET_TARGET_BYTES)))
    stats["pass2_mode"] = f"bucket-sort ({buckets} buckets)"
    paths = [workdir / f"bucket-{b:05d}" for b in range(buckets)]
    handles = [p.open("wb", buffering=1 << 16) for p in paths]
    try:
        for block in read_blocks(scan.spill):
            block = filtered(block)
            if not block.size:
                continue
            ids = (block["stripe"].astype(np.int64) * buckets) // max(stripe_count, 1)
            order = np.argsort(ids, kind="stable")
            ids_sorted = ids[order]
            bounds = np.searchsorted(ids_sorted, np.arange(buckets + 1))
            block_sorted = block[order]
            for b in np.flatnonzero(np.diff(bounds)).tolist():
                handles[b].write(block_sorted[bounds[b]:bounds[b + 1]].tobytes())
    finally:
        for h in handles:
            h.close()
    scan.spill.unlink(missing_ok=True)
    for path in paths:
        data = path.read_bytes()
        path.unlink()
        block = np.frombuffer(data, dtype=rec)
        if block.size:
            yield block[np.argsort(block["stripe"], kind="stable")]


def assemble_chunks(scan: ScanResult, loaded: LoadedV2, workdir: Path, *, wanted: Iterable[int] | None = None,
                    allow_partial: bool = False, stats: Counter | None = None,
                    failures: list | None = None) -> Iterator[tuple[int, bytes]]:
    """Pass 2: yield (chunk, verified stored bytes) in chunk order."""
    stats = stats if stats is not None else Counter()
    failures = failures if failures is not None else []
    m = loaded.manifest
    p = m.strand.payload_bytes
    k = m.erasure_code.data_shards
    n = k + m.erasure_code.parity_shards
    code = CauchyErasureCode(k, m.erasure_code.parity_shards)
    first = loaded.index["first_stripe"].astype(np.int64)
    count = loaded.index["stripe_count"].astype(np.int64)
    sizes = loaded.index["stored_size"].astype(np.int64)
    wanted_list = sorted(set(range(m.chunk_count) if wanted is None else wanted))
    carry: list[np.ndarray] = []
    blocks = _sorted_blocks(scan, loaded, workdir, stats)
    exhausted = False

    def records_until(stripe_end: int) -> np.ndarray:
        nonlocal exhausted
        while not exhausted and (not carry or int(carry[-1]["stripe"][-1]) < stripe_end):
            block = next(blocks, None)
            if block is None:
                exhausted = True
                break
            if block.size:
                carry.append(block)
        if not carry:
            return np.zeros(0, dtype=record_dtype(p))
        joined = concat_records(carry, carry[0].dtype)
        cut = int(np.searchsorted(joined["stripe"].astype(np.int64), stripe_end, side="left"))
        carry.clear()
        if cut < joined.size:
            carry.append(joined[cut:])
        return joined[:cut]

    for c in wanted_list:
        lo, stripes = int(first[c]), int(count[c])
        taken = records_until(lo + stripes)
        taken = taken[taken["stripe"].astype(np.int64) >= lo]
        try:
            yield c, _decode_chunk(c, taken, lo, stripes, int(sizes[c]), code, p, k, n, loaded, stats)
        except (InsufficientRedundancyError, IntegrityError) as error:
            if not allow_partial:
                raise
            failures.append({"chunk": c, "error": error.category, "message": str(error), **error.details})


def _decode_chunk(c: int, records: np.ndarray, lo: int, stripes: int, stored_size: int, code: CauchyErasureCode, p: int, k: int,
                  n: int, loaded: LoadedV2, stats: Counter) -> bytes:
    stats["chunks_assembled"] += 1
    if stripes == 0:
        stored = b""
        check_stored(loaded, c, stored)
        return stored
    shard_stripes, shard_ids, payloads, dup_stats = resolve_copies(records, p)
    stats.update(dup_stats)
    shards = np.zeros((stripes, n, p), dtype=np.uint8)
    present = np.zeros((stripes, n), dtype=bool)
    valid = shard_ids < n
    rows = shard_stripes[valid] - lo
    shards[rows, shard_ids[valid]] = payloads[valid]
    present[rows, shard_ids[valid]] = True
    used = used_data_shards(stored_size, stripes - 1, stripes, k, p)
    emitted = np.ones((stripes, n), dtype=bool)
    emitted[-1, used:k] = False
    present[-1, used:k] = True  # shortened: implicit zero shards
    shards[-1, used:k] = 0
    lost = (~present).sum(axis=1)
    stats["stripes_decoded"] += stripes
    stats["shards_erased"] += int(lost.sum())
    stats["stripes_outer_recovered"] += int((~present[:, :k]).any(axis=1).sum())
    stats["max_erasures_in_a_group"] = max(stats.get("max_erasures_in_a_group", 0), int(lost.max()))
    try:
        data = code.decode(shards, present)
    except InsufficientRedundancyError as error:
        bad = [lo + s for s in error.details["unrecoverable_stripes"]]
        stats["stripes_unrecoverable"] += len(bad)
        raise InsufficientRedundancyError(
            f"chunk {c}: {len(bad)} ECC group(s) lost more than {code.parity_shards} strands (worst lost {int(lost.max())}); "
            f"the outer code guarantees recovery of at most {code.parity_shards} per group",
            details={"chunk": c, "stripes": bad[:50], "parity_shards": code.parity_shards, "worst_group_erasures": int(lost.max())}) from None
    stored = data.reshape(-1)[:stored_size].tobytes()
    check_stored(loaded, c, stored)
    stats["stored_chunks_verified"] += 1
    return stored


# ======================================================================= driver
class ReadsArchive:
    """A DNA read/strand file opened as a format-5 archive (context manager; owns a temporary directory)."""

    def __init__(self, path: str | os.PathLike, key: bytes | None, *, options: DecodeOptionsV2 = DecodeOptionsV2(),
                 workers: int = 0, temp_dir: str | os.PathLike | None = None, require_key: bool = True,
                 geometry: FrameGeometry | None = None, wanted_chunks: list[int] | None = None, dna_index: dict | None = None,
                 progress: Callable[[int], None] | None = None):
        self.started = time.perf_counter()
        self.path = Path(path)
        self.workers = workers or default_workers()
        self.workdir = Path(tempfile.mkdtemp(prefix="vnxdna-decode-", dir=temp_dir))
        try:
            if geometry is None:
                frame_format, geometry = discover(self.path, quality_erasure_below=options.quality_erasure_below)
                if frame_format != FRAME_FORMAT:
                    raise InvalidInputError("these are V1 (frame format 4) reads; they are decoded by the V1 decoder")
            self.geometry = geometry
            scan_kwargs: dict[str, Any] = {}
            self.dna_index = dna_index
            if dna_index is not None and wanted_chunks is not None:
                scan_kwargs.update(_index_ranges(dna_index, wanted_chunks))
            self.scan = scan_file(self.path, geometry, options, self.workdir, workers=self.workers, progress=progress, **scan_kwargs)
            manifest_bytes, index_bytes, plain_bytes, tag = recover_metadata(self.scan.meta, geometry)
            self.loaded = load(manifest_bytes, index_bytes, plain_bytes, key, require_key=require_key)
            if geometry_of(self.loaded.manifest) != geometry:
                raise MetadataError("the recovered manifest describes a different strand geometry than the reads")
            self.stats: Counter = Counter()
            self.failures: list[dict[str, Any]] = []
        except BaseException:
            shutil.rmtree(self.workdir, ignore_errors=True)
            raise

    def stored_chunks(self, wanted: Iterable[int] | None = None, *, allow_partial: bool = False) -> Iterator[tuple[int, bytes]]:
        return assemble_chunks(self.scan, self.loaded, self.workdir, wanted=wanted, allow_partial=allow_partial, stats=self.stats,
                               failures=self.failures)

    def report(self) -> dict[str, Any]:
        reads = dict(self.scan.stats)
        tag = self.loaded.manifest.archive_tag
        reads["reads_foreign_archive"] = sum(c for t, c in self.scan.tags.items() if t != tag)
        repaired = any(self.stats.get(k, 0) for k in ("stripes_outer_recovered", "shards_conflict_majority", "shards_conflict_tie")) \
            or any(reads.get(k, 0) for k in ("reads_inner_corrected", "reads_indel_repaired"))
        return {"input": str(self.path), "input_kind": "dna-reads", "frame_format": FRAME_FORMAT,
                "geometry": self.geometry.to_dict(), "archive_id": self.loaded.manifest.archive_id,
                "encrypted": self.loaded.manifest.encrypted, "manifest_source": "dna-metadata-strands",
                "manifest_authentication": self.loaded.authentication, "reads": reads,
                "recovery": {k: v for k, v in self.stats.items()}, "validated_records_spilled": self.scan.records,
                "records_in_stripe_order": self.scan.sorted_by_stripe, "status": "RECOVERED" if repaired else "SUCCESS",
                "elapsed_s": time.perf_counter() - self.started}

    def close(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def __enter__(self) -> "ReadsArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _index_ranges(dna_index: dict, wanted: list[int]) -> dict[str, Any]:
    rows = dna_index["chunks"]["rows"]
    meta = dna_index["metadata"]
    if dna_index["strands_format"] == "vxs":
        ranges = [(meta["first_strand"], meta["strands"])] + [(rows[c][0], rows[c][1]) for c in wanted if rows[c][1]]
        return {"vxs_ranges": ranges}
    ranges = [(meta["byte_offset"], meta["byte_length"])] + [(rows[c][2], rows[c][3]) for c in wanted if rows[c][3]]
    return {"byte_ranges": ranges}
