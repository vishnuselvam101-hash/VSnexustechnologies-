"""V14 Phase 1: the storage contracts of ``vnxdna.device`` and their agreement with the Rust ``vnxd`` crate.

The golden files (``tests/v14/golden/contracts``) are reproduced here by the Python implementation and in
``src/vnxdna/native/rust/vnxd/tests/golden.rs`` by the Rust one; the error-code table is compared with the Rust
source directly, so the two languages cannot drift apart without a failing test.
"""
from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path

import pytest

from vnxdna.core.errors import CODES
from vnxdna.device import ERROR_CODES, DeviceError, ExtentSpan, Geometry, WriteState
from vnxdna.device import protocol as pr
from vnxdna.device import records as rc

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "golden" / "contracts"
RUST = ROOT / "src" / "vnxdna" / "native" / "rust" / "vnxd" / "src"


def _load(name):
    return json.loads((GOLDEN / name).read_text())


# ---- golden files (shared with the Rust tests) ------------------------------------------------------------------

@pytest.mark.parametrize("entry", _load("records.json"), ids=lambda e: e["name"])
def test_golden_records(entry):
    raw = rc.encode(rc.from_json(entry["record"]))
    assert raw.hex() == entry["hex"]
    rec, used = rc.decode(raw)
    assert used == len(raw) and rc.to_json(rec) == entry["record"]


@pytest.mark.parametrize("entry", _load("bad_records.json"), ids=lambda e: e["name"])
def test_golden_bad_records(entry):
    with pytest.raises(DeviceError) as e:
        rc.decode(bytes.fromhex(entry["hex"]))
    assert e.value.device_code == entry["error"]


@pytest.mark.parametrize("entry", _load("messages.json"), ids=lambda e: e["name"])
def test_golden_messages(entry):
    def run():
        v = pr.parse(entry["text"])
        if entry["direction"] == "request":
            pr.validate_request(entry["proto"], v)
        else:
            pr.validate_response(entry["proto"], entry["op"], v)   # an error response is returned, not raised
        return pr.canonical(v)

    if "canonical" in entry:
        assert run() == entry["canonical"]
    else:
        with pytest.raises(DeviceError) as e:
            run()
        assert e.value.device_code == entry["error"]


def test_golden_files_cover_every_record_kind_and_both_protocols():
    kinds = {e["record"]["kind"] for e in _load("records.json")}
    assert kinds == {"superblock", "map_update", "generation_released"}
    msgs = _load("messages.json")
    assert {m["proto"] for m in msgs if "canonical" in m} == set(pr.PROTOCOLS)
    assert {"VERSION_MISMATCH", "PROTOCOL"} <= {m["error"] for m in msgs if "error" in m}


# ---- cross-language tables --------------------------------------------------------------------------------------

def test_error_table_matches_the_rust_source():
    src = (RUST / "error.rs").read_text()
    rust = {name: (cls.lower(), errno) for name, cls, errno in
            re.findall(r'^\s+\w+ => "([A-Z_]+)", (Retryable|Permanent), (E[A-Z]+);', src, re.M)}
    errnos = dict(re.findall(r"pub const (E[A-Z]+): u32 = (\d+);", src))
    assert len(rust) == 17
    assert {k: (c, int(errnos[e])) for k, (c, e) in rust.items()} == {k: v[:2] for k, v in ERROR_CODES.items()}


def test_error_codes_map_onto_the_library_registry():
    for device_code, (cls, _, code) in ERROR_CODES.items():
        assert code in CODES
        e = DeviceError(device_code, "m")
        assert e.retryable == (cls == "retryable") and e.code == code and e.category == CODES[code][0]
        d = e.to_dict()
        assert d["code"] == code and d["details"]["device_code"] == device_code and d["stage"] == "device"
    with pytest.raises(ValueError):
        DeviceError("NOPE", "m")


def test_protocol_ops_match_the_rust_source():
    src = (RUST / "proto.rs").read_text()
    for proto, rust_name in (("vnx.mgmt", "Mgmt"), ("vnx.codec-ipc", "Codec")):
        block = src.split(f"Proto::{rust_name} => &[", 1)[1].split("\n        ],", 1)[0]
        ops = re.findall(r'\(\s*"([a-z_]+)",\s*(?:HELLO_ARGS|&\[)', block)   # op tuples, not field tuples
        assert ops == list(pr.OPS[proto]), proto


# ---- records -----------------------------------------------------------------------------------------------------

def _samples():
    return [rc.from_json(e["record"]) for e in _load("records.json")]


