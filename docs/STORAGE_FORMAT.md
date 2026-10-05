# VNX-DNA 3 storage format

**Decision: VNX-DNA 3 keeps archive format 5** (container file version 2, strand frame format 5), specified byte by
byte in [V2_FORMAT.md](V2_FORMAT.md). The V2 audit found the format's design sound: every V3 capability fits inside
it. A new format would have broken interoperability for no gain. V3 changes three things, all versioned:

1. one **optional required-feature** in the manifest, `final-seal-epoch-v3`, written only by encrypted stores that
   were resumed (§3);
2. the **store checkpoint** format `vnx-store-checkpoint-2` (HMAC-bound; §4);
3. **stricter reader rules** that reject files VNX-DNA 2.0 wrongly accepted (§5).

Everything else a V3 encoder writes is what V2 wrote. For an unencrypted, never-resumed store, only the manifest's
informational `encoder.version` field and the metadata strands that carry the manifest differ. This is checked
against files written by the 2.0.0 release in `tests/v3/test_compat_v3.py`.

## 1. Conceptual layout

The brief asked for an archive with header, index, metadata, addressed chunks (address, metadata, payload, ECC,
integrity) and a footer or manifest. Format 5 has all of these, in two representations: the container file and the
DNA strand pool. Either one is a complete archive on its own.

```
.vxdna container file (version 2)                    DNA strand pool (FASTA or VXS)
├── HEADER    magic, version, flags (16 B)           ├── METADATA STRANDS  manifest + both indexes, Cauchy 8+8,
├── BODY      stored chunks in order                 │                     frame kind 1, written first
│   └── chunk c: [zstd?] → [AES-256-GCM?]            └── DATA STRANDS, per chunk, per ECC group (stripe):
├── MANIFEST  canonical JSON: options, sizes,            ├── ADDRESS    archive tag (32 bit) · stripe (32 bit) · shard
│             codes, constraints, features, seal         ├── METADATA   frame format / kind nibble, scrambler variant
├── CHUNK INDEX   56 B/chunk: offset, size, codec,       ├── PAYLOAD    one outer-code shard (P bytes)
│                 AEAD epoch, stripes, SHA-256           ├── INTEGRITY  CRC-32 over address + payload
├── PLAIN INDEX   36 B/chunk: size, SHA-256              └── ECC        inner RS parity (r bytes); the group's
│                 (AES-GCM sealed when encrypted)                       parity strands carry the outer code
└── TRAILER   lengths, magic, SHA-256 of the file
```

| requirement | how format 5 meets it |
|---|---|
| versioning | container version (2), `format_version` (5), frame version nibble (5), `required_features`; unknown values are refused with exit 6 |
| deterministic serialisation | canonical JSON (sorted keys, no floats, no duplicate keys, bytes must equal their re-serialisation); fixed-width binary tables; deterministic archive ID when unencrypted (`options-v1` by default; opt-in `content-v1`, derived from the content, spec V6 §2.3.2); `created_at` null by default |
| integrity verification | trailer SHA-256 (file), `stored_sha256` (body), SHA-256 per stored chunk, SHA-256 per plaintext chunk, object SHA-256; HMAC-SHA256 and AES-GCM tags when encrypted |
| corruption detection | every layer above; per strand CRC-32 and inner RS |
| explicit metadata | the manifest records every parameter needed to decode; decoding never depends on the profile table |
| chunk identification | chunk index (container); stripe → chunk mapping through the authenticated index (DNA) |
| future compatibility | `required_features` (readers refuse what they do not implement), `extensions` object, reserved fields that must be zero |

## 2. Encoding stack (unchanged from V2)

```
plaintext chunk ─▶ compress (kept if smaller) ─▶ AES-256-GCM (optional) ─▶ stored chunk
stored chunk ─▶ split into ECC groups of K × P bytes ─▶ Cauchy RS parity (M shards) ─▶ K + M shards
shard ─▶ frame [variant | ver/kind | tag | stripe | shard | payload | CRC-32 | inner RS] ─▶ scramble ─▶ map to A/C/G/T
```

Separation of layers as the brief required: compression → encryption/authentication → chunking into ECC groups →
ECC → DNA encoding. ECC is applied to ciphertext, so decoding DNA never needs the key.

## 3. Optional feature `final-seal-epoch-v3`

**Problem (V2 audit C2).** An encrypted archive seals two records at finalisation: the content record (name, size,
object SHA-256; AEAD domain 1) and the plaintext index (domain 2). VNX-DNA 2.0 always sealed them in AEAD epoch 0.
Suppose a store was interrupted after its footer was written but before the rename, and the input then changed with
the same size and mtime. The resumed store sealed *different* finalisation records under the *same* nonce, which
leaks their XOR and the GHASH key.

