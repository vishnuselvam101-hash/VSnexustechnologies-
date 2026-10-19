# V14 storage contracts

Phase 1 specification (master directive §3.4, §5.1, §13; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P1). These are
the interfaces every later V14 module builds on. Two implementations exist and are kept identical by shared golden
files (§8):

* Rust: crate `src/vnxdna/native/rust/vnxd` (library + `vnxd` command), standard library only, no `unsafe` code;
* Python: package `vnxdna.device` (layer 6), modules `contracts`, `records`, `protocol`.

Nothing in this file is a storage device yet: the volume, I/O path, cache, backends and the block device that use
these contracts are built in P2–P8.

## 1. Addresses, extents, generations

| term | definition |
|---|---|
| block size | power of two in [512, 1 MiB] |
| geometry | `block_size`, `blocks_per_extent` (>= 1, extent <= 64 MiB), `capacity_blocks` (>= 1, capacity in bytes < 2^64) |
| extent | `blocks_per_extent` consecutive blocks; extent `e` covers bytes `[e * extent_bytes, …)`; the last extent may be shorter (`extent_len`) |
| generation | one stored, immutable version of one extent; numbered from 1, strictly increasing per extent; 0 means "none" in records |
| generation ref | (extent, generation): the key of the media adapter |
| span | the part of one extent a byte request touches: (extent, offset in extent, length, offset in the request buffer) |

`spans(offset, length)` splits a request in address order; a zero-length request is INVALID_ARGUMENT, a request
past the capacity is OUT_OF_RANGE. All arithmetic is checked (Rust: checked operations plus `overflow-checks = true`
in the release profile); nothing wraps.

## 2. Write states

`RECEIVED -> STAGED -> BACKEND_COMMITTED -> VERIFIED`; `RECEIVED`, `STAGED` and `BACKEND_COMMITTED` may also become
`FAILED`. Every other transition is an INTERNAL error.

| state | meaning | durable? |
|---|---|---|
| RECEIVED | the request is in a service buffer | no |
| STAGED | the extent bytes are in staging memory or the staging directory | no |
| BACKEND_COMMITTED | the backend acknowledged its documented commit point (§7, `Durability`) | yes, within that guarantee |
| VERIFIED | readback/integrity check passed and the mapping record is durable | yes |
| FAILED | the write failed; the previous mapping is unchanged | — |

## 3. Persisted record format `vnx.record/1`

All integers little-endian.

```
offset  size  field
     0     4  magic "VNXR"
     4     2  format version = 1
     6     2  kind
     8     4  payload length n (the kind's size; map_batch: 16 + 64 x entries)
    12     n  payload
  12+n     4  CRC-32 (zlib, reflected 0xEDB88320) of bytes [0, 12+n)
```

| kind | name | payload (size) |
|---|---|---|
| 1 | superblock | device_id 16 B, block_size u32, blocks_per_extent u32, capacity_blocks u64, flags u32 (bit 0 = extents sealed; other bits reserved = 0) (36 B) |
| 2 | map_update | txid u64 (>= 1), extent u64, generation u64 (>= 1), previous u64 (0 = none, else < generation), length u64, sha256 32 B (72 B) |
| 3 | generation_released | txid u64 (>= 1), extent u64, generation u64 (>= 1) (24 B) |
| 4 | map_batch | txid u64 (>= 1), count u32 (2..1024), reserved u32 = 0, then count entries of extent u64, generation u64 (>= 1), previous u64 (0 = none, else < generation), length u64, sha256 32 B; extents strictly increasing (16 + 64 x count B). One transaction remapping every listed extent at once (added in P4, before the contract is frozen at P12; kinds 1–3 unchanged) |

Decoding errors: VERSION_MISMATCH for a format version other than 1; CORRUPTION for everything else (bad magic,
unknown kind, wrong length, truncation, CRC mismatch, reserved flag, invalid geometry, generation 0, txid 0,
previous >= generation, a batch count that disagrees with the length, a reserved batch field, batch extents out of
order). Encoders refuse to write a record the decoder would reject. A plain record file is a concatenation of records;
reading stops at the first record that does not decode (`valid_len`). The volume journal stores every record twice
(mirrored pairs; [V14_VOLUME_AND_IO.md](V14_VOLUME_AND_IO.md) §2). The JSON description of a record (for
`vnxd inspect-journal` and the golden files) uses the field names above with `kind` = `superblock`, `map_update`,
`generation_released` or `map_batch` (whose `entries` is an array of objects with the entry fields) and hex strings
for `device_id` and `sha256`. A u64 field above 2^63 − 1 is legal in a record
but has no JSON description (protocol integers are signed 64-bit): describing it is an OVERFLOW error. Building a
record from a description checks the 32-bit fields and generation >= 1 (INVALID_ARGUMENT).

