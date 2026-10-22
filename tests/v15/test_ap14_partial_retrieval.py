"""AP-14: partial retrieval from a streamed archive (``vnxdna.pipeline.stream.read_range``).

A byte range of a ``vnx.stream/1`` archive is read by decoding only the extents the range overlaps; every extent is
decoded through the same per-extent decode the full stream decode uses, which verifies the extent's manifest SHA-256
before a byte of it is returned.

- the bytes of a range equal the slice of the full decode, for the edge ranges and for 30 seeded random ranges;
- the report names exactly the extents the range overlaps and the strand bytes the request had to touch;
- a corrupted extent fails only the ranges that touch it (the other ranges still return the right bytes);
- an invalid range (negative start or length, a range past the end) is a configuration error, never a partial slice;
- the report does not depend on the number of workers.
SIMULATED reads only.
"""
from __future__ import annotations

import hashlib
import json
import types

import numpy as np
import pytest
from typer.testing import CliRunner

from vnxdna.commands.cli import app
from vnxdna.core.errors import VNXConfigurationError, VNXError
from vnxdna.pipeline import stream as st
from vnxdna.recovery.options import DecodeOptions

EXTENT = 4096                 # the smallest extent (MIN_EXTENT): the stream has six of them
SEED = 20261022


def _data(n: int, seed: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8).tobytes()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A six-extent stream, its manifest and the bytes of the full decode (the reference for every range)."""
    d = tmp_path_factory.mktemp("ap14")
    data = _data(5 * EXTENT + 100, SEED)
    sink = d / "s.fasta"
    manifest = d / "s.fasta.stream.json"
    doc = st.encode_stream(data, sink, options=st.StreamOptions(extent_bytes=EXTENT), manifest=manifest)
    full = d / "full.bin"
    st.decode_stream(sink, manifest, full, options=DecodeOptions(workers=1))
    return types.SimpleNamespace(dir=d, data=data, sink=sink, manifest=manifest, doc=doc, full=full.read_bytes())


def _overlapping(doc: dict, start: int, length: int) -> list[int]:
    """The extent indices the range ``[start, start + length)`` overlaps (an empty range overlaps none)."""
    if length <= 0:
        return []
    out = []
    off = 0
    for rec in doc["extents"]:
        if off < start + length and start < off + rec["length"]:
            out.append(rec["index"])
        off += rec["length"]
    return out


def _ranges(total: int) -> list[tuple[int, int]]:
    """The edge ranges plus 30 seeded random ones."""
    edges = [(0, 0), (0, 1), (total - 1, 1), (total, 0), (0, total),
             (EXTENT, EXTENT), (EXTENT - 1, 2), (EXTENT - 10, 20), (2 * EXTENT - 5, 10), (total // 2, 1)]
    rng = np.random.default_rng(SEED)
    rand = []
    for _ in range(30):
        start = int(rng.integers(0, total))
        length = int(rng.integers(0, total - start + 1))
        rand.append((start, length))
    return edges + rand


def test_the_range_equals_the_slice_of_the_full_decode(built):
    total = len(built.full)
    assert len(built.doc["extents"]) >= 4
    for start, length in _ranges(total):
        data, rep = st.read_range(built.sink, built.manifest, start, length)
        assert data == built.full[start:start + length], (start, length)
        assert rep["range"] == [start, start + length]
        assert rep["extents"] == _overlapping(built.doc, start, length), (start, length)
        assert rep["bytes"] == len(data)
        assert rep["sha256"] == hashlib.sha256(data).hexdigest()
        assert rep["evidence"]["verified_extents"] == len(rep["extents"])


def test_only_the_extents_of_the_range_are_decoded(built):
    total = len(built.full)
    # one byte in the first extent: one extent is decoded, and far fewer strand bytes are read than the file holds
    data, rep = st.read_range(built.sink, built.manifest, 0, 1)
    assert rep["extents"] == [0] and data == built.full[:1]
    assert 0 < rep["strands_bytes_read"] < rep["strands_total_bytes"]
    assert rep["strands_total_bytes"] == sum(e["strands_bytes"] for e in built.doc["extents"])
    # the whole file: every extent, and every strand byte
    _, whole = st.read_range(built.sink, built.manifest, 0, total)
    assert whole["extents"] == [e["index"] for e in built.doc["extents"]]
    assert whole["strands_bytes_read"] == whole["strands_total_bytes"]
    # a range across an extent boundary touches exactly the two extents it covers
    _, across = st.read_range(built.sink, built.manifest, EXTENT - 10, 30)
    assert across["extents"] == [0, 1]
    # a range across two boundaries touches the three extents it covers
    _, three = st.read_range(built.sink, built.manifest, EXTENT - 10, 2 * EXTENT - 10)
    assert three["extents"] == [0, 1, 2]


def test_a_corrupted_extent_fails_only_the_ranges_that_touch_it(built, tmp_path):
    total = len(built.full)
    victim = 2
    rec = built.doc["extents"][victim]
    # the victim's strands are replaced by a run of one base: the extent can no longer be decoded
    sink = tmp_path / "corrupt.fasta"
    buf = bytearray(built.sink.read_bytes())
    seg = bytes(buf[rec["strands_offset"]:rec["strands_offset"] + rec["strands_bytes"]])
    buf[rec["strands_offset"]:rec["strands_offset"] + rec["strands_bytes"]] = bytes(65 if c in b"ACGTacgt" else c
                                                                                  for c in seg)
    sink.write_bytes(bytes(buf))
    # the ranges that touch the victim fail (never a partial or an unverified slice)
    for start, length in ((victim * EXTENT, 1), (victim * EXTENT + 10, 100), (victim * EXTENT - 5, 10),
                          (victim * EXTENT, EXTENT)):
        with pytest.raises(VNXError):
            st.read_range(sink, built.manifest, start, length)
    # the ranges that do not touch it still return the right bytes
    offset = 0
    for index, rec0 in enumerate(built.doc["extents"]):
        start = offset
        offset += rec0["length"]
        if index == victim:
            continue
        data, rep = st.read_range(sink, built.manifest, start, 100)
        assert data == built.full[start:start + 100]
        assert rep["extents"] == [index]
    # a range past the victim still fails (it covers the victim)
    with pytest.raises(VNXError):
        st.read_range(sink, built.manifest, 0, total)


def test_invalid_ranges_are_configuration_errors(built):
    total = len(built.full)
    for start, length in ((-1, 10), (0, -1), (-5, -5), (total + 1, 0), (total - 1, 2), (total, 1)):
        with pytest.raises(VNXConfigurationError):
            st.read_range(built.sink, built.manifest, start, length)
    for start, length in (("0", 10), (0, "10"), (True, 10), (0, True)):
        with pytest.raises(VNXConfigurationError):
            st.read_range(built.sink, built.manifest, start, length)


def test_the_report_does_not_depend_on_the_workers(built):
    _, one = st.read_range(built.sink, built.manifest, 100, 5000, options=DecodeOptions(workers=1))
    _, four = st.read_range(built.sink, built.manifest, 100, 5000, options=DecodeOptions(workers=4))
    assert one == four


def test_the_cli_reads_a_range(built, tmp_path):
    runner = CliRunner()
    out = tmp_path / "part.bin"
    res = runner.invoke(app, ["read-range", str(built.sink), str(built.manifest), "--start", "100", "--length", "500",
                              "-o", str(out)])
    assert res.exit_code == 0, res.output
    body = json.loads(res.stdout)
    assert body["kind"] == "stream-read-range" and body["status"] == "SUCCESS"
    assert body["result"]["range"] == [100, 600] and body["result"]["extents"] == [0]
    assert out.read_bytes() == built.full[100:600]
    # an invalid range is refused with the stable error contract (exit 7, configuration)
    bad = runner.invoke(app, ["read-range", str(built.sink), str(built.manifest), "--start", "-1", "--length", "10"])
    assert bad.exit_code == 7 and "CONFIGURATION_ERROR" in bad.output