**V3 rule.** Every chunk index entry records the AEAD epoch its chunk was sealed in. The finalisation records use
epoch `e_final = max(chunk epochs)`: 0 for a store that was never resumed, the resume's own epoch otherwise.

* If `e_final > 0`, the manifest's `required_features` includes `final-seal-epoch-v3`, and readers open both records
  in epoch `e_final`. VNX-DNA 2.0 does not know the feature and refuses such an archive with `UNSUPPORTED_FORMAT`
  (exit 6), instead of failing authentication.
* If `e_final = 0`, the feature is absent and the archive is byte-compatible with VNX-DNA 2.0.
* Archives that VNX-DNA 2.0 resumed have chunk epochs > 0 but no feature. V3 reads them with epoch 0, exactly as
  written (fixture `tests/fixtures/v2_0/resumed.vxdna`).

The feature is inside the HMAC-authenticated manifest, so it cannot be added or removed without the key.

## 4. Store checkpoint `vnx-store-checkpoint-2`

See [V2_FORMAT.md §4.4](V2_FORMAT.md#44-store-checkpoint). New in V3:

* `checkpoint_hmac`: HMAC-SHA256 under the archive's MAC key over the canonical checkpoint body, label
  `VNX-DNA/5 store checkpoint\0` (encrypted stores; `null` otherwise). The checkpoint holds the AEAD epoch. With
  only the unkeyed `checkpoint_sha256`, anyone able to write the output directory could roll the epoch back and make
  the next resume reuse nonces (V2 audit C2, second part).
* On `--resume`, the new epoch is written to the checkpoint **before** any chunk is sealed with it (V2 audit C1).
  VNX-DNA 2.0 persisted it only at the next periodic checkpoint, so two interrupted resumes in a row could reuse the
  first resume's nonces.
* A VNX-DNA 2.0 checkpoint (`-1`) is refused with a clear message; the store must restart without `--resume`.

## 5. Reader rules added in V3

| rule | V2 behaviour (audit id) |
|---|---|
| the container body length must equal the manifest's `stored_size` | padding after the last chunk was accepted and `verify` reported PASS (C3) |
| `verify` hashes the body and compares `stored_sha256` | never checked (C3) |
| `verify` reports a truncated body as FAIL | raised an exception, no report (C5) |
| the chunk count must fit the trailer's 32-bit index length (≤ 76,695,844 chunks) | accepted up front, crashed in `finish()` after the whole input (S1) |
| metadata-stream lengths from DNA are bounded by the metadata strands actually present | unauthenticated 32-bit lengths drove allocation (D2) |
| read lines longer than 100,002 bytes are cut while reading | whole lines were read into memory first (D3) |
| geometry discovery ignores read lengths whose frame cannot be an RS codeword (< 16 or > 255 bytes) | a few long junk reads aborted decoding with exit 7 (D1) |
| pools with metadata of several archives: the only decodable one is used, otherwise `--archive-tag` selects | always refused (D6) |

## 6. V2 → V3 compatibility summary

| direction | result |
|---|---|
| V3 reads V2 containers, strand files and read files | yes: plaintext, encrypted, and resumed-encrypted fixtures from the 2.0.0 release are restored, verified, random-accessed and decoded from DNA (`tests/v3/test_compat_v3.py`) |
| V2 reads V3 archives | yes, except encrypted archives whose store was resumed (they declare `final-seal-epoch-v3` and V2 refuses them with exit 6) |
| V3 reads V1 (format 4) and legacy V0.1 archives | yes, unchanged V1 code ([COMPATIBILITY.md](COMPATIBILITY.md)) |
| migration | not needed for V2 archives; `vnx-dna migrate` converts V1 → format 5 |

## 7. VNX4 containers in VNX-DNA 6

This page describes the V3 decisions (archive format 5). The current format is VNX4, written by `vnx` since 4.0:
[VNX4_FORMAT.md](VNX4_FORMAT.md) (container 4.0, frame 4, superblock 1), [V6_OUTER_CODE.md](V6_OUTER_CODE.md)
(superblock 2) and, as the single normative text for 6.x, [spec/VNX-DNA-SPEC-V6.md](spec/VNX-DNA-SPEC-V6.md). In 6.x the
container format does not change. What is new:

* `extensions.vnx` in the manifest records the writing software and specification version (spec §2.3.1), on by default;
* the archive ID of an unencrypted archive is `options-v1` by default; `content-v1` is opt-in (spec §2.3.2,
  [COMPATIBILITY.md](COMPATIBILITY.md));
* frame 6, superblock 3, primers and the wide address class are specified but not implemented
  ([V6_DEFERRED.md](V6_DEFERRED.md)).
