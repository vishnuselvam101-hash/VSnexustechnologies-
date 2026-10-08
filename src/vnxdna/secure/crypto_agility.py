"""Cryptographic algorithm registry and replaceable AEAD interface (VNX-Secure; docs/VNX_SECURE_SECURITY_MODEL.md §9).

The registry states, for each algorithm VNX uses or plans to use, its purpose, key size, implementation source and
status: IMPLEMENTED (used in this code base today), PLANNED (not implemented; a migration target) or EXPERIMENTAL.
No algorithm is invented here; every IMPLEMENTED entry is provided by the ``cryptography`` package (OpenSSL) or by
Python's ``hashlib``/``hmac``. The archive format records its AEAD by name (``manifest.encryption.algorithm``), so a
future suite can be added under a new name without redesigning the archive layout.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class Algorithm:
    name: str
    purpose: str
    key_bits: int
    source: str
    status: str             # IMPLEMENTED, PLANNED, EXPERIMENTAL
    assumptions: str
    quantum: str            # effect of a large quantum computer, as generally assessed
    migration: str


REGISTRY = (
    Algorithm("AES-256-GCM", "archive content and metadata encryption (vnxdna.archive.crypto)", 256, "cryptography (OpenSSL)",
              "IMPLEMENTED", "unique (key, nonce) per message; nonces are derived per domain and index", "Grover: ~128-bit margin remains",
              "keep; add a name-tagged suite if a replacement is needed"),
    Algorithm("HMAC-SHA256", "audit chain, tokens, state/baseline authentication (vnxdna.secure)", 256, "Python hmac/hashlib",
              "IMPLEMENTED", "secret key; SHA-256 as a PRF", "Grover: margin remains at 256-bit keys", "keep"),
    Algorithm("SHA-256", "chunk/file/stored digests, Merkle tree, integrity snapshot", 0, "Python hashlib", "IMPLEMENTED",
              "collision and second-preimage resistance", "collision search ~2^85 (BHT); considered adequate", "keep"),
    Algorithm("scrypt", "passphrase-based archive keys; principal secrets (vnxdna.secure)", 256, "cryptography / hashlib (OpenSSL)",
              "IMPLEMENTED", "memory-hard KDF; cost recorded with each hash", "not materially affected", "raise cost as hardware improves"),
    Algorithm("HKDF-SHA256", "archive sub-key derivation", 256, "cryptography (OpenSSL)", "IMPLEMENTED", "PRF assumption on HMAC",
              "not materially affected", "keep"),
    Algorithm("ML-KEM-768 (FIPS 203)", "future key encapsulation for multi-party archive key exchange", 0, "none in this build",
              "PLANNED", "module-lattice assumptions", "designed to resist known quantum attacks",
              "introduce when a reviewed implementation is available in the dependency set; hybrid with X25519 first"),
    Algorithm("ML-DSA-65 (FIPS 204)", "future signed manifests and release attestations", 0, "none in this build", "PLANNED",
              "module-lattice assumptions", "designed to resist known quantum attacks",
              "introduce alongside Ed25519 (hybrid) when a reviewed implementation is available"),
)


def registry() -> list[dict]:
    return [asdict(a) for a in REGISTRY]


class AEAD:
    """Replaceable authenticated encryption: ``seal``/``open`` with explicit nonce and associated data."""
    name = ""
    key_bytes = 0
    nonce_bytes = 0

    def seal(self, key: bytes, nonce: bytes, data: bytes, aad: bytes) -> bytes:
        raise NotImplementedError

    def open(self, key: bytes, nonce: bytes, data: bytes, aad: bytes) -> bytes:
        raise NotImplementedError


class AES256GCM(AEAD):
    name, key_bytes, nonce_bytes = "AES-256-GCM", 32, 12

    def seal(self, key, nonce, data, aad):
        return AESGCM(key).encrypt(nonce, data, aad)

    def open(self, key, nonce, data, aad):
        return AESGCM(key).decrypt(nonce, data, aad)


SUITES = {"AES-256-GCM": AES256GCM}


def aead(name: str) -> AEAD:
    """The implementation for a suite name; a PLANNED or unknown suite raises (fail closed)."""
    if name not in SUITES:
        planned = [a.name for a in REGISTRY if a.status == "PLANNED"]
        raise NotImplementedError(f"AEAD suite {name!r} is not implemented (planned: {planned})")
    return SUITES[name]()
