"""Read clustering by address indexing (scalable, order-independent).

Goal: group the reads that come from the same designed strand, without
comparing every read against every other read.

1. **Verified address.** A read of the strand length whose frame CRC passes
   (forward, reverse complement, or after inner-RS correction) has a
   verified address ``(kind, tag, stripe, shard)``. It joins that cluster
   directly, oriented forward. This is O(1) per read.
2. **Tentative address.** A read that fails validation (typically an indel
   after the header, or too many substitutions) has its 11 header bytes
   descrambled from the first nucleotides, or from the reverse complement of
   the last ones. If the version nibble is plausible and the archive tag is
   one that verified reads also carry, the read joins that address's cluster
   as a *tentative* member. A wrong proposal can only add noise to one
   cluster's consensus. The CRC and the chunk SHA-256 still decide, so it can
   never produce wrong data.
3. **Orphans.** Remaining reads are grouped by a minimizer sketch (k = 12,
   window 8) with leader clustering. Each orphan is compared only with
   leaders that share minimizers (an inverted index), never all-against-all.
   Orphan clusters have no address. Their consensus may still pass the CRC
   later.

Records are spread over bucket files on disk by a hash of the address, so
memory is bounded by one bucket. The output is JSON Lines::

    {"format": "vnx-clusters-1", ...}                         header
    {"id": 0, "address": [kind, tag, stripe, shard] | null,
     "verified": 3, "tentative": 1, "reads": ["ACGT…", …], "quals": ["II…", …]}
    …
    {"end": true, "stats": {...}}                              trailer

Shuffling the input reads changes only the order of the clusters and of the
reads inside them. Consensus voting is order-independent, so the decoded
result does not change (see tests).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from .. import __version__
from ..dna.mapping import _CODE_TO_ASCII, get_mapping, reverse_complement_codes
from ..errors import InvalidInputError, OutputError
from .archive import default_workers
from .decoder import discover
from .frame import FRAME_FORMAT, HEADER_BYTES, KEYSTREAMS, KIND_META, FrameGeometry, parse_batch, parse_one_corrected
from .strandio import ReadBatch, iter_batches

CLUSTER_FORMAT = "vnx-clusters-1"
BUCKET_TARGET_BYTES = 64 << 20
STATUS_ORPHAN, STATUS_VERIFIED, STATUS_TENTATIVE, STATUS_REASSIGNED = 0, 1, 2, 3
_HEAD_DTYPE = np.dtype([("status", "u1"), ("kind", "u1"), ("tag", "<u4"), ("stripe", "<u4"), ("shard", "u1"), ("length", "<u4")])


def _headers(codes_list: list[np.ndarray], geometry: FrameGeometry) -> np.ndarray:
    """Descramble the header of each sequence (first HEADER_BYTES bytes). Returns (n, 5) [ok, kind, tag, stripe, shard]."""
    mapping = get_mapping(geometry.mapping)
    need = HEADER_BYTES * mapping.nt_per_byte
    out = np.zeros((len(codes_list), 5), dtype=np.int64)
    usable = [i for i, c in enumerate(codes_list) if c.size >= need and (c[:need] < 4).all()]
    if not usable:
        return out
    frames, erasures = mapping.decode(np.stack([codes_list[i][:need] for i in usable]))
    head = frames[:, 1:HEADER_BYTES] ^ KEYSTREAMS[frames[:, 0], :HEADER_BYTES - 1]
    ok = ((head[:, 0] >> 4) == FRAME_FORMAT) & ((head[:, 0] & 0x0F) <= KIND_META) & ~erasures.any(axis=1)
    tag = head[:, 1:5].astype(np.int64)
    stripe = head[:, 5:9].astype(np.int64)
    idx = np.asarray(usable)
    out[idx, 0] = ok
    out[idx, 1] = head[:, 0] & 0x0F
    out[idx, 2] = (tag[:, 0] << 24) | (tag[:, 1] << 16) | (tag[:, 2] << 8) | tag[:, 3]
    out[idx, 3] = (stripe[:, 0] << 24) | (stripe[:, 1] << 16) | (stripe[:, 2] << 8) | stripe[:, 3]
    out[idx, 4] = head[:, 9]
    return out


def address_batch(batch: ReadBatch, geometry: FrameGeometry, inner_correction: bool = True):
    """Classify every read. Returns (heads (n,) _HEAD_DTYPE, forward-oriented codes, forward-oriented quals or None, stats)."""
    stats: Counter = Counter()
    n = batch.count
    mapping = get_mapping(geometry.mapping)
    length = geometry.strand_nt
    offsets = batch.offsets
    reads = [batch.codes[offsets[i]:offsets[i + 1]] for i in range(n)]
    quals = [batch.quals[offsets[i]:offsets[i + 1]] for i in range(n)] if batch.quals is not None else None
    heads = np.zeros(n, dtype=_HEAD_DTYPE)
    heads["length"] = batch.lengths
    oriented = list(reads)
    flipped = np.zeros(n, dtype=bool)
    exact = np.flatnonzero((batch.lengths == length) & np.array([(r < 4).all() if r.size == length else False for r in reads]))
    verified = np.zeros(n, dtype=bool)
    if exact.size:
        codes = np.stack([reads[i] for i in exact])
        for orientation, oriented_codes in ((0, codes), (1, reverse_complement_codes(codes))):
            pending = ~verified[exact]
            if not pending.any():
                break
            frames, erasures = mapping.decode(oriented_codes[pending])
            pb = parse_batch(geometry, frames, erasures)
            rows = exact[pending][pb.ok]
            heads["status"][rows] = STATUS_VERIFIED
            heads["kind"][rows], heads["tag"][rows] = pb.kind[pb.ok], pb.tag[pb.ok]
            heads["stripe"][rows], heads["shard"][rows] = pb.stripe[pb.ok], pb.shard[pb.ok]
            verified[rows] = True
            if orientation == 1:
                flipped[rows] = True
        if inner_correction:
            for i in exact[~verified[exact]].tolist():
                for orientation, seq in ((0, reads[i]), (1, reverse_complement_codes(reads[i]))):
                    frames, erasures = mapping.decode(seq[None, :])
                    parsed = parse_one_corrected(geometry, frames[0], erasures[0])
                    if parsed is not None:
                        kind, tag, stripe, shard, _ = parsed[0]
                        heads[i]["status"], heads[i]["kind"], heads[i]["tag"] = STATUS_VERIFIED, kind, tag
                        heads[i]["stripe"], heads[i]["shard"] = stripe, shard
                        verified[i] = True
                        flipped[i] = orientation == 1
                        stats["reads_inner_corrected"] += 1
                        break
    rest = np.flatnonzero(~verified)
    if rest.size:
        fwd = _headers([reads[i] for i in rest], geometry)
        rc = _headers([reverse_complement_codes(reads[i]) for i in rest], geometry)
        for pos, i in enumerate(rest.tolist()):
            choice = fwd[pos] if fwd[pos, 0] else (rc[pos] if rc[pos, 0] else None)
            if choice is None:
                continue
            heads[i]["status"] = STATUS_TENTATIVE
            heads[i]["kind"], heads[i]["tag"], heads[i]["stripe"], heads[i]["shard"] = choice[1], choice[2], choice[3], choice[4]
            flipped[i] = not fwd[pos, 0]
    for i in np.flatnonzero(flipped).tolist():
        oriented[i] = reverse_complement_codes(reads[i])
        if quals is not None:
            quals[i] = quals[i][::-1]
    stats["reads_total"] += n
    stats["reads_verified"] += int((heads["status"] == STATUS_VERIFIED).sum())
    stats["reads_tentative"] += int((heads["status"] == STATUS_TENTATIVE).sum())
    stats["reads_orphan"] += int((heads["status"] == STATUS_ORPHAN).sum())
    return heads, oriented, quals, stats


_CL: dict[str, Any] = {}


def _cl_setup(geometry: tuple, inner: bool) -> None:
    _CL.update(geometry=FrameGeometry(*geometry), inner=inner)


def _cl_task(batch: ReadBatch) -> tuple[bytes, dict]:
    heads, oriented, quals, stats = address_batch(batch, _CL["geometry"], _CL["inner"])
    parts = []
    for i in range(batch.count):
        q = quals[i] if quals is not None else np.full(oriented[i].size, 30, dtype=np.uint8)
        parts.append(heads[i:i + 1].tobytes() + oriented[i].astype(np.uint8).tobytes() + q.astype(np.uint8).tobytes())
    return b"".join(parts), dict(stats)


def _iter_records(data: bytes):
    pos = 0
    hsize = _HEAD_DTYPE.itemsize
    while pos < len(data):
        head = np.frombuffer(data[pos:pos + hsize], dtype=_HEAD_DTYPE)[0]
        pos += hsize
        length = int(head["length"])
        codes = np.frombuffer(data[pos:pos + length], dtype=np.uint8)
        pos += length
        quals = np.frombuffer(data[pos:pos + length], dtype=np.uint8)
        pos += length
        yield head, codes, quals


def _canonical(member) -> tuple[bytes, bytes]:
    """Sort key making cluster contents independent of read order: the read's bases, then its qualities."""
    return member[1].tobytes(), member[2].tobytes()