def test_every_single_bit_flip_and_truncation_is_rejected():
    for rec in _samples():
        raw = rc.encode(rec)
        for i in range(len(raw)):
            for bit in range(8):
                b = bytearray(raw)
                b[i] ^= 1 << bit
                with pytest.raises(DeviceError):
                    rc.decode(bytes(b))
            with pytest.raises(DeviceError) as e:
                rc.decode(raw[:i])
            assert e.value.device_code == "CORRUPTION"


def test_scan_stops_at_a_torn_tail():
    raws = [rc.encode(r) for r in _samples()]
    journal = b"".join(raws)
    full = rc.scan(journal)
    assert len(full.records) == len(raws) and full.valid_len == len(journal) and full.tail_error is None
    last = len(journal) - len(raws[-1])
    for cut in range(last, len(journal)):
        s = rc.scan(journal[:cut])
        assert (len(s.records), s.valid_len, s.tail_error is not None) == (len(raws) - 1, last, cut > last)
    # garbage after a valid prefix: the prefix survives, the garbage is the truncation point
    s = rc.scan(journal + b"\x00" * 7)
    assert s.valid_len == len(journal) and s.tail_error.device_code == "CORRUPTION"


def test_invalid_records_are_never_encoded():
    with pytest.raises(DeviceError) as e:
        rc.encode(rc.MapUpdate(1, 0, 2, 2, 0, bytes(32)))
    assert e.value.device_code == "CORRUPTION"
    with pytest.raises(DeviceError):
        rc.encode(rc.MapUpdate(0, 0, 1, 0, 0, bytes(32)))
    with pytest.raises(DeviceError) as e:
        rc.encode(rc.MapUpdate(1, 1 << 64, 1, 0, 0, bytes(32)))
    assert e.value.device_code == "OVERFLOW"
    with pytest.raises(DeviceError):
        rc.encode(rc.Superblock(bytes(16), Geometry(4096, 1, 1), 2))


def test_record_descriptions_are_validated_like_rust():
    base = {"kind": "map_update", "txid": 1, "extent": 0, "generation": 1, "previous": 0, "length": 0, "sha256": "00" * 32}
    for change in ({"generation": 0}, {"txid": -1}, {"sha256": "AB" * 32}, {"extra": 1}):
        with pytest.raises(DeviceError) as e:
            rc.from_json({**base, **change})
        assert e.value.device_code == "INVALID_ARGUMENT", change
    sb = _load("records.json")[0]["record"]
    for k in ("flags", "block_size", "blocks_per_extent"):
        with pytest.raises(DeviceError) as e:
            rc.from_json({**sb, k: 1 << 32})
        assert e.value.device_code == "INVALID_ARGUMENT", k
    # a legal u64 above 2^63 - 1 decodes, but has no JSON description
    big = rc.MapUpdate(1 << 63, 0, 1, 0, 0, bytes(32))
    assert rc.decode(rc.encode(big))[0] == big
    with pytest.raises(DeviceError) as e:
        rc.to_json(big)
    assert e.value.device_code == "OVERFLOW"


def test_frame_layout_is_the_documented_one():
    raw = rc.encode(_samples()[4])
    assert raw[:4] == b"VNXR" and struct.unpack_from("<HHI", raw, 4) == (1, 3, 24)
    assert struct.unpack_from("<I", raw, len(raw) - 4)[0] == zlib.crc32(raw[:-4]) and len(raw) == 12 + 24 + 4


# ---- write states, geometry --------------------------------------------------------------------------------------

def test_write_state_transition_table_is_exact():
    legal = {("RECEIVED", "STAGED"), ("STAGED", "BACKEND_COMMITTED"), ("BACKEND_COMMITTED", "VERIFIED"),
             ("RECEIVED", "FAILED"), ("STAGED", "FAILED"), ("BACKEND_COMMITTED", "FAILED")}
    for a in WriteState:
        for b in WriteState:
            if (a.value, b.value) in legal:
                assert a.advance(b) is b
            else:
                with pytest.raises(DeviceError):
                    a.advance(b)
    assert [s.value for s in WriteState if s.is_durable] == ["BACKEND_COMMITTED", "VERIFIED"]


@pytest.mark.parametrize("args,code", [((1000, 4, 10), "INVALID_ARGUMENT"), ((256, 4, 10), "INVALID_ARGUMENT"),
                                       ((2 << 20, 1, 10), "INVALID_ARGUMENT"), ((4096, 0, 10), "INVALID_ARGUMENT"),
                                       ((4096, 1 << 15, 10), "INVALID_ARGUMENT"), ((4096, 4, 0), "INVALID_ARGUMENT"),
                                       ((4096, 4, (1 << 64) - 1), "OVERFLOW")])
