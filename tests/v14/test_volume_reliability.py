"""V14 Phase 6 workstream D: streaming volume reliability of ``vnx.volume/1`` (docs/V14_VOLUME_RELIABILITY.md).

Fault injection against the existing volume, each followed by a reopen and a scrub:

- journal: a torn tail (every truncation offset, a zero-filled last append) is cut; anything else after the last
  verified record (a flipped byte at ANY offset, a duplicated or reordered record, a garbage or over-long tail) refuses
  to open, with nothing truncated and no extent file removed;
- failed flushes: an OSError on a strand-file write, a strand-file fsync, a directory fsync or the journal fsync is
  reported (never a silent success); a journal append that failed poisons the journal until the volume is reopened;
- interrupted encodes leave nothing behind after a read-write reopen; missing or partial extents are errors on read
  and DAMAGED in the scrub, never zeros or wrong bytes; a scrub checks the persisted journal as well;
- tampered (CRC-valid) commit records cannot point outside the volume or describe impossible extents;
- process termination: a real child process killed with SIGKILL at every fsync of a flush, in the middle of a
  journal line, and at an arbitrary moment of a write loop; the lock dies with the process;
- repeated recovery is idempotent; the cache and the write-back buffer stay within their bounds and are observable;
- a volume written by the base code (b69e70f, fixture ``golden/volume_v13_base.tar.gz``) still opens and verifies.
SIMULATED: the strands are files; no DNA is involved.
"""
from __future__ import annotations

import errno
import hashlib
import os
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import textwrap
import time
from pathlib import Path

import numpy as np
import pytest

from vnxdna.core.errors import VNXError, VNXFormatError, VNXIntegrityError, VNXOutputError
from vnxdna.volume import Journal, Volume, VolumeOptions
from vnxdna.volume import journal as jmod
from vnxdna.volume import volume as vmod

E = 4096
GOLDEN = Path(__file__).parent / "golden" / "volume_v13_base.tar.gz"
SRC = str(Path(vmod.__file__).resolve().parents[2])


def _data(n: int, seed: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8).tobytes()


def _opts(**kw) -> VolumeOptions:
    return VolumeOptions(extent_bytes=E, **kw)


def _files(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "extents").iterdir())


def _make(root: Path, n_extents: int = 3, seed: int = 1, overwrite: bool = True) -> bytes:
    """A volume with ``n_extents`` extents (the last one short) and, optionally, a second version of extent 0."""
    data = bytearray(_data((n_extents - 1) * E + 100, seed))
    with Volume.create(root, _opts()) as v:
        v.write(bytes(data))
        v.flush()
        if overwrite:
            v.seek(5)
            v.write(b"second version")
            data[5:19] = b"second version"
    return bytes(data)


def _assert_healthy(root: Path, expect: bytes | None = None, opts: VolumeOptions | None = None) -> dict:
    """Reopen read-write (recovery), check the content and a VERIFIED scrub; reopen read-only and scrub again."""
    with Volume.open(root, "rw", options=opts or _opts()) as v:
        if expect is not None:
            assert v.size == len(expect) and v.read_at(0, v.size) == expect
        rep = v.scrub()
        assert rep["status"] == "VERIFIED", rep
        assert rep["orphans"] == [] and rep["journal"]["status"] == "intact"
    with Volume.open(root, options=opts or _opts()) as v:
        assert v.scrub()["status"] == "VERIFIED"
    return rep


