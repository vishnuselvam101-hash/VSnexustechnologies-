"""Public V2 API: the whole storage lifecycle, dispatching V1 inputs to the V1 implementation.

::

    file ─store─▶ container (.vxdna v2) ─encode─▶ strands (FASTA / VXS) ─sequence─▶ reads (FASTQ)
                                                                                     │ cluster
    file ◀restore─ container ◀──decode── consensus (FASTA) ◀──consensus── clusters (JSONL)
    file ◀────────────────── recover ─────────────── reads or strands

Every function returns a JSON-serialisable report and raises a
:class:`vnxdna.errors.VNXDNAError` subclass on failure, except
:func:`verify`, which reports ``PASS``/``FAIL``. Outputs are written to temporary
files and renamed into place only after every applicable check has passed.

V1 (archive format 4) containers and reads are recognised and handled by the
unchanged V1 modules (:mod:`vnxdna.api`). V2 never writes format 4.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .. import __version__
from .. import api as v1
from ..errors import (IntegrityError, InvalidInputError, OutputError, UnrecoverableCorruptionError, UnsupportedFormatError,
                      VNXDNAError)
from . import archive as arc
from .container import container_version
from .decoder import DecodeOptionsV2, ReadsArchive, discover
from .encoder import encode_file, read_dna_index
from .profiles import StoreOptionsV2
from .sequencing import SequencingConfig, sequence_file
from .strandio import detect_format, iter_batches, StrandWriter, format_for_output, verify_vxs, vxs_info

__all__ = ["detect", "store", "encode", "simulate", "sequence", "reads_filter", "cluster", "consensus", "decode", "recover",
           "restore", "verify", "info", "extract", "migrate", "pipeline"]


# ======================================================================= detection
def detect(path: str | os.PathLike) -> str:
    """``container-v2``, ``container-v1``, ``reads-v2``, ``reads-v1``, ``clusters`` or a legacy V0.1 kind."""
    p = Path(path)
    if not p.exists():
        raise InvalidInputError(f"input not found: {p}")
    if p.is_file():
        version = container_version(p)
        if version == 2:
            return "container-v2"
        if version == 1:
            return "container-v1"
        if version is not None:
            raise UnsupportedFormatError(f"{p}: VNX-DNA container file version {version} is not supported")
        with p.open("rb") as handle:
            head = handle.read(1 << 16)
        if head.startswith(b"\x89VXSTRD\n"):
            return "reads-v2"
        if head.lstrip().startswith(b"{") and _is_cluster_file(p):
            return "clusters"
    kind = v1.detect_input(p)  # legacy V0.1 datasets / RD-1 archives / text reads
    if kind != "reads":
        return kind
    try:
        frame_format, _ = discover(p)
    except UnrecoverableCorruptionError:
        # no error-free read under default options: let the V2 decoder try with the caller's options
        # (for example quality-based erasures) and report its own precise error if it still fails
        return "reads-v2"
    return "reads-v2" if frame_format == 5 else "reads-v1"


def _is_cluster_file(p: Path) -> bool:
    try:
        with p.open("r", encoding="ascii") as handle:
            first = json.loads(handle.readline(1 << 20))
        return isinstance(first, dict) and first.get("format") == "vnx-clusters-1"
    except (OSError, ValueError, UnicodeDecodeError):
        return False


# ======================================================================= store / encode
def store(input_path, output_path, *, options: StoreOptionsV2 = StoreOptionsV2(), key: bytes | None = None, overwrite: bool = False,
          resume: bool = False, workers: int = 0, checkpoint_interval: int = 64) -> dict[str, Any]:
    return arc.store_file(input_path, output_path, options=options, key=key, overwrite=overwrite, resume=resume, workers=workers,
                          checkpoint_interval=checkpoint_interval)


def encode(container_path, output_path, *, fmt: str | None = None, workers: int = 0, overwrite: bool = False,
           write_index: bool = True) -> dict[str, Any]:
    kind = detect(container_path)
    if kind == "container-v1":
        if format_for_output(output_path, fmt) != "fasta":
            raise InvalidInputError("V1 containers encode to FASTA only; migrate them to V2 for VXS output")
        report = v1.encode(container_path, output_path, overwrite=overwrite)
        report["format_version"] = 4
        return report
    if kind != "container-v2":
        raise InvalidInputError(f"{container_path} is not a .vxdna container (encode takes the output of `vnx-dna store`)")
    return encode_file(container_path, output_path, fmt=fmt, workers=workers, overwrite=overwrite, write_index=write_index)


# ======================================================================= channel
def sequence(strands_path, output_path, config: SequencingConfig, *, fmt: str | None = None, overwrite: bool = False,
             temp_dir=None, report_path=None) -> dict[str, Any]:
    return sequence_file(strands_path, output_path, config, fmt=fmt, overwrite=overwrite, temp_dir=temp_dir, report_path=report_path)


def simulate(strands_path, output_path, config: SequencingConfig, *, fmt: str | None = None, overwrite: bool = False,
             temp_dir=None, report_path=None) -> dict[str, Any]:
    """Storage channel (synthesis errors, dropout, abundance) producing a molecule pool; same engine as :func:`sequence`."""
    report = sequence_file(strands_path, output_path, config, fmt=fmt, overwrite=overwrite, temp_dir=temp_dir, report_path=report_path)
    report["operation"] = "simulate"
    return report


def reads_filter(reads_path, output_path=None, *, min_length: int = 0, max_length: int = 0, min_mean_quality: float = 0.0,
                 drop_invalid: bool = True, overwrite: bool = False) -> dict[str, Any]:
    """Read validation and filtering: statistics, and optionally a filtered FASTQ/FASTA."""
    started = time.perf_counter()
    import numpy as np
    stats: Counter = Counter()
    length_hist: Counter = Counter()
    writer = None
    if output_path is not None:
        fmt = format_for_output(output_path)
        if fmt == "vxs":
            raise InvalidInputError("filtered reads are written as FASTQ or FASTA")
        writer = StrandWriter(output_path, fmt, overwrite=overwrite)
    try:
        for batch in iter_batches(reads_path, 65536):
            stats["reads_in"] += batch.count
            keep = np.ones(batch.count, dtype=bool)
            if batch.invalid is not None:
                stats["reads_invalid_symbols"] += int(batch.invalid.sum())
                if drop_invalid:
                    keep &= ~batch.invalid
            if min_length:
                keep &= batch.lengths >= min_length
            if max_length:
                keep &= batch.lengths <= max_length
            offsets = batch.offsets
            n_calls = np.add.reduceat((batch.codes == 4).astype(np.int64), offsets[:-1]) if batch.codes.size else np.zeros(batch.count)
            stats["n_calls"] += int(n_calls.sum())
            if batch.quals is not None and batch.codes.size:
                mean_q = np.add.reduceat(batch.quals.astype(np.int64), offsets[:-1]) / np.maximum(batch.lengths, 1)
                if min_mean_quality:
                    keep &= mean_q >= min_mean_quality
                stats["quality_sum"] += int(batch.quals.astype(np.int64).sum())
            length_hist.update(batch.lengths.tolist())
            stats["reads_kept"] += int(keep.sum())
            if writer is not None and keep.any():
                idx = np.flatnonzero(keep)
                from .sequencing import _take
                writer.write_batch(_take(batch, idx))
        written = writer.commit() if writer is not None else None
    except BaseException:
        if writer is not None:
            writer.abort()
        raise
    total_bases = sum(k * v for k, v in length_hist.items())
    common = length_hist.most_common(5)
    return {"status": "SUCCESS", "operation": "reads", "input": str(reads_path), "input_format": detect_format(reads_path),
            "reads_in": stats["reads_in"], "reads_kept": stats["reads_kept"], "reads_removed": stats["reads_in"] - stats["reads_kept"],
            "reads_invalid_symbols": stats["reads_invalid_symbols"], "n_calls": stats["n_calls"],
            "mean_quality": stats["quality_sum"] / total_bases if stats["quality_sum"] and total_bases else None,
            "length_most_common": [{"length": k, "reads": v} for k, v in common],
            "length_min": min(length_hist) if length_hist else None, "length_max": max(length_hist) if length_hist else None,
            "output": written, "elapsed_s": time.perf_counter() - started}


def cluster(reads_path, output_path, *, workers: int = 0, overwrite: bool = False, temp_dir=None) -> dict[str, Any]:
    from .cluster import cluster_file
    return cluster_file(reads_path, output_path, workers=workers, overwrite=overwrite, temp_dir=temp_dir)


def consensus(clusters_path, output_path, *, overwrite: bool = False, **kwargs) -> dict[str, Any]:
    from .consensus import consensus_file
    return consensus_file(clusters_path, output_path, overwrite=overwrite, **kwargs)


# ======================================================================= decode / restore / recover
def decode(reads_path, output_path, *, options: DecodeOptionsV2 = DecodeOptionsV2(), workers: int = 0, overwrite: bool = False,
           temp_dir=None) -> dict[str, Any]:
    """DNA reads → the original ``.vxdna`` container, byte for byte. No key needed."""
    kind = detect(reads_path)
    if kind == "reads-v1":
        report = v1.decode(reads_path, output_path, overwrite=overwrite)
        report["format_version"] = 4
        return report
    if kind != "reads-v2":
        raise InvalidInputError(f"{reads_path} is not a DNA read/strand file (decode takes FASTA/FASTQ/plain/VXS)")
    from .container import ContainerWriter
    with ReadsArchive(reads_path, None, options=options, workers=workers, temp_dir=temp_dir, require_key=False) as ra:
        writer = ContainerWriter(output_path, overwrite=overwrite)
        writer.start_fresh()
        try:
            for _, stored in ra.stored_chunks():
                writer.write_chunk(stored)
            published = writer.finish(ra.loaded.manifest_bytes, ra.loaded.index_bytes, ra.loaded.plain_stored)
        except BaseException:
            writer.close(remove=True)
            raise
        report = ra.report()
    report.update({"operation": "decode", "format_version": 5, "output": str(output_path), **published})
    return report


def restore(path, output_path, *, key: bytes | None = None, options: DecodeOptionsV2 = DecodeOptionsV2(), workers: int = 0,
            overwrite: bool = False, temp_dir=None) -> dict[str, Any]:
    """Container (V1/V2) or DNA reads → the original file, verified end to end."""
    kind = detect(path)
    if kind == "container-v2":
        return arc.restore_file(path, output_path, key=key, workers=workers, overwrite=overwrite)
    if kind in ("container-v1", "reads-v1"):
        report = v1.restore(path, output_path, key=key, overwrite=overwrite)
        report["format_version"] = 4
        return report
    if kind == "reads-v2":
        return _recover_v2(path, output_path, key=key, options=options, workers=workers, overwrite=overwrite, temp_dir=temp_dir)
    if kind.startswith("legacy"):
        raise UnsupportedFormatError(f"{path} is a {kind} archive; use `vnx-dna legacy restore`")
    raise InvalidInputError(f"{path} is not a VNX-DNA container or DNA read file")


def _recover_v2(path, output_path, *, key, options, workers, overwrite, temp_dir, operation: str = "restore") -> dict[str, Any]:
    with ReadsArchive(path, key, options=options, workers=workers, temp_dir=temp_dir) as ra:
        result = arc.write_verified_plaintext(ra.loaded, arc.verified_plaintext(ra.loaded, ra.stored_chunks(), ra.workers),
                                              output_path, overwrite=overwrite)
        report = ra.report()
    report.update({"operation": operation, "format_version": 5, "output": str(output_path), **result,
                   "output_sha256": result["recovered_sha256"]})
    return report


def recover(reads_path, output_path, *, key: bytes | None = None, options: DecodeOptionsV2 = DecodeOptionsV2(), workers: int = 0,
            overwrite: bool = False, temp_dir=None) -> dict[str, Any]:
    kind = detect(reads_path)
    if kind == "reads-v1":
        report = v1.recover(reads_path, output_path, key=key, overwrite=overwrite)
        report["format_version"] = 4
        return report
    if kind != "reads-v2":
        raise InvalidInputError(f"{reads_path} is not a DNA read file; use `restore` for containers")
    return _recover_v2(reads_path, output_path, key=key, options=options, workers=workers, overwrite=overwrite, temp_dir=temp_dir,
                       operation="recover")


# ======================================================================= extract
def extract(path, output_path, *, offset: int | None = None, length: int | None = None, chunk: int | None = None,
            end: int | None = None, key: bytes | None = None, dna_index: str | os.PathLike | None = None,
            options: DecodeOptionsV2 = DecodeOptionsV2(), workers: int = 0, overwrite: bool = False, temp_dir=None) -> dict[str, Any]:
    """Random access: recover ``[offset, offset+length)`` (or one chunk) decoding only what is needed."""
    kind = detect(path)
    if kind in ("container-v1", "reads-v1"):
        report = v1.extract(path, output_path, chunk=chunk, start=offset, end=(offset + length) if (offset is not None and length is not None) else end,
                            key=key, overwrite=overwrite)
        report["format_version"] = 4
        return report
    if chunk is not None and offset is not None:
        raise InvalidInputError("specify either --chunk or --offset/--length, not both")
    if kind == "container-v2":
        if chunk is not None:
            cf, loaded = arc.open_container(path, key)
            cf.close()
            cs = loaded.manifest.chunk_size
            if not 0 <= chunk < loaded.manifest.chunk_count:
                raise InvalidInputError(f"chunk {chunk} does not exist (archive has {loaded.manifest.chunk_count} chunks)")
            offset, length = chunk * cs, int(loaded.plain[chunk]["size"])
        if offset is None:
            raise InvalidInputError("specify --offset (and optionally --length) or --chunk")
        return arc.extract_range(path, output_path, offset=offset, length=length, key=key, overwrite=overwrite)
    if kind != "reads-v2":
        raise InvalidInputError(f"{path} is not a VNX-DNA container or DNA read file")
    return _extract_reads(path, output_path, offset=offset, length=length, chunk=chunk, key=key, dna_index=dna_index,
                          options=options, workers=workers, overwrite=overwrite, temp_dir=temp_dir)


def _extract_reads(path, output_path, *, offset, length, chunk, key, dna_index, options, workers, overwrite, temp_dir) -> dict[str, Any]:
    started = time.perf_counter()
    p = Path(path)
    index_path = Path(dna_index) if dna_index is not None else p.with_name(p.name + ".vxidx")
    index = None
    index_note = "no DNA index: every read was scanned, only the needed chunks were assembled"
    if index_path.exists():
        index = read_dna_index(index_path)
        if index["strands_file_bytes"] != p.stat().st_size:
            index, index_note = None, f"DNA index {index_path.name} does not match this file (size differs); scanned everything"
        else:
            index_note = f"DNA index {index_path.name}: read only the metadata strands and the needed chunks' strands"
    # first pass: metadata (from the index range when available) to learn the chunk layout
    wanted: list[int] | None = None
    if index is not None:
        rows = index["chunks"]["rows"]
        with ReadsArchive(p, key, options=options, workers=workers, temp_dir=temp_dir, wanted_chunks=[], dna_index=index) as meta_only:
            loaded = meta_only.loaded
        if loaded.manifest.seal.manifest_sha256 != index["manifest_sha256"]:
            raise IntegrityError("DNA index belongs to a different archive than the strands file")
        wanted = _wanted_chunks(loaded, offset, length, chunk)
    kwargs = {"wanted_chunks": wanted, "dna_index": index} if index is not None else {}
    with ReadsArchive(p, key, options=options, workers=workers, temp_dir=temp_dir, **kwargs) as ra:
        loaded = ra.loaded
        wanted = _wanted_chunks(loaded, offset, length, chunk) if wanted is None else wanted
        start = offset if chunk is None else chunk * loaded.manifest.chunk_size
        stop = (loaded.content.size if length is None else start + length) if chunk is None else start + int(loaded.plain[chunk]["size"])
        cs = loaded.manifest.chunk_size
        with arc.AtomicOutput(output_path, overwrite=overwrite) as out:
            for c, plain in arc.verified_plaintext(loaded, ra.stored_chunks(wanted), 1):
                lo, hi = max(start - c * cs, 0), min(stop - c * cs, len(plain))
                out.write(plain[lo:hi])
            out.commit()
        report = ra.report()
    report.update({"operation": "extract", "format_version": 5, "output": str(output_path),
                   "range": {"offset": start, "length": stop - start}, "bytes": out.size, "output_sha256": out.hash.hexdigest(),
                   "chunks_processed": wanted, "chunks_total": loaded.manifest.chunk_count,
                   "strands_processed": report["reads"].get("reads_total", 0), "dna_index": index_note,
                   "elapsed_s": time.perf_counter() - started})
    return report


def _wanted_chunks(loaded: arc.LoadedV2, offset: int | None, length: int | None, chunk: int | None) -> list[int]:
    m = loaded.manifest
    if chunk is not None:
        if not 0 <= chunk < m.chunk_count:
            raise InvalidInputError(f"chunk {chunk} does not exist (archive has {m.chunk_count} chunks)")
        return [chunk]
    if offset is None:
        raise InvalidInputError("specify --offset (and optionally --length) or --chunk")
    if loaded.content is None:
        raise InvalidInputError("archive is encrypted; a key is required for byte-range extraction")
    size = loaded.content.size
    stop = size if length is None else offset + length
    if offset < 0 or not offset <= stop <= size:
        raise InvalidInputError(f"invalid byte range offset={offset} length={length} for an object of {size} bytes")
    return loaded.chunk_range(offset, stop)


# ======================================================================= verify / info
def verify(path, *, key: bytes | None = None, against=None, options: DecodeOptionsV2 = DecodeOptionsV2(), workers: int = 0,
           temp_dir=None) -> dict[str, Any]:
    """Independent verification of a container, a strand/read file, or a recovered file (``against``)."""
    try:
        kind = detect(path)
    except VNXDNAError as error:
        return {"operation": "verify", "input": str(path), "status": "FAIL", "error": error.category, "exit_code": error.exit_code,
                "message": str(error), "checks": [{"check": "input-detection", "result": "FAIL", "detail": str(error)}]}
    if kind == "container-v2":
        return arc.verify_container(path, key=key, against=against, workers=workers)
    if kind in ("container-v1", "reads-v1"):
        report = v1.verify(path, key=key)
        report["format_version"] = 4
        return report
    if kind == "reads-v2":
        return _verify_reads(path, key=key, against=against, options=options, workers=workers, temp_dir=temp_dir)
    return {"operation": "verify", "input": str(path), "status": "FAIL", "exit_code": 6, "checks": [],
            "message": f"{kind} inputs are verified with `vnx-dna legacy info`"}


def _verify_reads(path, *, key, against, options, workers, temp_dir) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str | None = None) -> None:
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", **({"detail": detail} if detail else {})})

    report: dict[str, Any] = {"operation": "verify", "input": str(path), "input_kind": "dna-reads", "checks": checks}
    if detect_format(path) == "vxs":
        ok = verify_vxs(path)
        check("vxs-file-checksum", ok, "record SHA-256 matches the trailer" if ok else "VXS record SHA-256 mismatch")
    try:
        ra = ReadsArchive(path, key, options=options, workers=workers, temp_dir=temp_dir, require_key=False)
    except VNXDNAError as error:
        check("strands-and-metadata", False, f"{error.category}: {error}")
        return {**report, "status": "FAIL", "error": error.category, "exit_code": error.exit_code, "message": str(error)}
    with ra:
        loaded = ra.loaded
        m = loaded.manifest
        reads = ra.scan.stats
        check("strand-validation", True, f"{reads.get('reads_valid', 0)} of {reads.get('reads_total', 0)} reads/strands passed the frame CRC")
        check("manifest-from-dna", True, "manifest and both index tables rebuilt from metadata strands and validated")
        auth = loaded.authentication
        check("manifest-authentication", auth != "not-verified (no key)",
              {"hmac-sha256": "HMAC-SHA256 verified", "digest-only": "unencrypted archive: SHA-256 digest only",
               "not-verified (no key)": "encrypted archive: supply the key to authenticate"}[auth])
        digest = hashlib.sha256()
        size = 0
        plain_ok = 0
        plain_failures = []
        stored = 0
        for c, data in ra.stored_chunks(allow_partial=True):
            stored += 1
            if loaded.plain is not None:
                try:
                    piece = arc.open_chunk(loaded, c, data, stored_checked=True)
                    digest.update(piece)
                    size += len(piece)
                    plain_ok += 1
                except VNXDNAError as error:
                    plain_failures.append({"chunk": c, "error": error.category, "message": str(error)})
        check("ecc-recovery-and-stored-chunks", stored == m.chunk_count,
              f"{stored}/{m.chunk_count} chunks recovered by the ECC layers and SHA-256 verified"
              + (f"; damaged beyond recovery: chunks {[f['chunk'] for f in ra.failures][:20]}" if ra.failures else ""))
        if loaded.plain is not None:
            check("plaintext-chunks", plain_ok == m.chunk_count, f"{plain_ok}/{m.chunk_count} chunks authenticated and SHA-256 verified")
            if plain_ok == m.chunk_count:
                recomputed = digest.hexdigest()
                check("object-sha256", recomputed == loaded.content.sha256 and size == loaded.content.size, f"recomputed {recomputed}")
                report.update({"expected_sha256": loaded.content.sha256, "recovered_sha256": recomputed, "recovered_size": size})
            if against is not None:
                fc = arc.compare_file(loaded, against)
                report["file_comparison"] = fc
                check("file-matches-archive", fc["identical"], "file is byte-identical to the archived object" if fc["identical"]
                      else f"{len(fc['mismatched_chunks'])} chunk(s) differ")
        base = ra.report()
    report.update({"archive_id": m.archive_id, "format_version": 5, "encrypted": m.encrypted, "reads": base["reads"],
                   "recovery": base["recovery"], "damaged_chunks": ra.failures[:100], "plaintext_failures": plain_failures[:100]})
    ok = all(c["result"] == "PASS" for c in checks)
    report["status"] = "PASS" if ok else "FAIL"
    report["recovery_status"] = base["status"] if ok else "FAILED"
    report["exit_code"] = 0 if ok else (5 if ra.failures else 1)
    return report


def info(path, *, key: bytes | None = None, options: DecodeOptionsV2 = DecodeOptionsV2(), workers: int = 0, temp_dir=None) -> dict[str, Any]:
    kind = detect(path)
    if kind in ("container-v1", "reads-v1") or kind.startswith("legacy"):
        report = v1.info(path, key=key)
        report["format_version"] = 4 if not kind.startswith("legacy") else None
        return report
    if kind == "container-v2":
        cf, loaded = arc.open_container(path, key, require_key=False)
        cf.close()
        source = {"input": str(path), "input_kind": "container-v2", "container_bytes": cf.size}
    elif kind == "reads-v2":
        with ReadsArchive(path, key, options=options, workers=workers, temp_dir=temp_dir, require_key=False) as ra:
            loaded = ra.loaded
            source = {"input": str(path), "input_kind": "dna-reads", "reads": ra.report()["reads"]}
    elif kind == "clusters":
        raise InvalidInputError(f"{path} is a cluster file; run `vnx-dna consensus` on it")
    else:
        raise InvalidInputError(f"{path} is not a VNX-DNA archive")
    return describe(loaded, source)


def describe(loaded: arc.LoadedV2, source: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    from .encoder import expected_strands
    m = loaded.manifest
    k, mpar = m.erasure_code.data_shards, m.erasure_code.parity_shards
    strands = int(expected_strands(m, loaded.index).sum())
    meta_bytes = 16 + len(loaded.manifest_bytes) + len(loaded.index_bytes) + len(loaded.plain_stored)
    meta_strands = -(-meta_bytes // (8 * m.strand.payload_bytes)) * 16
    total = strands + meta_strands
    original = loaded.content.size if loaded.content is not None else None
    codecs = np.bincount(loaded.index["codec"], minlength=3).tolist()
    return {**source, "format": f"VNX-DNA archive format {m.format_version}", "encoder": f"{m.encoder.name} {m.encoder.version}",
            "archive_id": m.archive_id, "profile": m.profile, "created_at": m.created_at, "required_features": m.required_features,
            "manifest_authentication": loaded.authentication, "encryption": m.encryption.algorithm,
            "compression": f"{m.compression.algorithm} (level {m.compression.level}, per-chunk auto)",
            "chunks": m.chunk_count, "chunk_size": m.chunk_size,
            "chunks_by_codec": {"stored_raw": codecs[0], "zstd": codecs[1], "zlib": codecs[2]},
            "stored_bytes": m.stored_size,
            "erasure_code": {"algorithm": m.erasure_code.algorithm, "data_shards": k, "parity_shards": mpar,
                             "ecc_groups": m.erasure_code.stripe_count, "symbol": "GF(2^8) byte", "shard_bytes": m.strand.payload_bytes,
                             "group_bytes": k * m.strand.payload_bytes,
                             "guarantee": f"any {mpar} of the {k + mpar} strands of each ECC group may be lost"},
            "strand": m.strand.model_dump(), "constraints": m.constraints.model_dump(),
            "content": ({"name": loaded.content.name, "size": loaded.content.size, "sha256": loaded.content.sha256}
                        if loaded.content is not None else "encrypted (supply the key to view)"),
            "dna": {"strands_total": total, "metadata_strands": meta_strands, "dna_bases_total": total * m.strand.strand_nt,
                    "bases_per_original_byte": total * m.strand.strand_nt / original if original else None,
                    "net_bits_per_base": 8 * original / (total * m.strand.strand_nt) if original else None}}


# ======================================================================= migrate
def migrate(input_path, output_path, *, options: StoreOptionsV2 = StoreOptionsV2(), key: bytes | None = None,
            new_key: bytes | None = None, overwrite: bool = False, workers: int = 0, temp_dir=None) -> dict[str, Any]:
    """V1 archive (container or reads) → V2 container, verified before and after.

    The V1 archive is fully verified (every chunk authenticated and hashed,
    whole-object SHA-256 recomputed) while its plaintext is streamed into a
    temporary file. The V2 archive is then built from that file and verified
    independently. The V2 object SHA-256 must equal the V1 one. A corrupted V1
    archive raises before anything is written.
    """
    started = time.perf_counter()
    kind = detect(input_path)
    if kind not in ("container-v1", "reads-v1"):
        raise InvalidInputError(f"{input_path} is not a V1 archive ({kind}); nothing to migrate")
    workdir = Path(tempfile.mkdtemp(prefix="vnxdna-migrate-", dir=temp_dir))
    try:
        plain = workdir / "plaintext"
        before = v1.restore(input_path, plain, key=key)  # full V1 verification chain; raises on any corruption
        name = before.get("name")
        staged = plain
        if name:
            staged = workdir / name
            os.replace(plain, staged)
        stored = arc.store_file(staged, output_path, options=options, key=new_key if new_key is not None else key,
                                overwrite=overwrite, workers=workers)
        after = arc.verify_container(output_path, key=new_key if new_key is not None else key, workers=workers)
        if after["status"] != "PASS" or after.get("recovered_sha256") != before["expected_sha256"]:
            Path(output_path).unlink(missing_ok=True)
            raise IntegrityError("migrated archive failed verification; the V2 output was removed",
                                 details={"v1_sha256": before["expected_sha256"], "v2_sha256": after.get("recovered_sha256")})
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return {"status": "SUCCESS", "operation": "migrate", "input": str(input_path), "input_kind": kind, "output": str(output_path),
            "v1": {"archive_id": before["archive_id"], "sha256": before["expected_sha256"], "size": before["size"],
                   "verification": "full V1 chain (manifest, per-chunk SHA-256/AEAD, object SHA-256)"},
            "v2": {"archive_id": stored["archive_id"], "sha256": after.get("recovered_sha256"), "verification": after["status"],
                   "profile": options.profile, "encrypted": stored["encrypted"]},
            "sha256_match": True, "elapsed_s": time.perf_counter() - started}


# ======================================================================= pipeline
def pipeline(input_path, output_path, *, work_dir=None, options: StoreOptionsV2 = StoreOptionsV2(), key: bytes | None = None,
             channel: SequencingConfig | None = None, strand_format: str | None = None, workers: int = 0,
             decode_options: DecodeOptionsV2 = DecodeOptionsV2(), use_consensus: bool | None = None, resume: bool = False,
             cleanup: str = "keep", overwrite: bool = False, temp_dir=None, report_path=None) -> dict[str, Any]:
    """store → encode → [sequence → cluster → consensus] → decode → restore → verify, with real files.

    ``cleanup``: ``keep`` (all intermediates), ``outputs`` (keep the strand
    file and reports, delete the rest) or ``all`` (keep only the recovered file
    and the report). The recovered file is compared with the input by streaming
    SHA-256 and a byte comparison, independently of the archive's own checks.
    """
    started = time.perf_counter()
    src = Path(input_path)
    work = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix="vnxdna-pipeline-", dir=temp_dir))
    work.mkdir(parents=True, exist_ok=True)
    stem = src.name
    big = src.stat().st_size > 256 * 1024 * 1024
    fmt = strand_format or ("vxs" if big and (channel is None or not channel.changes_length) else "fasta")
    container = work / f"{stem}.vxdna"
    strands = work / f"{stem}.{fmt}"
    decoded = work / f"{stem}.decoded.vxdna"
    steps: dict[str, Any] = {}
    timings: dict[str, float] = {}

    def run(name, fn, *args, **kwargs):
        t = time.perf_counter()
        steps[name] = fn(*args, **kwargs)
        timings[name] = time.perf_counter() - t
        return steps[name]

    if resume and _container_matches(container, src, key):
        steps["store"] = {"status": "SKIPPED", "reason": "resume: complete container for this exact input (size and SHA-256 checked)"}
    else:
        run("store", arc.store_file, src, container, options=options, key=key, overwrite=True, resume=resume, workers=workers)
    if resume and _strands_match(strands, container):
        steps["encode"] = {"status": "SKIPPED", "reason": "resume: complete strand file for this container (DNA index checked)"}
    else:
        run("encode", encode_file, container, strands, fmt=fmt, workers=workers, overwrite=True)
    reads = strands
    if channel is not None:
        reads_fmt = "vxs" if fmt == "vxs" and not (channel.changes_length or channel.n_rate or channel.contamination_rate) else "fastq"
        reads = work / f"{stem}.reads.{reads_fmt}"
        run("sequence", sequence_file, strands, reads, channel, fmt=reads_fmt, overwrite=True, temp_dir=temp_dir)
        need_consensus = use_consensus if use_consensus is not None else (channel.coverage > 1 and reads_fmt == "fastq")
        if need_consensus:
            from .cluster import cluster_file
            from .consensus import consensus_file
            clusters = work / f"{stem}.clusters.jsonl"
            cons = work / f"{stem}.consensus.fasta"
            run("cluster", cluster_file, reads, clusters, workers=workers, overwrite=True, temp_dir=temp_dir)
            run("consensus", consensus_file, clusters, cons, overwrite=True)
            reads = cons
    run("decode", decode, reads, decoded, options=decode_options, workers=workers, overwrite=True, temp_dir=temp_dir)
    run("restore", arc.restore_file, decoded, output_path, key=key, workers=workers, overwrite=overwrite)
    run("verify", arc.verify_container, decoded, key=key, against=output_path, workers=workers)
    identical, in_sha, out_sha = _compare_files(src, Path(output_path))
    if not identical:  # unreachable unless a verification layer is broken; never report success
        raise IntegrityError("pipeline output differs from the input despite passing verification")
    container_identical = _files_equal(container, decoded)
    removed = []
    if cleanup in ("outputs", "all"):
        for path in [container, decoded] + [work / f"{stem}.clusters.jsonl"] + ([] if cleanup == "outputs" else [strands, reads]):
            if path.exists() and path != Path(output_path):
                path.unlink()
                removed.append(path.name)
    report = {"status": "RECOVERED" if steps["decode"].get("status") == "RECOVERED" else "SUCCESS", "operation": "pipeline",
              "simulation": "SOFTWARE SIMULATION" if channel is not None else None, "input": str(src), "output": str(output_path),
              "work_dir": str(work), "strand_format": fmt, "bytes_identical": identical, "input_sha256": in_sha, "output_sha256": out_sha,
              "container_roundtrip_identical": container_identical, "steps": steps, "timings_s": timings,
              "cleanup": {"policy": cleanup, "removed": removed}, "version": __version__,
              "elapsed_s": time.perf_counter() - started}
    if report_path is not None:
        Path(report_path).write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return report


def _container_matches(container: Path, src: Path, key: bytes | None) -> bool:
    """True only for a complete container that holds exactly ``src`` (its content size and SHA-256)."""
    if not container.exists():
        return False
    try:
        cf, loaded = arc.open_container(container, key)
        cf.close()
    except VNXDNAError:
        return False
    if loaded.content is None or loaded.content.size != src.stat().st_size:
        return False
    h = hashlib.sha256()
    with src.open("rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            h.update(block)
    return h.hexdigest() == loaded.content.sha256


def _strands_match(strands: Path, container: Path) -> bool:
    """True only for a strand file whose DNA index binds it to this container's manifest and to its own size."""
    index_path = strands.with_name(strands.name + ".vxidx")
    if not strands.exists() or not index_path.exists():
        return False
    try:
        index = read_dna_index(index_path)
        cf, loaded = arc.open_container(container, None, require_key=False)
        cf.close()
    except VNXDNAError:
        return False
    return index["manifest_sha256"] == loaded.manifest.seal.manifest_sha256 and index["strands_file_bytes"] == strands.stat().st_size


def _compare_files(a: Path, b: Path) -> tuple[bool, str, str]:
    ha, hb = hashlib.sha256(), hashlib.sha256()
    same = True
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            x, y = fa.read(4 << 20), fb.read(4 << 20)
            ha.update(x)
            hb.update(y)
            if x != y:
                same = False
            if not x and not y:
                break
    return same, ha.hexdigest(), hb.hexdigest()


def _files_equal(a: Path, b: Path) -> bool:
    if not a.exists() or not b.exists() or a.stat().st_size != b.stat().st_size:
        return False
    return _compare_files(a, b)[0]
