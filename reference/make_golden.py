#!/usr/bin/env python3
"""Build the LR-1 golden corpus under ``reference/golden/``.

WHAT THIS DOES
    Generates a small, fixed set of VNX-DNA format-4 archives with the
    production encoder, writes each one twice (as a ``.vxdna`` container and
    as the FASTA strand pool it encodes to), and records in ``index.json``
    the SHA-256 of every artefact and of the original payload.

    The corpus pins the format: if a future change alters a single byte of a
    container, a strand or the manifest, the digests in ``index.json`` stop
    matching and the test fails.

    The corpus is deliberately small.  Every archive holds a few hundred
    bytes of payload, so the whole corpus is a few tens of kilobytes.

    The payload of each archive is a literal byte string, so the corpus is
    reproducible.  Three of the four archives regenerate byte for byte.  The
    encrypted one does not: the container builder draws a fresh random
    archive id per archive, and the AEAD key and the nonces are derived from
    it, so its container and its strand pool differ on every run while
    still decoding to the same payload.  Its *payload* digest is therefore
    the stable part of that entry; the container and FASTA digests pin the
    committed files, not the generator.

    ``--check`` verifies the committed corpus against the digests in
    index.json.  It does not regenerate, because regeneration of the
    encrypted entry is not byte-stable.

USAGE
    python3 reference/make_golden.py            # write the corpus
    python3 reference/make_golden.py --check    # verify the corpus without writing

references:
    src/vnxdna/api.py                (store_bytes, encode)
    src/vnxdna/container/builder.py  (StoreOptions)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src"))

from vnxdna.api import encode, store_bytes  # noqa: E402
from vnxdna.container.builder import StoreOptions  # noqa: E402

#: the key used by the encrypted corpus entry.  It is a published test key
#: for a synthetic archive with a synthetic payload; it protects nothing.
GOLDEN_KEY = bytes(range(32))

#: The corpus.  Each entry names the parameters of one archive.
#: - profile: the name used in the file names and in index.json
#: - payload: the deterministic payload bytes
#: - options: the StoreOptions for the archive
#: - key: the encryption key, or None
CORPUS: list[dict[str, Any]] = [
    {
        "profile": "plain-2bit",
        "payload": b"the quick brown fox jumps over the lazy dog\n" * 3,
        "options": {"data_shards": 8, "parity_shards": 4, "mapping": "2bit", "payload_bytes": 20,
                    "inner_parity_bytes": 4, "compression": "none", "compression_level": 0,
                    "chunk_size": 1024},
        "key": None,
    },
    {
        "profile": "zlib-rotation3",
        "payload": bytes(range(256)) * 2,
        "options": {"data_shards": 8, "parity_shards": 4, "mapping": "rotation3", "payload_bytes": 32,
                    "inner_parity_bytes": 8, "compression": "zlib", "compression_level": 9,
                    "chunk_size": 1024},
        "key": None,
    },
    {
        "profile": "codebook8-plain",
        "payload": b"".join(bytes([i % 251]) for i in range(400)),
        "options": {"data_shards": 8, "parity_shards": 4, "mapping": "codebook8", "payload_bytes": 16,
                    "inner_parity_bytes": 6, "compression": "none", "compression_level": 0,
                    "chunk_size": 4096},
        "key": None,
    },
    {
        "profile": "encrypted-2bit",
        "payload": b"the quick brown fox jumps over the lazy dog\n" * 3,
        "options": {"data_shards": 8, "parity_shards": 4, "mapping": "2bit", "payload_bytes": 20,
                    "inner_parity_bytes": 4, "compression": "none", "compression_level": 0,
                    "chunk_size": 1024},
        "key": GOLDEN_KEY,
    },
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build() -> dict[str, Any]:
    """Build every archive in memory and return the index."""
    entries = []
    for spec in CORPUS:
        options = StoreOptions(**spec["options"])
        payload = spec["payload"]
        key = spec["key"]
        blob = store_bytes(payload, options, key=key, name=f"{spec['profile']}.bin")
        container = HERE / "golden" / f"{spec['profile']}.vxdna"
        fasta = HERE / "golden" / f"{spec['profile']}.fasta"
        container.parent.mkdir(parents=True, exist_ok=True)
        container.write_bytes(blob)
        encode(container, fasta, overwrite=True)
        fasta_bytes = fasta.read_bytes()
        entries.append({
            "profile": spec["profile"],
            "container": container.name,
            "container_sha256": digest(blob),
            "container_bytes": len(blob),
            "fasta": fasta.name,
            "fasta_sha256": digest(fasta_bytes),
            "fasta_bytes": len(fasta_bytes),
            "payload_sha256": digest(payload),
            "payload_bytes": len(payload),
            "encrypted": key is not None,
            "options": spec["options"],
        })
    return {
        "format": "VNX-DNA",
        "format_version": 4,
        "description": "LR-1 golden corpus: the archives that pin the format-4 byte layout.",
        "generated_by": "reference/make_golden.py",
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify the corpus without writing it")
    args = parser.parse_args()

    index_path = HERE / "golden" / "index.json"
    if args.check:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        failures = []
        for entry in index["entries"]:
            for kind in ("container", "fasta"):
                path = HERE / "golden" / entry[kind]
                if not path.is_file():
                    failures.append(f"{path.name}: missing")
                    continue
                actual = digest(path.read_bytes())
                if actual != entry[f"{kind}_sha256"]:
                    failures.append(f"{path.name}: sha256 {actual} != recorded {entry[f'{kind}_sha256']}")
        for line in failures:
            print("FAIL:", line)
        print(f"golden corpus check: {'PASS' if not failures else 'FAIL'} ({len(index['entries'])} entries)")
        return 1 if failures else 0

    index = build()
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    total = sum(entry["container_bytes"] + entry["fasta_bytes"] for entry in index["entries"])
    print(f"wrote {len(index['entries'])} golden archives to {HERE / 'golden'} ({total} bytes)")
    for entry in index["entries"]:
        print(f"  {entry['profile']:16s} container {entry['container_bytes']:6d} B  fasta {entry['fasta_bytes']:6d} B  "
              f"payload {entry['payload_bytes']:4d} B  sha256 {entry['payload_sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
