"""The volume's write-ahead journal: one canonical-JSON record per line, each carrying its own CRC-32 and its sequence
number, appended, flushed and fsynced (docs/V14_VOLUME_RELIABILITY.md §2).

Replay reads the verified records in order and classifies what follows the last one:

- nothing: ``clean``;
- the shape the one in-flight append can leave behind when the process or the machine stops (every append is fsynced
  before the next one starts, so only the last append can be incomplete): at most :data:`MAX_RECORD_BYTES` bytes, no
  newline before its last byte, and either a prefix of a record line (unterminated, starting with ``{`` and not closing
  its object before its last byte) or a zero-filled range (a write the file system extended but did not fill): a
  ``torn`` tail, cut by the next append;
- anything else (a complete line that does not verify, a valid record out of sequence, a record followed by more
  bytes, garbage, an over-long tail, damage followed by valid records): the committed journal is damaged; replay
  raises :class:`VNXIntegrityError` and nothing is changed.

An append that fails after it has started writing (an OSError on write, flush or fsync, or an interrupt) leaves the
journal file in an unknown state (after a failed fsync, a later fsync may report success for data that never reached
the disk): the journal is *poisoned* and refuses further appends until it is replayed again by a new open.
"""
from __future__ import annotations

import errno
import os
import zlib
from pathlib import Path

from vnxdna.core.errors import VNXFormatError, VNXIntegrityError, VNXOutputError
from vnxdna.core.util import canonical_json, parse_canonical_json

JOURNAL_FORMAT = "vnx.volume-journal/1"
MAX_RECORD_BYTES = 1024          # one record line, newline included (commit records are ~370-450 bytes)

# directory fsync errors that mean "not supported here" (some network and FUSE file systems), not a failed sync
_DIR_FSYNC_UNSUPPORTED = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EBADF, errno.EISDIR, errno.EACCES}