## 4. Integrity primitives

CRC-32 (zlib) frames records against torn writes and bit errors. SHA-256 (FIPS 180-4; Rust implementation tested
against the FIPS vectors) binds a mapping to the bytes of the generation it names. Neither is authentication:
authenticity of stored data comes from the AES-256-GCM seal applied by the existing security layer (P4).

## 5. Errors

| code | class | errno to the host | registry code (`core.errors.CODES`) |
|---|---|---|---|
| INVALID_ARGUMENT | permanent | EINVAL | FORMAT_ERROR |
| OUT_OF_RANGE | permanent | EINVAL | FORMAT_ERROR |
| NO_SPACE | permanent | ENOSPC | INTERNAL_ERROR |
| UNAUTHORIZED | permanent | EPERM | INTERNAL_ERROR |
| READ_ONLY | permanent | EPERM | INTERNAL_ERROR |
| NOT_FOUND | permanent | EIO | INTERNAL_ERROR |
| CORRUPTION | permanent | EIO | INTEGRITY_ERROR |
| BACKEND_UNAVAILABLE | retryable | EIO | INTERNAL_ERROR |
| BACKEND_BUSY | retryable | EIO | INTERNAL_ERROR |
| TIMEOUT | retryable | EIO | INTERNAL_ERROR |
| RESOURCE_EXHAUSTED | retryable | ENOMEM | RESOURCE_LIMIT |
| CANCELLED | permanent | ESHUTDOWN | INTERNAL_ERROR |
| SHUTTING_DOWN | permanent | ESHUTDOWN | INTERNAL_ERROR |
| PROTOCOL | permanent | EINVAL | FORMAT_ERROR |
| VERSION_MISMATCH | permanent | ENOTSUP | FEATURE_UNSUPPORTED |
| OVERFLOW | permanent | EOVERFLOW | FORMAT_ERROR |
| INTERNAL | permanent | EIO | INTERNAL_ERROR |

The errno set is the one the Linux NBD driver passes to the block layer. In Python every code is a
`vnxdna.device.DeviceError` (a `VNXError`, stage `device`, with `device_code`, `error_class`, `errno` and the
registry `code`). Retry policy belongs to the fallback module (P4): only retryable codes are retried, a bounded
number of times.

## 6. Protocols `vnx.mgmt/1` and `vnx.codec-ipc/1`

Transport: a Unix-domain socket, one canonical-JSON message per line; a line that is not valid UTF-8 is a PROTOCOL
error. Canonical JSON = Python `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
(`core.util.canonical_json`): sorted keys, no whitespace, the short escapes `\" \\ \n \r \t \b \f`, and every other
character outside printable ASCII 0x20–0x7e (DEL included) as lowercase `\uXXXX` (surrogate pairs above U+FFFF).
The parser accepts the contract subset only: integers in the signed 64-bit range (no fractions, exponents, NaN or
`-0`), no duplicate keys, no unpaired surrogates, no unescaped control characters, document <= 1 MiB, and depth
<= 32, where the top-level value is depth 1 and every nested value (scalars included) is one deeper: 32 nested empty
arrays are accepted, a scalar inside 32 arrays is not. Builders refuse values the parser would refuse.

```
request : {"args":{…},"id":N,"kind":"request","op":OP,"proto":P,"v":1}
response: {"id":N,"kind":"response","ok":true,"proto":P,"result":{…},"v":1}
        | {"error":{"class":C,"code":E,"message":M},"id":N,"kind":"response","ok":false,"proto":P,"v":1}
```

Validation order (identical in both languages, so both report the same code): JSON syntax -> message is an object ->
`proto` -> `v` (another integer: VERSION_MISMATCH) -> exact envelope keys -> known op -> exact fields with types;
every failure except the version is PROTOCOL. An error response must carry a known code with its own class. The
first exchange on a connection is `hello`. A well-formed error response is a valid message: both APIs return its
carried error (Rust `Ok(Err(e))`, Python a returned `DeviceError`) instead of reporting it as invalid, so an error
response carrying PROTOCOL is never confused with a malformed message.

Field types: `str` 1–1024 UTF-8 bytes; `count` integer >= 0; `positive` integer >= 1; `sha256` 64 lowercase hex
digits; `name` a staging file name, 1–255 bytes of `[A-Za-z0-9._-]` not starting with `.` (never a path); `object`
any object (validated by the receiver); `versions` non-empty list of integers >= 1; `health` one of
`ok`, `degraded`, `failed`; `hex128` 32 lowercase hex digits (a device id).

