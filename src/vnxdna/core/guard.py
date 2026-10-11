"""Opt-in VNX-Secure gate (core layer so legacy and SDK entry points share it; the secure package is loaded lazily) for encode /
decode / restore / extract and the SDK metadata reads (V11 WP4).

Default behaviour is unchanged: with no :class:`SecureContext`, no token/session variable and ``VNX_SECURE_REQUIRE`` unset,
:func:`enforce` returns immediately. When a context is given (or ``VNX_SECURE_TOKEN``/``VNX_SECURE_SESSION`` is set), the request is evaluated
by the existing control plane (:meth:`ControlPlane.authorize`) *before* any work starts, and every failure (denial, missing or
partial context, unreadable home, any unexpected error) raises :class:`AccessDenied`: the gate fails closed, so a failed check can
never reach SUCCESS. Tokens are never placed in exceptions, logs or reports. No cryptographic primitive is added here.

Resource key. The resource is ``archive:<archive_id>`` (the 32-hex id from the container manifest), so renaming or copying an
archive cannot dodge a lock. Read files carry no id before decoding: they are checked as ``archive:path:<resolved path>`` at
entry and re-checked as ``archive:<archive_id>`` (:func:`rebind`) once the manifest is recovered, before any plaintext exists.
A lock on the legacy alias ``archive:<file name>`` (what :class:`vnxdna.secure.gate.ArchiveGate` and older homes use) is honoured
as well. The id of an *unencrypted* container is not authenticated without a key; such an archive offers no confidentiality to
protect, the gate there is access control on the operation only.

Freshness. The control plane is cached per process (its in-memory rate limits and detections persist across calls), but before
every decision the persisted files of the home (state, principals, policy, baseline, audit log) are compared with what this
process last saw; any outside change (a lock, a revocation, a recovery) rebuilds the plane from disk. Tightening state found
only in memory (locks, revocations, contained sessions, a more severe system state) is carried over, so a rebuild never
loosens the gate. Residual: two processes deciding at the same instant are not serialised against each other.
"""
from __future__ import annotations

import contextvars
import functools
import json
import os
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

__all__ = ["SecureContext", "enforce", "rebind", "gated", "required", "context_from_env", "resource_of", "active"]

ENV_REQUIRE = "VNX_SECURE_REQUIRE"
ENV_KEYS = ("VNX_SECURE_HOME", "VNX_SECURE_TOKEN", "VNX_SECURE_SESSION")
_OPS = {"encode": "write", "decode": "read", "restore": "extract", "extract": "extract",
        "list": "list", "verify": "verify", "locate": "locate"}
_WATCHED = ("state.json", "principals.json", "policy.json", "baseline.json", "audit.log")
_MAX_MANIFEST = 1 << 20
_CACHE: dict[str, Any] = {}
_SEEN: dict[str, tuple] = {}
_LOCK = threading.RLock()
#: (operation class, resource, context) already allowed inside the current :func:`gated` call, so nested gated layers do not
#: re-ask. ``None`` outside any gated call: a direct :func:`enforce` then always asks the control plane and remembers nothing.
_GRANTED: contextvars.ContextVar[frozenset | None] = contextvars.ContextVar("vnx_secure_granted", default=None)


@dataclass(frozen=True)
class SecureContext:
    """Credentials for one session: the home of the control plane, a token from ``ControlPlane.login`` and its session."""
    home: str | os.PathLike
    token: str = field(repr=False)
    session: str


class _Partial:
    """Marker: some but not all ``VNX_SECURE_*`` variables are set (a misconfiguration, denied, never ignored)."""


def required() -> bool:
    return os.environ.get(ENV_REQUIRE, "").strip().lower() in ("1", "true", "yes", "on")


def context_from_env() -> SecureContext | None:
    """``VNX_SECURE_HOME`` + ``VNX_SECURE_TOKEN`` + ``VNX_SECURE_SESSION`` (env, never argv, so no token in process lists).

    Returns ``None`` when neither a token nor a session is set (``VNX_SECURE_HOME`` alone is also the default home of the
    ``vnx secure`` admin commands, so it does not turn the gate on by itself). A token or a session without the full set is a
    misconfiguration and raises :class:`AccessDenied` (``partial_context``): it never silently disables the gate."""
    vals = [os.environ.get(k) or "" for k in ENV_KEYS]
    if all(vals):
        return SecureContext(*vals)
    if any(vals[1:]):
        missing = ", ".join(k for k, v in zip(ENV_KEYS, vals) if not v)
        raise _control().AccessDenied("partial_context", f"VNX-Secure is partially configured (missing {missing})")
    return None


def active(ctx: SecureContext | None = None) -> bool:
    """True when a decision would be made (context given, a token or session in the environment, or required)."""
    return ctx is not None or required() or any(os.environ.get(k) for k in ENV_KEYS[1:])


def _control() -> Any:
    return import_module("vnxdna.secure.control")


