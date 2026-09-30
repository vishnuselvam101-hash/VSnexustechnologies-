"""Public Python API for the complete VNX-DNA storage lifecycle.

::

    file ──store──▶ container (.vxdna) ──encode──▶ DNA strands (FASTA)
                                                        │ simulate (optional channel damage)
    file ◀─restore── container ◀──────decode────── DNA reads (FASTA/FASTQ/plain)
    file ◀──────────────────recover──────────────── DNA reads

Every function returns a JSON-serializable report ``dict``. Failures raise a
:class:`vnxdna.errors.VNXDNAError` subclass, except :func:`verify`, which
returns a report whose ``status`` is ``"PASS"`` or ``"FAIL"``. Outputs are
written atomically, and only after every applicable check has passed. No
function ever writes unverified data.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__
from .channel import ChannelConfig, simulate as _simulate_pool
from .container import manifest as mf
from .container import vxdna
from .container.builder import StoreOptions, build_container
from .container.reader import ContainerReader, LoadedManifest, body_source, load_manifest
from .dna.reads import read_sequences, write_fasta
from .errors import (IntegrityError, InvalidInputError, OutputError, UnsupportedFormatError, VNXDNAError)
from .legacy import v0_1 as legacy
from .storage.decoder import (DecodeOptions, ReadsSource, discover_geometry, read_statistics, recover_manifest_from_dna,
                              scan_reads)
from .storage.encoder import META_DATA_SHARDS, META_PARITY_SHARDS, efficiency, encode_container, geometry_of, used_data_shards

MAX_INPUT_BYTES = 4 * 1024 ** 3

__all__ = ["StoreOptions", "DecodeOptions", "ChannelConfig", "store", "store_bytes", "encode", "decode", "restore", "recover",
           "verify", "info", "extract", "simulate", "pipeline", "detect_input"]


# ======================================================================= input detection
def detect_input(path: str | os.PathLike) -> str:
    """Classify ``path``: ``container``, ``reads``, ``legacy-v0.1-dataset`` or ``legacy-rd1-archive``."""
    p = Path(path)
    if not p.exists():
        raise InvalidInputError(f"input not found: {p}")
    kind = legacy.detect(p)
    if kind == legacy.V01_DATASET:
        return "legacy-v0.1-dataset"
    if kind == legacy.RD1_ARCHIVE:
        return "legacy-rd1-archive"
    if p.is_dir():
        raise InvalidInputError(f"{p} is a directory, not a VNX-DNA container or read file")
    if vxdna.is_container(p):
        return "container"
    with p.open("rb") as handle:
        head = handle.read(4096)
    text = head.lstrip()
    if text[:1] in (b">", b"@") or (text and all(c in b"ACGTNacgtn\r\n\t " for c in text)):
        return "reads"
    if text.startswith(b"{"):
        raise UnsupportedFormatError(f"{p} is JSON but not a recognised VNX-DNA container or legacy archive")
    raise InvalidInputError(f"{p} is not a VNX-DNA container (.vxdna) or a DNA read file (FASTA/FASTQ/plain)")


def _reject_legacy(kind: str, path: Path) -> None:
    if kind.startswith("legacy"):
        raise UnsupportedFormatError(f"{path} is a {kind} archive; decode it explicitly with `vnx-dna legacy restore` "
                                     "(legacy archives are never read by the format-4 decoder)")


def _read_input_file(path: str | os.PathLike) -> bytes:
    p = Path(path)
    if not p.is_file():
        raise InvalidInputError(f"input file not found: {p}")
    if p.stat().st_size > MAX_INPUT_BYTES:
        raise InvalidInputError(f"input exceeds {MAX_INPUT_BYTES} bytes (in-memory implementation limit)")
    try:
        return p.read_bytes()
    except OSError as error:
        raise InvalidInputError(f"cannot read {p}: {error.strerror or error}") from None


# ======================================================================= opening archives
class OpenedArchive:
    """A reader plus a description of where its data came from."""

    def __init__(self, reader: ContainerReader, kind: str, report: dict[str, Any], scan=None):
        self.reader = reader
        self.kind = kind
        self.report = report
        self.scan = scan


def _open(path: str | os.PathLike, key: bytes | None, *, require_key: bool, options: DecodeOptions,
          strict_container: bool = True) -> OpenedArchive:
    p = Path(path)
    kind = detect_input(p)
    _reject_legacy(kind, p)
    if kind == "container":
        cf = vxdna.read(p, strict=strict_container)
        loaded = load_manifest(cf.manifest_bytes, key, require_key=require_key)
        reader = ContainerReader(loaded, body_source(loaded.manifest, cf.body))
        return OpenedArchive(reader, kind, {"input": str(p), "input_kind": "container", "container_checksum_ok": cf.trailer_ok})
    sequences = read_sequences(p)
    if not sequences:
        raise InvalidInputError(f"{p} contains no sequences")
    geometry = discover_geometry(sequences)
    scan = scan_reads(sequences, geometry, options)
    loaded = load_manifest(recover_manifest_from_dna(scan), key, require_key=require_key)
    if geometry_of(loaded.manifest) != geometry:
        scan = scan_reads(sequences, geometry_of(loaded.manifest), options)
    reader = ContainerReader(loaded, ReadsSource(scan, loaded.manifest))
    return OpenedArchive(reader, "reads", {"input": str(p), "input_kind": "dna-reads", "manifest_source": "dna-metadata-strands"}, scan)


def _status(stats: dict[str, Any]) -> str:
    repaired = any(stats.get(k, 0) for k in ("stripes_outer_recovered", "reads_inner_corrected", "reads_indel_repaired",
                                             "shards_conflict_majority", "shards_conflict_tie"))
    return "RECOVERED" if repaired else "SUCCESS"


def _recovery_report(opened: OpenedArchive, stats: dict[str, Any]) -> dict[str, Any]:
    report = {**opened.report, "archive_id": opened.reader.manifest.archive_id, "encrypted": opened.reader.manifest.encrypted,
              "manifest_authentication": opened.reader.loaded.authentication}
    if opened.scan is not None:
        report["reads"] = read_statistics(opened.scan, opened.reader.manifest)
        merged = {**report["reads"], **stats}
    else:
        merged = stats
    report["recovery"] = {k: v for k, v in stats.items()}
    report["status"] = _status(merged)
    return report


# ======================================================================= store / encode
def store_bytes(data: bytes, options: StoreOptions = StoreOptions(), key: bytes | None = None, name: str | None = None) -> bytes:
    """Build a container and return the ``.vxdna`` file bytes."""
    c = build_container(data, options, key, name)
    return vxdna.serialize(c.manifest_bytes, c.body)


def store(input_path: str | os.PathLike, output_path: str | os.PathLike, *, options: StoreOptions = StoreOptions(),
          key: bytes | None = None, overwrite: bool = False) -> dict[str, Any]:
    """File → ``.vxdna`` container (compression, encryption, chunking, manifest)."""
    data = _read_input_file(input_path)
    c = build_container(data, options, key, Path(input_path).name)
    blob = vxdna.serialize(c.manifest_bytes, c.body)
    vxdna.atomic_write(output_path, blob, overwrite=overwrite)
    return {"status": "SUCCESS", "operation": "store", "output": str(output_path), "archive_id": c.manifest.archive_id,
            "encrypted": c.manifest.encrypted, "original_bytes": len(data), "original_sha256": hashlib.sha256(data).hexdigest(),
            "container_bytes": len(blob), "stored_bytes": c.manifest.stored_size, "chunks": len(c.manifest.chunks),
            "compression": c.manifest.compression.algorithm}


def encode(container_path: str | os.PathLike, output_path: str | os.PathLike, *, overwrite: bool = False) -> dict[str, Any]:
    """``.vxdna`` container → FASTA strand pool (redundancy, ECC, DNA mapping, metadata strands).

    The stored chunks are checked against their manifest SHA-256 before
    encoding, so a corrupted container is never written to DNA. No key is needed.
    """
    p = Path(container_path)
    kind = detect_input(p)
    _reject_legacy(kind, p)
    if kind != "container":
        raise InvalidInputError(f"{p} is not a .vxdna container (encode takes the output of `vnx-dna store`)")
    cf = vxdna.read(p)
    loaded = load_manifest(cf.manifest_bytes, None, require_key=False)
    reader = ContainerReader(loaded, body_source(loaded.manifest, cf.body))
    reader.read_body()
    stored = [reader.stored_chunk(i, Counter()) for i in range(len(loaded.manifest.chunks))]
    out = Path(output_path)
    if out.exists() and not overwrite:
        raise OutputError(f"output already exists: {out} (use --force to overwrite)")
    encoded = encode_container(loaded.manifest, cf.manifest_bytes, stored)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        write_fasta(out, encoded.sequences, encoded.labels)
    except OSError as error:
        raise OutputError(f"cannot write {out}: {error.strerror or error}") from None
    return {"status": "SUCCESS", "operation": "encode", "output": str(out), "archive_id": loaded.manifest.archive_id,
            "strands": len(encoded.sequences), "strand_nt": loaded.manifest.strand.strand_nt,
            "fasta_sha256": _file_sha256(out), "efficiency": encoded.stats}


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ======================================================================= decode / restore / recover
def decode(reads_path: str | os.PathLike, output_path: str | os.PathLike, *, options: DecodeOptions = DecodeOptions(),
           overwrite: bool = False) -> dict[str, Any]:
    """DNA reads → ``.vxdna`` container. No key is needed (ciphertext is recovered as-is).

    Every stored chunk is verified against its manifest SHA-256 before the
    container is written. The result is byte-identical to the original container.
    """
    p = Path(reads_path)
    if detect_input(p) != "reads":
        _reject_legacy(detect_input(p), p)
        raise InvalidInputError(f"{p} is not a DNA read file (decode takes FASTA/FASTQ/plain sequences)")
    opened = _open(p, None, require_key=False, options=options)
    body, stats = opened.reader.read_body()
    blob = vxdna.serialize(opened.reader.loaded.manifest_bytes, body)
    vxdna.atomic_write(output_path, blob, overwrite=overwrite)
    report = _recovery_report(opened, stats)
    report.update({"operation": "decode", "output": str(output_path), "container_bytes": len(blob),
                   "container_sha256": hashlib.sha256(blob).hexdigest()})
    return report


def restore(path: str | os.PathLike, output_path: str | os.PathLike, *, key: bytes | None = None,
            options: DecodeOptions = DecodeOptions(), overwrite: bool = False) -> dict[str, Any]:
    """Container **or** DNA reads → original file, fully verified (SHA-256 recomputed)."""
    opened = _open(path, key, require_key=True, options=options)
    data, stats = opened.reader.read_all()
    vxdna.atomic_write(output_path, data, overwrite=overwrite)
    report = _recovery_report(opened, stats)
    report.update({"operation": "restore", "output": str(output_path), "name": stats["name"], "size": stats["size"],
                   "expected_sha256": stats["expected_sha256"], "recovered_sha256": stats["recovered_sha256"],
                   "output_sha256": hashlib.sha256(data).hexdigest(), "sha256_match": True})
    return report


def recover(reads_path: str | os.PathLike, output_path: str | os.PathLike, *, key: bytes | None = None,
            options: DecodeOptions = DecodeOptions(), overwrite: bool = False) -> dict[str, Any]:
    """DNA reads → original file in one step (decode + restore)."""
    p = Path(reads_path)
    if detect_input(p) != "reads":
        raise InvalidInputError(f"{p} is not a DNA read file; use `restore` for containers")
    report = restore(p, output_path, key=key, options=options, overwrite=overwrite)
    report["operation"] = "recover"
    return report


def extract(path: str | os.PathLike, output_path: str | os.PathLike, *, chunk: int | None = None, start: int | None = None,
            end: int | None = None, key: bytes | None = None, options: DecodeOptions = DecodeOptions(),
            overwrite: bool = False) -> dict[str, Any]:
    """Random access: recover one chunk, or a byte range, decoding only the stripes involved."""
    if (chunk is None) == (start is None):
        raise InvalidInputError("specify exactly one of --chunk or --start (with optional --end)")
    opened = _open(path, key, require_key=True, options=options)
    reader = opened.reader
    if chunk is not None:
        readout = reader.read_chunks([chunk])
        data, stats = readout.chunks[chunk], readout.stats
        info_ = reader.content.chunks[chunk]
        selection = {"chunk": chunk, "offset": info_.offset, "size": info_.size, "chunk_sha256": info_.sha256}
    else:
        data, stats = reader.read_range(start, end)
        selection = {"range": stats["range"], "chunks": stats["chunks_decoded"]}
    vxdna.atomic_write(output_path, data, overwrite=overwrite)
    report = _recovery_report(opened, stats)
    total = reader.manifest.erasure_code.stripe_count
    report.update({"operation": "extract", "output": str(output_path), "selection": selection, "bytes": len(data),
                   "output_sha256": hashlib.sha256(data).hexdigest(), "chunks_total": len(reader.manifest.chunks),
                   "stripes_total": total, "stripes_decoded": stats.get("stripes_decoded", 0)})
    return report


# ======================================================================= verify / info
def verify(path: str | os.PathLike, *, key: bytes | None = None, options: DecodeOptions = DecodeOptions()) -> dict[str, Any]:
    """Independently verify a container or read file. Never writes output.

    Checks, in order: structure (container trailer or strand discovery), manifest
    schema and seal, authentication (HMAC, if a key is supplied), ECC recovery,
    each stored-chunk SHA-256, and, when plaintext is reachable, AEAD tags,
    decompression and a freshly recomputed SHA-256 of the whole object.
    """
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {"operation": "verify", "input": str(path), "checks": checks}

    def check(name: str, ok: bool, detail: str | None = None) -> None:
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", **({"detail": detail} if detail else {})})

    try:
        opened = _open(path, key, require_key=False, options=options, strict_container=False)
    except VNXDNAError as error:
        check("structure-and-manifest", False, f"{error.category}: {error}")
        return {**report, "status": "FAIL", "error": error.category, "exit_code": error.exit_code, "message": str(error)}
    reader = opened.reader
    m = reader.manifest
    report.update({"input_kind": opened.report["input_kind"], "archive_id": m.archive_id, "encrypted": m.encrypted,
                   "format_version": m.format_version})
    if opened.kind == "container":
        check("container-checksum", opened.report["container_checksum_ok"],
              None if opened.report["container_checksum_ok"] else "file trailer SHA-256 mismatch (file corrupted)")
    else:
        check("strand-discovery", True, f"{opened.scan.stats.get('reads_valid', 0)} of {opened.scan.stats.get('reads_total', 0)} reads validated")
    check("manifest-schema-and-digest", True)
    auth = reader.loaded.authentication
    check("manifest-authentication", auth != "not-verified (no key)" or not m.encrypted,
          {"hmac-sha256": "HMAC-SHA256 verified", "digest-only": "unencrypted archive: SHA-256 digest only (detects corruption, not tampering)",
           "not-verified (no key)": "encrypted archive: supply the key to authenticate"}[auth])
    stats: Counter = Counter()
    failures = []
    digest = hashlib.sha256()
    size = 0
    for c in range(len(m.chunks)):
        try:
            stored = reader.stored_chunk(c, stats)
            if reader.content is not None:
                piece = reader.plain_chunk(c, stored, stats)
                digest.update(piece)
                size += len(piece)
        except VNXDNAError as error:
            failures.append({"chunk": c, "error": error.category, "message": str(error)})
    stored_failures = [f for f in failures if f["error"] == "INSUFFICIENT_REDUNDANCY" or "stored bytes" in f["message"]]
    check("ecc-recovery-and-stored-chunks", not stored_failures,
          f"{stats.get('stored_chunks_verified', 0)}/{len(m.chunks)} stored chunks recovered and SHA-256 verified")
    if reader.content is not None:
        plain_ok = not failures
        check("plaintext-chunks", plain_ok, f"{stats.get('plaintext_chunks_verified', 0)}/{len(m.chunks)} chunks "
              "authenticated/decompressed and SHA-256 verified")
        if plain_ok:
            recomputed = digest.hexdigest()
            match = recomputed == reader.content.sha256 and size == reader.content.size
            check("object-sha256", match, f"recomputed {recomputed}")
            report.update({"original_size": reader.content.size, "recovered_size": size, "digest_algorithm": "SHA-256",
                           "expected_sha256": reader.content.sha256, "recovered_sha256": recomputed})
    else:
        report["note"] = "encrypted archive verified at the ciphertext level only; supply the key to verify plaintext"
    report["failures"] = failures
    report["recovery"] = dict(stats)
    if opened.scan is not None:
        report["reads"] = read_statistics(opened.scan, m)
    ok = all(c["result"] == "PASS" for c in checks)
    report["status"] = "PASS" if ok else "FAIL"
    report["recovery_status"] = _status({**report.get("reads", {}), **stats}) if ok else "FAILED"
    if not ok:
        codes = {"INSUFFICIENT_REDUNDANCY": 5, "AUTHENTICATION_FAILED": 4}
        report["exit_code"] = next((codes[f["error"]] for f in failures if f["error"] in codes), 1)
    else:
        report["exit_code"] = 0
    return report


def strand_counts(m: mf.Manifest) -> int:
    """Number of data + parity strands that ``encode`` emits for this manifest (metadata strands excluded)."""
    k, p = m.erasure_code.data_shards, m.strand.payload_bytes
    n = k + m.erasure_code.parity_shards
    total = 0
    for record in m.chunks:
        if record.stripe_count:
            used = used_data_shards(record.stored_size, record.stripe_count - 1, record.stripe_count, k, p)
            total += record.stripe_count * n - (k - used)
    return total


def info(path: str | os.PathLike, *, key: bytes | None = None, options: DecodeOptions = DecodeOptions()) -> dict[str, Any]:
    """Describe an archive (container or DNA reads) from its authenticated metadata."""
    p = Path(path)
    kind = detect_input(p)
    if kind.startswith("legacy"):
        return {"input": str(p), "input_kind": kind, "note": "legacy V0.1 archive; use `vnx-dna legacy info/restore`"}
    opened = _open(p, key, require_key=False, options=options)
    m = opened.reader.manifest
    manifest_bytes = opened.reader.loaded.manifest_bytes
    data_strands = strand_counts(m)
    meta_stripes = -(-(8 + len(manifest_bytes)) // (META_DATA_SHARDS * m.strand.payload_bytes))
    meta_strands = meta_stripes * (META_DATA_SHARDS + META_PARITY_SHARDS)
    content = opened.reader.content
    out = {
        **opened.report,
        "format": f"{m.format} format {m.format_version}", "encoder": f"{m.encoder.name} {m.encoder.version}",
        "archive_id": m.archive_id, "created_at": m.created_at, "required_features": m.required_features,
        "manifest_authentication": opened.reader.loaded.authentication,
        "encryption": m.encryption.algorithm, "compression": f"{m.compression.algorithm} (level {m.compression.level})",
        "chunk_size": m.chunk_size, "chunks": len(m.chunks),
        "erasure_code": {"algorithm": m.erasure_code.algorithm, "data_shards": m.erasure_code.data_shards,
                         "parity_shards": m.erasure_code.parity_shards, "stripes": m.erasure_code.stripe_count,
                         "guarantee": f"any {m.erasure_code.parity_shards} of the {m.erasure_code.data_shards + m.erasure_code.parity_shards} "
                                      "strands of a stripe may be lost"},
        "strand": m.strand.model_dump(), "constraints": m.constraints.model_dump(),
        "content": ({"name": content.name, "size": content.size, "sha256": content.sha256} if content is not None
                    else "encrypted (name, size and SHA-256 are sealed; supply the key to view)"),
        "efficiency": efficiency(m, data_strands, meta_strands),
    }
    if opened.scan is not None:
        out["reads"] = read_statistics(opened.scan, m)
    return out


# ======================================================================= simulate / pipeline
def simulate(reads_path: str | os.PathLike, output_path: str | os.PathLike, config: ChannelConfig, *,
             report_path: str | os.PathLike | None = None, events_path: str | os.PathLike | None = None,
             overwrite: bool = False) -> dict[str, Any]:
    """Pass a strand pool through the synthetic channel. Records actual events and provenance."""
    from .provenance import environment

    p = Path(reads_path)
    if detect_input(p) != "reads":
        raise InvalidInputError(f"{p} is not a DNA read file (simulate takes the FASTA from `vnx-dna encode`)")
    sequences = read_sequences(p)
    out = Path(output_path)
    if out.exists() and not overwrite:
        raise OutputError(f"output already exists: {out} (use --force to overwrite)")
    reads, report = _simulate_pool(sequences, config, record_events=events_path is not None)
    events = report.pop("events", None)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        write_fasta(out, reads, [f"read{i}" for i in range(len(reads))])
        if events_path is not None:
            with Path(events_path).open("w", encoding="utf-8") as handle:
                for event in events or []:
                    handle.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError as error:
        raise OutputError(f"cannot write simulation output: {error.strerror or error}") from None
    report = {"status": "SUCCESS", "operation": "simulate", "input": str(p), "output": str(out),
              "input_sha256": _file_sha256(p), "output_sha256": _file_sha256(out), "channel": report,
              "provenance": environment()}
    if events_path is not None:
        report["events_file"] = str(events_path)
    if report_path is not None:
        vxdna.atomic_write(report_path, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode(), overwrite=True)
    return report


def pipeline(input_path: str | os.PathLike, output_path: str | os.PathLike, *, work_dir: str | os.PathLike,
             store_options: StoreOptions = StoreOptions(), channel: ChannelConfig | None = None, key: bytes | None = None,
             decode_options: DecodeOptions = DecodeOptions(), overwrite: bool = False) -> dict[str, Any]:
    """Run the whole lifecycle with real files: store → encode → simulate → decode → restore → verify."""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    stem = Path(input_path).name
    container, fasta, damaged, decoded = (work / f"{stem}.vxdna", work / f"{stem}.fasta", work / f"{stem}.damaged.fasta",
                                          work / f"{stem}.decoded.vxdna")
    steps: dict[str, Any] = {}
    steps["store"] = store(input_path, container, options=store_options, key=key, overwrite=overwrite)
    steps["encode"] = encode(container, fasta, overwrite=overwrite)
    reads = fasta
    if channel is not None:
        steps["simulate"] = simulate(fasta, damaged, channel, overwrite=overwrite)
        reads = damaged
    steps["decode"] = decode(reads, decoded, options=decode_options, overwrite=overwrite)
    steps["restore"] = restore(decoded, output_path, key=key, overwrite=overwrite)
    steps["verify"] = verify(decoded, key=key)
    original = _read_input_file(input_path)
    recovered = Path(output_path).read_bytes()
    identical = original == recovered
    if not identical:  # unreachable unless a verification layer is broken; never report success
        raise IntegrityError("pipeline output differs from the input despite passing verification")
    return {"status": "SUCCESS" if steps["decode"]["status"] == "SUCCESS" else "RECOVERED", "operation": "pipeline",
            "input": str(input_path), "output": str(output_path), "work_dir": str(work), "bytes_identical": identical,
            "input_sha256": hashlib.sha256(original).hexdigest(), "output_sha256": hashlib.sha256(recovered).hexdigest(),
            "container_roundtrip_identical": container.read_bytes() == decoded.read_bytes(), "steps": steps,
            "version": __version__}
