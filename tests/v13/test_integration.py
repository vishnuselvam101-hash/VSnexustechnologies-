"""V13 integration: the SDK functions and `vnx` commands for streaming encode/decode, the volume and the decode result
cache, and the result cache inside sdk.decode (hit/miss/invalidation/eviction). SIMULATED reads only."""
from __future__ import annotations

import json
import os

import numpy as np
import pytest
from typer.testing import CliRunner

from vnxdna import sdk
from vnxdna.commands.cli import app
from vnxdna.core import schema
from vnxdna.sdk import cache as sc
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import encoder as en

KIB = 1 << 10


def _check(res: sdk.Result) -> dict:
    doc = res.to_json()
    schema.validate(doc, "vnx.result/1")
    schema.validate(res.to_cli(), "vnx.result/1")
    json.dumps(doc, default=str)
    return doc


def _data(n: int, seed: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8).tobytes()


@pytest.fixture(scope="module")
def reads(tmp_path_factory):
    d = tmp_path_factory.mktemp("v13int")
    datagen.generate(d / "in.bin", 120_000, "random", 13201)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    ch.simulate_file(d / "s.fasta", d / "r.fastq", ch.ChannelConfig(seed=19, coverage=4, substitution_rate=0.01,
                                                                    insertion_rate=0.002, deletion_rate=0.002))
    return {"archive": d / "a.vnx", "reads": d / "r.fastq"}


# ------------------------------------------------------------------------------------------------------ SDK stream
def test_sdk_stream_round_trip(tmp_path):
    data = _data(2 * 64 * KIB + 99, 1)
    (tmp_path / "in.bin").write_bytes(data)
    r = sdk.stream_encode(tmp_path / "in.bin", tmp_path / "s.fasta", extent_bytes=64 * KIB)
    doc = _check(r)
    assert doc["kind"] == "stream-encode" and doc["status"] == "SUCCESS" and doc["formats"]["stream"] == "vnx.stream/1"
    assert doc["result"]["total_bytes"] == len(data) and len(doc["result"]["extents"]) == 3
    assert [o["role"] for o in doc["outputs"]] == ["strands", "manifest"]
    r2 = sdk.stream_decode(tmp_path / "s.fasta", tmp_path / "s.fasta.stream.json", tmp_path / "out.bin")
    doc2 = _check(r2)
    assert doc2["kind"] == "stream-decode" and (tmp_path / "out.bin").read_bytes() == data
    assert doc2["result"]["by_range"] == 3
    r3 = sdk.stream_encode(data, tmp_path / "b.fasta", extent_bytes=64 * KIB, manifest=tmp_path / "b.json")
    assert r3.body["manifest"] == str(tmp_path / "b.json") and r3.inputs == ()


# ------------------------------------------------------------------------------------------------------ SDK volume
def test_sdk_volume(tmp_path):
    data = _data(64 * KIB + 500, 2)
    (tmp_path / "in.bin").write_bytes(data)
    c = _check(sdk.volume_create(tmp_path / "v", extent_bytes=32 * KIB))
    assert c["kind"] == "volume-create" and c["result"]["size"] == 0 and c["formats"]["volume"] == "vnx.volume/1"
    w = _check(sdk.volume_write(tmp_path / "v", tmp_path / "in.bin"))
    assert w["result"]["bytes"] == len(data) and w["result"]["size"] == len(data) and w["result"]["extents"] == 3
    w2 = _check(sdk.volume_write(tmp_path / "v", b"PATCH", offset=32 * KIB - 2))
    assert w2["result"]["offset"] == 32 * KIB - 2 and [x["extent"] for x in w2["result"]["flush"]["committed"]] == [0, 1]
    want = data[:32 * KIB - 2] + b"PATCH" + data[32 * KIB + 3:]
    rd = _check(sdk.volume_read(tmp_path / "v", 32 * KIB - 10, 20, tmp_path / "part.bin"))
    assert (tmp_path / "part.bin").read_bytes() == want[32 * KIB - 10:32 * KIB + 10]
    assert rd["result"]["extents_decoded"] == 2 and rd["outputs"][0]["role"] == "data"
    rd2 = sdk.volume_read(tmp_path / "v", 0, len(want))
    assert rd2.body["data"] == want
    st = _check(sdk.volume_stat(tmp_path / "v"))
    assert st["result"]["versions"] == 5 and st["result"]["extents"] == 3 and len(st["result"]["extent_records"]) == 3
    sb = _check(sdk.volume_scrub(tmp_path / "v"))
    assert sb["status"] == "VERIFIED" and sb["result"]["verified"] == 3
    with pytest.raises(sdk.VNXError):
        sdk.volume_create(tmp_path / "v")