def test_geometry_validation(args, code):
    with pytest.raises(DeviceError) as e:
        Geometry(*args)
    assert e.value.device_code == code


def test_spans_cover_every_request_exactly():
    g = Geometry(4096, 4, 10)
    assert (g.capacity_bytes, g.extent_bytes, g.extent_count, g.extent_len(2)) == (40960, 16384, 3, 8192)
    assert g.spans(16000, 20000) == [ExtentSpan(0, 16000, 384, 0), ExtentSpan(1, 0, 16384, 384),
                                     ExtentSpan(2, 0, 3232, 16768)]
    for off in range(0, 40960, 509):
        for n in (1, 511, 4096, 16384, 20000):
            if off + n > 40960:
                with pytest.raises(DeviceError) as e:
                    g.spans(off, n)
                assert e.value.device_code == "OUT_OF_RANGE"
                continue
            pos = 0
            for s in g.spans(off, n):
                assert s.buf_offset == pos and s.extent * 16384 + s.offset == off + pos
                assert s.offset + s.length <= g.extent_len(s.extent)
                pos += s.length
            assert pos == n
    with pytest.raises(DeviceError):
        g.spans(0, 0)


# ---- protocol ----------------------------------------------------------------------------------------------------

def test_request_and_response_builders_validate():
    r = pr.request("vnx.mgmt", 1, "hello", {"client": "t", "versions": [1]})
    assert pr.validate_request("vnx.mgmt", pr.parse(pr.canonical(r)))["op"] == "hello"
    ok = pr.ok_response("vnx.mgmt", 1, "hello", {"server": "vnxd", "version": 1})
    assert pr.validate_response("vnx.mgmt", "hello", ok) == {"server": "vnxd", "version": 1}
    err = pr.error_response("vnx.codec-ipc", 2, DeviceError("CORRUPTION", "é" * 600))
    assert len(err["error"]["message"].encode()) <= 1024
    carried = pr.validate_response("vnx.codec-ipc", "decode_extent", pr.parse(pr.canonical(err)))
    assert isinstance(carried, DeviceError) and carried.device_code == "CORRUPTION"
    # an error response carrying PROTOCOL is a valid message, distinct from an invalid one
    proto_err = pr.error_response("vnx.mgmt", 3, DeviceError("PROTOCOL", "unknown op"))
    assert pr.validate_response("vnx.mgmt", "stats", proto_err).device_code == "PROTOCOL"
    for bad in ({"x": 1},):
        with pytest.raises(DeviceError):
            pr.request("vnx.mgmt", 1, "stats", bad)
    # integers the wire cannot carry are refused by the builders too
    with pytest.raises(DeviceError):
        pr.request("vnx.mgmt", 1 << 64, "stats", {})
    with pytest.raises(DeviceError):
        pr.ok_response("vnx.mgmt", 1, "capacity", {"block_size": 1 << 63, "blocks_per_extent": 1, "capacity_blocks": 1,
                                                    "capacity_bytes": 1, "extent_count": 1})
    # strings holding a lone surrogate (e.g. from surrogateescape decoding) are PROTOCOL, never another exception
    for text in ('"\udc80"', '{"a":"\ud800x"}'):
        with pytest.raises(DeviceError) as e:
            pr.parse(text)
        assert e.value.device_code == "PROTOCOL"
    with pytest.raises(DeviceError):
        pr.request("vnx.mgmt", 1, "hello", {"client": "\udc80", "versions": [1]})
    assert pr.error_response("vnx.mgmt", 1, DeviceError("INTERNAL", "\udc80"))["error"]["message"]


@pytest.mark.parametrize("name,ok", [("e7-g2.fasta", True), ("a", True), ("X_1-2.bin", True), ("", False),
                                     (".", False), ("..", False), (".hidden", False), ("a/b", False),
                                     ("../x", False), ("/etc/passwd", False), ("a b", False), ("é", False),
                                     ("a" * 256, False), (7, False)])
def test_staging_names_are_never_paths(name, ok):
    assert pr.is_staging_name(name) is ok


def test_parser_rejects_non_contract_json():
    for bad in ["", "1.5", "1e3", "01", "-0", "-", "[1,]", '{"a":1,}', '{"a":1,"a":2}', "{a:1}", '"\\x"',
                '"\\ud800"', '"\\udc00"', '"a\x01"', "[1] 2", "99999999999999999999", "tru", "nul", "NaN",
                "Infinity"]:
        with pytest.raises(DeviceError) as e:
            pr.parse(bad)
        assert e.value.device_code == "PROTOCOL", bad
    assert pr.parse("[" * 32 + "]" * 32) is not None
    with pytest.raises(DeviceError):
        pr.parse("[" * 33 + "]" * 33)
