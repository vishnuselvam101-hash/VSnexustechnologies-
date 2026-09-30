"""Extended fuzz campaign (longer than the CI fuzz tests).

    python research/experiments/extended_fuzz.py [--mutations 3000]

1. Byte-level mutations (bit flips, deletions, insertions, truncation) of a
   plaintext and an encrypted .vxdna container, in three modes: raw file bytes,
   body mutated (any edit, or length-preserving bit flips) with a re-sealed file trailer, and manifest mutated with a
   re-sealed trailer. Each outcome must be the exact original data or a
   VNXDNAError.
2. Every manifest field replaced by each of 16 hostile values (or deleted).
   Each must be rejected with a VNXDNAError or leave the manifest unchanged.

Prints a summary. Exits 1 if any unstructured exception or wrong output occurs.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import sys

from vnxdna.api import store_bytes
from vnxdna.container import vxdna
from vnxdna.container.builder import StoreOptions
from vnxdna.container.reader import ContainerReader, body_source, load_manifest
from vnxdna.errors import VNXDNAError

KEY = hashlib.sha256(b"fuzz-only key").digest()
OPTIONS = StoreOptions(chunk_size=4096, data_shards=8, parity_shards=4, payload_bytes=24)
HOSTILE = [None, True, False, -1, 2 ** 70, "x", "", [], {}, 0, 1, "0" * 64, [1], {"a": 1}, "../x", "é"]


def restore(blob: bytes, key):
    cf = vxdna.parse(blob)
    loaded = load_manifest(cf.manifest_bytes, key)
    return ContainerReader(loaded, body_source(loaded.manifest, cf.body)).read_all()[0]


def mutate(blob: bytes, rng: random.Random) -> bytes:
    b = bytearray(blob)
    for _ in range(rng.randint(1, 8)):
        op, i = rng.randrange(4), rng.randrange(max(1, len(b)))
        if op == 0 and b:
            b[i] ^= 1 << rng.randrange(8)
        elif op == 1:
            del b[i:i + rng.randrange(1, 40)]
        elif op == 2:
            b[i:i] = rng.randbytes(rng.randrange(1, 20))
        else:
            b = b[: rng.randrange(len(b) + 1)]
    return bytes(b)


MODES = ("raw", "body-resealed", "body-bitflip-resealed", "manifest-resealed")


def apply_mode(blob: bytes, rng: random.Random, mode: str) -> bytes:
    """raw: mutate the file bytes (the trailer usually catches it).
    body-resealed / manifest-resealed: mutate one part, then rebuild the header and trailer,
    so the mutation reaches the manifest, chunk hash, AEAD and decompression layers."""
    if mode == "raw":
        return mutate(blob, rng)
    cf = vxdna.parse(blob)
    if mode == "body-resealed":
        return vxdna.serialize(cf.manifest_bytes, mutate(cf.body, rng))
    if mode == "body-bitflip-resealed":  # length-preserving: reaches chunk SHA-256 / AEAD / decompression
        body = bytearray(cf.body)
        for _ in range(rng.randint(1, 4)):
            body[rng.randrange(len(body))] ^= 1 << rng.randrange(8)
        return vxdna.serialize(cf.manifest_bytes, bytes(body))
    return vxdna.serialize(mutate(cf.manifest_bytes, rng), cf.body)


def paths(node, path):
    yield path
    if isinstance(node, dict):
        for k, v in node.items():
            yield from paths(v, path + [k])
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from paths(v, path + [i])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutations", type=int, default=3000)
    args = ap.parse_args()
    data = random.Random(1).randbytes(3000) + b"compressible " * 400
    targets = [(store_bytes(data, OPTIONS, None, "f"), None), (store_bytes(data, OPTIONS, KEY, "f"), KEY)]
    bad: collections.Counter = collections.Counter()
    outcomes: collections.Counter = collections.Counter()
    for seed in range(args.mutations):
        rng = random.Random(seed)
        for (blob, key), mode in [(t, m) for t in targets for m in MODES]:
            try:
                out = restore(apply_mode(blob, rng, mode), key)
                outcomes["exact" if out == data else "WRONG"] += 1
                if out != data:
                    bad["wrong output"] += 1
            except VNXDNAError as error:
                outcomes[error.category] += 1
            except Exception as error:  # noqa: BLE001 - this is what we are hunting for
                bad[f"{type(error).__name__}: {str(error)[:80]}"] += 1
    base = json.loads(vxdna.parse(targets[0][0]).manifest_bytes)
    field_cases = 0
    for path in [p for p in paths(base, []) if p]:
        for value in HOSTILE + ["<delete>"]:
            raw = json.loads(json.dumps(base))
            parent = raw
            for step in path[:-1]:
                parent = parent[step]
            if value == "<delete>":
                if not isinstance(parent, dict):
                    continue
                parent.pop(path[-1])
            else:
                parent[path[-1]] = value
            field_cases += 1
            try:
                load_manifest(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode(), None)
                if raw != base:
                    bad[f"accepted modified manifest at {path}"] += 1
            except VNXDNAError:
                pass
            except Exception as error:  # noqa: BLE001
                bad[f"{path}={value!r}: {type(error).__name__}"] += 1
    print(json.dumps({"container_mutations": len(targets) * len(MODES) * args.mutations, "outcomes": dict(outcomes), "manifest_field_cases": field_cases,
                      "problems": dict(bad)}, indent=2, sort_keys=True))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
