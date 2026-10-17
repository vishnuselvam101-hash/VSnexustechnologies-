"""V13 volume (vnxdna.volume): a disk-like, crash-consistent volume on top of the codec.

- write/append/seek/read/read_at round trips across extent boundaries; a range read decodes only the extents it
  touches; the hot-extent cache serves repeated reads; uncommitted writes are visible to their writer;
- overwrites create new versions of the touched extents only; a write beyond the end zero-fills the gap; the
  write-back buffer flushes itself above its limit;
- crash consistency: a flush interrupted after the strand file is published but before its journal record leaves the
  volume at its last commit (the orphan file is removed on the next read-write open); a torn journal tail is ignored
  and cut by the next append; the state is replayed from the journal alone;
- scrub re-verifies every extent and reports a damaged or replaced strand file; a strand file replaced by a SIMULATED
  read pool of the same extent still reads back;
- one writer at a time; read-only opens; encrypted volumes need their key; option validation.
SIMULATED reads only; the strands are files.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from vnxdna.archive.operations import ArchiveOptions
from vnxdna.core.errors import VNXConfigurationError, VNXError, VNXOutputError
from vnxdna.v4 import channel as ch
from vnxdna.volume import JOURNAL_FORMAT, Journal, Volume, VolumeOptions
from vnxdna.volume import volume as vmod

KIB = 1 << 10
E = 32 * KIB


def _data(n: int, seed: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8).tobytes()


def _opts(**kw) -> VolumeOptions:
    return VolumeOptions(extent_bytes=E, **kw)


def test_round_trip_reads_and_cache(tmp_path):
    data = _data(3 * E + 1234, 1)
    with Volume.create(tmp_path / "v", _opts()) as v:
        assert v.size == 0 and v.stat()["extents"] == 0
        assert v.write(data) == len(data) and v.size == len(data) and v.tell() == len(data)
        assert v.read_at(E - 10, 30) == data[E - 10:E + 20]               # read-your-writes before any flush
        rep = v.flush()
        assert [c["extent"] for c in rep["committed"]] == [0, 1, 2, 3] and rep["committed"][3]["length"] == 1234
        st = v.stat()
        assert st["extents"] == 4 and st["versions"] == 4 and st["dirty"] == {"extents": 0, "bytes": 0}
        assert st["io"]["extents_encoded"] == 4 and st["io"]["encode_mb_s"] > 0
    with Volume.open(tmp_path / "v") as v:
        assert v.mode == "r" and v.size == len(data)
        assert v.read_at(E - 7, 20) == data[E - 7:E + 13]
        st = v.stat()
        assert st["io"]["extents_decoded"] == 2 and st["cache"]["entries"] == 2 and st["cache"]["misses"] == 2
        assert v.read_at(E - 7, 20) == data[E - 7:E + 13] and v.stat()["cache"]["hits"] == 2
        assert v.read_at(3 * E + 1000, 10_000) == data[3 * E + 1000:]      # clipped at the end
        assert v.read_at(len(data), 5) == b"" and v.read_at(10 * E, 5) == b""
        v.seek(5)
        assert v.read(10) == data[5:15] and v.tell() == 15
        v.seek(-100, os.SEEK_END)
        assert v.read() == data[-100:] and v.tell() == len(data)
        assert v.read_at(0, len(data)) == data
        with pytest.raises(VNXConfigurationError):
            v.write(b"x")                                                   # read-only
    with pytest.raises(VNXConfigurationError):
        v.read(1)                                                           # closed
    # the extent files are ordinary strand files, one archive each
    names = sorted(p.name for p in (tmp_path / "v" / "extents").iterdir())
    assert names == ["e0-v1.fasta", "e1-v1.fasta", "e2-v1.fasta", "e3-v1.fasta"]


def test_overwrite_versions_gaps_and_write_back(tmp_path):
    data = bytearray(_data(2 * E + 500, 2))
    with Volume.create(tmp_path / "v", _opts(write_back_bytes=0)) as v:        # flush after every write
        v.write(bytes(data))
        assert v.stat()["io"]["flushes"] == 1 and v.stat()["versions"] == 3
        patch = _data(100, 3)
        v.seek(E + 50)
        v.write(patch)
        data[E + 50:E + 150] = patch
        assert v.stat()["versions"] == 4 and v.extents()[1]["version"] == 2 and v.extents()[0]["version"] == 1
        assert v.read_at(0, len(data)) == bytes(data)
        v.seek(4 * E + 10)                                                       # beyond the end: zero gap
        v.write(b"tail")
        data += bytes(4 * E + 10 - len(data)) + b"tail"
        assert v.size == len(data) and v.read_at(2 * E + 400, 300) == bytes(data[2 * E + 400:2 * E + 700])
        assert v.read_at(0, len(data)) == bytes(data)
        v.append(b"more")
        data += b"more"
    with Volume.open(tmp_path / "v") as v:
        assert v.read_at(0, v.size) == bytes(data)
        assert sorted(v.committed) == [0, 1, 2, 3, 4] and v.committed[2]["version"] >= 2
        assert sum(int(r["length"]) for r in v.extents()) == len(data)
    with Volume.create(tmp_path / "w", _opts(write_back_bytes=E)) as v:
        for k in range(5):
            v.write(_data(E // 2, 10 + k))
        assert v.stat()["io"]["flushes"] >= 1 and v.stat()["dirty"]["bytes"] <= E


def test_crash_between_publish_and_journal_is_rolled_back(tmp_path, monkeypatch):
    data = _data(2 * E, 4)
    with Volume.create(tmp_path / "v", _opts()) as v:
        v.write(data)
        v.flush()
    files_before = sorted(p.name for p in (tmp_path / "v" / "extents").iterdir())

    def crash(self, rec):
        raise OSError("simulated power loss before the journal record")
    monkeypatch.setattr(Journal, "append", crash)
    v = Volume.open(tmp_path / "v", "rw", options=_opts())
    v.seek(10)
    v.write(b"changed")
    with pytest.raises(OSError):
        v.flush()
    with pytest.raises(OSError):
        v.close()                                        # the dirty extent still cannot be committed; the lock is released
    monkeypatch.undo()
    assert "e0-v2.fasta" in {p.name for p in (tmp_path / "v" / "extents").iterdir()}   # the orphan of the crash
    with Volume.open(tmp_path / "v", "rw", options=_opts()) as v:
        assert sorted(p.name for p in (tmp_path / "v" / "extents").iterdir()) == files_before
        assert v.stat()["journal"]["orphans_removed"] == 1 and v.read_at(0, 2 * E) == data   # last commit, unchanged
        v.seek(10)
        v.write(b"changed")
        v.flush()
    with Volume.open(tmp_path / "v") as v:
        assert v.read_at(8, 12) == data[8:10] + b"changed" + data[17:20]


def test_torn_journal_tail_is_ignored_and_cut(tmp_path):
    data = _data(E + 10, 5)
    with Volume.create(tmp_path / "v", _opts()) as v:
        v.write(data)
        v.flush()
    j = tmp_path / "v" / "journal.log"
    good = j.read_bytes()
    j.write_bytes(good + b'{"op":"commit","extent":9,"seq":3,"crc":"deadbeef"}\n{"op":"commit","ext')
    with Volume.open(tmp_path / "v") as v:
        assert v.stat()["journal"]["bad_tail_bytes"] > 0 and sorted(v.committed) == [0, 1]
        assert v.read_at(0, len(data)) == data
    with Volume.open(tmp_path / "v", "rw", options=_opts()) as v:
        v.append(b"z")
        v.flush()
    assert j.read_bytes().startswith(good) and b"deadbeef" not in j.read_bytes()
    with Volume.open(tmp_path / "v") as v:
        assert v.stat()["journal"]["bad_tail_bytes"] == 0 and v.read_at(0, v.size) == data + b"z"
    # a journal without its header is not a volume journal
    j.write_bytes(b"")
    with pytest.raises(VNXError):
        Volume.open(tmp_path / "v")
    jj = Journal(tmp_path / "j.log")
    jj.create()
    jj.append({"op": "note", "text": "x"})
    assert Journal(tmp_path / "j.log").replay()[1]["text"] == "x" and jj.records[0]["format"] == JOURNAL_FORMAT


def test_scrub_and_reads_from_a_simulated_pool(tmp_path):
    data = _data(2 * E + 77, 6)
    with Volume.create(tmp_path / "v", _opts()) as v:
        v.write(data)
        v.flush()
        s = v.scrub()
        assert s["status"] == "VERIFIED" and s["verified"] == 3 and s["failed"] == [] and s["orphans"] == []
    ext = tmp_path / "v" / "extents"
    # extent 1's strands replaced by a SIMULATED read pool of themselves: changed file, still readable
    strands = (ext / "e1-v1.fasta").read_bytes()
    ch.simulate_file(ext / "e1-v1.fasta", tmp_path / "r.fastq", ch.ChannelConfig(seed=3, coverage=4, substitution_rate=0.01))
    (ext / "e1-v1.fasta").write_bytes((tmp_path / "r.fastq").read_bytes())
    # extent 2's strands truncated: damaged
    (ext / "e2-v1.fasta").write_bytes((ext / "e2-v1.fasta").read_bytes()[:2000])
    with Volume.open(tmp_path / "v") as v:
        s = v.scrub()
        assert s["status"] == "DAMAGED" and s["failed"] == [2] and s["changed_files"] == [1, 2]
        assert s["results"][1]["decoded"] is True and s["results"][2]["error"]
        assert v.read_at(E + 5, 100) == data[E + 5:E + 105]             # decoded from the read pool
        with pytest.raises(VNXError):
            v.read_at(2 * E, 10)
    (ext / "e2-v1.fasta").write_bytes(strands)                            # the wrong strands: a different extent
    with Volume.open(tmp_path / "v") as v:
        assert v.scrub()["failed"] == [2]
        with pytest.raises(VNXError):
            v.read_at(2 * E, 10)                                          # never returns another extent's bytes


def test_locking_read_only_and_options(tmp_path):
    with Volume.create(tmp_path / "v", _opts()) as v:
        v.write(b"abc")
        with pytest.raises(VNXOutputError):
            Volume.open(tmp_path / "v", "rw", options=_opts())            # second writer refused
        r = Volume.open(tmp_path / "v")                                    # readers are fine
        assert r.size == 0                                                 # ...and see the last commit only
        r.close()
    with Volume.open(tmp_path / "v", "rw", options=_opts()) as v:
        assert v.read_at(0, 3) == b"abc"
    with pytest.raises(VNXOutputError):
        Volume.create(tmp_path / "v", _opts())
    Volume.create(tmp_path / "v", _opts(), overwrite=True).close()
    assert Volume.open(tmp_path / "v").size == 0
    for bad in (dict(extent_bytes=100), dict(cache_bytes=-1), dict(workers=0), dict(write_back_bytes=-5)):
        with pytest.raises(VNXConfigurationError):
            VolumeOptions(**{"extent_bytes": E, **bad}).validate()
    with pytest.raises(VNXConfigurationError):
        Volume.open(tmp_path / "v", "rw", options=VolumeOptions(extent_bytes=2 * E))
    with pytest.raises(VNXConfigurationError):
        Volume.open(tmp_path / "v", "rwx")
    (tmp_path / "not").mkdir()
    with pytest.raises(VNXError):
        Volume.open(tmp_path / "not")
    with Volume.open(tmp_path / "v") as v:
        with pytest.raises(VNXConfigurationError):
            v.seek(-1)
        with pytest.raises(VNXConfigurationError):
            v.read_at(-1, 1)
        with pytest.raises(VNXConfigurationError):
            v.seek(0, 7)


def test_encrypted_volume(tmp_path):
    key = bytes(range(32))
    data = _data(E + 5, 7)
    with Volume.create(tmp_path / "v", _opts(archive=ArchiveOptions(key=key))) as v:
        v.write(data)
    with pytest.raises(VNXConfigurationError):
        Volume.open(tmp_path / "v")
    with Volume.open(tmp_path / "v", key=key) as v:
        assert v.stat()["encrypted"] is True and v.read_at(0, v.size) == data
    with Volume.open(tmp_path / "v", key=bytes(32)) as v:                      # wrong key: fails closed on read
        with pytest.raises(VNXError):
            v.read_at(0, 10)


def test_cache_is_bounded(tmp_path):
    data = _data(4 * E, 8)
    with Volume.create(tmp_path / "v", _opts()) as v:
        v.write(data)
    with Volume.open(tmp_path / "v", options=_opts(cache_bytes=2 * E)) as v:
        assert v.read_at(0, 4 * E) == data
        st = v.stat()["cache"]
        assert st["entries"] == 2 and st["bytes"] == 2 * E and st["evictions"] == 2
    with Volume.open(tmp_path / "v", options=_opts(cache_bytes=0)) as v:
        assert v.read_at(0, 10) == data[:10] and v.read_at(0, 10) == data[:10]
        assert v.stat()["cache"]["entries"] == 0 and v.stat()["io"]["extents_decoded"] == 2
    assert vmod.VOLUME_FORMAT == "vnx.volume/1"