# ------------------------------------------------------------------------------------------------------ result cache
def test_decode_result_cache(reads, tmp_path, monkeypatch):
    cdir = tmp_path / "cache"
    a = sdk.decode(reads["reads"], tmp_path / "a.vnx", cache_dir=cdir)
    assert a.status == "SUCCESS" and a.body["cache"] == {**a.body["cache"], "hit": False, "stored": True}
    key = a.body["cache"]["key"]
    assert sdk.cache_stats(cdir).body["entries"] == 1
    b = sdk.decode(reads["reads"], tmp_path / "b.vnx", cache_dir=cdir)
    assert b.body["cache"]["hit"] is True and b.body["cache"]["key"] == key
    assert (tmp_path / "b.vnx").read_bytes() == (tmp_path / "a.vnx").read_bytes() == reads["archive"].read_bytes()
    assert b.body["container_sha256"] == a.body["container_sha256"] and b.body["reads"] == a.body["reads"]
    _check(b)
    # parallelism does not change the key; an option that changes the decode does
    same = sdk.decode(reads["reads"], tmp_path / "c.vnx", options=sdk.DecodeOptions(workers=2, engine_threads=3), cache_dir=cdir)
    assert same.body["cache"]["hit"] is True
    other = sdk.decode(reads["reads"], tmp_path / "d.vnx", options=sdk.DecodeOptions(band=5), cache_dir=cdir)
    assert other.body["cache"]["hit"] is False and other.body["cache"]["key"] != key
    assert sdk.cache_stats(cdir).body["entries"] == 2
    # a changed input invalidates (different content, same path)
    alt = tmp_path / "r2.fastq"
    alt.write_bytes(reads["reads"].read_bytes().replace(b"\n+\n", b"\n+x\n", 1))
    assert sdk.decode(alt, tmp_path / "e.vnx", cache_dir=cdir).body["cache"]["hit"] is False
    # a damaged entry is dropped, not served
    entry = cdir / key[:2] / key
    (entry / "container.vnx").write_bytes(b"garbage")
    f = sdk.decode(reads["reads"], tmp_path / "f.vnx", cache_dir=cdir)
    assert f.body["cache"]["hit"] is False and (tmp_path / "f.vnx").read_bytes() == reads["archive"].read_bytes()
    # the environment configures it; the store is bounded; selected-file decodes bypass it; clear empties it
    monkeypatch.setenv(sc.ENV_DIR, str(cdir))
    monkeypatch.setenv(sc.ENV_BYTES, "1")
    g = sdk.decode(reads["reads"], tmp_path / "g.vnx", options=sdk.DecodeOptions(band=4))
    assert g.body["cache"]["stored"] is True and sdk.cache_stats().body["entries"] == 0     # evicted at once: limit 1 byte
    monkeypatch.delenv(sc.ENV_BYTES)
    h = sdk.decode(reads["reads"], tmp_path / "h.vnx")
    assert h.body["cache"]["hit"] is False and sdk.cache_stats().body["entries"] == 1
    sel = sdk.decode(reads["reads"], select=["in.bin"], extract_to=tmp_path / "sel")
    assert "cache" not in sel.body
    assert sdk.cache_clear().body["removed"] == 1 and sdk.cache_stats().body["entries"] == 0
    monkeypatch.delenv(sc.ENV_DIR)
    assert sdk.cache_stats().body["enabled"] is False
    with pytest.raises(sdk.VNXError):
        sdk.cache_clear()
    assert "cache" not in sdk.decode(reads["reads"], tmp_path / "i.vnx").body


