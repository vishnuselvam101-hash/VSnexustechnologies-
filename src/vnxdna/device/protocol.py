"""Service protocols ``vnx.mgmt/1`` and ``vnx.codec-ipc/1`` (docs/V14_CONTRACTS.md §6); mirror of ``vnxd/src/proto.rs``.

One canonical-JSON message per line (``core.util.canonical_json``). Parsing is strict (no floats, no duplicate keys,
no lone surrogates, no ``-0``, 64-bit integers, depth <= 32, <= 1 MiB) and validation runs in the same order as the
Rust implementation, so both report the same code for the same message: JSON syntax -> object -> ``proto`` ->
``v`` (VERSION_MISMATCH) -> envelope keys -> op -> fields (all PROTOCOL).
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..core.util import canonical_json
from .contracts import ERROR_CODES, DeviceError

VERSION = 1
MAX_DEPTH = 32
MAX_DOC = 1 << 20
PROTOCOLS = ("vnx.mgmt", "vnx.codec-ipc")
HEALTH_STATES = ("ok", "degraded", "failed")
_I64 = (-(1 << 63), (1 << 63) - 1)
_NAME = re.compile(r"[A-Za-z0-9._-]{1,255}")
_SHA = re.compile(r"[0-9a-f]{64}")

_HEX128 = re.compile(r"[0-9a-f]{32}")
_SEAL_ARGS = {"job": "count", "device_id": "hex128", "extent": "count", "generation": "positive", "input": "name",
              "input_sha256": "sha256", "output": "name"}
_SEAL_RESULT = {"job": "count", "output_sha256": "sha256", "output_bytes": "positive"}
_HELLO = ({"client": "str", "versions": "versions"}, {"server": "str", "version": "positive"})
#: protocol -> op -> (request args schema, ok-result schema)
OPS: dict[str, dict[str, tuple[dict[str, str], dict[str, str]]]] = {
    "vnx.mgmt": {
        "hello": _HELLO,
        "capacity": ({}, {"block_size": "positive", "blocks_per_extent": "positive", "capacity_blocks": "positive",
                          "capacity_bytes": "positive", "extent_count": "positive"}),
        "health": ({}, {"state": "health", "detail": "object"}),
        "stats": ({}, {"counters": "object"}),
        "shutdown": ({}, {}),
        "authenticate": ({"token": "str"}, {"role": "str"}),
    },
    "vnx.codec-ipc": {
        "hello": _HELLO,
        "encode_extent": ({"job": "count", "extent": "count", "generation": "positive", "input": "name",
                           "input_sha256": "sha256", "output": "name", "options": "object"},
                          {"job": "count", "output_sha256": "sha256", "output_bytes": "positive"}),
        "decode_extent": ({"job": "count", "extent": "count", "generation": "positive", "input": "name",
                           "output": "name", "expect_sha256": "sha256", "expect_bytes": "positive",
                           "options": "object"},
                          {"job": "count", "output_sha256": "sha256", "output_bytes": "positive"}),
        "key_check": ({"device_id": "hex128"}, {"key_check": "sha256"}),
        "seal_extent": (_SEAL_ARGS, _SEAL_RESULT),
        "open_extent": (_SEAL_ARGS, _SEAL_RESULT),
    },
}


def _bad(msg: str) -> DeviceError:
    return DeviceError("PROTOCOL", msg)


def is_staging_name(s: Any) -> bool:
    """A file name the codec worker may open inside the staging directory (never a path)."""
    return isinstance(s, str) and _NAME.fullmatch(s) is not None and not s.startswith(".")


def _is_int(v: Any) -> bool:
    """An integer the wire format can carry (signed 64-bit)."""
    return type(v) is int and _I64[0] <= v <= _I64[1]


def _utf8_len(s: str) -> int | None:
    """UTF-8 length, or None for a string holding a lone surrogate (not encodable, never valid on the wire)."""
    try:
        return len(s.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _check_ty(key: str, ty: str, v: Any) -> None:
    ok = {
        "str": lambda: isinstance(v, str) and 1 <= (_utf8_len(v) or 0) <= 1024,
        "count": lambda: _is_int(v) and v >= 0,
        "positive": lambda: _is_int(v) and v >= 1,
        "sha256": lambda: isinstance(v, str) and _SHA.fullmatch(v) is not None,
        "name": lambda: is_staging_name(v),
        "object": lambda: isinstance(v, dict),
        "versions": lambda: isinstance(v, list) and len(v) > 0 and all(_is_int(x) and x >= 1 for x in v),
        "health": lambda: v in HEALTH_STATES,
        "hex128": lambda: isinstance(v, str) and _HEX128.fullmatch(v) is not None,
    }[ty]()
    if not ok:
        raise _bad(f"field {key!r}: expected {ty}")


def _check_fields(what: str, schema: dict[str, str], v: Any) -> None:
    if not isinstance(v, dict):
        raise _bad(f"{what} must be an object")
    for k, ty in schema.items():
        if k not in v:
            raise _bad(f"{what}: missing field {k!r}")
        _check_ty(k, ty, v[k])
    extra = sorted(set(v) - set(schema))
    if extra:
        raise _bad(f"{what}: unknown field {extra[0]!r}")


def _no_dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise _bad(f"duplicate key {k!r}")
        out[k] = v
    return out


def _int(text: str) -> int:
    if text == "-0":
        raise _bad("negative zero is not canonical")
    v = int(text)
    if not _I64[0] <= v <= _I64[1]:
        raise _bad(f"integer {text} outside the 64-bit range")
    return v


def _no_float(text: str) -> Any:
    raise _bad(f"non-integer number {text} (floats are not allowed)")


def _walk(v: Any, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise _bad(f"nesting deeper than {MAX_DEPTH}")
    if isinstance(v, str):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in v):
            raise _bad("unpaired surrogate")
    elif isinstance(v, list):
        for x in v:
            _walk(x, depth + 1)
    elif isinstance(v, dict):
        for k, x in v.items():
            _walk(k, depth)
            _walk(x, depth + 1)


def parse(text: str) -> Any:
    """Strict JSON (the contract subset); every violation is a PROTOCOL error."""
    size = _utf8_len(text)
    if size is None:
        raise _bad("unpaired surrogate")
    if size > MAX_DOC:
        raise _bad(f"document exceeds {MAX_DOC} bytes")
    try:
        v = json.loads(text, object_pairs_hook=_no_dupes, parse_int=_int, parse_float=_no_float,
                       parse_constant=_no_float)
    except DeviceError:
        raise
    except (ValueError, RecursionError) as e:
        raise _bad(f"invalid JSON: {e}") from None
    _walk(v, 1)
    return v


def canonical(v: Any) -> str:
    return canonical_json(v).decode("ascii")


def _envelope(proto: str, v: Any) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise _bad("a message must be a JSON object")
    if v.get("proto") != proto:
        raise _bad(f"expected proto {proto!r}")
    ver = v.get("v")
    if not _is_int(ver):
        raise _bad("missing or invalid version")
    if ver != VERSION:
        raise DeviceError("VERSION_MISMATCH", f"{proto} version {ver} (supported: 1)")
    return v


def _keys(v: dict[str, Any], want: set[str]) -> None:
    if set(v) != want:
        raise _bad(f"envelope keys {sorted(v)}, expected {sorted(want)}")


def _op(proto: str, op: Any) -> tuple[dict[str, str], dict[str, str]]:
    if not isinstance(op, str) or op not in OPS[proto]:
        raise _bad(f"unknown {proto} op {op!r}")
    return OPS[proto][op]


def validate_request(proto: str, v: Any) -> dict[str, Any]:
    o = _envelope(proto, v)
    _keys(o, {"proto", "v", "kind", "id", "op", "args"})
    if o["kind"] != "request":
        raise _bad('kind must be "request"')
    if not (_is_int(o["id"]) and o["id"] >= 0):
        raise _bad("id must be an integer >= 0")
    if not isinstance(o["op"], str):
        raise _bad("op must be a string")
    args, _ = _op(proto, o["op"])
    _check_fields("args", args, o["args"])
    return o


def validate_response(proto: str, op: str, v: Any) -> dict[str, Any] | DeviceError:
    """Validate a response to ``op``: the result object of an ok response, or the carried :class:`DeviceError` of a
    well-formed error response (returned, not raised, so it is never confused with an invalid message)."""
    o = _envelope(proto, v)
    ok = o.get("ok") is True
    _keys(o, {"proto", "v", "kind", "id", "ok", "result" if ok else "error"})
    if o["kind"] != "response":
        raise _bad('kind must be "response"')
    if not (_is_int(o["id"]) and o["id"] >= 0):
        raise _bad("id must be an integer >= 0")
    if not isinstance(o["ok"], bool):
        raise _bad("ok must be a boolean")
    _, result = _op(proto, op)
    if ok:
        _check_fields("result", result, o["result"])
        return o["result"]
    _check_fields("error", {"code": "str", "class": "str", "message": "str"}, o["error"])
    e = o["error"]
    if e["code"] not in ERROR_CODES:
        raise _bad("unknown error code")
    if e["class"] != ERROR_CODES[e["code"]][0]:
        raise _bad("error class does not match its code")
    return DeviceError(e["code"], e["message"])


def request(proto: str, msg_id: int, op: str, args: dict[str, Any]) -> dict[str, Any]:
    v = {"proto": proto, "v": VERSION, "kind": "request", "id": msg_id, "op": op, "args": args}
    return validate_request(proto, v)


def ok_response(proto: str, msg_id: int, op: str, result: dict[str, Any]) -> dict[str, Any]:
    v = {"proto": proto, "v": VERSION, "kind": "response", "id": msg_id, "ok": True, "result": result}
    validate_response(proto, op, v)     # ok is True: a valid message returns its result
    return v


def error_response(proto: str, msg_id: int, error: DeviceError) -> dict[str, Any]:
    # a str field holds 1..1024 UTF-8 bytes: cut on a character boundary; an empty message is replaced by the code
    message = error.device_message.encode("utf-8", "replace")[:1024].decode("utf-8", "ignore") or error.device_code
    return {"proto": proto, "v": VERSION, "kind": "response", "id": msg_id, "ok": False,
            "error": {"code": error.device_code, "class": error.error_class, "message": message}}
