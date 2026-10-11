"""Opt-in VNX-Secure gate for encode / decode / restore (V11 WP4).

Default behaviour is unchanged: with no :class:`SecureContext` and ``VNX_SECURE_REQUIRE`` unset, :func:`enforce` returns
immediately. When a context is given (or ``VNX_SECURE_REQUIRE=1``), the request is evaluated by the existing control plane
(:meth:`ControlPlane.authorize`) *before* any work starts, and every failure (denial, missing context, unreadable home,
any unexpected error) raises :class:`AccessDenied`: the gate fails closed, so a failed check can never reach SUCCESS.
Tokens are never placed in exceptions, logs or reports. No cryptographic primitive is added here.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .control import AccessDenied, ControlPlane

__all__ = ["SecureContext", "enforce", "required", "context_from_env", "AccessDenied"]

ENV_REQUIRE = "VNX_SECURE_REQUIRE"
_OPS = {"encode": "write", "decode": "read", "restore": "extract", "extract": "extract"}
_CACHE: dict[str, ControlPlane] = {}
_LOCK = threading.Lock()


@dataclass(frozen=True)
class SecureContext:
    """Credentials for one session: the home of the control plane, a token from ``ControlPlane.login`` and its session."""
    home: str | os.PathLike
    token: str = field(repr=False)
    session: str


def required() -> bool:
    return os.environ.get(ENV_REQUIRE, "").strip().lower() in ("1", "true", "yes", "on")


def context_from_env() -> SecureContext | None:
    """``VNX_SECURE_HOME`` + ``VNX_SECURE_TOKEN`` + ``VNX_SECURE_SESSION`` (env, never argv, so no token in process lists)."""
    home, tok, ses = (os.environ.get(k) for k in ("VNX_SECURE_HOME", "VNX_SECURE_TOKEN", "VNX_SECURE_SESSION"))
    return SecureContext(home, tok, ses) if home and tok and ses else None


def _plane(home) -> ControlPlane:
    key = str(Path(home).resolve())
    with _LOCK:
        cp = _CACHE.get(key)
        if cp is None:
            if not (Path(key) / "secure.key").exists():
                raise FileNotFoundError("not a VNX-Secure home")
            cp = _CACHE[key] = ControlPlane(Path(key))
        return cp


def enforce(operation: str, path: str | os.PathLike, ctx: SecureContext | None = None) -> None:
    """Return only if the request is allowed; otherwise raise :class:`AccessDenied` (always, when a gate is active)."""
    if ctx is None:
        ctx = context_from_env()
        if ctx is None:
            if required():
                raise AccessDenied("no_context", "VNX-Secure is required but no security context was supplied")
            return
    elif not isinstance(ctx, SecureContext):
        raise AccessDenied("bad_context", "invalid security context")
    op = _OPS.get(operation)
    if op is None:
        raise AccessDenied("bad_operation", f"unknown operation {operation!r}")
    try:
        _plane(ctx.home).authorize(ctx.token, ctx.session, uuid.uuid4().hex, time.time(), f"archive:{Path(path).name}", op)
    except AccessDenied:
        raise
    except Exception as e:  # fail closed: nothing but an explicit allow passes
        raise AccessDenied("gate_error", f"security check failed ({type(e).__name__})") from None