# ------------------------------------------------------------------------------------------------------ CLI
def _ok(r) -> dict:
    assert r.exit_code == 0, r.output
    return json.loads(r.stdout)


def test_cli_stream_volume_cache(reads, tmp_path):
    runner = CliRunner()
    data = _data(40 * KIB, 5)
    (tmp_path / "in.bin").write_bytes(data)
    out = _ok(runner.invoke(app, ["stream", "encode", str(tmp_path / "in.bin"), str(tmp_path / "s.fasta"), "--extent-bytes",
                                  str(16 * KIB)]))
    assert out["kind"] == "stream-encode" and len(out["result"]["extents"]) == 3
    out = _ok(runner.invoke(app, ["stream", "decode", str(tmp_path / "s.fasta"), str(tmp_path / "s.fasta.stream.json"),
                                  str(tmp_path / "out.bin")]))
    assert out["kind"] == "stream-decode" and (tmp_path / "out.bin").read_bytes() == data
    r = runner.invoke(app, ["stream", "decode", str(tmp_path / "s.fasta"), str(tmp_path / "missing.json"), str(tmp_path / "x.bin")])
    assert r.exit_code != 0 and not (tmp_path / "x.bin").exists()
    v = str(tmp_path / "vol")
    assert _ok(runner.invoke(app, ["volume", "init", v, "--extent-bytes", str(16 * KIB)]))["result"]["extents"] == 0
    assert _ok(runner.invoke(app, ["volume", "write", v, str(tmp_path / "in.bin")]))["result"]["extents"] == 3
    assert _ok(runner.invoke(app, ["volume", "write", v, str(tmp_path / "in.bin"), "--offset", "100"]))["result"]["size"] == 40 * KIB + 100
    _ok(runner.invoke(app, ["volume", "read", v, "--offset", "50", "--length", "100", "-o", str(tmp_path / "r.bin")]))
    assert (tmp_path / "r.bin").read_bytes() == data[50:100] + data[:50]
    st = _ok(runner.invoke(app, ["volume", "stat", v]))
    assert st["result"]["versions"] == 6 and st["result"]["extents"] == 3      # 40 KiB + 100 bytes fit in 3 extents
    assert _ok(runner.invoke(app, ["volume", "scrub", v]))["status"] == "VERIFIED"
    (tmp_path / "vol" / "extents" / "e0-v2.fasta").write_bytes(b">x\nACGT\n")
    r = runner.invoke(app, ["volume", "scrub", v])
    assert r.exit_code == 1 and json.loads(r.stdout)["result"]["failed"] == [0]
    cdir = str(tmp_path / "cache")
    a = _ok(runner.invoke(app, ["decode", str(reads["reads"]), "-o", str(tmp_path / "a.vnx"), "--cache-dir", cdir]))
    assert a["result"]["cache"]["hit"] is False
    b = _ok(runner.invoke(app, ["decode", str(reads["reads"]), "-o", str(tmp_path / "b.vnx"), "--cache-dir", cdir]))
    assert b["result"]["cache"]["hit"] is True and (tmp_path / "b.vnx").read_bytes() == reads["archive"].read_bytes()
    assert _ok(runner.invoke(app, ["cache", "stats", "--dir", cdir]))["result"]["entries"] == 1
    assert _ok(runner.invoke(app, ["cache", "clear", "--dir", cdir]))["result"]["removed"] == 1
    assert os.environ.get(sc.ENV_DIR) is None
