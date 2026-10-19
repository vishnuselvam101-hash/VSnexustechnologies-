"""Sealed extent format ``vnx.sealed-extent/1`` (docs/V14_SECURITY_MODEL.md §3); the cipher behind ``vnxd``'s
``SealedMedia``.

Only the established primitives of the VNX-DNA security layer are used (``cryptography``/OpenSSL): AES-256-GCM and
HKDF-SHA256. Layout (integers little-endian)::

    offset  size  field
         0     4  magic "VNXS"
         4     2  format version (1)
         6     2  algorithm (1 = AES-256-GCM)
         8    16  salt (random, fresh for every sealed object)
        24     8  key check (first 8 bytes of HKDF(master, salt=device_id, info="VNX-DNA/14 key check"))
        32     8  plaintext length n
        40     n  ciphertext
      40+n    16  GCM tag

* key: ``HKDF-SHA256(master, salt=salt, info="VNX-DNA/14 extent key" || device_id || extent || generation)``. A fresh
  random 128-bit salt per object gives every object its own key, so the fixed nonce is never reused under a key, even
  if a device is re-created with the same id and key (no reliance on generation numbers for nonce uniqueness);
* associated data: the 40-byte header, ``"VNX-DNA/14 sealed extent"``, device_id, extent and generation (u64
  big-endian): an object moved to another extent, generation or device fails authentication;
* ``open`` refuses with CORRUPTION for anything that does not authenticate, a key-check mismatch included (never
  returns other bytes); the wrong key for a device is reported as UNAUTHORIZED once, by ``vnxd`` at open, from the
  key check stored with the backend's marker.
"""
from __future__ import annotations

import hmac
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .contracts import DeviceError

MAGIC = b"VNXS"
VERSION = 1
ALG_AES256GCM = 1
HEADER = struct.Struct("<4sHH16s8sQ")
TAG_BYTES = 16
_NONCE = b"VNX-DNA/14\x00\x01"   # 12 bytes; unique per key because every object has its own key
_AAD_LABEL = b"VNX-DNA/14 sealed extent"
U64_MAX = (1 << 64) - 1


def _hkdf(master: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(master)


def _check(master: bytes, device_id: bytes, extent: int, generation: int) -> None:
    if len(master) != 32:
        raise DeviceError("INVALID_ARGUMENT", "the key must be 32 bytes")
    if len(device_id) != 16:
        raise DeviceError("INVALID_ARGUMENT", "device_id must be 16 bytes")
    if not (0 <= extent <= U64_MAX and 1 <= generation <= U64_MAX):
        raise DeviceError("INVALID_ARGUMENT", "extent or generation out of range")


def key_check(master: bytes, device_id: bytes) -> bytes:
    """32-byte key check value of ``master`` for one device (identifies the key, does not reveal it)."""
    _check(master, device_id, 0, 1)
    return _hkdf(master, device_id, b"VNX-DNA/14 key check")


def _binding(device_id: bytes, extent: int, generation: int) -> bytes:
    return device_id + struct.pack(">QQ", extent, generation)


def seal(master: bytes, device_id: bytes, extent: int, generation: int, plain: bytes) -> bytes:
    return _seal_with_salt(master, device_id, extent, generation, plain, os.urandom(16))


def _seal_with_salt(master: bytes, device_id: bytes, extent: int, generation: int, plain: bytes, salt: bytes) -> bytes:
    """Known-answer tests only: a repeated salt repeats the key and the nonce."""
    _check(master, device_id, extent, generation)
    if len(salt) != 16:
        raise DeviceError("INVALID_ARGUMENT", "salt must be 16 bytes")
    header = HEADER.pack(MAGIC, VERSION, ALG_AES256GCM, salt, key_check(master, device_id)[:8], len(plain))
    binding = _binding(device_id, extent, generation)
    key = _hkdf(master, salt, b"VNX-DNA/14 extent key" + binding)
    return header + AESGCM(key).encrypt(_NONCE, bytes(plain), header + _AAD_LABEL + binding)


def open_sealed(master: bytes, device_id: bytes, extent: int, generation: int, sealed: bytes) -> bytes:
    _check(master, device_id, extent, generation)
    if len(sealed) < HEADER.size + TAG_BYTES:
        raise DeviceError("CORRUPTION", "sealed extent shorter than its header and tag")
    magic, version, alg, salt, kcv, n = HEADER.unpack_from(sealed)
    if magic != MAGIC:
        raise DeviceError("CORRUPTION", "not a sealed extent (bad magic)")
    if version != VERSION:
        raise DeviceError("VERSION_MISMATCH", f"sealed extent format version {version} (supported: 1)")
    if alg != ALG_AES256GCM:
        raise DeviceError("CORRUPTION", f"unknown sealing algorithm {alg}")
    if len(sealed) != HEADER.size + n + TAG_BYTES:
        raise DeviceError("CORRUPTION", f"sealed extent of {len(sealed)} bytes announces {n} plaintext bytes")
    if not hmac.compare_digest(kcv, key_check(master, device_id)[:8]):
        # the device's key is verified once, at open (the backend marker): here a mismatch is a tampered header or
        # an object of another device or key, all of which are damage to this extent
        raise DeviceError("CORRUPTION", "sealed extent's key check does not match (another key, device, or a tampered header)")
    binding = _binding(device_id, extent, generation)
    key = _hkdf(master, salt, b"VNX-DNA/14 extent key" + binding)
    header = sealed[:HEADER.size]
    try:
        return AESGCM(key).decrypt(_NONCE, sealed[HEADER.size:], header + _AAD_LABEL + binding)
    except InvalidTag:
        raise DeviceError("CORRUPTION", f"sealed extent {extent} generation {generation} failed authentication") from None
