"""Format-5 container → DNA strands, chunk-parallel and streaming.

Layers, in order (see ``docs/ECC.md``):

1. **ECC groups.** Each stored chunk is cut into ``P``-byte shards, grouped
   into stripes of ``K`` data shards. One stripe is one ECC group.
2. **Outer code.** Cauchy Reed–Solomon over GF(256) adds ``M`` parity shards
   per stripe. Any ``M`` of the ``K + M`` strands of a stripe may be lost.
3. **Shortening.** In a chunk's last stripe, data shards lying entirely in the
   zero padding are not emitted (the decoder knows they are zero). The code
   stays MDS.
4. **Framing** (frame format 5): address, CRC-32, inner RS parity.
5. **Mapping and screening** under the manifest's sequence constraints.

Metadata strands (the manifest and both index tables, Cauchy 8+8) are written
first, so a strand file alone is a complete archive.

Scalability: worker processes each take one chunk (read with ``pread`` from the
container, verified against the index SHA-256, encoded, serialised). The main
process appends the results in chunk order with at most ``2 × workers``
chunks in flight, so memory is bounded by the chunk size and the worker count.
The output is written atomically. A DNA index (``<output>.vxidx``) records
where every chunk's strands are in the output file, for random access.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from ..ecc.cauchy import CauchyErasureCode
from ..errors import IntegrityError, InvalidInputError, OutputError
from . import manifest as mf
from .archive import LoadedV2, default_workers, open_container
from .constraints import ConstraintSpecV2
from .frame import CRC_BYTES, HEADER_BYTES, KIND_DATA, KIND_META, FrameGeometry, build_strands
from .strandio import ReadBatch, StrandWriter, format_for_output, serialize_batch
from .paths import atomic_write_text
from .workers import process_pool

META_MAGIC = b"VNX5"
META_DATA_SHARDS = 8
META_PARITY_SHARDS = 8
SCREEN_BATCH = 16384
DNA_INDEX_FORMAT = "vnx-dna-index-1"


def geometry_of(m: mf.Manifest) -> FrameGeometry:
    return FrameGeometry(m.strand.mapping, m.strand.payload_bytes, m.strand.inner_parity_bytes)


def constraints_of(m: mf.Manifest) -> ConstraintSpecV2:
    return ConstraintSpecV2.from_dict(m.constraints.model_dump())


def used_data_shards(stored_size: int, row: int, stripe_count: int, k: int, p: int) -> int:
    """Data shards of a chunk stripe that carry stored bytes; the rest of the last stripe are implicit zeros."""
    if row < stripe_count - 1:
        return k
    tail = stored_size - (stripe_count - 1) * k * p
    return -(-tail // p)


# moved to vnxdna.codec.cauchy (V6 Phase 2, M2) so that the vnx codec does not import this V2 module
from ..codec.cauchy import _PARITY_TABLES, cauchy_parity  # noqa: E402,F401


def stripe_rows(stored: bytes, code: CauchyErasureCode, p: int, first_stripe: int, *, shorten: bool):
    """(rows (N, P), stripe index (N,), shard index (N,)) for one chunk or metadata stream."""
    k = code.data_shards
    stripe_bytes = k * p
    stripes = -(-len(stored) // stripe_bytes)
    if stripes == 0:
        empty = np.zeros(0, dtype=np.int64)
        return np.zeros((0, p), dtype=np.uint8), empty, empty
    buffer = np.zeros(stripes * stripe_bytes, dtype=np.uint8)
    buffer[: len(stored)] = np.frombuffer(stored, dtype=np.uint8)
    data = buffer.reshape(stripes, k, p)
    full = np.concatenate([data, cauchy_parity(code, data)], axis=1)
    n = code.total_shards
    keep = np.ones((stripes, n), dtype=bool)
    if shorten:
        keep[-1, used_data_shards(len(stored), stripes - 1, stripes, k, p):k] = False
    rows = full[keep]
    stripe_index = np.broadcast_to(np.arange(first_stripe, first_stripe + stripes, dtype=np.int64)[:, None], (stripes, n))[keep]
    shard_index = np.broadcast_to(np.arange(n, dtype=np.int64)[None, :], (stripes, n))[keep]
    return rows, stripe_index, shard_index


def metadata_stream(loaded: LoadedV2) -> bytes:
    return (META_MAGIC + len(loaded.manifest_bytes).to_bytes(4, "big") + len(loaded.index_bytes).to_bytes(4, "big")
            + len(loaded.plain_stored).to_bytes(4, "big") + loaded.manifest_bytes + loaded.index_bytes + loaded.plain_stored)


def expected_strands(m: mf.Manifest, index: np.ndarray) -> np.ndarray:
    """Number of strands ``encode`` emits for each chunk (data + parity, shortened)."""
    k, p, n = m.erasure_code.data_shards, m.strand.payload_bytes, m.erasure_code.data_shards + m.erasure_code.parity_shards
    sizes = index["stored_size"].astype(np.int64)
    stripes = index["stripe_count"].astype(np.int64)
    tail = sizes - np.maximum(stripes - 1, 0) * k * p
    used_last = -(-tail // p)
    return np.where(stripes > 0, stripes * n - (k - used_last), 0)


def _labels(tag: int, kinds: np.ndarray, stripes: np.ndarray, shards: np.ndarray) -> list[str]:
    return [f"vnx5:{tag:08x}:{'d' if k == KIND_DATA else 'm'}:{st}:{sh}"
            for k, st, sh in zip(kinds.tolist(), stripes.tolist(), shards.tolist())]


def encode_rows(geometry: FrameGeometry, spec: ConstraintSpecV2, tag: int, kind: int, rows: np.ndarray, stripes: np.ndarray,
                shards: np.ndarray, fmt: str) -> tuple[bytes, int, int, int]:
    """Screen and serialise strands in bounded batches. Returns (bytes, strands, bases, max variant)."""
    parts = []
    count = bases = 0
    max_variant = 0
    for start in range(0, rows.shape[0], SCREEN_BATCH):
        sl = slice(start, start + SCREEN_BATCH)
        kinds = np.full(rows[sl].shape[0], kind, dtype=np.uint8)
        codes, variants = build_strands(geometry, spec, tag, kinds, stripes[sl], shards[sl], rows[sl])
        labels = None if fmt == "vxs" else _labels(tag, kinds, stripes[sl], shards[sl])
        data, c, b = serialize_batch(ReadBatch.from_matrix(codes), fmt, labels, 0, geometry.strand_nt)
        parts.append(data)
        count += c
        bases += b
        if variants.size:
            max_variant = max(max_variant, int(variants.max()))
    return b"".join(parts), count, bases, max_variant


# ---------------------------------------------------------------- worker side
_WORKER: dict[str, Any] = {}


def _worker_setup(container: str, geometry: tuple, spec: dict, k: int, m: int, tag: int, fmt: str) -> None:
    _WORKER.clear()
    _WORKER.update(fd=os.open(container, os.O_RDONLY), geometry=FrameGeometry(*geometry), spec=ConstraintSpecV2.from_dict(spec),
                   code=CauchyErasureCode(k, m), tag=tag, fmt=fmt)


def _encode_chunk(task: tuple[int, int, int, bytes, int]) -> tuple[int, bytes, int, int, int]:
    from .container import HEADER_BYTES as BODY_START
    c, offset, size, sha, first_stripe = task
    w = _WORKER
    stored = os.pread(w["fd"], size, BODY_START + offset)
    if len(stored) != size or hashlib.sha256(stored).digest() != sha:
        raise IntegrityError(f"chunk {c}: stored bytes do not match the index SHA-256; refusing to encode a corrupted container",
                             details={"chunk": c})
    rows, stripes, shards = stripe_rows(stored, w["code"], w["geometry"].payload_bytes, first_stripe, shorten=True)
    data, count, bases, max_variant = encode_rows(w["geometry"], w["spec"], w["tag"], KIND_DATA, rows, stripes, shards, w["fmt"])
    return c, data, count, bases, max_variant


# ---------------------------------------------------------------- main
def encode_file(container_path: str | os.PathLike, output_path: str | os.PathLike, *, fmt: str | None = None, workers: int = 0,
                overwrite: bool = False, write_index: bool = True, progress=None) -> dict[str, Any]:
    """Encode a version-2 container into a strand file (FASTA or VXS). No key is needed."""
    started = time.perf_counter()
    workers = workers or default_workers()
    fmt = format_for_output(output_path, fmt)
    if fmt == "fastq":
        raise InvalidInputError("encode writes designed strands (FASTA or VXS); FASTQ reads come from `vnx-dna sequence`")
    cf, loaded = open_container(container_path, None, require_key=False)
    cf.close()
    m = loaded.manifest
    geometry = geometry_of(m)
    spec = constraints_of(m)
    tag = m.archive_tag
    out = Path(output_path)
    index_path = out.with_name(out.name + ".vxidx")
    if index_path.exists() and not overwrite and write_index:
        raise OutputError(f"output already exists: {index_path} (use --force to overwrite)")
    meta_rows, meta_stripes, meta_shards = stripe_rows(metadata_stream(loaded), CauchyErasureCode(META_DATA_SHARDS, META_PARITY_SHARDS),
                                                       geometry.payload_bytes, 0, shorten=False)
    chunk_entries = np.zeros((m.chunk_count, 4), dtype=np.int64)  # first strand, strands, byte offset, byte length
    max_variant = 0
    with StrandWriter(out, fmt, strand_nt=geometry.strand_nt, overwrite=overwrite) as writer:
        meta_offset = writer.bytes_written
        data, count, bases, mv = encode_rows(geometry, spec, tag, KIND_META, meta_rows, meta_stripes, meta_shards, fmt)
        writer.write_bytes(data, count, bases)
        meta_entry = {"first_strand": 0, "strands": count, "byte_offset": meta_offset, "byte_length": len(data)}
        max_variant = max(max_variant, mv)
        tasks = ((c, int(r["offset"]), int(r["stored_size"]), r["stored_sha256"].tobytes(), int(r["first_stripe"]))
                 for c, r in enumerate(loaded.index))
        init = (str(Path(container_path)), (geometry.mapping, geometry.payload_bytes, geometry.inner_parity_bytes), spec.to_dict(),
                m.erasure_code.data_shards, m.erasure_code.parity_shards, tag, fmt)

        def consume(result: tuple[int, bytes, int, int, int]) -> None:
            nonlocal max_variant
            c, data, count, bases, mv = result
            chunk_entries[c] = (writer.count, count, writer.bytes_written, len(data))
            writer.write_bytes(data, count, bases)
            max_variant = max(max_variant, mv)
            if progress is not None:
                progress(c + 1, m.chunk_count)

        if workers <= 1:
            _worker_setup(*init)
            for task in tasks:
                consume(_encode_chunk(task))
        else:
            with process_pool(workers, initializer=_worker_setup, initargs=init) as pool:
                pending: deque = deque()
                for task in tasks:
                    pending.append(pool.submit(_encode_chunk, task))
                    if len(pending) >= 2 * workers:
                        consume(pending.popleft().result())
                while pending:
                    consume(pending.popleft().result())
        written = writer.commit()
    if write_index:
        write_dna_index(index_path, loaded, out, fmt, geometry.strand_nt, meta_entry, chunk_entries, written)
    elif overwrite and os.path.lexists(index_path):
        # the strand file was replaced: an index left from the previous file describes a different archive and made
        # random access fail instead of falling back to a scan (V3 release review)
        index_path.unlink()
    elapsed = time.perf_counter() - started
    k, mpar = m.erasure_code.data_shards, m.erasure_code.parity_shards
    strands = written["records"]
    original = loaded.content.size if loaded.content is not None else None
    efficiency = {
        "stored_bytes": m.stored_size, "chunks": m.chunk_count, "ecc_groups": m.erasure_code.stripe_count,
        "outer_code": f"{k}+{mpar} Cauchy RS", "guaranteed_erasures_per_group": mpar,
        "metadata_strands": meta_entry["strands"], "data_and_parity_strands": strands - meta_entry["strands"],
        "parity_strands": m.erasure_code.stripe_count * mpar, "strands_total": strands, "strand_nt": geometry.strand_nt,
        "dna_bases_total": written["bases"], "frame_overhead_bytes_per_strand": HEADER_BYTES + CRC_BYTES + geometry.inner_parity_bytes,
        "bases_per_stored_byte": written["bases"] / m.stored_size if m.stored_size else None,
        "bases_per_original_byte": written["bases"] / original if original else None,
        "net_bits_per_base": 8 * original / written["bases"] if original else None,
        "screening_max_variant": max_variant,
    }
    return {"status": "SUCCESS", "operation": "encode", "format_version": 5, "input": str(container_path), "output": str(out),
            "output_format": fmt, "archive_id": m.archive_id, "strands": strands, "dna_bases": written["bases"],
            "average_strand_nt": written["bases"] / strands if strands else 0, "output_bytes": written["bytes"],
            "output_sha256": written["file_sha256"], "dna_index": str(index_path) if write_index else None,
            "efficiency": efficiency, "workers": workers, "elapsed_s": elapsed,
            "throughput_mb_s": m.stored_size / elapsed / 1e6 if elapsed else None}


def write_dna_index(path: Path, loaded: LoadedV2, strands_path: Path, fmt: str, strand_nt: int, meta_entry: dict,
                    chunk_entries: np.ndarray, written: dict) -> None:
    """DNA index: archive → chunk → ECC groups → strand ordinals → byte ranges in the strand file.

    The index is an accelerator for random access. It is bound to the archive
    (manifest SHA-256) and to the exact strand file (size and SHA-256), and it
    carries its own SHA-256. It never decides correctness: every chunk decoded
    through it is still checked against the authenticated chunk index.
    """
    m = loaded.manifest
    body = {"format": DNA_INDEX_FORMAT, "archive_id": m.archive_id, "manifest_sha256": m.seal.manifest_sha256,
            "strands_file": strands_path.name, "strands_format": fmt, "strands_file_bytes": written["bytes"],
            "strands_file_sha256": written["file_sha256"], "strand_nt": strand_nt, "strands_total": written["records"],
            "data_shards": m.erasure_code.data_shards, "parity_shards": m.erasure_code.parity_shards,
            "group_strands": m.erasure_code.data_shards + m.erasure_code.parity_shards,
            "metadata": meta_entry,
            "chunks": {"columns": ["first_strand", "strands", "byte_offset", "byte_length", "first_stripe", "stripe_count"],
                       "rows": np.concatenate([chunk_entries, loaded.index["first_stripe"].astype(np.int64)[:, None],
                                               loaded.index["stripe_count"].astype(np.int64)[:, None]], axis=1).tolist()}}
    body["index_sha256"] = hashlib.sha256(mf.canonical_bytes(body)).hexdigest()
    atomic_write_text(path, json.dumps(body, sort_keys=True, separators=(",", ":")), encoding="ascii")


def read_dna_index(path: str | os.PathLike) -> dict[str, Any]:
    p = Path(path)
    try:
        body = json.loads(p.read_text(encoding="ascii"))
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise InvalidInputError(f"DNA index {p} is unreadable: {error}") from None
    if not isinstance(body, dict) or body.get("format") != DNA_INDEX_FORMAT:
        raise InvalidInputError(f"{p} is not a VNX-DNA DNA index")
    claimed = body.pop("index_sha256", None)
    if hashlib.sha256(mf.canonical_bytes(body)).hexdigest() != claimed:
        raise InvalidInputError(f"DNA index {p} is corrupted (its SHA-256 does not match)")
    return body
