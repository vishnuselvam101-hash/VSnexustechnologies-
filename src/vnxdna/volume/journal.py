"""The volume's write-ahead journal: one canonical-JSON record per line, each carrying its own CRC-32, appended and
fsynced. Replay stops at the first line that does not verify (a torn write), which is then the point the next append
truncates to; everything before it is durable."""
from __future__ import annotations

import os
import zlib
from pathlib import Path

from vnxdna.core.errors import VNXFormatError
from vnxdna.core.fsio import fsync_dir
from vnxdna.core.util import canonical_json, parse_canonical_json

JOURNAL_FORMAT = "vnx.volume-journal/1"


class Journal:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.records: list[dict] = []
        self.good_bytes = 0
        self.bad_tail_bytes = 0

    def create(self) -> None:
        """Start an empty journal (header record), durably."""
        self.records, self.good_bytes, self.bad_tail_bytes = [], 0, 0
        open(self.path, "xb").close()               # refuses an existing journal
        self.append({"op": "header", "format": JOURNAL_FORMAT, "seq": 0})

    @staticmethod
    def _line(rec: dict) -> bytes:
        body = canonical_json(rec)
        return canonical_json({**rec, "crc": f"{zlib.crc32(body) & 0xFFFFFFFF:08x}"}) + b"\n"

    def replay(self) -> list[dict]:
        """Load every verified record; a torn or corrupt tail is ignored and reported (``bad_tail_bytes``)."""
        self.records, self.good_bytes, self.bad_tail_bytes = [], 0, 0
        try:
            data = self.path.read_bytes()
        except OSError as error:
            raise VNXFormatError(f"cannot read the volume journal {self.path}: {error.strerror or error}", stage="volume") from None
        pos = 0
        while pos < len(data):
            nl = data.find(b"\n", pos)
            if nl < 0:
                break                                       # unterminated tail: torn write
            line = data[pos:nl]
            try:
                rec = parse_canonical_json(line, "journal record")
                crc = rec.pop("crc")
                if not isinstance(crc, str) or f"{zlib.crc32(canonical_json(rec)) & 0xFFFFFFFF:08x}" != crc:
                    break
                if rec.get("seq") != len(self.records):
                    break                                   # a gap or replay: nothing after it is trusted
            except (VNXFormatError, ValueError, KeyError, TypeError):
                break                                       # not canonical, no CRC, or corrupt: a torn tail
            self.records.append(rec)
            pos = nl + 1
            self.good_bytes = pos
        self.bad_tail_bytes = len(data) - self.good_bytes
        if not self.records or self.records[0].get("op") != "header" or self.records[0].get("format") != JOURNAL_FORMAT:
            raise VNXFormatError(f"{self.path} is not a {JOURNAL_FORMAT} journal", stage="volume")
        return self.records

    def append(self, rec: dict) -> dict:
        """Append ``rec`` (sequence number assigned here) durably: a torn tail is cut first, the line is written,
        flushed and fsynced, and the directory entry is synced."""
        rec = {**rec, "seq": len(self.records)}
        line = self._line(rec)
        with open(self.path, "r+b" if self.path.exists() else "wb") as f:
            if self.bad_tail_bytes:
                f.truncate(self.good_bytes)
                self.bad_tail_bytes = 0
            f.seek(self.good_bytes)
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        fsync_dir(self.path.parent)
        self.records.append(rec)
        self.good_bytes += len(line)
        return rec


__all__ = ["JOURNAL_FORMAT", "Journal"]
