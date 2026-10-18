"""Generate the cross-language golden files of the V14 storage contracts (docs/V14_CONTRACTS.md §8).

    PYTHONPATH=src python tests/v14/golden/contracts/generate.py

Writes ``records.json`` (valid records: description + bytes), ``bad_records.json`` (byte strings and the error code
decoding must report) and ``messages.json`` (protocol messages: input text + canonical form, or the error code). The
outputs are committed; the Rust crate (``vnxd/tests/golden.rs``) and the Python tests (``tests/v14``) must reproduce
every entry. Regenerating changes the contract: only with a format-version change.
"""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

from vnxdna.device import protocol as pr
from vnxdna.device import records as rc
from vnxdna.device.contracts import DeviceError

HERE = Path(__file__).parent

RECORDS = [
    ("superblock_sealed", {"kind": "superblock", "device_id": "00112233445566778899aabbccddeeff", "block_size": 4096,
                           "blocks_per_extent": 256, "capacity_blocks": 262144, "flags": 1}),
    ("superblock_short_last_extent", {"kind": "superblock", "device_id": "ffffffffffffffffffffffffffffffff",
                                      "block_size": 512, "blocks_per_extent": 3, "capacity_blocks": 10, "flags": 0}),
    ("map_update_first", {"kind": "map_update", "txid": 1, "extent": 0, "generation": 1, "previous": 0,
                          "length": 1048576, "sha256": hashlib.sha256(b"extent 0 generation 1").hexdigest()}),
    ("map_update_remap", {"kind": "map_update", "txid": 9223372036854775807, "extent": 4095, "generation": 7,
                          "previous": 6, "length": 5, "sha256": hashlib.sha256(b"hello").hexdigest()}),
    ("generation_released", {"kind": "generation_released", "txid": 2, "extent": 4095, "generation": 6}),
    # appended in P4 (atomic multi-extent writes); the entries above are unchanged byte for byte
    ("map_batch", {"kind": "map_batch", "txid": 3, "entries": [
        {"extent": 4, "generation": 2, "previous": 1, "length": 1048576,
         "sha256": hashlib.sha256(b"extent 4 generation 2").hexdigest()},
        {"extent": 5, "generation": 1, "previous": 0, "length": 1048576,
         "sha256": hashlib.sha256(b"extent 5 generation 1").hexdigest()},
        {"extent": 4095, "generation": 9, "previous": 8, "length": 5, "sha256": hashlib.sha256(b"hello").hexdigest()},
    ]}),
]


def _with_crc(body: bytes) -> bytes:
    return body + struct.pack("<I", zlib.crc32(body))


def bad_records(good: bytes) -> list[tuple[str, bytes]]:
    """Malformed byte strings derived from one valid map-update record."""
    body = good[:-4]
    flip = bytearray(good)
    flip[20] ^= 0x01
    return [
        ("empty", b""),
        ("truncated_header", good[:11]),
        ("truncated_payload", good[:40]),
        ("missing_crc_byte", good[:-1]),
        ("bad_magic", _with_crc(b"VNXQ" + body[4:])),
        ("future_version", _with_crc(body[:4] + struct.pack("<H", 2) + body[6:])),
        ("unknown_kind", _with_crc(body[:6] + struct.pack("<H", 9) + body[8:])),
        ("wrong_length", _with_crc(body[:8] + struct.pack("<I", len(body) - 13) + body[12:-1])),
        ("payload_bit_flip", bytes(flip)),
        ("crc_bit_flip", good[:-1] + bytes([good[-1] ^ 0x80])),
        ("generation_zero", _with_crc(body[:12 + 16] + struct.pack("<Q", 0) + body[12 + 24:])),
        ("previous_not_older", _with_crc(body[:12 + 24] + struct.pack("<Q", 1) + body[12 + 32:])),
        ("txid_zero", _with_crc(body[:12] + struct.pack("<Q", 0) + body[12 + 8:])),
        ("superblock_bad_block_size", rc.encode(rc.from_json(RECORDS[0][1]))[:-4].replace(
            struct.pack("<I", 4096), struct.pack("<I", 4000), 1)),
        ("superblock_unknown_flag", None),  # filled below
        # map_batch (P4)
        ("batch_count_mismatch", None),
        ("batch_reserved_set", None),
        ("batch_extents_unordered", None),
        ("batch_single_entry", None),
        ("batch_partial_entry", None),
    ]


