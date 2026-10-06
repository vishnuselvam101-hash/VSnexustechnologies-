"""Pure functions of the V7 protocol section 4.1 (data split rule). Standard library only; no data access.

Amendment 1 (docs/V7_PROTOCOL.md): a held-out run exists only where runs have disjoint reference sets; every reference gets
a bucket b = int(SHA-256(canonical sequence), 16) mod 10 (canonical = the lexicographically smaller of the sequence and its
reverse complement, upper-case ASCII). FIT = buckets 0-5, DEV = 6-7, HELDOUT = 8-9 plus the held-out run.
"""
from __future__ import annotations

import hashlib

_COMP = bytes.maketrans(b"ACGTN", b"TGCAN")

FIT, DEV, HELDOUT = "FIT", "DEV", "HELDOUT"
SPLITS = (FIT, DEV, HELDOUT)


def canonical(seq: bytes) -> bytes:
    seq = seq.strip().upper()
    return min(seq, seq.translate(_COMP)[::-1])


def bucket(seq: bytes) -> int:
    return int(hashlib.sha256(canonical(seq)).hexdigest(), 16) % 10


def bucket_split(b: int) -> str:
    if not 0 <= b <= 9:
        raise ValueError(b)
    return FIT if b <= 5 else DEV if b <= 7 else HELDOUT


def heldout_run(dataset_id: str, runs: list[str]) -> str:
    """Held-out run of a dataset whose runs have disjoint reference sets: runs sorted by name, index
    int(SHA-256('VNX-V7-HELDOUT/' + dataset_id), 16) mod n_runs."""
    rs = sorted(runs)
    return rs[int(hashlib.sha256(("VNX-V7-HELDOUT/" + dataset_id).encode()).hexdigest(), 16) % len(rs)]


def list_sha256(items) -> str:
    """SHA-256 of a list as written to disk: one item per line, each terminated by a newline."""
    return hashlib.sha256("".join(f"{i}\n" for i in items).encode()).hexdigest()
