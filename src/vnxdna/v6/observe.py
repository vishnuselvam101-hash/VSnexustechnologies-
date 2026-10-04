"""Structured decode events (JSON lines) for operators and the control plane.

An *observer* is any callable taking one event dict. :class:`JsonlObserver` writes one JSON object per line, flushed
per event, so a tail of the file is always a valid prefix; ``vnx decode --events PATH`` uses it. Every event carries::

    ts          UTC ISO-8601 time            task_id     caller-chosen ID (default: random 12 hex)
    event       decode_start | pass1_progress | pass1_end | recovery_round | pass2_end | verify | decode_end | error
    stage       pipeline stage name          elapsed     seconds since decode start
    rss_bytes   parent-process peak RSS      archive_id  once the superblock is known

plus event-specific metrics (reads processed/accepted/rejected, queue depth, worker CPU seconds and utilisation,
recovery attempts, groups decoded/failed, verification result). Events are observability only: nothing in the
decode depends on them, and an observer that raises is detached rather than allowed to change the decode.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import stat
import time
from pathlib import Path

from ..v4.util import peak_rss_bytes


class Events:
    """Event emitter bound to one decode (no-op when ``observer`` is None)."""

    def __init__(self, observer=None, task_id: str | None = None, t0: float | None = None):
        self.observer = observer
        self.task_id = task_id or secrets.token_hex(6)
        self.t0 = time.perf_counter() if t0 is None else t0
        self.archive_id: str | None = None
        self.failed: str | None = None

    def __bool__(self) -> bool:
        return self.observer is not None

    def emit(self, event: str, stage: str, **metrics) -> None:
        if self.observer is None:
            return
        rec = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds"), "task_id": self.task_id,
               "event": event, "stage": stage, "elapsed": round(time.perf_counter() - self.t0, 4),
               "rss_bytes": peak_rss_bytes(), "pid": os.getpid()}
        if self.archive_id:
            rec["archive_id"] = self.archive_id
        rec.update(metrics)
        try:
            self.observer(rec)
        except Exception as error:  # noqa: BLE001 - observability must never change a decode
            self.failed = f"{type(error).__name__}: {error}"
            self.observer = None


class JsonlObserver:
    """Append events as JSON lines to ``path`` (created with mode 0o600).

    Only a regular file is accepted: a symlink is refused (``O_NOFOLLOW``) and a FIFO, device or socket is refused
    after a non-blocking open (a FIFO without a reader would otherwise block the decode forever)."""

    def __init__(self, path: str | os.PathLike):
        from ..v4.errors import VNXConfigurationError
        self.path = Path(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise VNXConfigurationError(f"cannot open events file {self.path}: {error.strerror}") from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise VNXConfigurationError(f"events file {self.path} is not a regular file")
            os.set_blocking(fd, True)
        except BaseException:
            os.close(fd)
            raise
        self._f = os.fdopen(fd, "a", encoding="utf-8")

    def __call__(self, event: dict) -> None:
        self._f.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
