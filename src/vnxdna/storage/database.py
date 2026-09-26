"""Local SQLite registry for archive metadata; archive payloads remain self-contained."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any

class ArchiveRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS archives (
                archive_id TEXT PRIMARY KEY, path TEXT NOT NULL, created_at TEXT NOT NULL,
                file_count INTEGER NOT NULL, logical_bytes INTEGER NOT NULL)""")
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
    def upsert(self, manifest: dict[str, Any], path: str | Path) -> None:
        files = manifest.get("files", [])
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO archives VALUES (?, ?, ?, ?, ?)", (
                manifest["archive_id"], str(Path(path).resolve()), manifest["created_at"],
                len(files), sum(item["original_size"] for item in files)))
    def get(self, archive_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT archive_id,path,created_at,file_count,logical_bytes FROM archives WHERE archive_id=?", (archive_id,)).fetchone()
        return None if row is None else dict(zip(("archive_id", "path", "created_at", "file_count", "logical_bytes"), row))
