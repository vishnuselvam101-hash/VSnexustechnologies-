# VNX-DNA formats (archive format 4)

Three layers are specified here. All integers are big-endian.

1. the **`.vxdna` container file** (container file version 1), produced by `store` and `decode`;
2. the **manifest** (archive format 4), canonical JSON inside the container and inside the metadata strands;
3. the **strand frame** (frame format 4) and the **metadata-strand stream**, produced by `encode`.

The V0.1 formats (dataset formats 1–3 and RD-1 `0.1`) are described in [COMPATIBILITY.md](COMPATIBILITY.md).

## 1. Container file `.vxdna` (version 1)

| offset | size | field |
|---|---|---|
| 0 | 8 | magic `89 56 58 44 4E 41 0D 0A` (`\x89VXDNA\r\n`) |
| 8 | 2 | container file version = `1` |
| 10 | 2 | flags = `0`. Nonzero is rejected as unsupported. |
| 12 | 4 | manifest length *L* (≤ 64 MiB) |
| 16 | 8 | body length *B* (must equal `manifest.stored_size`) |
| 24 | *L* | canonical manifest JSON (§2) |
| 24+*L* | *B* | body: stored chunks concatenated in chunk order |
| 24+*L*+*B* | 32 | SHA-256 of bytes `[0, 24+L+B)` |

The trailer detects accidental file corruption. It is not authentication. A decoder must reject:
- bad magic;
- an unknown version or flags;
- a length mismatch (truncation or trailing data);
- a trailer mismatch. `verify` reports this failure and continues its per-chunk checks.

The file is a pure function of the manifest and the stored chunks. That is why decoding DNA reproduces it byte for
byte.

## 2. Manifest (archive format 4)

**Canonical form.** UTF-8 JSON with keys sorted, separators `,` and `:`, ASCII escaping. It must contain no
floating-point numbers, no NaN/Infinity and no duplicate keys. A manifest that is not byte-identical to its own
canonical re-serialization is rejected.

**Top-level fields** (all required; no others allowed):

| field | type | meaning |
|---|---|---|
| `format` | `"VNX-DNA"` | magic |
| `format_version` | `4` | archive format |
| `encoder` | `{name: "vnxdna", version}` | the software that wrote the archive |
| `required_features` | list | exactly the mechanisms needed to decode (below). An unknown entry makes the archive unsupported. |
| `archive_id` | 32 hex | 128-bit ID. Random when encrypted; content-derived (SHA-256 of options, name and data) when not. |
| `created_at` | string \| null | optional, recorded verbatim (`null` by default for determinism) |
| `chunk_size` | int | plaintext bytes per chunk (the last chunk may be shorter) |
| `compression` | `{algorithm: none\|zlib\|zstd, level}` | requested codec |
| `encryption` | `{algorithm: none\|AES-256-GCM, kdf: HKDF-SHA256\|null, salt: 32 hex\|null, key_check: 16 hex\|null}` | |
| `stored_size`, `stored_sha256` | | total body size and its SHA-256 |
| `chunks[]` | `{index, stored_size, stored_sha256, first_stripe, stripe_count}` | stored (post-compression/encryption) chunks |
| `content` | object \| null | clear content description (unencrypted archives only) |
| `sealed_content` | base64 \| null | AES-GCM-sealed content description (encrypted archives only) |
| `erasure_code` | `{algorithm: cauchy-rs-gf256, data_shards K, parity_shards M, stripe_count}` | outer code |
| `strand` | `{frame_format: 4, mapping, payload_bytes P, inner_ecc, inner_parity_bytes r, crc: crc32, scrambler: shake128, frame_bytes, strand_nt}` | strand geometry |
| `constraints` | `{gc_min_percent, gc_max_percent, max_homopolymer, gc_window_nt, forbidden_motifs, check_reverse_complement}` | screening rules used when encoding |
| `dna_manifest` | `{enabled: true, scheme: cauchy-rs-gf256-8+8}` | metadata strands |
| `extensions` | object | the only open object. Unknown keys here are ignored, but they are still covered by the seal. |
| `seal` | `{manifest_sha256, manifest_hmac_sha256\|null}` | see below |

`content` / sealed content: `{name: string|null (plain file name ≤255 chars), size, sha256, chunks[]: {index, offset,
size, sha256}}`. These fields hold the plaintext name, size and SHA-256 of the whole object and of every chunk.

**Seal.** `manifest_sha256` is the SHA-256 of the canonical manifest *without* the `seal` member. It detects
corruption, but anyone can recompute it. `manifest_hmac_sha256` is HMAC-SHA256 of the same bytes under a key
derived from the user's key. It is present if and only if the archive is encrypted, and it authenticates every
manifest field (see [SECURITY.md](SECURITY.md)).