def sync_dir(path: str | os.PathLike) -> None:
    """fsync a directory so the entries created or renamed in it are durable. Unlike ``core.fsio.fsync_dir`` a real
    failure (EIO, ENOSPC, ...) raises :class:`VNXOutputError`; only "unsupported" errors are ignored."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as error:
        raise VNXOutputError(f"cannot open directory {path} to sync it: {error.strerror or error}", stage="volume") from None
    try:
        os.fsync(fd)
    except OSError as error:
        if error.errno not in _DIR_FSYNC_UNSUPPORTED:
            raise VNXOutputError(f"directory sync failed for {path}: {error.strerror or error}", stage="volume") from None
    finally:
        os.close(fd)


def _crc(rec: dict) -> str:
    return f"{zlib.crc32(canonical_json(rec)) & 0xFFFFFFFF:08x}"


def _verify(line: bytes) -> dict | None:
    """The record a line holds, or None when it is not a canonical record with a matching CRC."""
    try:
        rec = parse_canonical_json(line, "journal record")
        if not isinstance(rec, dict):
            return None
        crc = rec.pop("crc")
    except (VNXFormatError, ValueError, KeyError, TypeError):
        return None
    return rec if isinstance(crc, str) and _crc(rec) == crc else None


def _object_closes_before_end(tail: bytes) -> bool:
    """Does the JSON object starting at ``tail[0]`` close before the last byte (so ``tail`` is not a record prefix)?
    Scanning stops at the first NUL (a zero-filled range: what follows is unknown)."""
    depth, in_str, esc = 0, False, False
    for k, b in enumerate(tail):
        if b == 0:
            return False
        if in_str:
            if esc:
                esc = False
            elif b == 0x5C:                      # backslash
                esc = True
            elif b == 0x22:
                in_str = False
        elif b == 0x22:
            in_str = True
        elif b in (0x7B, 0x5B):
            depth += 1
        elif b in (0x7D, 0x5D):
            depth -= 1
            if depth <= 0:
                return k < len(tail) - 1
    return False


def classify_tail(tail: bytes) -> str | None:
    """None for a torn last append (see the module docstring), else why ``tail`` is damage."""
    if len(tail) > MAX_RECORD_BYTES:
        return f"{len(tail)} unverified bytes after the last record: longer than one record ({MAX_RECORD_BYTES})"
    nl = tail.find(b"\n")
    if 0 <= nl < len(tail) - 1:
        return "more than one line after the last verified record"
    zeros = b"\x00" in tail
    if nl == len(tail) - 1:                      # a complete line
        if zeros:
            return None                          # the last append, with a range the file system left zero-filled
        if _verify(tail[:-1]) is not None:
            return "a valid record out of sequence"
        return "a complete record that does not verify (CRC, JSON or framing)"
    if tail[0] == 0:
        return None                              # zero-filled from the start of the append
    if tail[0] != 0x7B:
        return "bytes that are not the start of a record"
    if _object_closes_before_end(tail):
        return "a complete record followed by stray bytes (not a record prefix)"
    return None                                  # a prefix of the record line being appended


class Journal:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.records: list[dict] = []
        self.good_bytes = 0
        self.bad_tail_bytes = 0
        self.tail_state = "clean"               # "clean" or "torn" after a replay
        self.poisoned: str | None = None        # why appends are refused (a failed append), else None

    def create(self) -> None:
        """Start an empty journal (header record), durably."""
        self.records, self.good_bytes, self.bad_tail_bytes, self.tail_state = [], 0, 0, "clean"
        open(self.path, "xb").close()               # refuses an existing journal
        self.append({"op": "header", "format": JOURNAL_FORMAT, "seq": 0})
        sync_dir(self.path.parent)                  # the journal's directory entry

    @staticmethod
    def _line(rec: dict) -> bytes:
        return canonical_json({**rec, "crc": _crc(rec)}) + b"\n"

    def replay(self) -> list[dict]:
        """Load every verified record. A torn last append is ignored and reported (``bad_tail_bytes``, ``tail_state``);
        any other unverified content raises :class:`VNXIntegrityError` (the journal is left untouched)."""
        records: list[dict] = []
        good = 0
        try:
            data = self.path.read_bytes()
        except OSError as error:
            raise VNXFormatError(f"cannot read the volume journal {self.path}: {error.strerror or error}", stage="volume") from None
        pos = 0
        while pos < len(data):
            nl = data.find(b"\n", pos)
            if nl < 0:
                break                                       # unterminated tail
            rec = _verify(data[pos:nl])
            if rec is None or rec.get("seq") != len(records):
                break                                       # nothing after it is trusted without classification
            records.append(rec)
            pos = nl + 1
            good = pos
        if not records or records[0].get("op") != "header" or records[0].get("format") != JOURNAL_FORMAT:
            raise VNXFormatError(f"{self.path} is not a {JOURNAL_FORMAT} journal", stage="volume")
        tail = data[good:]
        if tail:
            why = classify_tail(tail)
            if why is not None:
                raise VNXIntegrityError(
                    f"volume journal {self.path} is damaged at byte {good}: {why} (not a torn last append; the volume "
                    "is not opened and nothing is changed)", stage="volume",
                    details={"good_bytes": good, "records": len(records), "reason": why})
        self.records, self.good_bytes, self.bad_tail_bytes = records, good, len(tail)
        self.tail_state = "torn" if tail else "clean"
        self.poisoned = None
        return self.records

    def append(self, rec: dict) -> dict:
        """Append ``rec`` (sequence number assigned here) durably: the file is cut to the last verified record, the
        line is written, flushed and fsynced, and the directory is synced. Any failure on the way poisons the
        journal (later appends raise :class:`VNXOutputError` until a new replay)."""
        if self.poisoned:
            raise VNXOutputError(f"volume journal {self.path} is unusable after a failed append ({self.poisoned}); "
                                 "close and reopen the volume", stage="volume")
        rec = {**rec, "seq": len(self.records)}
        line = self._line(rec)
        if len(line) > MAX_RECORD_BYTES:
            raise VNXOutputError(f"journal record too long ({len(line)} > {MAX_RECORD_BYTES} bytes)", stage="volume")
        created = not self.path.exists()
        try:
            with open(self.path, "wb" if created else "r+b") as f:
                f.truncate(self.good_bytes)                 # a torn tail, or what a failed append left behind
                f.seek(self.good_bytes)
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            if created:
                sync_dir(self.path.parent)
        except BaseException as error:
            self.poisoned = f"{type(error).__name__}: {error}"[:200]
            raise
        self.records.append(rec)
        self.good_bytes += len(line)
        self.bad_tail_bytes, self.tail_state = 0, "clean"
        return rec


__all__ = ["JOURNAL_FORMAT", "MAX_RECORD_BYTES", "Journal", "classify_tail", "sync_dir"]
