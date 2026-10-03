"""V4 cryptography: established primitives only (AES-256-GCM, HKDF-SHA256, HMAC-SHA256, scrypt).

Order of operations (documented in docs/VNX4_FORMAT.md §6): plaintext chunk →
compression → AES-256-GCM. Compression must precede encryption because
ciphertext is incompressible; the leak this creates (per-chunk compressed size)
is documented in docs/SECURITY.md.

Keys
----
* a 32-byte **key file** (raw or 64 hex characters), or
* a **passphrase** stretched with scrypt (N = 2^15, r = 8, p = 1 by default;
  parameters and salt are recorded in the manifest, never the key).

``master → HKDF-SHA256(salt = archive salt, info = label)`` derives independent
keys for chunk encryption, the manifest/table MAC, the keyed chunk identifiers
and a 16-byte key check. A wrong key is detected by the key check before any
decryption is attempted.

Nonces are ``domain (4) ‖ index (8)``. They are unique under one key because
the key is derived from a fresh random 16-byte salt for every archive and every
(domain, index) pair is used once per archive. AES-GCM associated data binds
the archive ID, domain, index and the total count, so reordering, truncation and
splicing are detected.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .errors import VNXConfigurationError, VNXIntegrityError, VNXKeyError

TAG_BYTES = 16
SALT_BYTES = 16
DOMAIN_CHUNK = 0
DOMAIN_FILE_TABLE = 1
DOMAIN_REFS = 2
SCRYPT_DEFAULT = {"n": 1 << 15, "r": 8, "p": 1}
SCRYPT_MAX_N = 1 << 20


def load_key_file(path: str | os.PathLike) -> bytes:
    """32-byte key from a small regular file: 32 raw bytes, 64 hex characters or 44 base64 characters (V3 key files)."""
    import base64
    import stat as _stat
    p = Path(path)
    try:
        with open(p, "rb") as handle:
            if not _stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise VNXKeyError(f"key file {p} is not a regular file")
            raw = handle.read(1025)          # bounded: never read /dev/zero or a huge file into memory
    except OSError as error:
        raise VNXKeyError(f"cannot read key file {p}: {error.strerror or error}") from None
    if len(raw) > 1024:
        raise VNXKeyError(f"key file {p} is too large to be a 32-byte key")
    text = raw.strip()
    if len(text) == 64:
        try:
            return bytes.fromhex(text.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            pass
    if len(text) == 44:
        try:
            key = base64.b64decode(text, validate=True)
            if len(key) == 32:
                return key
        except ValueError:
            pass
    if len(raw) == 32:
        return raw
    raise VNXKeyError(f"key file {p} must contain 32 raw bytes, 64 hex or 44 base64 characters")


def generate_key_file(path: str | os.PathLike) -> None:
    p = Path(path)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(os.urandom(32).hex() + "\n")


def scrypt_master(passphrase: str, salt: bytes, params: dict) -> bytes:
    n, r, p = int(params["n"]), int(params["r"]), int(params["p"])
    if n < 2 or n & (n - 1) or n > SCRYPT_MAX_N or not 1 <= r <= 32 or not 1 <= p <= 16:
        raise VNXConfigurationError(f"unsupported scrypt parameters {params} (N power of two ≤ 2^20, r ≤ 32, p ≤ 16)")
    if not passphrase:
        raise VNXKeyError("empty passphrase")
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))


def _hkdf(master: bytes, salt: bytes, label: str, length: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=f"VNX4 {label}".encode()).derive(master)


@dataclass(frozen=True)
class ArchiveKeys:
    aead: bytes
    mac: bytes
    chunk_id: bytes
    key_check: bytes

    @classmethod
    def derive(cls, master: bytes, salt: bytes) -> "ArchiveKeys":
        if len(master) != 32:
            raise VNXKeyError("master key must be 32 bytes")
        return cls(_hkdf(master, salt, "aead key"), _hkdf(master, salt, "mac key"), _hkdf(master, salt, "chunk id key"),
                   _hkdf(master, salt, "key check", 16))

    def check(self, expected_hex: str) -> None:
        if not hmac.compare_digest(self.key_check.hex(), expected_hex):
            raise VNXKeyError("wrong key or passphrase (key check does not match)", hint="use the key that created the archive")


def nonce(domain: int, index: int) -> bytes:
    return domain.to_bytes(4, "big") + index.to_bytes(8, "big")


def aad(archive_id: bytes, domain: int, index: int, count: int) -> bytes:
    return b"VNX4 aead\x00" + archive_id + domain.to_bytes(4, "big") + index.to_bytes(8, "big") + count.to_bytes(8, "big")


class Sealer:
    """AES-256-GCM sealing bound to an archive ID. ``count`` is the number of items in the domain."""

    def __init__(self, keys: ArchiveKeys, archive_id: bytes):
        self.keys = keys
        self.archive_id = archive_id
        self._aead = AESGCM(keys.aead)

    def seal(self, domain: int, index: int, count: int, data: bytes) -> bytes:
        return self._aead.encrypt(nonce(domain, index), data, aad(self.archive_id, domain, index, count))

    def open(self, domain: int, index: int, count: int, data: bytes, what: str) -> bytes:
        try:
            return self._aead.decrypt(nonce(domain, index), data, aad(self.archive_id, domain, index, count))
        except InvalidTag:
            raise VNXIntegrityError(f"authentication failed for {what} (tampered, truncated, reordered or wrong key)",
                                    stage="crypto") from None

    def mac(self, data: bytes) -> bytes:
        return hmac.new(self.keys.mac, b"VNX4 manifest\x00" + data, hashlib.sha256).digest()

    def chunk_id(self, plaintext: bytes) -> bytes:
        return hmac.new(self.keys.chunk_id, plaintext, hashlib.sha256).digest()


def public_chunk_id(plaintext: bytes) -> bytes:
    """Content address of an unencrypted chunk."""
    return hashlib.sha256(b"VNX4 chunk\x00" + plaintext).digest()
