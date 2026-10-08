"""Safe deception resources (VNX-Secure; docs/VNX_SECURE_ARCHITECTURE.md §6).

Decoys are names only, under the ``deception:`` namespace, listed alongside real resources so that enumeration can
find them. They hold no data, credentials or secrets: the default policy denies them to every role, and any request
for one is a CRITICAL finding (``deception`` detector), which contains the session. Nothing here opens sockets,
touches the file system or can act on another system. ``synthetic_content`` produces labelled filler bytes for
display in reports only.
"""
from __future__ import annotations

import hashlib

DEFAULT_DECOYS = ("deception:finance-2026-keys.vnx", "deception:customer-export.vnx", "deception:admin-backup.vnx")
LABEL = b"VNX-SECURE SYNTHETIC DECOY - NO REAL DATA\n"


class DeceptionEnvironment:
    def __init__(self, decoys: tuple = DEFAULT_DECOYS):
        if not all(d.startswith("deception:") for d in decoys):
            raise ValueError("decoy names must be in the deception: namespace")
        self.decoys = tuple(decoys)

    def listing(self, real: list[str]) -> list[str]:
        return sorted(set(real) | set(self.decoys))

    def is_decoy(self, resource: str) -> bool:
        return resource.startswith("deception:")

    @staticmethod
    def synthetic_content(name: str, size: int = 256) -> bytes:
        out, i = bytearray(LABEL), 0
        while len(out) < size:
            out += hashlib.sha256(f"{name}/{i}".encode()).digest()
            i += 1
        return bytes(out[:size])
