"""Streaming authenticated encryption for archive format 5.

Only established primitives from ``cryptography`` (OpenSSL) are used. The
construction is the V1 one with format-5 domain labels:

* **Key**: a 256-bit random master key (``vnx-dna keygen``). No password KDF.
* **Key separation**: HKDF-SHA256 with a fresh random 128-bit salt per archive
  derives an AEAD key, a manifest MAC key and a 64-bit key-check value. The
  ``info`` labels start with ``VNX-DNA/5``, so format-4 and format-5 keys differ
  even for the same master key and salt.
* **Chunked AEAD (streaming)**: each chunk is sealed independently with
  AES-256-GCM, so memory is bounded by the chunk size whatever the file size.

  - nonce = (epoch ≪ 8 | domain) (4 bytes) ‖ chunk index (8 bytes). The AEAD
    key is unique per archive (random salt). Within an archive each (epoch,
    domain, index) is used once: a fresh store seals every chunk in epoch 0,
    and each ``store --resume`` seals the chunks it (re)writes in the next
    epoch, recorded per chunk in the authenticated chunk index. A chunk that
    was sealed and written just before an interruption and is sealed again
    after the resume therefore never reuses a nonce, even if the input
    changed in between.
  - associated data = ``"VNX-DNA/5 aead"`` ‖ archive ID ‖ (epoch ≪ 8 | domain)
    ‖ index ‖ chunk count.

  This detects a **modified** chunk (GCM tag), a **reordered** or **duplicated**
  chunk (the index is bound into the nonce and AD, so a chunk decrypts only at
  its own position), a **missing** or **truncated** archive (the chunk count is
  bound into every chunk and into the authenticated manifest), and splicing
  between archives (archive ID in the AD). The chunk count is known before the
  first chunk is sealed because ``store`` takes a regular file of known size and
  fails if the size changes while it is being read.
* **Manifest and index authentication**: HMAC-SHA256 over the canonical
  manifest. The manifest records the SHA-256 of the binary chunk index and of
  the (sealed) plaintext index, so the HMAC covers them too.
* **Downgrade protection**: ``format_version`` is inside the HMAC, and the
  ``VNX-DNA/5`` prefix in every label and AD means format-5 ciphertext cannot be
  presented to the format-4 decoder or the other way round.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..container.crypto import KEY_BYTES, SALT_BYTES, TAG_BYTES, generate_key, parse_key  # noqa: F401 (re-exported)
from ..errors import AuthenticationError, ConfigurationError

DOMAIN_CHUNK = 0
DOMAIN_CONTENT = 1
DOMAIN_PLAIN_INDEX = 2
_AD_PREFIX = b"VNX-DNA/5 aead"


def _hkdf(master: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(master)


@dataclass(frozen=True)
class ArchiveKeys:
    aead: bytes
    mac: bytes
    check: bytes

    @classmethod
    def derive(cls, master: bytes, salt: bytes) -> "ArchiveKeys":
        if len(master) != KEY_BYTES or len(salt) != SALT_BYTES:
            raise ConfigurationError("invalid key or salt length")
        return cls(aead=_hkdf(master, salt, b"VNX-DNA/5 aead key"),
                   mac=_hkdf(master, salt, b"VNX-DNA/5 manifest mac key"),
                   check=_hkdf(master, salt, b"VNX-DNA/5 key check", 8))


def new_salt() -> bytes:
    return os.urandom(SALT_BYTES)


MAX_EPOCH = 255


def _tagged(domain: int, epoch: int) -> int:
    if not 0 <= epoch <= MAX_EPOCH:
        raise ConfigurationError(f"AEAD epoch must be in 0..{MAX_EPOCH}")
    return (epoch << 8) | domain


def _nonce(domain: int, index: int) -> bytes:
    return domain.to_bytes(4, "big") + index.to_bytes(8, "big")


def _ad(archive_id: bytes, domain: int, index: int, count: int) -> bytes:
    return _AD_PREFIX + archive_id + domain.to_bytes(4, "big") + index.to_bytes(8, "big") + count.to_bytes(8, "big")


class ChunkCipher:
    """Seals and opens the chunks of one archive (one AESGCM object, reused)."""

    def __init__(self, keys: ArchiveKeys, archive_id: bytes, chunk_count: int):
        self.keys = keys
        self.archive_id = archive_id
        self.chunk_count = chunk_count
        self._aead = AESGCM(keys.aead)

    def seal(self, domain: int, index: int, plaintext: bytes, count: int | None = None, epoch: int = 0) -> bytes:
        count = self.chunk_count if count is None else count
        tagged = _tagged(domain, epoch)
        return self._aead.encrypt(_nonce(tagged, index), plaintext, _ad(self.archive_id, tagged, index, count))

    def open(self, domain: int, index: int, ciphertext: bytes, count: int | None = None, epoch: int = 0) -> bytes:
        count = self.chunk_count if count is None else count
        if len(ciphertext) < TAG_BYTES:
            raise AuthenticationError("ciphertext is shorter than the authentication tag")
        tagged = _tagged(domain, epoch)
        try:
            return self._aead.decrypt(_nonce(tagged, index), ciphertext, _ad(self.archive_id, tagged, index, count))
        except InvalidTag as error:
            what = {DOMAIN_CHUNK: "chunk", DOMAIN_CONTENT: "sealed content", DOMAIN_PLAIN_INDEX: "sealed plaintext index"}[domain]
            raise AuthenticationError(f"AES-GCM authentication failed for {what} {index} (tampered ciphertext or wrong key)",
                                      details={"domain": what, "index": index}) from error


def mac(keys: ArchiveKeys, message: bytes) -> str:
    return hmac.new(keys.mac, message, hashlib.sha256).hexdigest()


def mac_equal(expected_hex: str, actual_hex: str) -> bool:
    return hmac.compare_digest(expected_hex.encode(), actual_hex.encode())
