"""Worker process pools that cannot outlive the command (V3 release review). The implementation lives in
:mod:`vnxdna.core.workers` since V14 (layered packages may not import legacy code); this module re-exports it."""
from __future__ import annotations

from vnxdna.core.workers import _POLL_S, _Pool, _init, _watch_parent, process_pool

__all__ = ["process_pool", "_Pool", "_init", "_watch_parent", "_POLL_S"]