def _fsync_fault(monkeypatch, *, when, err=errno.EIO, times: int = 1):
    """Make ``os.fsync`` fail with ``err`` for the next ``times`` calls whose fd satisfies ``when(path, is_dir)``."""
    real = os.fsync
    state = {"left": times, "hits": 0}

    def fsync(fd):
        path = os.readlink(f"/proc/self/fd/{fd}")
        if state["left"] and when(path, stat.S_ISDIR(os.fstat(fd).st_mode)):
            state["left"] -= 1
            state["hits"] += 1
            raise OSError(err, os.strerror(err))
        return real(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    return state


# ======================================================================================== journal classification
def test_corrupt_last_record_is_damage_not_a_torn_tail(tmp_path):
    """A flipped byte in the last committed record used to open as a 'torn tail': the commit vanished and a read-write
    open deleted its strand file as an orphan. It is damage: the volume does not open, nothing is changed."""
    root = tmp_path / "v"
    _make(root)
    j = root / "journal.log"
    raw = j.read_bytes()
    lines = raw.split(b"\n")
    last = bytearray(lines[-2])
    k = last.find(b'"length":') + len(b'"length":')
    last[k] ^= 0x01                                      # a digit becomes another digit: still canonical JSON
    lines[-2] = bytes(last)
    j.write_bytes(b"\n".join(lines))
    before, files = j.read_bytes(), _files(root)
    for mode in ("r", "rw"):
        with pytest.raises(VNXIntegrityError):
            Volume.open(root, mode, options=_opts())
    assert j.read_bytes() == before and _files(root) == files
    j.write_bytes(raw)
    _assert_healthy(root)


def test_every_single_byte_flip_in_the_journal_refuses_to_open(tmp_path):
    """Exhaustive: a flipped bit at every offset of the journal (two bit positions) never opens silently."""
    root = tmp_path / "v"
    _make(root)
    j = root / "journal.log"
    raw = j.read_bytes()
    jj = Journal(j)
    for off in range(len(raw)):
        for bit in (0x01, 0x20):
            bad = bytearray(raw)
            bad[off] ^= bit
            j.write_bytes(bytes(bad))
            with pytest.raises((VNXIntegrityError, VNXFormatError)):
                jj.replay()
    j.write_bytes(raw)
    assert len(jj.replay()) == 5 and jj.bad_tail_bytes == 0


def test_duplicated_reordered_and_garbage_tails_are_refused(tmp_path):
    root = tmp_path / "v"
    _make(root)
    j = root / "journal.log"
    raw = j.read_bytes()
    lines = raw.split(b"\n")[:-1]
    cases = {
        "duplicated last record": raw + lines[-1] + b"\n",
        "swapped last two records": b"\n".join(lines[:-2] + [lines[-1], lines[-2]]) + b"\n",
        "complete fake record + fragment": raw + b'{"op":"commit","extent":9,"seq":5,"crc":"deadbeef"}\n{"op":"commit","ext',
        "garbage line": raw + b"not json at all\n",
        "garbage that is not a record prefix": raw + b"xyz",
        "a record followed by a stray byte": raw[:-1] + b"#",
        "zero fill longer than one record": raw + bytes(jmod.MAX_RECORD_BYTES + 1),
        "two lines of zeros and text": raw + bytes(40) + b"\n" + bytes(40),
    }
    for name, content in cases.items():
        j.write_bytes(content)
        with pytest.raises(VNXIntegrityError):
            Volume.open(root, options=_opts())
        assert j.read_bytes() == content, name
    j.write_bytes(raw)
    _assert_healthy(root)


def test_truncated_journal_at_every_offset(tmp_path):
    """Every truncation of the journal is a torn tail of the last append (or, inside the header, not a journal):
    the state is the last complete record, a read-write open plus an append cuts the tail, the scrub verifies."""
    root = tmp_path / "v"
    data = _make(root)
    j = root / "journal.log"
    raw = j.read_bytes()
    ends = [i + 1 for i, b in enumerate(raw) if b == 0x0A]             # offsets just after each complete record
    jj = Journal(j)
    for off in range(len(raw) + 1):
        j.write_bytes(raw[:off])
        if off < ends[0]:
            with pytest.raises(VNXFormatError):
                jj.replay()
            continue
        recs = jj.replay()
        complete = max(e for e in ends if e <= off)
        assert jj.good_bytes == complete and jj.bad_tail_bytes == off - complete
        assert len(recs) == ends.index(complete) + 1
        assert jj.tail_state == ("clean" if off == complete else "torn")
    # recovery (rw open, orphan removal, append cutting the tail, scrub) at a sample of offsets, each on a copy
    for off in (ends[1] + 7, ends[2] - 1, ends[3] + 100, len(raw) - 1, len(raw)):
        work = tmp_path / f"c{off}"
        shutil.copytree(root, work)
        (work / "journal.log").write_bytes(raw[:off])
        n_commits = sum(1 for e in ends[1:] if e <= off)
        with Volume.open(work, "rw", options=_opts()) as v:
            assert len(v.journal.records) == n_commits + 1
            assert len(_files(work)) == n_commits                      # uncommitted files removed as orphans
            v.append(b"!")
            v.flush()
        assert (work / "journal.log").read_bytes().startswith(raw[:max(e for e in ends if e <= off)])
        with Volume.open(work, options=_opts()) as v:
            assert v.journal.bad_tail_bytes == 0 and v.scrub()["status"] == "VERIFIED"
            if off == len(raw):
                assert v.read_at(0, v.size) == data + b"!"


def test_zero_filled_last_append_is_a_torn_tail(tmp_path):
    """The shapes a torn append leaves behind: zeros past the end, or the last line with a zero-filled range and its
    newline. Both are cut; the commit they would have been was never acknowledged."""
    root = tmp_path / "v"
    _make(root, overwrite=False)
    j = root / "journal.log"
    raw = j.read_bytes()
    lines = raw.split(b"\n")[:-1]
    last = lines[-1]
    prefix = raw[:len(raw) - len(last) - 1]
    shapes = [prefix + bytes(len(last) + 1),                        # the whole append reads back as zeros
              prefix + last[:50] + bytes(len(last) - 50) + b"\n",   # first page written, the rest zero, newline landed
              prefix + bytes(100) + last[100:] + b"\n",             # a zero hole at the front of the line
              prefix + last[:200]]                                  # a plain prefix
    for content in shapes:
        j.write_bytes(content)
        jj = Journal(j)
        assert len(jj.replay()) == len(lines) - 1 and jj.tail_state == "torn"
    work = tmp_path / "w"
    shutil.copytree(root, work)
    (work / "journal.log").write_bytes(shapes[1])
    with Volume.open(work, "rw", options=_opts()) as v:
        v.write(b"after the torn append")
    _assert_healthy(work)
    with Volume.open(work, options=_opts()) as v:
        assert v.read_at(0, 21) == b"after the torn append"


# ======================================================================================== failed flushes
def test_journal_write_failure_poisons_the_journal(tmp_path, monkeypatch):
    """A failed append left its partial line in place; the next (shorter) append was written over it at the old offset
    and the rest of the failed line stayed behind a valid record. Now a failed append poisons the journal: later
    appends refuse until it is reopened, and the reopen sees a plain torn tail."""
    path = tmp_path / "j.log"
    jj = Journal(path)
    jj.create()
    real_fsync = os.fsync

    def failing_fsync(fd):
        raise OSError(errno.EIO, "injected EIO")
    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError):
        jj.append({"op": "note", "text": "x" * 300})
    monkeypatch.setattr(os, "fsync", real_fsync)
    with pytest.raises(VNXOutputError):
        jj.append({"op": "note", "text": "y"})
    fresh = Journal(path)
    recs = fresh.replay()                               # the failed line is complete in the file: it may count
    assert [r.get("text") for r in recs[1:]] in ([], ["x" * 300])
    fresh.append({"op": "note", "text": "z"})
    again = Journal(path)
    assert [r.get("text") for r in again.replay()[1:]][-1] == "z" and again.bad_tail_bytes == 0

    # a write that fails half-way (ENOSPC) leaves a prefix: a torn tail for the next reader
    path2 = tmp_path / "j2.log"
    j2 = Journal(path2)
    j2.create()
    real_open = open

    class HalfFile:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def __getattr__(self, name):
            return getattr(self.f, name)

        def write(self, b):
            self.f.write(b[:len(b) // 2])
            self.f.flush()
            raise OSError(errno.ENOSPC, "injected ENOSPC")

    monkeypatch.setattr(jmod, "open", lambda *a, **k: HalfFile(real_open(*a, **k)), raising=False)
    with pytest.raises(OSError):
        j2.append({"op": "note", "text": "a" * 200})
    monkeypatch.undo()
    with pytest.raises(VNXOutputError):
        j2.append({"op": "note", "text": "b"})
    r2 = Journal(path2)
    assert len(r2.replay()) == 1 and r2.tail_state == "torn" and r2.bad_tail_bytes > 0


def test_volume_journal_fsync_failure_is_reported_and_poisons(tmp_path, monkeypatch):
    root = tmp_path / "v"
    old = _make(root, overwrite=False)
    v = Volume.open(root, "rw", options=_opts())
    v.write(b"NEW")
    st = _fsync_fault(monkeypatch, when=lambda p, d: p.endswith("journal.log"))
    with pytest.raises(OSError):
        v.flush()
    assert st["hits"] == 1
    monkeypatch.undo()
    snapshot = {n: hashlib.sha256((root / "extents" / n).read_bytes()).hexdigest() for n in _files(root)}
    with pytest.raises(VNXOutputError):
        v.flush()                                        # poisoned: no retry over a record whose fate is unknown
    with pytest.raises(VNXOutputError):
        v.close()                                        # the dirty extent cannot be committed; the lock is released
    assert {n: hashlib.sha256((root / "extents" / n).read_bytes()).hexdigest() for n in _files(root)} == snapshot
    with Volume.open(root, "rw", options=_opts()) as v2:
        got = v2.read_at(0, v2.size)
    assert got in (old, b"NEW" + old[3:])               # the unacknowledged commit may or may not have landed
    _assert_healthy(root, got)


def test_directory_fsync_failure_is_not_a_silent_success(tmp_path, monkeypatch):
    """core.fsio.fsync_dir swallows every error: a flush whose directory sync failed reported success."""
    root = tmp_path / "v"
    old = _make(root, overwrite=False)
    with Volume.open(root, "rw", options=_opts()) as v:
        v.write(b"NEW")
        # a persistent EIO on extents/ (the strand writer's own directory sync swallows the first one)
        st = _fsync_fault(monkeypatch, when=lambda p, d: d and p.endswith("extents"), times=5)
        with pytest.raises(VNXOutputError):
            v.flush()
        assert st["hits"] == 2
        monkeypatch.undo()
        v.flush()                                        # the journal was not touched: a retry is allowed
    _assert_healthy(root, b"NEW" + old[3:])
    # an unsupported directory fsync (EINVAL, some network/FUSE file systems) is not an error
    with Volume.open(root, "rw", options=_opts()) as v:
        v.write(b"OK!")
        _fsync_fault(monkeypatch, when=lambda p, d: d, err=errno.EINVAL, times=100)
        v.flush()
        monkeypatch.undo()
    _assert_healthy(root, b"OK!" + old[3:])


def test_strand_file_write_and_fsync_failures(tmp_path, monkeypatch):
    """ENOSPC while writing a strand file, EIO on its fsync: the flush raises, nothing is committed, no partial file
    survives, a retry in the same session commits, and the volume reopens and verifies."""
    from vnxdna.dnaenc import strandio
    root = tmp_path / "v"
    old = _make(root, overwrite=False)
    real_raw = strandio.StrandWriter._raw
    calls = {"n": 0}

    def enospc(self, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(errno.ENOSPC, "injected ENOSPC")
        return real_raw(self, data)
    with Volume.open(root, "rw", options=_opts()) as v:
        v.write(b"AAA")
        monkeypatch.setattr(strandio.StrandWriter, "_raw", enospc)
        with pytest.raises(OSError):
            v.flush()
        monkeypatch.undo()
        assert not [n for n in _files(root) if n.endswith(".partial")] and v.stat()["dirty"]["extents"] == 1
        st = _fsync_fault(monkeypatch, when=lambda p, d: ".partial" in p and "/extents/" in p)
        with pytest.raises(OSError):
            v.flush()
        assert st["hits"] == 1
        monkeypatch.undo()
        assert _files(root) == ["e0-v1.fasta", "e1-v1.fasta", "e2-v1.fasta"]
        v.flush()
    _assert_healthy(root, b"AAA" + old[3:])


def test_interrupted_encode_leaves_nothing_behind(tmp_path, monkeypatch):
    """KeyboardInterrupt in the middle of an extent encode (a partial strand file on disk): the context manager drops
    the dirty state, the reopen removes the leftovers, the last commit stands."""
    root = tmp_path / "v"
    old = _make(root, overwrite=False)
    real = vmod.encode_extent

    def interrupted(src, target, *a, **k):
        Path(target).with_name("." + Path(target).name + ".123.partial").write_bytes(b">s\nACGT\n")
        Path(target).write_bytes(b">half\nACG")           # even a half-written file under the final name
        raise KeyboardInterrupt
    monkeypatch.setattr(vmod, "encode_extent", interrupted)
    with pytest.raises(KeyboardInterrupt):
        with Volume.open(root, "rw", options=_opts()) as v:
            v.write(b"lost")
            v.flush()
    monkeypatch.setattr(vmod, "encode_extent", real)
    with Volume.open(root, "rw", options=_opts()) as v:
        assert v.stat()["journal"]["orphans_removed"] == 2
    _assert_healthy(root, old)


# ======================================================================================== damaged extents and scrub
def test_missing_and_partial_extents_fail_closed(tmp_path):
    root = tmp_path / "v"
    data = _make(root, overwrite=False)
    f1 = root / "extents" / "e1-v1.fasta"
    good = f1.read_bytes()
    for name, damage in (("missing", lambda: f1.unlink()),
                         ("truncated", lambda: f1.write_bytes(good[:len(good) // 3])),
                         ("emptied", lambda: f1.write_bytes(b""))):
        damage()
        with Volume.open(root, options=_opts()) as v:
            assert v.read_at(0, E) == data[:E]                 # an intact extent still reads
            with pytest.raises(VNXError):
                v.read_at(E, 10)
            rep = v.scrub()
            assert rep["status"] == "DAMAGED" and rep["failed"] == [1], name
            assert rep["journal"]["status"] == "intact"
        f1.write_bytes(good)
    _assert_healthy(root, data)


def test_scrub_checks_the_persisted_journal(tmp_path):
    """The scrub verified the extents against the journal held in memory: a journal damaged on disk after the open
    (the volume would no longer open) was reported VERIFIED."""
    root = tmp_path / "v"
    data = _make(root)
    j = root / "journal.log"
    raw = j.read_bytes()
    with Volume.open(root, options=_opts()) as v:
        bad = bytearray(raw)
        bad[raw.find(b'"sha256":"') + 12] ^= 0x01            # damage in the first commit record
        j.write_bytes(bytes(bad))
        rep = v.scrub()
        assert rep["status"] == "DAMAGED" and rep["journal"]["status"] == "damaged" and rep["journal"]["error"]
        j.write_bytes(raw[:-1] + b"")                        # the last record now unterminated: lost on reopen
        rep = v.scrub()
        assert rep["status"] == "DAMAGED" and rep["journal"]["status"] == "diverged"
        j.write_bytes(raw)
        assert v.scrub()["status"] == "VERIFIED"
    _assert_healthy(root, data)


def test_tampered_commit_records_are_refused(tmp_path):
    """Records with a valid CRC (the CRC is not a MAC) must still describe extents of this volume: a file name outside
    extents/, an impossible length, a version that does not increase, or non-integer fields refuse to open
    (VNXFormatError, never a TypeError or a read outside the volume)."""
    root = tmp_path / "v"
    _make(root)
    j = root / "journal.log"
    raw = j.read_bytes()
    lines = raw.split(b"\n")[:-1]
    import json
    last = json.loads(lines[-1])
    last.pop("crc")

    def with_last(**changes) -> bytes:
        rec = {**last, **changes}
        return b"\n".join(lines[:-1] + [Journal._line(rec).rstrip(b"\n")]) + b"\n"
    outside = tmp_path / "outside.fasta"
    shutil.copy(root / "extents" / last["file"], outside)
    for changes in ({"extent": 7, "file": "e7-v2.fasta"}, {"version": 3, "file": "e0-v3.fasta"},
                    {"file": "../../outside.fasta"}, {"file": "/etc/passwd"}, {"file": "e0-v1.fasta"},
                    {"length": E + 1}, {"length": -1}, {"extent": -1}, {"extent": "0"}, {"version": 1},
                    {"version": True}, {"sha256": "zz"}, {"archive_tag": 7}, {"length": None}):
        j.write_bytes(with_last(**changes))
        with pytest.raises(VNXFormatError):
            Volume.open(root, options=_opts())
    j.write_bytes(raw)
    _assert_healthy(root)


# ======================================================================================== bounds
def test_cache_bound_under_range_reads_and_oversized_entries(tmp_path):
    root = tmp_path / "v"
    data = _make(root, n_extents=6, overwrite=False)
    rng = np.random.default_rng(3)
    with Volume.open(root, options=_opts(cache_bytes=2 * E + 100)) as v:
        for _ in range(60):
            off = int(rng.integers(0, len(data)))
            n = int(rng.integers(1, 3 * E))
            assert v.read_at(off, n) == data[off:off + n]
            c = v.stat()["cache"]
            assert c["bytes"] <= c["limit"] and c["bytes"] == sum(len(b) for b in v._cache.values())
        assert v.stat()["cache"]["evictions"] > 0
    # an entry larger than the whole cache is not cached, and does not evict what is there
    with Volume.open(root, options=_opts(cache_bytes=1000)) as v:
        assert v.read_at(5 * E, 100) == data[5 * E:]           # the 100-byte last extent fits
        assert v.read_at(0, 10) == data[:10]                   # a full extent does not
        c = v.stat()["cache"]
        assert c["entries"] == 1 and c["bytes"] == 100 and c["evictions"] == 0


def test_write_back_bound_holds_inside_one_large_write(tmp_path):
    """The write-back buffer was checked only after a whole write: one 8-extent write held 8 extents dirty with a
    2-extent limit, and a write far beyond the end zero-filled the whole gap in memory. The bound now holds within a
    write (limit plus the extent being filled); the write still counts as one write-back flush."""
    root = tmp_path / "v"
    data = _data(8 * E, 9)
    with Volume.create(root, _opts(write_back_bytes=2 * E)) as v:
        v.write(data)
        st = v.stat()
        assert st["write_back"]["peak_bytes"] <= 2 * E + E and st["write_back"]["limit"] == 2 * E
        assert st["io"]["flushes"] == 1 and st["versions"] == 8 - st["dirty"]["extents"]
        v.seek(40 * E + 3)                                  # a 32-extent zero gap
        v.write(b"far")
        assert v.stat()["write_back"]["peak_bytes"] <= 3 * E
    data = data + bytes(32 * E + 3) + b"far"
    _assert_healthy(root, data)
    with Volume.create(tmp_path / "z", _opts(write_back_bytes=0)) as v:     # 0 = flush on every write
        v.write(data[:3 * E])
        st = v.stat()
        assert st["dirty"]["extents"] == 0 and st["write_back"]["peak_bytes"] <= E and st["io"]["flushes"] == 1
    _assert_healthy(tmp_path / "z", data[:3 * E])


# ======================================================================================== recovery idempotency
def test_repeated_recovery_is_idempotent(tmp_path):
    """A crashed flush (orphan strand file + torn journal tail): any number of read-only and read-write reopens and
    scrubs give the same state; only an append cuts the tail."""
    root = tmp_path / "v"
    data = _make(root)
    j = root / "journal.log"
    raw = j.read_bytes()
    shutil.copy(root / "extents" / "e1-v1.fasta", root / "extents" / "e1-v2.fasta")      # published, not journaled
    j.write_bytes(raw + b'{"archive_id":"0f')                                             # the torn record
    reports = []
    for mode in ("r", "r", "rw", "rw", "r"):
        with Volume.open(root, mode, options=_opts()) as v:
            assert v.read_at(0, v.size) == data and v.journal.bad_tail_bytes == 17
            rep = v.scrub()
            reports.append((rep["status"], rep["verified"], rep["journal"]["status"], rep["orphans"]))
            assert j.read_bytes() == raw + b'{"archive_id":"0f'
    # an orphan is a harmless leftover (reported, not damage); read-only opens leave it, read-write opens remove it
    assert reports == [("VERIFIED", 3, "intact", ["e1-v2.fasta"])] * 2 + [("VERIFIED", 3, "intact", [])] * 3
    assert "e1-v2.fasta" not in _files(root)
    with Volume.open(root, "rw", options=_opts()) as v:
        assert v.stat()["journal"]["orphans_removed"] == 0
        v.append(b"+")
    _assert_healthy(root, data + b"+")


# ======================================================================================== process termination
CHILD = textwrap.dedent("""
    import os, signal, sys
    sys.path.insert(0, {src!r})
    from vnxdna.volume import Volume, VolumeOptions, Journal
    mode, root, arg = sys.argv[1], sys.argv[2], int(sys.argv[3])
    E = {E}
    def pattern(gen, n):
        return bytes(((i * 7 + gen * 13) % 251) for i in range(n))
    if mode == "fsync":                       # die (SIGKILL) at the arg-th fsync of the flush
        real = os.fsync
        count = [0]
        def fsync(fd):
            count[0] += 1
            if count[0] == arg:
                os.kill(os.getpid(), signal.SIGKILL)
            return real(fd)
        with Volume.open(root, "rw", options=VolumeOptions(extent_bytes=E)) as v:
            v.write(pattern(1, 3 * E))
            os.fsync = fsync
            v.flush()
            os.fsync = real
        print("count", count[0], flush=True)
    elif mode == "torn":                      # die after writing arg bytes of the first journal line of the flush
        def torn(self, rec):
            line = self._line({{**rec, "seq": len(self.records)}})
            with open(self.path, "r+b") as f:
                f.seek(self.good_bytes)
                f.write(line[:arg])
                f.flush()
            os.kill(os.getpid(), signal.SIGKILL)
        with Volume.open(root, "rw", options=VolumeOptions(extent_bytes=E)) as v:
            v.write(pattern(1, 3 * E))
            Journal.append = torn
            v.flush()
    elif mode == "loop":                      # write generations until killed; print each acknowledged one
        with Volume.open(root, "rw", options=VolumeOptions(extent_bytes=E, write_back_bytes=1 << 30)) as v:
            print("ready", flush=True)
            for gen in range(1, 10_000):
                v.seek(0)
                v.write(pattern(gen, 2 * E))
                v.flush()
                print("ack", gen, flush=True)
    elif mode == "hold":                      # hold the write lock until killed
        with Volume.open(root, "rw", options=VolumeOptions(extent_bytes=E)) as v:
            print("ready", flush=True)
            signal.pause()
""")


def _pattern(gen: int, n: int) -> bytes:
    return bytes(((i * 7 + gen * 13) % 251) for i in range(n))


def _child(tmp_path: Path) -> Path:
    p = tmp_path / "child.py"
    p.write_text(CHILD.format(src=SRC, E=E))
    return p


def _run(child: Path, *args, timeout: float = 120) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(child), *map(str, args)], capture_output=True, text=True,
                          timeout=timeout, env=env)


def _per_extent_old_or_new(got: bytes, old: bytes, new: bytes) -> list[str]:
    """Each extent equals its old or its new content; extents commit in ascending order, so the new ones are a
    prefix of the extent list."""
    states = []
    for i in range(0, len(new), E):
        part = got[i:i + E]
        states.append("new" if part == new[i:i + E] else "old" if part == old[i:i + E] else "neither")
    assert "neither" not in states and states == sorted(states, key=lambda s: s != "new"), states
    return states


def test_sigkill_at_every_fsync_of_a_flush(tmp_path):
    base = tmp_path / "base"
    with Volume.create(base, _opts()) as v:
        v.write(_pattern(0, 3 * E))
    child = _child(tmp_path)
    probe = tmp_path / "probe"
    shutil.copytree(base, probe)
    out = _run(child, "fsync", probe, 0)
    assert out.returncode == 0, out.stderr
    n_fsync = int(out.stdout.split()[-1])
    assert n_fsync >= 9                                      # per extent: strand file, extents/, journal (+ its dir)
    _assert_healthy(probe, _pattern(1, 3 * E))
    seen = set()
    for k in range(1, n_fsync + 1):
        work = tmp_path / f"k{k}"
        shutil.copytree(base, work)
        res = _run(child, "fsync", work, k)
        assert res.returncode == -signal.SIGKILL, (k, res.stderr)
        with Volume.open(work, "rw", options=_opts()) as v:     # the lock died with the process
            got = v.read_at(0, v.size)
        seen.add(tuple(_per_extent_old_or_new(got, _pattern(0, 3 * E), _pattern(1, 3 * E))))
        _assert_healthy(work, got)
    assert ("old", "old", "old") in seen and ("new", "new", "old") in seen


def test_sigkill_in_the_middle_of_a_journal_line(tmp_path):
    base = tmp_path / "base"
    with Volume.create(base, _opts()) as v:
        v.write(_pattern(0, 3 * E))
    child = _child(tmp_path)
    for cut in (0, 1, 120, 300, 365):
        work = tmp_path / f"t{cut}"
        shutil.copytree(base, work)
        res = _run(child, "torn", work, cut)
        assert res.returncode == -signal.SIGKILL, res.stderr
        jj = Journal(work / "journal.log")
        jj.replay()
        assert jj.bad_tail_bytes == cut and jj.tail_state == ("torn" if cut else "clean")
        _assert_healthy(work, _pattern(0, 3 * E))            # the extent file of the torn commit is an orphan


def test_sigkill_at_an_arbitrary_moment_and_the_lock(tmp_path):
    root = tmp_path / "v"
    with Volume.create(root, _opts()) as v:
        v.write(_pattern(0, 2 * E))
    child = _child(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    for delay in (0.0, 0.13, 0.31):
        proc = subprocess.Popen([sys.executable, str(child), "loop", str(root), "0"], stdout=subprocess.PIPE,
                                text=True, env=env)
        try:
            assert proc.stdout.readline().strip() == "ready"
            with pytest.raises(VNXOutputError):
                Volume.open(root, "rw", options=_opts())       # one writer at a time, across processes
            acked = 0
            while acked < 3:
                acked = int(proc.stdout.readline().split()[1])
            time.sleep(delay)
        finally:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=60)
        for line in proc.stdout.read().split("\n"):
            if line.startswith("ack"):
                acked = int(line.split()[1])
        with Volume.open(root, "rw", options=_opts()) as v:
            got = v.read_at(0, v.size)
        gens = [g for g in range(acked, acked + 3) if _pattern(g, 2 * E) == got]
        mixed = [g for g in range(acked, acked + 3)
                 if got[:E] == _pattern(g + 1, E) and got[E:] == _pattern(g, 2 * E)[E:]]
        assert gens or mixed, "the content is neither an acknowledged generation nor a later one"
        _assert_healthy(root, got)
    # a SIGKILLed lock holder releases the lock
    proc = subprocess.Popen([sys.executable, str(child), "hold", str(root), "0"], stdout=subprocess.PIPE, text=True,
                            env=env)
    try:
        assert proc.stdout.readline().strip() == "ready"
        with pytest.raises(VNXOutputError):
            Volume.open(root, "rw", options=_opts())
        with Volume.open(root, options=_opts()) as v:            # readers are never blocked
            assert v.scrub()["status"] == "VERIFIED"
    finally:
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=60)
    _assert_healthy(root)


# ======================================================================================== compatibility
def test_volume_written_by_the_base_code_still_opens(tmp_path):
    """Fixture written by the V13/V14 base code (b69e70f): 4,396 bytes in 2 extents of 4,096, extent 0 rewritten once
    (e0-v1, e0-v2, e1-v1). The format is unchanged: it opens, reads, scrubs, and accepts new commits."""
    with tarfile.open(GOLDEN) as t:
        t.extractall(tmp_path, filter="data")
    root = tmp_path / "vol"
    expect = bytearray(bytes((i * 131 + 7) % 251 for i in range(E + 300)))
    expect[10:29] = b"base-code overwrite"
    assert hashlib.sha256(expect).hexdigest() == "4ea7741819b4a7abed66191d640069fdad38e0edcaa8f40ecab277c7a62acc47"
    with Volume.open(root, options=_opts()) as v:
        assert v.meta["software"] == "13.0.0" and [r["version"] for r in v.extents()] == [2, 1]
        assert v.read_at(0, v.size) == expect
    _assert_healthy(root, bytes(expect))
    with Volume.open(root, "rw", options=_opts()) as v:
        v.append(b"new")
    _assert_healthy(root, bytes(expect) + b"new")