**Semantic rules** (all enforced, each raising `MetadataError`):
- `required_features` must be exactly the set implied by the mapping and encryption state;
- an encrypted archive has `sealed_content` and an HMAC and no clear `content`, and the reverse holds for an
  unencrypted one;
- chunk indices are contiguous;
- stripes are contiguous, with `stripe_count = ceil(stored_size / (K·P))`;
- the chunk sizes sum to `stored_size`;
- the content chunks cover `[0, size)` exactly;
- the strand geometry is internally consistent;
- `K + M ≤ 256`;
- the compression level is in range;
- the name contains no path separators or control characters.

**Features** (format 4): `chunked-v1`, `outer-cauchy-rs-v1`, `inner-rs-v1`, `frame4-crc32-v1`, `scrambler-shake128-v1`,
`mapping-2bit` / `mapping-rotation3` / `mapping-codebook8`, and `aes-256-gcm-hkdf-v1` (encrypted only).

**Versioning rules.**
- A different `format`, a `format_version` ≠ 4, or an unknown required feature raises `UnsupportedFormatError`
  (exit code 6).
- An unknown field outside `extensions`, a missing field or a mistyped field raises `MetadataError` (exit code 3).
- A future format must bump `format_version` or add a required feature. It must never reinterpret an existing
  field.

## 3. Strand frame (format 4)

Frame bytes before the DNA mapping:

| offset | size | field |
|---|---|---|
| 0 | 1 | scrambler variant *v* (not scrambled) |
| 1 | 1 | `(4 << 4) | kind`. The high nibble is the frame format (4). The kind is 0 = data/parity shard, 1 = metadata. |
| 2 | 3 | archive tag = first 3 bytes of `archive_id` |
| 5 | 3 | stripe index (0 … 2²⁴−1) |
| 8 | 1 | shard index within the stripe (0 … K+M−1; data shards first) |
| 9 | P | payload (one outer-code shard) |
| 9+P | 4 | CRC-32 (zlib/IEEE) over bytes 1 … 8+P *before* scrambling |
| 13+P | r | inner RS parity over GF(2⁸)/0x11D (fcr = 0, generator 2) of bytes 0 … 12+P |

Bytes 1 … 12+P are XORed with `SHAKE-128("VNX-DNA/4 scrambler" ‖ v)`. The frame is `13 + P + r` bytes (≤ 255), and
the strand is `frame_bytes × nt_per_byte` nucleotides. The default is P = 40 and r = 8, giving 61 bytes = 244 nt
with the 2bit mapping.

**Derived identity.** The chunk comes from the manifest's stripe ranges, and the byte offset inside the stored chunk is
`((stripe − first_stripe)·K + shard)·P`. The payload length is P, except in the zero-padded tail, which the manifest
determines.

**Shortening.** In the last stripe of each chunk, data shards `u … K−1` are not emitted, where
`u = ceil((stored_size − (stripe_count−1)·K·P) / P)`. The decoder treats them as known-zero shards.

**Accepting a read.** A read is accepted only if the CRC verifies, either directly, after reverse-complementing, or
after inner-RS correction. The CRC is always re-checked after correction. Frame version and kind must be valid.
FASTA/FASTQ headers are ignored.

## 4. Metadata strands

The stream is `"VNXM" ‖ uint32 length ‖ canonical manifest`. It is split into P-byte shards, 8 data shards per stripe,
and protected by Cauchy 8+8 (16 strands per stripe). The strands are written as frame kind 1 with the same archive tag,
stripe index 0, 1, … and shard 0 … 15. They are not shortened. Stripe 0 is decoded first to learn the length.

**Geometry discovery.** Without a container, the decoder tries, for the most common read lengths, every mapping and
every even `r ∈ [0, 64]`. P follows from the read length. A hypothesis wins by the number of clean reads that pass the
CRC.

## 5. Efficiency terms (reported by `info` / `encode`)

| term | definition |
|---|---|
| `compressed_bytes` | stored bytes minus the 16-byte GCM tags |
| `encryption_overhead_bytes` | 16 × chunks (encrypted) |
| `outer_parity_bytes` | stripes × M × P |
| `padding_bytes` | emitted data shards × P − stored bytes |
| `frame_overhead_bytes` | strands × (9 header + 4 CRC + r) |
| `dna_bases_total` | strands × strand_nt, metadata strands included |
| `bases_per_original_byte`, `net_bits_per_base` | end-to-end density *including every overhead*. The compression ratio is reported separately and is **not** a DNA-storage efficiency. |