| protocol | op | args | result |
|---|---|---|---|
| both | hello | client str, versions versions | server str, version positive |
| vnx.mgmt | capacity | — | block_size, blocks_per_extent, capacity_blocks, capacity_bytes, extent_count (positive) |
| vnx.mgmt | health | — | state health, detail object |
| vnx.mgmt | stats | — | counters object |
| vnx.mgmt | shutdown | — | — |
| vnx.mgmt | authenticate (P4) | token str | role str |
| vnx.codec-ipc | encode_extent | job count, extent count, generation positive, input name, input_sha256 sha256, output name, options object | job count, output_sha256 sha256, output_bytes positive |
| vnx.codec-ipc | decode_extent | job count, extent count, generation positive, input name, output name, expect_sha256 sha256, expect_bytes positive, options object | job count, output_sha256 sha256, output_bytes positive |
| vnx.codec-ipc | key_check (P4) | device_id hex128 | key_check sha256 |
| vnx.codec-ipc | seal_extent, open_extent (P4) | job count, device_id hex128, extent count, generation positive, input name, input_sha256 sha256, output name | job count, output_sha256 sha256, output_bytes positive |

Authorisation of `vnx.mgmt` and the sealing operations are specified in [V14_SECURITY_MODEL.md](V14_SECURITY_MODEL.md).

New operations are added by later phases with a version bump only if an existing message changes meaning; adding an
op to a protocol is compatible (an older peer answers PROTOCOL "unknown op").

## 7. Media-adapter contract

`MediaAdapter` (Rust trait): `info()` (kind, `Durability`, `max_in_flight`), `put(ref, bytes) -> CommitReceipt`
(ref, length, SHA-256), `get(ref)`, `remove(ref)`, `list()`, `sync()`.

| durability | meaning |
|---|---|
| volatile | lost when the process exits; never reported to the host as durable |
| file-fsync | written to a file and fsynced on the host's storage (electronic storage, not DNA) |
| simulated-dna | handed to the DNA channel simulator: a simulation, not physical storage |

Rules, checked by `media::conformance` against every adapter: `put` of an existing generation is INVALID_ARGUMENT
(generations are immutable); a receipt describes exactly the bytes given; `get` returns exactly the stored bytes or
an error, never other bytes; `remove` of a missing generation and `get` after `remove` are NOT_FOUND; `list` is in
ascending order; a `remove` touches only its own generation (the other generations of the extent, other extents
and `list` are checked after it). `MemoryMedia` (volatile, in-process) passes the suite and is used to test the layers above the media
boundary; it is never a device backend.

Phase 7 adds the adapters behind this contract without changing it (media contract v1: the trait, the three
durability classes and the conformance rules are as above; the P1 golden files are unchanged):
`image` (file-fsync, electronic, not DNA), `dna-sim` (simulated-dna, the VNX-DNA codec over a simulated channel),
`faulty` (fault injection over any adapter; durability of the wrapped adapter) and `dna-lab-sim` (simulated-dna, the
future physical-DNA lab interface over its simulator). Each passes `media::conformance`. Specification:
[V14_MEDIA_ADAPTERS.md](V14_MEDIA_ADAPTERS.md). There is no durability class for physical DNA in v1; adding one is a
versioned contract change for the phase that connects real equipment. The `vnx.codec-ipc/1` codec operations
`encode_extent`/`decode_extent` (schemas of §6, unchanged) are served by `python -m vnxdna.device.dnasim`; their
`options` content is defined in V14_MEDIA_ADAPTERS.md §2.

## 8. Cross-language golden files

`tests/v14/golden/contracts/` holds `records.json` (descriptions and bytes of every record kind), `bad_records.json`
(15 malformed byte strings with the error code) and `messages.json` (9 valid messages with their canonical form,
including DEL/control-character escaping and a message exactly at the depth limit, and 17 invalid ones with their
error code). `generate.py` wrote them from the Python implementation; they change only
with a format or protocol version. `tests/v14/test_device_contracts.py` (Python) and
`src/vnxdna/native/rust/vnxd/tests/golden.rs` (Rust) must reproduce every entry; the Python test also compares the
error table and the op lists with the Rust source.

## 9. `vnxd` command (Phase 1)

`vnxd version` prints name, version, record format and protocol versions as canonical JSON. `vnxd inspect-journal
FILE` prints every record of a record file, `valid_len` and the reason reading stopped; exit 0 when the whole file
decodes, 3 when it stops early, 1 when the file cannot be read, 2 on a usage error. Build:
`cargo build --release --offline --locked --manifest-path src/vnxdna/native/rust/vnxd/Cargo.toml`.
