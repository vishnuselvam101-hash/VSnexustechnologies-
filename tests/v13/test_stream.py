"""V13 streaming encode/decode (vnxdna.pipeline.stream): a byte stream of any length -> extents -> ordinary archives ->
strands, and back, with bounded in-flight work.

- round trips (one and several workers, odd sizes, empty streams, byte iterators and file objects as sources) rebuild
  the stream byte for byte; the manifest (vnx.stream/1) records every extent with its archive ID and strand byte range;
- decoding uses each extent's strand range while the sink is the encoder's file, and falls back to the whole pool by
  archive ID when it is not (a SIMULATED sequenced pool of the whole stream);
- fail-closed: a tampered manifest, a truncated strand file, a wrong extent hash or a missing key never produce output;
- encrypted extents round-trip with the key and are refused without it.
SIMULATED reads only.
"""
from __future__ import annotations

import hashlib
import io
import json
import os

import numpy as np
import pytest

from vnxdna.archive.operations import ArchiveOptions
from vnxdna.core.errors import VNXError, VNXFormatError, VNXIntegrityError
from vnxdna.pipeline import stream as st
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.recovery.options import DecodeOptions
from vnxdna.v4 import channel as ch

KIB = 1 << 10


def _data(n: int, seed: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8).tobytes()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.mark.parametrize("workers,size", [(1, 3 * 64 * KIB + 17), (2, 5 * 64 * KIB - 1)])
def test_round_trip_by_range(tmp_path, workers, size):
    data = _data(size, 1301 + workers)
    src = tmp_path / "in.bin"
    src.write_bytes(data)
    sink = tmp_path / "s.fasta"
    opt = st.StreamOptions(extent_bytes=64 * KIB, workers=workers)
    doc = st.encode_stream(src, sink, options=opt)
    n = -(-size // (64 * KIB))
    assert doc["format"] == st.STREAM_FORMAT and doc["total_bytes"] == size and doc["sha256"] == _sha(data)
    assert len(doc["extents"]) == n and [e["index"] for e in doc["extents"]] == list(range(n))
    assert doc["strands_bytes"] == sink.stat().st_size and doc["strands_sha256"] == _sha(sink.read_bytes())
    man = st.load_manifest(tmp_path / "s.fasta.stream.json")
    assert man["extents"] == doc["extents"]
    lengths = [e["length"] for e in man["extents"]]
    assert sum(lengths) == size and all(x == 64 * KIB for x in lengths[:-1]) and 0 < lengths[-1] <= 64 * KIB
    assert len({e["archive_id"] for e in man["extents"]}) == n            # one archive per extent
    for e in man["extents"]:                                              # the ranges tile the sink
        assert e["strands_offset"] + e["strands_bytes"] <= doc["strands_bytes"]
    assert man["extents"][-1]["strands_offset"] + man["extents"][-1]["strands_bytes"] == doc["strands_bytes"]
    out = tmp_path / "out.bin"
    rep = st.decode_stream(sink, tmp_path / "s.fasta.stream.json", out, options=DecodeOptions(workers=workers))
    assert out.read_bytes() == data
    assert rep["extents"] == n and rep["by_range"] == n and rep["by_pool"] == 0 and rep["mb_s"] > 0


def test_sources_and_empty_stream(tmp_path):
    data = _data(100 * KIB + 5, 7)
    pieces = [data[i:i + 7777] for i in range(0, len(data), 7777)]
    a = st.encode_stream(iter(pieces), tmp_path / "a.fasta", options=st.StreamOptions(extent_bytes=32 * KIB))
    b = st.encode_stream(io.BytesIO(data), tmp_path / "b.fasta", options=st.StreamOptions(extent_bytes=32 * KIB))
    c = st.encode_stream(data, tmp_path / "c.fasta", options=st.StreamOptions(extent_bytes=32 * KIB))
    assert a["sha256"] == b["sha256"] == c["sha256"] == _sha(data) and a["extents"] == b["extents"] == c["extents"]
    assert (tmp_path / "a.fasta").read_bytes() == (tmp_path / "b.fasta").read_bytes()
    e = st.encode_stream(b"", tmp_path / "e.fasta", options=st.StreamOptions(extent_bytes=4096))
    assert e["extents"] == [] and e["total_bytes"] == 0 and (tmp_path / "e.fasta").stat().st_size == 0
    st.decode_stream(tmp_path / "e.fasta", tmp_path / "e.fasta.stream.json", tmp_path / "e.bin")
    assert (tmp_path / "e.bin").read_bytes() == b""
    with pytest.raises(VNXError):
        st.encode_stream(data, tmp_path / "a.fasta", options=st.StreamOptions(extent_bytes=32 * KIB))   # exists
    with pytest.raises(VNXError):
        st.StreamOptions(extent_bytes=100).validate()
    with pytest.raises(VNXError):
        st.encode_stream(data, "-", options=st.StreamOptions())                                        # stdout needs a manifest


def test_decode_from_a_simulated_pool_by_archive_id(tmp_path):
    data = _data(3 * 32 * KIB + 100, 99)
    sink = tmp_path / "s.fasta"
    st.encode_stream(data, sink, options=st.StreamOptions(extent_bytes=32 * KIB))
    reads = tmp_path / "r.fastq"
    ch.simulate_file(sink, reads, ch.ChannelConfig(seed=5, coverage=3, substitution_rate=0.01, insertion_rate=0.001,
                                                   deletion_rate=0.001))
    out = tmp_path / "out.bin"
    rep = st.decode_stream(reads, sink.with_name("s.fasta.stream.json"), out)
    assert out.read_bytes() == data and rep["by_pool"] == 4 and rep["by_range"] == 0
    # the same pool, every extent forced through the pool path and through the (now meaningless) ranges
    with pytest.raises(VNXError):
        st.decode_stream(reads, sink.with_name("s.fasta.stream.json"), tmp_path / "x.bin", by_range=True)
    assert not (tmp_path / "x.bin").exists()


def test_fail_closed(tmp_path):
    data = _data(2 * 16 * KIB, 3)
    sink = tmp_path / "s.fasta"
    man = tmp_path / "m.json"
    st.encode_stream(data, sink, options=st.StreamOptions(extent_bytes=16 * KIB), manifest=man)
    doc = json.loads(man.read_text())
    bad = tmp_path / "bad.json"
    # wrong whole-stream hash: every extent decodes, the final check refuses to publish
    bad.write_text(json.dumps({**doc, "sha256": "0" * 64}, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(VNXIntegrityError):
        st.decode_stream(sink, bad, tmp_path / "o1.bin")
    assert not (tmp_path / "o1.bin").exists()
    # wrong extent hash
    ext = [dict(e) for e in doc["extents"]]
    ext[1]["sha256"] = "1" * 64
    bad.write_text(json.dumps({**doc, "extents": ext}, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(VNXIntegrityError):
        st.decode_stream(sink, bad, tmp_path / "o2.bin")
    # truncated strand file
    cut = tmp_path / "cut.fasta"
    cut.write_bytes(sink.read_bytes()[: doc["extents"][1]["strands_offset"] + 100])
    with pytest.raises(VNXError):
        st.decode_stream(cut, man, tmp_path / "o3.bin")
    assert not (tmp_path / "o3.bin").exists()
    # not a manifest
    (tmp_path / "x.json").write_text("{}")
    with pytest.raises(VNXFormatError):
        st.load_manifest(tmp_path / "x.json")
    # malformed fields are format errors, never raw ValueError/TypeError from the decode
    for field, value in (("strands_offset", -1), ("length", "3"), ("archive_tag", "zz12"), ("archive_tag", "abcde"),
                         ("sha256", "0" * 63), ("strands_bytes", True)):
        ext = [dict(e) for e in doc["extents"]]
        ext[0][field] = value
        bad.write_text(json.dumps({**doc, "extents": ext}, sort_keys=True, separators=(",", ":")) + "\n")
        with pytest.raises(VNXFormatError):
            st.load_manifest(bad)
    bad.write_text(json.dumps({**doc, "total_bytes": doc["total_bytes"] + 1}, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(VNXFormatError):
        st.load_manifest(bad)


def test_encrypted_extents(tmp_path):
    key = bytes(range(32))
    data = _data(40 * KIB, 11)
    sink = tmp_path / "s.fasta"
    doc = st.encode_stream(data, sink, options=st.StreamOptions(extent_bytes=16 * KIB, archive=ArchiveOptions(key=key)))
    assert doc["encrypted"] is True
    out = tmp_path / "o.bin"
    st.decode_stream(sink, sink.with_name("s.fasta.stream.json"), out, key=key)
    assert out.read_bytes() == data
    with pytest.raises(VNXError):
        st.decode_stream(sink, sink.with_name("s.fasta.stream.json"), tmp_path / "o2.bin")
    assert not (tmp_path / "o2.bin").exists()


def test_extent_helpers_are_shared_building_blocks(tmp_path):
    data = _data(12 * KIB, 21)
    p = tmp_path / "e.bin"
    p.write_bytes(data)
    rec = st.encode_extent(p, tmp_path / "e.fasta", DNAOptions(), ArchiveOptions())
    assert rec["length"] == len(data) and rec["sha256"] == _sha(data) and rec["strands"] > 0
    assert st.decode_extent(tmp_path / "e.fasta", rec, DecodeOptions()) == data
    with pytest.raises(VNXIntegrityError):
        st.decode_extent(tmp_path / "e.fasta", {**rec, "sha256": "f" * 64}, DecodeOptions())
    assert os.path.exists(tmp_path / "e.fasta")
