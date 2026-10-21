"""Persisted record format ``vnx.record/1`` (docs/V14_CONTRACTS.md §3); mirror of ``vnxd/src/record.rs``.

Frame (little-endian): magic ``VNXR`` | u16 format version 1 | u16 kind | u32 payload length | payload |
u32 CRC-32 (zlib) of everything before it. Payloads have a fixed size per kind, except ``map_batch`` (16 bytes +
64 per entry, 2..1024 entries). Decoding is strict. ``scan`` reads a plain concatenation of records up to the first
that does not decode; the volume journal stores every record twice (docs/V14_VOLUME_AND_IO.md §2).
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Any, Union

from .contracts import DeviceError, Geometry

MAGIC = b"VNXR"
FORMAT_VERSION = 1
HEADER = struct.Struct("<4sHHI")
KIND_SUPERBLOCK, KIND_MAP_UPDATE, KIND_GENERATION_RELEASED, KIND_MAP_BATCH = 1, 2, 3, 4
MAX_BATCH_EXTENTS = 1024
FLAG_SEALED = 1
#: Superblock flag: the volume is WORM (write-once-read-many).
FLAG_WORM = 2
_KNOWN_FLAGS = FLAG_SEALED | FLAG_WORM
_SUPERBLOCK = struct.Struct("<16sIIQI")
_MAP_UPDATE = struct.Struct("<QQQQQ32s")
_RELEASED = struct.Struct("<QQQ")
_BATCH_HEAD = struct.Struct("<QII")
_BATCH_ENTRY = struct.Struct("<QQQQ32s")
_PAYLOAD = {KIND_SUPERBLOCK: _SUPERBLOCK, KIND_MAP_UPDATE: _MAP_UPDATE, KIND_GENERATION_RELEASED: _RELEASED}
U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class Superblock:
    device_id: bytes
    geometry: Geometry
    flags: int


@dataclass(frozen=True)
class MapUpdate:
    """From transaction ``txid`` on, ``extent`` is served by ``generation`` (``previous`` 0 = none)."""

    txid: int
    extent: int
    generation: int
    previous: int
    length: int
    sha256: bytes


@dataclass(frozen=True)
class GenerationReleased:
    txid: int
    extent: int
    generation: int


@dataclass(frozen=True)
class BatchEntry:
    extent: int
    generation: int
    previous: int
    length: int
    sha256: bytes


@dataclass(frozen=True)
class MapBatch:
    """Transaction ``txid`` remaps every extent of ``entries`` at once (2..MAX_BATCH_EXTENTS, extents increasing)."""

    txid: int
    entries: tuple[BatchEntry, ...]


Record = Union[Superblock, MapUpdate, GenerationReleased, MapBatch]


def _corrupt(msg: str) -> DeviceError:
    return DeviceError("CORRUPTION", msg)


def _kind(rec: Record) -> int:
    return {Superblock: KIND_SUPERBLOCK, MapUpdate: KIND_MAP_UPDATE, GenerationReleased: KIND_GENERATION_RELEASED,
            MapBatch: KIND_MAP_BATCH}[type(rec)]


def validate(rec: Record) -> None:
    """The invariants the decoder enforces (so nothing invalid is ever written)."""
    if isinstance(rec, Superblock):
        if len(rec.device_id) != 16:
            raise DeviceError("INVALID_ARGUMENT", "device_id must be 16 bytes")
        if rec.flags & ~_KNOWN_FLAGS:
            raise _corrupt(f"unknown superblock flags {rec.flags:#x}")
        return
    if isinstance(rec, MapBatch):
        if not 0 <= rec.txid <= U64_MAX:
            raise DeviceError("OVERFLOW", "record field outside 0..2^64-1")
        if rec.txid == 0:
            raise _corrupt("map batch with txid 0")
        if not 2 <= len(rec.entries) <= MAX_BATCH_EXTENTS:
            raise _corrupt(f"map batch of {len(rec.entries)} extents (2..={MAX_BATCH_EXTENTS})")
        for i, e in enumerate(rec.entries):
            _validate_entry(e.extent, e.generation, e.previous, e.length, e.sha256, "map batch")
            if i and rec.entries[i - 1].extent >= e.extent:
                raise _corrupt("map batch: extents not strictly increasing")
        return
    ints = (rec.txid, rec.extent, rec.generation) + ((rec.previous, rec.length) if isinstance(rec, MapUpdate) else ())
    if any(not 0 <= v <= U64_MAX for v in ints):
        raise DeviceError("OVERFLOW", "record field outside 0..2^64-1")
    if rec.generation == 0:
        raise _corrupt("generation 0")
    if rec.txid == 0:
        raise _corrupt(f"{type(rec).__name__} with txid 0")
    if isinstance(rec, MapUpdate):
        _validate_entry(rec.extent, rec.generation, rec.previous, rec.length, rec.sha256, "map update")


def _validate_entry(extent: int, generation: int, previous: int, length: int, sha256: bytes, what: str) -> None:
    if any(not 0 <= v <= U64_MAX for v in (extent, generation, previous, length)):
        raise DeviceError("OVERFLOW", "record field outside 0..2^64-1")
    if generation == 0:
        raise _corrupt("generation 0")
    if len(sha256) != 32:
        raise DeviceError("INVALID_ARGUMENT", "sha256 must be 32 bytes")
    if previous and previous >= generation:
        raise _corrupt(f"{what}: previous generation not older than the new one")


def encode(rec: Record) -> bytes:
    validate(rec)
    if isinstance(rec, Superblock):
        g = rec.geometry
        payload = _SUPERBLOCK.pack(rec.device_id, g.block_size, g.blocks_per_extent, g.capacity_blocks, rec.flags)
    elif isinstance(rec, MapUpdate):
        payload = _MAP_UPDATE.pack(rec.txid, rec.extent, rec.generation, rec.previous, rec.length, rec.sha256)
    elif isinstance(rec, MapBatch):
        payload = _BATCH_HEAD.pack(rec.txid, len(rec.entries), 0) + b"".join(
            _BATCH_ENTRY.pack(e.extent, e.generation, e.previous, e.length, e.sha256) for e in rec.entries)
    else:
        payload = _RELEASED.pack(rec.txid, rec.extent, rec.generation)
    body = HEADER.pack(MAGIC, FORMAT_VERSION, _kind(rec), len(payload)) + payload
    return body + struct.pack("<I", zlib.crc32(body))


def decode(buf: bytes, offset: int = 0) -> tuple[Record, int]:
    """Decode one record at ``offset``; returns it with the number of bytes it occupies."""
    view = memoryview(buf)[offset:]
    if len(view) < HEADER.size:
        raise _corrupt(f"truncated header ({len(view)} bytes)")
    magic, version, kind, n = HEADER.unpack_from(view)
    if magic != MAGIC:
        raise _corrupt("bad record magic")
    if version != FORMAT_VERSION:
        raise DeviceError("VERSION_MISMATCH", f"record format version {version} (supported: 1)")
    if kind == KIND_MAP_BATCH:
        if (n < _BATCH_HEAD.size + 2 * _BATCH_ENTRY.size or n > _BATCH_HEAD.size + MAX_BATCH_EXTENTS * _BATCH_ENTRY.size
                or (n - _BATCH_HEAD.size) % _BATCH_ENTRY.size):
            raise _corrupt(f"map batch payload length {n}")
    elif kind not in _PAYLOAD:
        raise _corrupt(f"unknown record kind {kind}")
    elif n != _PAYLOAD[kind].size:
        raise _corrupt(f"record kind {kind}: payload length {n}, expected {_PAYLOAD[kind].size}")
    total = HEADER.size + n + 4
    if len(view) < total:
        raise _corrupt(f"truncated record ({len(view)} of {total} bytes)")
    (stored,) = struct.unpack_from("<I", view, total - 4)
    if zlib.crc32(view[:HEADER.size + n]) != stored:
        raise _corrupt("record CRC-32 mismatch")
    rec: Record
    if kind == KIND_MAP_BATCH:
        txid, count, reserved = _BATCH_HEAD.unpack_from(view, HEADER.size)
        if reserved:
            raise _corrupt("map batch: reserved field set")
        if count != (n - _BATCH_HEAD.size) // _BATCH_ENTRY.size:
            raise _corrupt(f"map batch: count {count} does not match its payload length {n}")
        base = HEADER.size + _BATCH_HEAD.size
        rec = MapBatch(txid, tuple(BatchEntry(*_BATCH_ENTRY.unpack_from(view, base + i * _BATCH_ENTRY.size))
                                   for i in range(count)))
        validate(rec)
        return rec, total
    fields = _PAYLOAD[kind].unpack_from(view, HEADER.size)
    if kind == KIND_SUPERBLOCK:
        device_id, bs, bpe, cap, flags = fields
        try:
            geometry = Geometry(bs, bpe, cap)
        except DeviceError as e:
            raise _corrupt(f"superblock geometry: {e.device_message}") from None
        rec = Superblock(device_id, geometry, flags)
    elif kind == KIND_MAP_UPDATE:
        rec = MapUpdate(*fields)
    else:
        rec = GenerationReleased(*fields)
    validate(rec)
    return rec, total


@dataclass(frozen=True)
class Scan:
    records: list[Record]
    valid_len: int                       # truncation point for the next append
    tail_error: DeviceError | None       # why reading stopped before the end, if it did


def scan(buf: bytes) -> Scan:
    records: list[Record] = []
    pos = 0
    while pos < len(buf):
        try:
            rec, used = decode(buf, pos)
        except DeviceError as e:
            return Scan(records, pos, e)
        records.append(rec)
        pos += used
    return Scan(records, pos, None)


I64_MAX = (1 << 63) - 1


def to_json(rec: Record) -> dict[str, Any]:
    """JSON description (inspection output, golden files); same keys as ``vnxd inspect-journal``.

    Protocol JSON integers are signed 64-bit: a field above 2^63 - 1 cannot be described and is an OVERFLOW error.
    """
    out = _describe(rec)
    for item in [out] + list(out.get("entries", [])):
        for v in item.values():
            if type(v) is int and v > I64_MAX:
                raise DeviceError("OVERFLOW", f"{v} exceeds the JSON integer range")
    return out


def _describe(rec: Record) -> dict[str, Any]:
    if isinstance(rec, Superblock):
        g = rec.geometry
        return {"kind": "superblock", "device_id": rec.device_id.hex(), "block_size": g.block_size,
                "blocks_per_extent": g.blocks_per_extent, "capacity_blocks": g.capacity_blocks, "flags": rec.flags}
    if isinstance(rec, MapUpdate):
        return {"kind": "map_update", "txid": rec.txid, "extent": rec.extent, "generation": rec.generation,
                "previous": rec.previous, "length": rec.length, "sha256": rec.sha256.hex()}
    if isinstance(rec, MapBatch):
        return {"kind": "map_batch", "txid": rec.txid,
                "entries": [{"extent": e.extent, "generation": e.generation, "previous": e.previous,
                             "length": e.length, "sha256": e.sha256.hex()} for e in rec.entries]}
    return {"kind": "generation_released", "txid": rec.txid, "extent": rec.extent, "generation": rec.generation}


_ENTRY_KEYS = {"extent", "generation", "previous", "length", "sha256"}
_KEYS = {
    "superblock": {"kind", "device_id", "block_size", "blocks_per_extent", "capacity_blocks", "flags"},
    "map_update": {"kind", "txid", "extent", "generation", "previous", "length", "sha256"},
    "generation_released": {"kind", "txid", "extent", "generation"},
    "map_batch": {"kind", "txid", "entries"},
}


def from_json(d: dict[str, Any]) -> Record:
    kind = d.get("kind") if isinstance(d, dict) else None
    if kind not in _KEYS:
        raise DeviceError("INVALID_ARGUMENT", f"unknown record kind {kind!r}")
    if set(d) != _KEYS[kind]:
        raise DeviceError("INVALID_ARGUMENT", f"{kind}: fields must be exactly {sorted(_KEYS[kind])}")

    def u(k: str) -> int:
        v = d[k]
        if type(v) is not int or v < 0:
            raise DeviceError("INVALID_ARGUMENT", f"{kind}.{k} must be an integer >= 0")
        return v

    def u32(k: str) -> int:
        v = u(k)
        if v > 0xFFFF_FFFF:
            raise DeviceError("INVALID_ARGUMENT", f"{kind}.{k} exceeds 32 bits")
        return v

    def gen(k: str) -> int:
        v = u(k)
        if v == 0:
            raise DeviceError("INVALID_ARGUMENT", "generation 0 is reserved for 'none'")
        return v

    def h(k: str, n: int) -> bytes:
        v = d[k]
        if not (isinstance(v, str) and len(v) == 2 * n and all(c in "0123456789abcdef" for c in v)):
            raise DeviceError("INVALID_ARGUMENT", f"{kind}.{k} must be {2 * n} lowercase hex digits")
        return bytes.fromhex(v)

    rec: Record
    if kind == "superblock":
        rec = Superblock(h("device_id", 16), Geometry(u32("block_size"), u32("blocks_per_extent"),
                                                       u("capacity_blocks")), u32("flags"))
    elif kind == "map_update":
        rec = MapUpdate(u("txid"), u("extent"), gen("generation"), u("previous"), u("length"), h("sha256", 32))
    elif kind == "map_batch":
        items = d["entries"]
        if not isinstance(items, list):
            raise DeviceError("INVALID_ARGUMENT", "map_batch.entries must be an array")
        entries = []
        for it in items:
            if not isinstance(it, dict) or set(it) != _ENTRY_KEYS:
                raise DeviceError("INVALID_ARGUMENT", f"map_batch entry fields must be exactly {sorted(_ENTRY_KEYS)}")
            m = from_json({"kind": "map_update", "txid": 1, **it})
            assert isinstance(m, MapUpdate)
            entries.append(BatchEntry(m.extent, m.generation, m.previous, m.length, m.sha256))
        rec = MapBatch(u("txid"), tuple(entries))
        try:
            validate(rec)
        except DeviceError as e:
            raise DeviceError("INVALID_ARGUMENT", e.device_message) from None
    else:
        rec = GenerationReleased(u("txid"), u("extent"), gen("generation"))
    return rec
