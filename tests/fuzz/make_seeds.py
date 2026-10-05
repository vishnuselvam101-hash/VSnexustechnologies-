"""Regenerate the small seed corpora of the Python fuzz targets: ``PYTHONPATH=src:tests python -m fuzz.make_seeds``.

Seeds are deterministic (fixed archive ID, salt and TEST-ONLY key; no randomness) so that regenerating them gives the
same bytes. File names are content hashes (no extension: .gitignore ignores *.bin / *.fastq).
"""
from __future__ import annotations

import hashlib
import shutil
import struct

import numpy as np

from fuzz import harnesses as h


def _put(target: str, blobs: list[bytes]) -> None:
    d = h.CORPUS / target
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for b in blobs:
        (d / hashlib.sha1(b).hexdigest()).write_bytes(b)


def _superblocks() -> list[bytes]:
    from vnxdna.v4 import encoder as en
    from vnxdna.v4.frame import PROFILES
    lay, k, m = PROFILES["v4-balanced"]
    size = 10_000
    groups = -(-size // (k * lay.payload_bytes))
    sha = hashlib.sha256(b"seed").digest()
    v1 = en.Superblock("cauchy-rs", k, m, lay, "dense", 0, bytes(range(16)), size, sha, 64, groups).pack()
    v2 = en.Superblock("cauchy-rs", k, m, lay, "dense", 0, bytes(range(16)), size, sha, 64, groups, en.SB_VERSION_V6,
                       4, 2, "interleaved").pack()
    lt = en.Superblock("lt-fountain", 40, 20, lay, "dense", 7, bytes(16), size, sha, 0, -(-size // (40 * lay.payload_bytes))).pack()
    return [bytes([mode]) + sb for sb in (v1, v2, lt) for mode in (0, 1)] + [b"\x01" + v1[:50], b"\x07" + bytes(96)]


def _vxs() -> bytes:
    from vnxdna.v2 import strandio as sio
    rng = np.random.default_rng(3)
    nt = 37
    rb = -(-nt // 4)
    rec = rng.integers(0, 256, 5 * rb, dtype=np.uint8).tobytes()
    return b"\x01" + nt.to_bytes(2, "big") + rec


def main() -> None:
    a = h.seed_archives()
    _put("py-container", [a["plain"], a["enc"]])
    _put("py-manifest", [b"\x00", b"\x01", b"\x00\x02\x10\x00\x00\x05\x01\x07", b"\x01\x01\x20\x00\x00\x04",
                         b"\x02{}", b'\x03{"format":"VNX4"}'])
    _put("py-superblock", _superblocks())
    _put("py-frame", [bytes([p, 3, 1, 0, 0, 0, 7, 2, 0, 5, 9, 1, 3]) for p in range(4)] + [bytes([0, 1, 0]) + bytes(64)])
    _put("py-reads", [b"\x01\x02\x03\x01\x00\x01\x05ACGT\n+\nIIII\n@q\nAC\n+\n!!\n", b"\x02\x04\x03\x04\x00\x00\x07h\nACGTN\nAC\n>i\n\nG\n",
                      b"\x03\x04\x03\x05\x01\x00\x02ACGT\nNNNN\r\n\nacgt\n", b"\x00\x01\x02\x02\x02\x02\x03\x0bBAM\x01"])
    from vnxdna.v2 import strandio as sio
    _put("py-vxs", [_vxs(), b"\x03\x00\x05" + bytes(8), b"\x02" + bytes(40), sio.VXS_MAGIC + bytes(24)])
    _put("py-rs", [bytes([254, 16, 3, 0, 1, 2, 3, 4, 8] + [5, 7] * 8), bytes([39, 15, 2, 1]) + bytes(80),
                   bytes([60, 4, 1, 2, 9, 9, 9, 9, 3, 5, 1, 1, 6, 1, 0])])
    _put("py-align", [bytes([i, 3, 1, 1, 1, 1, 0, 20, 1, 4, 1, 2, 3, 4, 2, 0, 5, 1, 1, 7]) for i in range(4)]
         + [bytes([0, 5, 2, 2, 2, 2, 1, 10, 0, 2, 0, 0, 30]) + b"\x00\x01\x02\x03\x04\xff" * 5])
    _put("py-bomb", [bytes([0x01, 0x00, 0x10, 0x00, 0x10]), bytes([0x2b, 0x00, 0x10, 0x00, 0x10]),
                     bytes([0x05, 0x01, 0x00, 0x01, 0x00]), bytes([0x00, 0x00, 0x04]) + b"\x28\xb5\x2f\xfd" + bytes(12)])
    _put("py-decode", [b"\x01" + bytes(12), b"\x03\x00\x00\x00\x01" + bytes(8), b"\x05" + struct.pack(">I", 7) + bytes(4)
                       + b"\x10" + bytes(40), b"\x00>x\nACGT\n"])


if __name__ == "__main__":
    main()