def _signature(home: Path) -> tuple:
    sig = []
    for name in _WATCHED:
        try:
            st = (home / name).stat()
            sig.append((st.st_ino, st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            sig.append(None)
    return tuple(sig)


def _carry_over(old: Any, new: Any) -> None:
    """Keep in-memory tightening (never loosen) when a plane is rebuilt from disk."""
    sev = import_module("vnxdna.secure.state").SEVERITY
    new.cont.locked_resources |= old.cont.locked_resources
    new.cont.contained_sessions |= old.cont.contained_sessions
    new.tokens.revoked_tokens |= old.tokens.revoked_tokens
    new.tokens.revoked_sessions |= old.tokens.revoked_sessions
    for k, v in old.tokens.revoked_principals.items():
        new.tokens.revoked_principals[k] = max(v, new.tokens.revoked_principals.get(k, v))
    new.findings, new.detector, new.requests, new.replay = old.findings, old.detector, old.requests, old.replay
    if sev.get(old.sm.state, 0) > sev.get(new.sm.state, 0):
        new.sm.escalate(old.sm.state, "carried over from this process", new.clock())


def _plane(home) -> tuple[Any, str]:
    key = str(Path(home).resolve())
    if not (Path(key) / "secure.key").exists():
        raise FileNotFoundError("not a VNX-Secure home")
    cp, sig = _CACHE.get(key), _signature(Path(key))
    if cp is None or _SEEN.get(key) != sig:
        fresh = _control().ControlPlane(Path(key))
        if cp is not None:
            _carry_over(cp, fresh)
        cp = _CACHE[key] = fresh
    return cp, key


def _archive_id(p: Path) -> str | None:
    """The manifest ``archive_id`` of a V1/V2 ``.vxdna`` or VNX4 container, else None. Header/footer reads only, no key, and
    no import of the container packages (this is the core layer); layouts: container/vxdna.py, v2/container.py, VNX4 spec."""
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            head = f.read(24)
            if head[:8] == b"\x89VNX4\r\n\x1a" and size >= 16 + 112:            # VNX4: manifest just before the trailer
                f.seek(size - 112)
                mn = struct.unpack(">QQQQQ", f.read(40))[4]
                start = size - 112 - mn
            elif head[:8] == b"\x89VXDNA\r\n" and head[8:10] == b"\x00\x01" and len(head) == 24:   # V1: header, manifest
                mn, start = int.from_bytes(head[12:16], "big"), 24
            elif head[:8] == b"\x89VXDNA\r\n" and head[8:10] == b"\x00\x02" and size >= 16 + 64:   # V2: body, manifest, ...
                f.seek(size - 64)
                tail = f.read(64)
                mn, start = int.from_bytes(tail[8:12], "big"), 16 + int.from_bytes(tail[0:8], "big")
            else:
                return None
            if not 0 < mn <= _MAX_MANIFEST or not 0 <= start <= size - mn:
                return None
            f.seek(start)
            aid = json.loads(f.read(mn)).get("archive_id")
    except Exception:  # noqa: BLE001 - not a readable container: fall back to the path key
        return None
    return _norm_id(aid)


def _norm_id(aid: Any) -> str | None:
    if isinstance(aid, (bytes, bytearray)):
        aid = bytes(aid).hex()
    if isinstance(aid, str) and 8 <= len(aid) <= 128 and all(c in "0123456789abcdef" for c in aid.lower()):
        return aid.lower()
    return None


def resource_of(path: str | os.PathLike, archive_id: Any = None) -> str:
    """Canonical resource: ``archive:<archive_id>`` when known, else ``archive:path:<resolved path>``."""
    aid = _norm_id(archive_id) if archive_id is not None else _archive_id(Path(path))
    return f"archive:{aid}" if aid else f"archive:path:{Path(path).resolve()}"


def _resolve_ctx(ctx: Any) -> SecureContext | None:
    AccessDenied = _control().AccessDenied
    if ctx is None:
        ctx = context_from_env()
        if ctx is None:
            if required():
                raise AccessDenied("no_context", "VNX-Secure is required but no security context was supplied")
            return None
    elif not isinstance(ctx, SecureContext):
        raise AccessDenied("bad_context", "invalid security context")
    return ctx


def enforce(operation: str, path: str | os.PathLike, ctx: SecureContext | None = None, *, archive_id: Any = None) -> None:
    """Return only if the request is allowed; otherwise raise :class:`AccessDenied` (always, when a gate is active)."""
    if not active(ctx):
        return
    AccessDenied = _control().AccessDenied
    try:
        ctx = _resolve_ctx(ctx)
        if ctx is None:
            return
        op = _OPS.get(operation)
        if op is None:
            raise AccessDenied("bad_operation", f"unknown operation {operation!r}")
        resource = resource_of(path, archive_id)
        grant, granted = (op, resource, ctx), _GRANTED.get()
        if granted is not None and grant in granted:
            return
        aliases = {f"archive:{Path(path).name}", f"archive:path:{Path(path).resolve()}"} - {resource}
        with _LOCK:
            cp, key = _plane(ctx.home)
            try:
                if aliases & cp.cont.locked_resources:
                    raise AccessDenied("resource_locked", "archive is locked pending recovery")
                cp.authorize(ctx.token, ctx.session, uuid.uuid4().hex, time.time(), resource, op)
            finally:
                _SEEN[key] = _signature(Path(key))   # our own audit append is not an outside change
        if granted is not None:
            _GRANTED.set(granted | {grant})
    except AccessDenied:
        raise
    except Exception as e:  # fail closed: nothing but an explicit allow passes
        raise AccessDenied("gate_error", f"security check failed ({type(e).__name__})") from None


def rebind(operation: str, path: str | os.PathLike, archive_id: Any, ctx: SecureContext | None = None) -> None:
    """Re-check under the archive id once a manifest is known (read files); a no-op when the id was already authorized."""
    if active(ctx):
        if _norm_id(archive_id) is None:
            raise _control().AccessDenied("gate_error", "security check failed (archive id unavailable)")
        enforce(operation, path, ctx, archive_id=archive_id)


def gated(operation: str):
    """Decorator: enforce ``operation`` on the first positional argument before the call; grants are scoped to the call."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(path, *args, **kwargs):
            token = _GRANTED.set(_GRANTED.get() or frozenset())
            try:
                enforce(operation, path)
                return fn(path, *args, **kwargs)
            finally:
                _GRANTED.reset(token)
        return wrapper
    return deco
