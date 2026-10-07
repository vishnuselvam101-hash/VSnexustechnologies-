"""Future VNX-RAM security interface (ARCHITECTURAL ONLY; docs/VNX_SECURE_ARCHITECTURE.md §8).

VNX-RAM hardware does not exist. This module fixes the software contract a future memory subsystem would have to meet
(authorize, verify, revoke, quarantine a region) and ships one in-process reference implementation over byte buffers,
used only to test the contract. It protects nothing outside this Python process.
"""
from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod


class MemoryAccessDenied(Exception):
    pass


class MemoryGuard(ABC):
    @abstractmethod
    def secure_memory_region(self, region: str, size: int, owner: str) -> None: ...

    @abstractmethod
    def authorize_memory_access(self, region: str, principal: str, op: str) -> bool: ...

    @abstractmethod
    def revoke_memory_access(self, region: str, principal: str) -> None: ...

    @abstractmethod
    def verify_memory_integrity(self, region: str) -> str: ...

    @abstractmethod
    def quarantine_memory_region(self, region: str, reason: str) -> None: ...


class SoftwareMemoryGuard(MemoryGuard):
    """Reference implementation (SIMULATED): regions are bytearrays tagged with HMAC-SHA256 after each authorized write."""

    def __init__(self, key: bytes):
        self.key, self.regions, self.acl, self.tags, self.quarantined = key, {}, {}, {}, {}

    def _tag(self, region: str) -> bytes:
        return hmac.new(self.key, region.encode() + bytes(self.regions[region]), hashlib.sha256).digest()

    def secure_memory_region(self, region, size, owner):
        if region in self.regions:
            raise ValueError("region exists")
        self.regions[region] = bytearray(size)
        self.acl[region] = {owner: {"read", "write"}}
        self.tags[region] = self._tag(region)

    def authorize_memory_access(self, region, principal, op):
        return region in self.regions and region not in self.quarantined and op in self.acl[region].get(principal, ())

    def grant(self, region, principal, ops):
        self.acl[region][principal] = set(ops)

    def revoke_memory_access(self, region, principal):
        self.acl.get(region, {}).pop(principal, None)

    def write(self, region, principal, offset, data):
        if not self.authorize_memory_access(region, principal, "write"):
            raise MemoryAccessDenied(f"{principal} may not write {region}")
        if self.verify_memory_integrity(region) != "VERIFIED":
            raise MemoryAccessDenied(f"{region} failed integrity; quarantine it")
        buf = self.regions[region]
        if offset < 0 or offset + len(data) > len(buf):
            raise MemoryAccessDenied("out of bounds")
        buf[offset:offset + len(data)] = data
        self.tags[region] = self._tag(region)

    def read(self, region, principal, offset, n):
        if not self.authorize_memory_access(region, principal, "read"):
            raise MemoryAccessDenied(f"{principal} may not read {region}")
        if self.verify_memory_integrity(region) != "VERIFIED":
            raise MemoryAccessDenied(f"{region} failed integrity")
        buf = self.regions[region]
        if offset < 0 or n < 0 or offset + n > len(buf):
            raise MemoryAccessDenied("out of bounds")
        return bytes(buf[offset:offset + n])

    def verify_memory_integrity(self, region):
        if region not in self.regions:
            return "UNKNOWN"
        return "VERIFIED" if hmac.compare_digest(self.tags[region], self._tag(region)) else "FAILED"

    def quarantine_memory_region(self, region, reason):
        self.quarantined[region] = reason