def _record(head, codes: np.ndarray, quals: np.ndarray) -> bytes:
    h = np.zeros(1, dtype=_HEAD_DTYPE)
    h[0] = head
    h["length"] = codes.size
    return h.tobytes() + codes.astype(np.uint8).tobytes() + quals.astype(np.uint8).tobytes()


def _best_cluster(codes: np.ndarray, hashes: np.ndarray, owners: np.ndarray, min_shared: float) -> tuple[int, bool] | None:
    """Strong cluster sharing the most minimizers with the read (forward or reverse complement), if enough."""
    best = None
    for flipped, oriented in ((False, codes), (True, reverse_complement_codes(codes))):
        mins = np.fromiter(_minimizers(oriented), dtype=np.int64)
        if not mins.size:
            continue
        lo = np.searchsorted(hashes, mins, side="left")
        hi = np.searchsorted(hashes, mins, side="right")
        hits = np.concatenate([owners[a:b] for a, b in zip(lo.tolist(), hi.tolist()) if b > a]) if (hi > lo).any() else None
        if hits is None:
            continue
        counts = np.bincount(hits)
        cluster = int(counts.argmax())
        shared = int(counts[cluster])
        if shared >= max(4, min_shared * mins.size) and (best is None or shared > best[2]):
            best = (cluster, flipped, shared)
    return (best[0], best[1]) if best is not None else None


