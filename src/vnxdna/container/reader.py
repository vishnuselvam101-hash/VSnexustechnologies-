"""Authenticate a manifest and turn stored chunks back into verified plaintext.

A :class:`ContainerReader` is independent of where the stored chunks come from.
A *chunk source* is any callable ``source(chunk_index, stats) -> bytes``:

* :func:`body_source` slices the body of a ``.vxdna`` file;
* :class:`vnxdna.storage.decoder.ReadsSource` rebuilds chunks from DNA reads
  through the inner and outer codes.

Verification chain, each check recomputed from bytes in hand:

1. manifest: strict schema, then SHA-256 digest (unencrypted) or key check +
   HMAC-SHA256 (encrypted); sealed content is opened with AES-256-GCM;
2. stored chunk: SHA-256 of the stored bytes vs. the manifest record;
3. ciphertext: AES-256-GCM tag, AD = archive ID ‖ chunk index ‖ chunk count;
4. decompression bounded by the recorded plaintext size;
5. chunk plaintext SHA-256;
6. whole-object SHA-256 (``read_all``).
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

from ..errors import (AuthenticationError, InsufficientRedundancyError, IntegrityError, InvalidInputError, KeyRequiredError,
                      MetadataError, WrongKeyError)
from . import compression, crypto
from . import manifest as mf

ChunkSource = Callable[[int, Counter], bytes]


@dataclass
class LoadedManifest:
    manifest: mf.Manifest
    manifest_bytes: bytes
    content: mf.Content | None
    keys: crypto.ArchiveKeys | None
    authentication: str  # "hmac-sha256" | "digest-only" | "not-verified (no key)"


def load_manifest(data: bytes, key: bytes | None, *, require_key: bool = True) -> LoadedManifest:
    """Parse, validate and authenticate a manifest.

    Unencrypted archives carry only an unkeyed SHA-256 digest: it detects
    corruption, not deliberate tampering. Encrypted archives are authenticated
    with HMAC-SHA256 under a key derived from the user's key.
    """
    raw = mf.parse_json(data)
    manifest = mf.validate(raw)
    if mf.canonical_bytes(raw) != data:
        raise MetadataError("manifest is not in canonical form")
    payload = mf.digest_payload(raw)
    digest_ok = hashlib.sha256(payload).hexdigest() == manifest.seal.manifest_sha256
    if not manifest.encrypted:
        if not digest_ok:
            raise MetadataError("manifest digest mismatch: the manifest is corrupted or was modified")
        return LoadedManifest(manifest, data, manifest.content, None, "digest-only")
    if key is None:
        if require_key:
            raise KeyRequiredError("archive is encrypted; supply the key (--key-file or VNXDNA_KEY)")
        if not digest_ok:
            raise MetadataError("manifest digest mismatch: the manifest is corrupted or was modified")
        return LoadedManifest(manifest, data, None, None, "not-verified (no key)")
    keys = crypto.ArchiveKeys.derive(key, bytes.fromhex(manifest.encryption.salt or ""))
    if not crypto.mac_equal(manifest.encryption.key_check or "", keys.check.hex()):
        if not digest_ok:
            raise AuthenticationError("manifest authentication failed: manifest corrupted or tampered (key check also mismatched)")
        raise WrongKeyError("wrong key: the key-check value does not match this archive")
    if not crypto.mac_equal(manifest.seal.manifest_hmac_sha256 or "", crypto.mac(keys, payload)):
        raise AuthenticationError("manifest authentication failed: HMAC mismatch (manifest tampered or corrupted)")
    sealed = crypto.open_sealed(keys, bytes.fromhex(manifest.archive_id), crypto.DOMAIN_SEALED, 0, 1, manifest.sealed_bytes())
    content = mf.parse_content(sealed)
    mf.check_content(manifest, content)
    return LoadedManifest(manifest, data, content, keys, "hmac-sha256")


def body_source(manifest: mf.Manifest, body: bytes) -> ChunkSource:
    if len(body) != manifest.stored_size:
        raise InvalidInputError(f"container body is {len(body)} bytes but the manifest records {manifest.stored_size}")
    offsets = [0]
    for record in manifest.chunks:
        offsets.append(offsets[-1] + record.stored_size)

    def source(c: int, stats: Counter) -> bytes:
        stats["chunks_read_from_container"] += 1
        return body[offsets[c]:offsets[c + 1]]
    return source


@dataclass
class ChunkReadout:
    chunks: dict[int, bytes]
    failures: list[dict[str, Any]]
    stats: dict[str, Any]


class ContainerReader:
    def __init__(self, loaded: LoadedManifest, source: ChunkSource):
        self.loaded = loaded
        self.manifest = loaded.manifest
        self.content = loaded.content
        self.source = source

    # ---------------------------------------------------------------- stored layer (no key needed)
    def stored_chunk(self, c: int, stats: Counter) -> bytes:
        record = self.manifest.chunks[c]
        stored = self.source(c, stats)
        if len(stored) != record.stored_size or hashlib.sha256(stored).hexdigest() != record.stored_sha256:
            raise IntegrityError(f"chunk {c}: stored bytes do not match the manifest SHA-256", details={"chunk": c})
        stats["stored_chunks_verified"] += 1
        return stored

    def plain_chunk(self, c: int, stored: bytes, stats: Counter) -> bytes:
        if self.content is None:
            raise KeyRequiredError("archive is encrypted; a key is required to recover plaintext")
        if self.loaded.keys is not None:
            stored = crypto.open_sealed(self.loaded.keys, bytes.fromhex(self.manifest.archive_id), crypto.DOMAIN_CHUNK, c,
                                        len(self.manifest.chunks), stored)
            stats["chunks_authenticated"] += 1
        info = self.content.chunks[c]
        plain = compression.decompress(stored, self.manifest.compression.algorithm, info.size)
        if hashlib.sha256(plain).hexdigest() != info.sha256:
            raise IntegrityError(f"chunk {c}: plaintext SHA-256 mismatch", details={"chunk": c})
        stats["plaintext_chunks_verified"] += 1
        return plain

    def read_chunks(self, wanted: list[int], *, plaintext: bool = True, allow_partial: bool = False) -> ChunkReadout:
        n = len(self.manifest.chunks)
        for c in wanted:
            if isinstance(c, bool) or not isinstance(c, int) or not 0 <= c < n:
                raise InvalidInputError(f"chunk {c} does not exist (archive has {n} chunks: 0..{n - 1})")
        if plaintext and self.content is None:
            raise KeyRequiredError("archive is encrypted; a key is required to recover plaintext")
        stats: Counter = Counter()
        chunks: dict[int, bytes] = {}
        failures: list[dict[str, Any]] = []
        for c in sorted(set(wanted)):
            try:
                stored = self.stored_chunk(c, stats)
                chunks[c] = self.plain_chunk(c, stored, stats) if plaintext else stored
            except (InsufficientRedundancyError, IntegrityError, AuthenticationError) as error:
                if not allow_partial:
                    raise
                failures.append({"chunk": c, "error": error.category, "message": str(error)})
        return ChunkReadout(chunks, failures, dict(stats))

    def read_all(self) -> tuple[bytes, dict[str, Any]]:
        """Decode everything; the whole-object SHA-256 is recomputed here, not copied."""
        readout = self.read_chunks(list(range(len(self.manifest.chunks))))
        assert self.content is not None
        data = b"".join(readout.chunks[i] for i in range(len(self.manifest.chunks)))
        recomputed = hashlib.sha256(data).hexdigest()
        if recomputed != self.content.sha256 or len(data) != self.content.size:
            raise IntegrityError("whole-object SHA-256 mismatch after reconstruction",
                                 details={"expected_sha256": self.content.sha256, "recovered_sha256": recomputed})
        return data, {**readout.stats, "name": self.content.name, "size": len(data),
                      "expected_sha256": self.content.sha256, "recovered_sha256": recomputed, "sha256_match": True}

    def read_body(self) -> tuple[bytes, dict[str, Any]]:
        """All stored chunks, each verified against its SHA-256 (no key needed)."""
        readout = self.read_chunks(list(range(len(self.manifest.chunks))), plaintext=False)
        body = b"".join(readout.chunks[i] for i in range(len(self.manifest.chunks)))
        if hashlib.sha256(body).hexdigest() != self.manifest.stored_sha256:
            raise IntegrityError("stored body SHA-256 mismatch")
        return body, readout.stats

    def chunks_for_range(self, start: int, end: int | None) -> tuple[list[int], int, int]:
        if self.content is None:
            raise KeyRequiredError("archive is encrypted; a key is required to recover plaintext")
        size = self.content.size
        end = size if end is None else end
        if isinstance(start, bool) or not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= size:
            raise InvalidInputError(f"invalid byte range {start}:{end} for an object of {size} bytes")
        cs = self.manifest.chunk_size
        return (list(range(start // cs, (end - 1) // cs + 1)) if end > start else []), start, end

    def read_range(self, start: int, end: int | None = None) -> tuple[bytes, dict[str, Any]]:
        """Random access: decode only the chunks covering plaintext bytes [start, end)."""
        wanted, start, end = self.chunks_for_range(start, end)
        readout = self.read_chunks(wanted)
        blob = b"".join(readout.chunks[c] for c in wanted)
        offset = wanted[0] * self.manifest.chunk_size if wanted else start
        return blob[start - offset:end - offset], {**readout.stats, "range": [start, end], "chunks_decoded": wanted}
