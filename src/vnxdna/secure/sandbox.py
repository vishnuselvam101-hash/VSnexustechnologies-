"""Process sandbox for parsing untrusted archives (VNX-Secure; docs/VNX_SECURE_ARCHITECTURE.md §2).

``run_limited`` runs a function in a forked child with an address-space limit (RLIMIT_AS), a CPU-time limit
(RLIMIT_CPU) and a wall-clock timeout; the child is killed when it exceeds any of them. A crash, a resource blow-up or
a hang in the parser then ends that child, not the caller. Exceptions raised by the function are re-raised in the
parent with the same type when it is a ``vnxdna.core.errors`` type, otherwise as :class:`SandboxError`.

Scope: POSIX only (fork + setrlimit). This is resource and fault isolation, not a security boundary against code
execution: the child has the same user, file-system view and network access as the parent.
"""
from __future__ import annotations

import multiprocessing as mp
import resource

from ..core import errors as E


class SandboxError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def _child(conn, fn, args, kwargs, mem_bytes, cpu_seconds):
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        conn.send(("ok", fn(*args, **kwargs)))
    except MemoryError:
        conn.send(("err", "MemoryError", "memory limit exceeded"))
    except BaseException as e:  # noqa: BLE001 - everything is reported to the parent
        conn.send(("err", type(e).__name__, str(e)[:2000]))
    finally:
        conn.close()


def run_limited(fn, *args, mem_bytes: int = 1 << 30, cpu_seconds: int = 30, timeout: float = 60.0, **kwargs):
    ctx = mp.get_context("fork")
    parent, child = ctx.Pipe(duplex=False)
    p = ctx.Process(target=_child, args=(child, fn, args, kwargs, mem_bytes, cpu_seconds), daemon=True)
    p.start()
    child.close()
    try:
        if not parent.poll(timeout):
            p.kill()
            raise SandboxError("timeout", f"no result within {timeout:.0f}s")
        try:
            msg = parent.recv()
        except EOFError:
            raise SandboxError("crashed", f"child exited with code {p.exitcode}")
    finally:
        p.join(5)
        if p.is_alive():
            p.kill()
        parent.close()
    if msg[0] == "ok":
        return msg[1]
    _, name, text = msg
    cls = getattr(E, name, None)
    if isinstance(cls, type) and issubclass(cls, E.VNXError):
        raise cls(text)
    if name == "MemoryError":
        raise SandboxError("memory_limit", text)
    raise SandboxError("error", f"{name}: {text}")
