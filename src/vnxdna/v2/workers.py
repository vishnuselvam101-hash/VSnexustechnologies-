"""Worker process pools that cannot outlive the command (V3 release review).

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
    # a forked worker inherits the CLI's SIGTERM/SIGHUP handler (which turns them into KeyboardInterrupt for cleanup);
    # a worker has nothing to clean up and must simply end when the parent terminates it
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_DFL)
    threading.Thread(target=_watch_parent, args=(parent,), daemon=True, name="vnxdna-parent-watch").start()
    if initializer is not None:
        initializer(*initargs)


def process_pool(workers: int, initializer: Optional[Callable[..., Any]] = None, initargs: tuple = ()) -> ProcessPoolExecutor:
    """A ``ProcessPoolExecutor`` whose workers exit when the parent process dies."""
    return ProcessPoolExecutor(workers, initializer=_init, initargs=(os.getpid(), initializer, tuple(initargs)))
