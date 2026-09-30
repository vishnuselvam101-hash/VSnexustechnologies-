"""Confidentiality and authentication for format-4 archives.

Only established primitives from ``cryptography`` (OpenSSL) are used:

* **Key**: a 256-bit random master key supplied by the user (``vnx-dna keygen``).
  VNX-DNA never derives keys from passwords.
* **Key separation**: HKDF-SHA256 with a fresh random 128-bit per-archive salt
  derives three independent subkeys (``info`` labels below): an AEAD key, a
  manifest MAC key, and a key-check value.
* **Confidentiality + ciphertext integrity**: AES-256-GCM per chunk. The nonce is
  ``domain (4 bytes) ‖ index (8 bytes)``. Nonces are unique because every archive
  has its own random salt and therefore its own AEAD key, and within one archive
  each (domain, index) pair is used once. Associated data binds the ciphertext to
  the archive ID, chunk index and chunk count, which prevents chunk swapping,
  reordering and truncation.
* **Manifest authentication**: HMAC-SHA256 over the canonical manifest.
* **Wrong-key detection**: a 64-bit key-check value derived from the master key and
  salt lets the decoder tell a wrong key apart from a tampered manifest. It reveals
  nothing about the key beyond allowing guesses to be checked, which the MAC and
  ciphertext already allow.

Error correction and erasure coding are applied *after* encryption, so they
operate on ciphertext and never need the key.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..errors import AuthenticationError, ConfigurationError

KEY_BYTES = 32
SALT_BYTES = 16
TAG_BYTES = 16
DOMAIN_CHUNK = 0
DOMAIN_SEALED = 1
_AD_PREFIX = b"VNX-DNA/4 aead"


def generate_key() -> str:
    """Return a new random master key, base64url encoded (44 characters)."""
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode("ascii")


def parse_key(text: str | bytes) -> bytes:
    """Accept a 32-byte key as base64url/base64 (44 chars) or hex (64 chars)."""
    if isinstance(text, bytes):
        text = text.decode("ascii", errors="replace")
    text = text.strip()
    raw: bytes | None = None
    if len(text) == 64:
        try:
            raw = bytes.fromhex(text)
        except ValueError:
            raw = None
    if raw is None:
        try:
            raw = base64.b64decode(text.replace("-", "+").replace("_", "/"), validate=True)
        except (binascii.Error, ValueError):
            raw = None
    if raw is None or len(raw) != KEY_BYTES:
        raise ConfigurationError("key must be 32 bytes encoded as base64 (44 characters) or hex (64 characters)")
    return raw


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
        return cls(aead=_hkdf(master, salt, b"VNX-DNA/4 aead key"),
                   mac=_hkdf(master, salt, b"VNX-DNA/4 manifest mac key"),
                   check=_hkdf(master, salt, b"VNX-DNA/4 key check", 8))


def new_salt() -> bytes:
    return os.urandom(SALT_BYTES)


def _nonce(domain: int, index: int) -> bytes:
    return domain.to_bytes(4, "big") + index.to_bytes(8, "big")


def _ad(archive_id: bytes, domain: int, index: int, count: int) -> bytes:
    return _AD_PREFIX + archive_id + domain.to_bytes(4, "big") + index.to_bytes(8, "big") + count.to_bytes(8, "big")


def seal(keys: ArchiveKeys, archive_id: bytes, domain: int, index: int, count: int, plaintext: bytes) -> bytes:
    return AESGCM(keys.aead).encrypt(_nonce(domain, index), plaintext, _ad(archive_id, domain, index, count))


def open_sealed(keys: ArchiveKeys, archive_id: bytes, domain: int, index: int, count: int, ciphertext: bytes) -> bytes:
    if len(ciphertext) < TAG_BYTES:
        raise AuthenticationError("ciphertext is shorter than the authentication tag")
    try:
        return AESGCM(keys.aead).decrypt(_nonce(domain, index), ciphertext, _ad(archive_id, domain, index, count))
    except InvalidTag as error:
        what = "chunk" if domain == DOMAIN_CHUNK else "sealed metadata"
        raise AuthenticationError(f"AES-GCM authentication failed for {what} {index} (tampered ciphertext or wrong key)") from error


def mac(keys: ArchiveKeys, message: bytes) -> str:
    return hmac.new(keys.mac, message, hashlib.sha256).hexdigest()


def mac_equal(expected_hex: str, actual_hex: str) -> bool:
    return hmac.compare_digest(expected_hex.encode(), actual_hex.encode())