def _minimizers(codes: np.ndarray, k: int = 12, w: int = 8) -> set[int]:
    clean = np.where(codes > 3, 0, codes).astype(np.int64)
    if clean.size < k + w:
        return set()
    powers = 4 ** np.arange(k - 1, -1, -1, dtype=np.int64)
    kmers = np.lib.stride_tricks.sliding_window_view(clean, k) @ powers
    hashed = (kmers * 0x9E3779B1) & 0xFFFFFFFF
    return set(np.lib.stride_tricks.sliding_window_view(hashed, w).min(axis=1).tolist())


def _ascii(codes: np.ndarray) -> str:
    return _CODE_TO_ASCII[codes].tobytes().decode("ascii")


def _qual_ascii(q: np.ndarray) -> str:
    return (np.minimum(q, 93) + 33).astype(np.uint8).tobytes().decode("ascii")


def cluster_file(reads_path: str | os.PathLike, output_path: str | os.PathLike, *, workers: int = 0, overwrite: bool = False,
                 temp_dir: str | os.PathLike | None = None, max_orphans: int = 200_000, orphan_min_shared: float = 0.3,
                 inner_correction: bool = True, reassign_min_shared: float = 0.3, max_index_clusters: int = 2_000_000,
                 strong_min_reads: int = 2) -> dict[str, Any]:
    """Cluster reads by address. Streaming, bucketed on disk, parallel address scan."""
    started = time.perf_counter()
    workers = workers or default_workers()
    out = Path(output_path)
    if out.exists() and not overwrite:
        raise OutputError(f"output already exists: {out} (use --force to overwrite)")
    frame_format, geometry = discover(reads_path)
    if frame_format != FRAME_FORMAT:
        raise InvalidInputError("these are V1 (frame format 4) reads; clustering is a V2 feature")
    workdir = Path(tempfile.mkdtemp(prefix="vnxdna-cluster-", dir=temp_dir))
    stats: Counter = Counter()
    try:
        size = Path(reads_path).stat().st_size
        buckets = max(1, min(1024, -(-size * 2 // BUCKET_TARGET_BYTES)))
        handles = [(workdir / f"b{b:05d}").open("wb", buffering=1 << 16) for b in range(buckets)]
        orphan_handle = (workdir / "orphans").open("wb", buffering=1 << 16)
        verified_tags: Counter = Counter()

        def consume(result: tuple[bytes, dict]) -> None:
            data, batch_stats = result
            stats.update(batch_stats)
            per_bucket: dict[int, list[bytes]] = defaultdict(list)
            orphans: list[bytes] = []
            pos = 0
            hsize = _HEAD_DTYPE.itemsize
            while pos < len(data):
                head = np.frombuffer(data[pos:pos + hsize], dtype=_HEAD_DTYPE)[0]
                end = pos + hsize + 2 * int(head["length"])
                record = data[pos:end]
                pos = end
                if head["status"] == STATUS_ORPHAN:
                    orphans.append(record)
                    continue
                if head["status"] == STATUS_VERIFIED:
                    verified_tags[int(head["tag"])] += 1
                key = (int(head["stripe"]) * 1_000_003 + int(head["shard"]) * 31 + int(head["kind"])) % buckets
                per_bucket[key].append(record)
            for b, records in per_bucket.items():
                handles[b].write(b"".join(records))
            orphan_handle.write(b"".join(orphans))

        init = ((geometry.mapping, geometry.payload_bytes, geometry.inner_parity_bytes), inner_correction)
        batches = iter_batches(reads_path, 16384)
        if workers <= 1:
            _cl_setup(*init)
            for batch in batches:
                consume(_cl_task(batch))
        else:
            with ProcessPoolExecutor(workers, initializer=_cl_setup, initargs=init) as pool:
                pending: deque = deque()
                for batch in batches:
                    pending.append(pool.submit(_cl_task, batch))
                    if len(pending) >= 2 * workers:
                        consume(pending.popleft().result())
                while pending:
                    consume(pending.popleft().result())
        for h in handles:
            h.close()
        orphan_handle.close()
        known_tags = {t for t, c in verified_tags.items()}
        # pass A: group each bucket; strong groups (a verified read, or >= 3 reads) keep their reads and contribute a
        # representative to a minimizer index; reads of weak groups and orphans get a second chance in pass C
        weak_path = workdir / "weak"
        strong_keys: list[tuple] = []
        index_hash: list[np.ndarray] = []
        index_owner: list[np.ndarray] = []
        with weak_path.open("wb", buffering=1 << 16) as weak:
            for b in range(buckets):
                path = workdir / f"b{b:05d}"
                groups: dict[tuple, list] = defaultdict(list)
                for head, codes, quals in _iter_records(path.read_bytes()):
                    if int(head["tag"]) not in known_tags:  # header damaged in the tag: second chance by similarity
                        stats["tentative_unknown_tag"] += 1
                        weak.write(_record(head, codes, quals))
                        continue
                    groups[(int(head["kind"]), int(head["tag"]), int(head["stripe"]), int(head["shard"]))].append((head, codes, quals))
                strong_records = []
                for address in sorted(groups):
                    members = sorted(groups[address], key=_canonical)
                    verified = [m for m in members if m[0]["status"] == STATUS_VERIFIED]
                    if verified or len(members) >= strong_min_reads:
                        strong_records.extend(_record(m[0], m[1], m[2]) for m in members)
                        if len(strong_keys) < max_index_clusters:
                            rep = (verified or members)[0][1]
                            mins = np.fromiter(_minimizers(rep), dtype=np.int64)
                            index_hash.append(mins)
                            index_owner.append(np.full(mins.size, len(strong_keys), dtype=np.int64))
                            strong_keys.append(address)
                    else:
                        weak.write(b"".join(_record(m[0], m[1], m[2]) for m in members))
                path.write_bytes(b"".join(strong_records))
            weak.write((workdir / "orphans").read_bytes())
        (workdir / "orphans").unlink()
        stats["clusters_strong"] = len(strong_keys)
        # pass B: sorted numpy minimizer index over strong representatives (hash -> cluster)
        hashes = np.concatenate(index_hash) if index_hash else np.zeros(0, np.int64)
        owners = np.concatenate(index_owner) if index_owner else np.zeros(0, np.int64)
        order = np.argsort(hashes, kind="stable")
        hashes, owners = hashes[order], owners[order]
        # pass C: second chance for weak reads: join the strong cluster that shares enough minimizers (either orientation)
        re_handles = [(workdir / f"r{b:05d}").open("wb", buffering=1 << 16) for b in range(buckets)]
        leftover = (workdir / "leftover").open("wb", buffering=1 << 16)
        orphan_out = (workdir / "orphans2").open("wb", buffering=1 << 16)
        for head, codes, quals in _iter_records(weak_path.read_bytes()):
            target = _best_cluster(codes, hashes, owners, reassign_min_shared) if hashes.size else None
            if target is not None:
                cluster, flipped = target
                kind, tag, stripe, shard = strong_keys[cluster]
                new = head.copy()
                new["status"], new["kind"], new["tag"], new["stripe"], new["shard"] = STATUS_REASSIGNED, kind, tag, stripe, shard
                seq, q = (reverse_complement_codes(codes), quals[::-1]) if flipped else (codes, quals)
                key = (stripe * 1_000_003 + shard * 31 + kind) % buckets
                re_handles[key].write(_record(new, seq, q))
                stats["reads_reassigned_to_strong_cluster"] += 1
            elif head["status"] == STATUS_TENTATIVE:
                leftover.write(_record(head, codes, quals))
            else:
                orphan_out.write(_record(head, codes, quals))
        for h in re_handles:
            h.close()
        leftover.close()
        orphan_out.close()
        weak_path.unlink()
        # pass D: emit strong clusters (with reassigned reads), then leftover tentative groups, then orphan clusters
        tmp = out.with_name("." + out.name + ".partial")
        cluster_id = 0
        sizes: Counter = Counter()

        def emit(handle, address, members) -> None:
            nonlocal cluster_id
            line = {"id": cluster_id, "address": list(address) if address is not None else None,
                    "verified": sum(1 for m in members if m[0] == STATUS_VERIFIED),
                    "tentative": sum(1 for m in members if m[0] in (STATUS_TENTATIVE, STATUS_REASSIGNED)),
                    "reads": [_ascii(m[1]) for m in members], "quals": [_qual_ascii(m[2]) for m in members]}
            handle.write(json.dumps(line, separators=(",", ":")) + "\n")
            sizes[min(len(members), 50)] += 1
            cluster_id += 1

        with tmp.open("w", encoding="ascii") as handle:
            handle.write(json.dumps({"format": CLUSTER_FORMAT, "encoder": f"vnxdna {__version__}", "source": str(reads_path),
                                     "geometry": geometry.to_dict(), "archive_tags": sorted(known_tags)}, sort_keys=True) + "\n")
            for b in range(buckets):
                groups = defaultdict(list)
                for name in (f"b{b:05d}", f"r{b:05d}"):
                    path = workdir / name
                    for head, codes, quals in _iter_records(path.read_bytes()):
                        groups[(int(head["kind"]), int(head["tag"]), int(head["stripe"]), int(head["shard"]))].append((head["status"], codes, quals))
                    path.unlink()
                for address in sorted(groups):
                    emit(handle, address, sorted(groups[address], key=_canonical))
            groups = defaultdict(list)
            for head, codes, quals in _iter_records((workdir / "leftover").read_bytes()):
                groups[(int(head["kind"]), int(head["tag"]), int(head["stripe"]), int(head["shard"]))].append((head["status"], codes, quals))
            for address in sorted(groups):
                emit(handle, address, sorted(groups[address], key=_canonical))
            stats["clusters_tentative_only"] = len(groups)
            orphan_clusters = _cluster_orphans(workdir / "orphans2", geometry, max_orphans, orphan_min_shared, stats)
            for members in orphan_clusters:
                emit(handle, None, [(STATUS_ORPHAN, c, q) for c, q in members])
            stats["clusters_addressed"] = cluster_id - len(orphan_clusters)
            stats["clusters_orphan"] = len(orphan_clusters)
            handle.write(json.dumps({"end": True, "stats": dict(stats)}, sort_keys=True) + "\n")
        os.replace(tmp, out)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return {"status": "SUCCESS", "operation": "cluster", "input": str(reads_path), "output": str(out),
            "geometry": geometry.to_dict(), "clusters": cluster_id, "cluster_size_histogram": {int(k): v for k, v in sorted(sizes.items())},
            "stats": dict(stats), "method": "address indexing (verified CRC, tentative header), minimizer-index reassignment of weak reads to strong clusters, minimizer leader clustering for the rest",
            "elapsed_s": time.perf_counter() - started}


def _cluster_orphans(path: Path, geometry: FrameGeometry, max_orphans: int, min_shared: float, stats: Counter) -> list[list]:
    """Leader clustering of unaddressed reads through a minimizer inverted index (no all-against-all comparison)."""
    leaders: list[list] = []
    index: dict[int, list[int]] = defaultdict(list)
    count = 0
    length = geometry.strand_nt
    records = sorted(_iter_records(path.read_bytes()), key=lambda r: (r[1].tobytes(), r[2].tobytes()))  # order-independent
    for head, codes, quals in records:
        if count >= max_orphans:
            stats["orphans_dropped_over_limit"] += 1
            continue
        if abs(codes.size - length) > length // 4:
            stats["orphans_dropped_length"] += 1
            continue
        count += 1
        best = None
        for oriented, q in ((codes, quals), (reverse_complement_codes(codes), quals[::-1])):
            sketch = _minimizers(oriented)
            if not sketch:
                continue
            votes: Counter = Counter()
            for m in sketch:
                for leader in index.get(m, ()):
                    votes[leader] += 1
            if votes:
                leader, shared = votes.most_common(1)[0]
                if shared >= min_shared * len(sketch) and (best is None or shared > best[2]):
                    best = (leader, (oriented, q), shared)
        if best is not None:
            leaders[best[0]].append(best[1])
            stats["orphans_joined"] += 1
            continue
        leaders.append([(codes, quals)])
        for m in _minimizers(codes):
            index[m].append(len(leaders) - 1)
    stats["orphans_clustered"] = count
    return leaders


def iter_clusters(path: str | os.PathLike):
    """Yield (header, cluster dicts...) from a cluster file, validating its framing."""
    p = Path(path)
    with p.open("r", encoding="ascii") as handle:
        first = handle.readline()
        try:
            header = json.loads(first)
        except ValueError:
            raise InvalidInputError(f"{p} is not a VNX-DNA cluster file") from None
        if not isinstance(header, dict) or header.get("format") != CLUSTER_FORMAT:
            raise InvalidInputError(f"{p} is not a VNX-DNA cluster file ({CLUSTER_FORMAT})")
        yield header
        ended = False
        for line in handle:
            item = json.loads(line)
            if item.get("end"):
                ended = True
                break
            yield item
        if not ended:
            raise InvalidInputError(f"{p} is truncated (no end record)")
