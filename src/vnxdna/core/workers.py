"""Worker process pools that cannot outlive the command (V3 release review; moved here from ``vnxdna.v2.workers`` in
V14 so the layered packages, e.g. ``vnxdna.pipeline``, can use it; ``vnxdna.v2.workers`` re-exports it).

A ``ProcessPoolExecutor`` worker is not tied to its parent's life: when the
parent was killed with SIGTERM or SIGKILL, the workers stayed alive as orphans
(PPID 1, ~90 MB each). Every pool is created here; each worker runs a daemon
thread that exits the worker as soon as its parent is gone.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Optional

_POLL_S = 0.5


def _watch_parent(parent: int) -> None:
    while True:
        time.sleep(_POLL_S)
        if os.getppid() != parent:
            os._exit(1)


def _init(parent: int, initializer: Optional[Callable[..., Any]], initargs: tuple) -> None:
    # A forked worker inherits the CLI's SIGTERM/SIGHUP handler (which turns them into KeyboardInterrupt for cleanup);
    # a worker has nothing to clean up and must simply end when the parent terminates it. Ctrl-C reaches the whole
    # process group: workers ignore it (each used to print a KeyboardInterrupt traceback) and are killed by the parent.
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    threading.Thread(target=_watch_parent, args=(parent,), daemon=True, name="vnxdna-parent-watch").start()
    if initializer is not None:
        initializer(*initargs)


class _Pool(ProcessPoolExecutor):
    """On an interrupt (Ctrl-C, or SIGTERM/SIGHUP turned into KeyboardInterrupt by the CLI) the workers are killed and
    the pool is abandoned instead of joined. A worker killed while writing its result leaves a partial message in the
    result pipe, and ``shutdown(wait=True)`` then waits forever for the pool's management thread (seen in the release
    review: ``recover`` hung after SIGTERM). Ordinary errors keep the normal, graceful shutdown. The CLI ends an
    interrupted command with ``os._exit`` after its cleanup, so the abandoned threads are never joined at exit."""

    _aborted = False

    def abort(self) -> None:
        """The interrupt path: kill the workers and abandon the pool (never joined). Idempotent."""
        self._aborted = True
        for process in list((getattr(self, "_processes", None) or {}).values()):
            if process.is_alive():
                process.kill()
        self.shutdown(wait=False, cancel_futures=True)

    def close(self) -> None:
        """The normal path for pools not used as a context manager: a graceful shutdown that cancels queued tasks; a
        no-op after :meth:`abort` (joining an aborted pool is exactly the hang ``abort`` avoids)."""
        if not self._aborted:
            self.shutdown(wait=True, cancel_futures=True)

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and issubclass(exc_type, KeyboardInterrupt):
            self.abort()
            return False
        return super().__exit__(exc_type, exc, tb)


def process_pool(workers: int, initializer: Optional[Callable[..., Any]] = None, initargs: tuple = (),
                 mp_context: Any = None) -> "_Pool":
    """A ``ProcessPoolExecutor`` whose workers exit when the parent process dies and that never hangs on an interrupt.
    ``mp_context``: an explicit multiprocessing context (e.g. ``get_context("fork")`` for workers that inherit state
    the parent built instead of receiving it pickled); None = the platform default."""
    return _Pool(workers, mp_context=mp_context, initializer=_init, initargs=(os.getpid(), initializer, tuple(initargs)))