def bad_batches(good: bytes) -> dict[str, bytes]:
    """Malformed byte strings derived from one valid map-batch record (3 entries)."""
    body = good[:-4]
    head = 12
    entry = 64

    def length(b: bytes, n: int) -> bytes:
        return b[:8] + struct.pack("<I", n) + b[12:]

    first, second = body[head + 16:head + 16 + entry], body[head + 16 + entry:head + 16 + 2 * entry]
    return {
        "batch_count_mismatch": _with_crc(body[:head + 8] + struct.pack("<I", 2) + body[head + 12:]),
        "batch_reserved_set": _with_crc(body[:head + 12] + struct.pack("<I", 1) + body[head + 16:]),
        "batch_extents_unordered": _with_crc(body[:head + 16] + second + first + body[head + 16 + 2 * entry:]),
        "batch_single_entry": _with_crc(length(body[:head + 8] + struct.pack("<I", 1) + body[head + 12:head + 16 + entry],
                                               16 + entry)),
        "batch_partial_entry": _with_crc(length(body[:-8], len(body) - 8 - head)),
    }


MESSAGES: list[tuple[str, str, str, str, str]] = [
    # (name, proto, direction, op, text)
    ("mgmt_hello", "vnx.mgmt", "request", "hello",
     '{"proto": "vnx.mgmt", "v": 1, "kind": "request", "id": 0, "op": "hello",'
     ' "args": {"client": "vnx-cli 14 \\u00e9\\ud83d\\ude00 \\"q\\" \\\\ /", "versions": [1]}}'),
    ("mgmt_capacity_ok", "vnx.mgmt", "response", "capacity",
     '{"v":1,"proto":"vnx.mgmt","kind":"response","id":5,"ok":true,"result":{"block_size":4096,'
     '"blocks_per_extent":256,"capacity_blocks":262144,"capacity_bytes":1073741824,"extent_count":1024}}'),
    ("mgmt_health_ok", "vnx.mgmt", "response", "health",
     '{"proto":"vnx.mgmt","v":1,"kind":"response","id":6,"ok":true,'
     '"result":{"state":"degraded","detail":{"backend":"unavailable","since_ms":1200}}}'),
    ("mgmt_stats_request", "vnx.mgmt", "request", "stats",
     '{"proto":"vnx.mgmt","v":1,"kind":"request","id":7,"op":"stats","args":{}}'),
    ("mgmt_error_response", "vnx.mgmt", "response", "shutdown",
     '{"proto":"vnx.mgmt","v":1,"kind":"response","id":8,"ok":false,'
     '"error":{"code":"BACKEND_BUSY","class":"retryable","message":"flush in progress\\n"}}'),
    ("codec_encode", "vnx.codec-ipc", "request", "encode_extent",
     '{"proto":"vnx.codec-ipc","v":1,"kind":"request","id":11,"op":"encode_extent","args":{"job":11,"extent":3,'
     '"generation":2,"input":"e3-g2.bin","input_sha256":"' + "ab" * 32 + '","output":"e3-g2.fasta",'
     '"options":{"profile":"default","workers":2}}}'),
    ("codec_decode_ok", "vnx.codec-ipc", "response", "decode_extent",
     '{"proto":"vnx.codec-ipc","v":1,"kind":"response","id":12,"ok":true,'
     '"result":{"job":12,"output_sha256":"' + "0f" * 32 + '","output_bytes":1048576}}'),
    ("mgmt_del_escaped", "vnx.mgmt", "request", "hello",
     '{"proto":"vnx.mgmt","v":1,"kind":"request","id":2,"op":"hello","args":{"client":"a\\u007fb\\u001f~","versions":[1,2]}}'),
    # depth: response 1, result 2, counters 3, then 29 nested arrays reach the limit of 32 exactly
    ("mgmt_depth_limit", "vnx.mgmt", "response", "stats",
     '{"proto":"vnx.mgmt","v":1,"kind":"response","id":3,"ok":true,"result":{"counters":{"a":'
     + "[" * 29 + "]" * 29 + '}}}'),
    # invalid messages: (expected error code is computed and must match in both languages)
    ("bad_depth_over_limit", "vnx.mgmt", "response", "stats",
     '{"proto":"vnx.mgmt","v":1,"kind":"response","id":3,"ok":true,"result":{"counters":{"a":'
     + "[" * 30 + "]" * 30 + '}}}'),
    ("bad_json", "vnx.mgmt", "request", "hello", '{"proto":"vnx.mgmt",'),
    ("bad_float", "vnx.mgmt", "request", "stats",
     '{"proto":"vnx.mgmt","v":1,"kind":"request","id":1.0,"op":"stats","args":{}}'),
    ("bad_duplicate_key", "vnx.mgmt", "request", "stats",
     '{"proto":"vnx.mgmt","v":1,"kind":"request","id":1,"id":2,"op":"stats","args":{}}'),
    ("bad_lone_surrogate", "vnx.mgmt", "request", "hello",
     '{"proto":"vnx.mgmt","v":1,"kind":"request","id":1,"op":"hello","args":{"client":"\\ud800","versions":[1]}}'),
    ("bad_not_object", "vnx.mgmt", "request", "stats", '[1,2]'),
    ("bad_wrong_proto", "vnx.mgmt", "request", "stats",
     '{"proto":"vnx.codec-ipc","v":1,"kind":"request","id":1,"op":"stats","args":{}}'),
    ("bad_future_version", "vnx.mgmt", "request", "stats",
     '{"proto":"vnx.mgmt","v":2,"kind":"request","id":1,"op":"stats"}'),
    ("bad_extra_envelope_key", "vnx.mgmt", "request", "stats",
     '{"proto":"vnx.mgmt","v":1,"kind":"request","id":1,"op":"stats","args":{},"x":0}'),
    ("bad_unknown_op", "vnx.mgmt", "request", "stats",
     '{"proto":"vnx.mgmt","v":1,"kind":"request","id":1,"op":"format","args":{}}'),
    ("bad_path_name", "vnx.codec-ipc", "request", "encode_extent",
     '{"proto":"vnx.codec-ipc","v":1,"kind":"request","id":1,"op":"encode_extent","args":{"job":1,"extent":0,'
     '"generation":1,"input":"../etc/passwd","input_sha256":"' + "ab" * 32 + '","output":"o","options":{}}}'),
    ("bad_generation_zero", "vnx.codec-ipc", "request", "encode_extent",
     '{"proto":"vnx.codec-ipc","v":1,"kind":"request","id":1,"op":"encode_extent","args":{"job":1,"extent":0,'
     '"generation":0,"input":"i","input_sha256":"' + "ab" * 32 + '","output":"o","options":{}}}'),
    ("bad_sha_uppercase", "vnx.codec-ipc", "response", "decode_extent",
     '{"proto":"vnx.codec-ipc","v":1,"kind":"response","id":1,"ok":true,'
     '"result":{"job":1,"output_sha256":"' + "AB" * 32 + '","output_bytes":1}}'),
    ("bad_error_class", "vnx.mgmt", "response", "stats",
     '{"proto":"vnx.mgmt","v":1,"kind":"response","id":1,"ok":false,'
     '"error":{"code":"TIMEOUT","class":"permanent","message":"x"}}'),
    ("bad_health_state", "vnx.mgmt", "response", "health",
     '{"proto":"vnx.mgmt","v":1,"kind":"response","id":1,"ok":true,"result":{"state":"fine","detail":{}}}'),
    ("bad_negative_zero", "vnx.mgmt", "request", "stats",
     '{"proto":"vnx.mgmt","v":1,"kind":"request","id":-0,"op":"stats","args":{}}'),
    ("bad_int_range", "vnx.mgmt", "request", "stats",
     '{"proto":"vnx.mgmt","v":1,"kind":"request","id":9223372036854775808,"op":"stats","args":{}}'),
]


