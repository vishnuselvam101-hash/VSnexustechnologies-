"""Round trips through the whole chain (store → encode → DNA → decode → restore) for every data category."""
from __future__ import annotations

import os

import pytest

from v2_support import FAST, KEY, write
from vnxdna.v2 import api
from vnxdna.v2.archive import restore_file, store_file
from vnxdna.v2.encoder import encode_file

CASES = {
    "empty": b"",
    "one-byte": b"\x00",
    "tiny-text": b"VNX",
    "unicode-text": "DNA データ ДНК 🧬 — ünïcödé\n".encode() * 300,
    "repetitive": b"A" * 50_000,
    "incompressible": os.urandom(30_000),
    "chunk-boundary": os.urandom(4096 * 3),
    "chunk-boundary-plus-one": os.urandom(4096 * 3 + 1),
}


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("key", [None, KEY], ids=["plain", "encrypted"])
@pytest.mark.parametrize("fmt", ["fasta", "vxs"])
def test_every_category_round_trips_through_dna(tmp_path, name, key, fmt):
    data = CASES[name]
    src = write(tmp_path / "in.bin", data)
    store_file(src, tmp_path / "a.vxdna", options=FAST, key=key, workers=1)
    restore_file(tmp_path / "a.vxdna", tmp_path / "c.bin", key=key)
    assert (tmp_path / "c.bin").read_bytes() == data
    encode_file(tmp_path / "a.vxdna", tmp_path / f"s.{fmt}", workers=1)
    r = api.recover(tmp_path / f"s.{fmt}", tmp_path / "o.bin", key=key, workers=1)
    assert r["status"] == "SUCCESS" and (tmp_path / "o.bin").read_bytes() == data
