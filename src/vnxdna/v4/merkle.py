"""Merkle tree over chunk records (RFC 6962 / Certificate-Transparency construction).

``leaf = SHA-256(0x00 ‖ record)``, ``node = SHA-256(0x01 ‖ left ‖ right)``. A
tree of n > 1 leaves splits at k, the largest power of two smaller than n
(left subtree k leaves, right subtree n − k). The empty tree's root is
SHA-256(""). Domain separation (0x00 / 0x01) prevents a leaf from being
presented as an interior node (second-preimage attack on naive trees).

The construction lets ``vnx verify --chunk i`` authenticate one chunk with
⌈log2 n⌉ hashes against the root recorded in the manifest, without reading or
decoding any other chunk.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence

EMPTY_ROOT = hashlib.sha256(b"").digest()


def leaf_hash(record: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + record).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def root_from_leaves(leaves: Sequence[bytes]) -> bytes:
    """Root over already-hashed leaves (iterative, O(n) memory for the level being built)."""
    n = len(leaves)
    if n == 0:
        return EMPTY_ROOT
    # RFC 6962 structure equals: repeatedly pair adjacent nodes left to right, carrying an odd last node up
    # unchanged. (Proof: the largest-power-of-two split keeps every complete left subtree aligned.)
    level = list(leaves)
    while len(level) > 1:
        nxt = [node_hash(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0]


def root_recursive(leaves: Sequence[bytes]) -> bytes:
    """Reference implementation that follows RFC 6962 literally (used by tests)."""
    n = len(leaves)
    if n == 0:
        return EMPTY_ROOT
    if n == 1:
        return leaves[0]
    k = _split(n)
    return node_hash(root_recursive(leaves[:k]), root_recursive(leaves[k:]))


def inclusion_proof(leaves: Sequence[bytes], index: int) -> list[bytes]:
    """Audit path for leaf ``index`` (RFC 6962 §2.1.1), bottom-up."""
    n = len(leaves)
    if not 0 <= index < n:
        raise IndexError("leaf index out of range")
    path: list[bytes] = []

    def walk(lo: int, hi: int, m: int) -> None:
        size = hi - lo
        if size == 1:
            return
        k = _split(size)
        if m < k:
            walk(lo, lo + k, m)
            path.append(root_from_leaves(leaves[lo + k:hi]))
        else:
            walk(lo + k, hi, m - k)
            path.append(root_from_leaves(leaves[lo:lo + k]))

    walk(0, n, index)
    return path


def verify_inclusion(leaf: bytes, index: int, size: int, proof: Sequence[bytes], root: bytes) -> bool:
    """RFC 9162 §2.1.3.2 verification of an audit path."""
    if not 0 <= index < size:
        return False
    fn, sn = index, size - 1
    r = leaf
    for p in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            if not fn & 1:
                while fn and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == root