def check_message(proto: str, direction: str, op: str, text: str) -> dict[str, str]:
    """{"canonical": …} for a valid message (an error response is valid), else {"error": code}."""
    try:
        v = pr.parse(text)
        if direction == "request":
            pr.validate_request(proto, v)
        else:
            pr.validate_response(proto, op, v)   # a well-formed error response is returned, not raised
        return {"canonical": pr.canonical(v)}
    except DeviceError as e:
        return {"error": e.device_code}


def main() -> None:
    recs = []
    for name, desc in RECORDS:
        raw = rc.encode(rc.from_json(desc))
        assert rc.to_json(rc.decode(raw)[0]) == desc
        recs.append({"name": name, "record": desc, "hex": raw.hex()})
    good = bytes.fromhex(recs[2]["hex"])
    sb = rc.encode(rc.from_json(RECORDS[0][1]))[:-4]
    bad = []
    for name, raw in bad_records(good):
        if name == "superblock_bad_block_size":
            raw = _with_crc(raw)
        if name == "superblock_unknown_flag":
            raw = _with_crc(sb[:-4] + struct.pack("<I", 3))
        if name.startswith("batch_"):
            raw = bad_batches(bytes.fromhex(recs[5]["hex"]))[name]
        try:
            rc.decode(raw)
        except DeviceError as e:
            bad.append({"name": name, "hex": raw.hex(), "error": e.device_code})
            continue
        raise AssertionError(f"{name} decoded")
    msgs = [{"name": n, "proto": p, "direction": d, "op": o, "text": t, **check_message(p, d, o, t)}
            for n, p, d, o, t in MESSAGES]
    for m in msgs:
        assert ("canonical" in m) == (not m["name"].startswith("bad_")), m
    for fname, data in (("records.json", recs), ("bad_records.json", bad), ("messages.json", msgs)):
        (HERE / fname).write_text(json.dumps(data, indent=1, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    main()
