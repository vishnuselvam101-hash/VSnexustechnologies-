# VNX-DNA V2 formats (archive format 5)

All integers are big-endian unless stated otherwise. Format 4 (V1) is specified in [FORMAT.md](FORMAT.md) and
stays readable ([COMPATIBILITY.md](COMPATIBILITY.md)).

1. the **container file** `.vxdna` version 2;
2. the **manifest** (archive format 5, canonical JSON) and its two binary tables;
3. the **strand frame** format 5 and the **metadata strands**;
4. the **VXS** packed strand file, the **DNA index** `.vxidx`, the **cluster file** and the **store checkpoint**.

## 1. Container file `.vxdna`, version 2

| offset | size | field |
|---|---|---|
| 0 | 8 | magic `89 56 58 44 4E 41 0D 0A` (`\x89VXDNA\r\n`, same as version 1) |
| 8 | 2 | container file version = `2` |
| 10 | 2 | flags = `0` (nonzero → unsupported) |
| 12 | 4 | reserved = `0` |
| 16 | B | **body**: stored chunks in chunk order |
| 16+B | L | canonical manifest JSON (§2) |
| 16+B+L | I | chunk index (56 bytes × chunks) |
| 16+B+L+I | J | plaintext index (36 bytes × chunks, + 16-byte GCM tag when sealed) |
| end−64 | 8 | B |
| end−56 | 4 | L (≤ 1 MiB) |
| end−52 | 4 | I |
| end−48 | 4 | J |
| end−44 | 4 | reserved = `0` |
| end−40 | 8 | trailer magic `VXDNAEND` |
| end−32 | 32 | SHA-256 of every preceding byte |

A reader must reject bad magic, an unknown version, nonzero flags or reserved fields, a missing trailer magic
(truncated file or unfinished `.partial`), and sizes that do not add up to the file size. The trailer SHA-256 is
checked by `verify` and by full reads. Random access checks each chunk's SHA-256 instead.

**Writing.** `store` writes `<output>.partial` (mode 0600), fsyncs it, and publishes it with `os.replace`. A
killed store leaves only the partial file and, if a checkpoint was reached, `<output>.partial.ckpt` and
`<output>.partial.idx` (§4.4).

**Decoding reproduces the file byte for byte.** The file is a pure function of the manifest, both tables and the
stored chunks, and all of them are carried in the DNA.

## 2. Manifest (archive format 5)

Canonical UTF-8 JSON: sorted keys, separators `,` `:`, ASCII escapes, no floats, no NaN, no duplicate keys. The bytes
must equal their own canonical re-serialisation.

| field | type | meaning |
|---|---|---|
| `format` / `format_version` | `"VNX-DNA"` / `5` | |
| `encoder` | `{name: "vnxdna", version}` | |
| `required_features` | list | exactly the set implied by mapping and encryption (below). Unknown → unsupported |
| `archive_id` | 32 hex | random when encrypted; `SHA-256("VNX-DNA/5 archive-id\0" ‖ canonical(options, name) ‖ SHA-256(data))[:16]` otherwise |
| `created_at` | string \| null | recorded verbatim; null by default (determinism) |
| `profile` | `[a-z0-9_-]{1,32}` | informational (`balanced`, `archival-custom`, …) |
| `chunk_size`, `chunk_count` | int | plaintext bytes per chunk (≤ 64 MiB); number of chunks (`max(1, ⌈size/chunk_size⌉)`) |
| `compression` | `{algorithm: zstd\|zlib\|none, level, policy: "auto"}` | per chunk: compressed only when smaller (codec in the index) |
| `encryption` | `{algorithm: none\|AES-256-GCM, kdf: HKDF-SHA256\|null, salt, key_check}` | |
| `stored_size`, `stored_sha256` | | body length and SHA-256 |
| `chunk_index` | `{format: "vnx-chunk-index-1", entry_bytes: 56, entries, sha256}` | |
| `plain_index` | `{format: "vnx-plain-index-1", entry_bytes: 36, sealed, stored_bytes, sha256}` | SHA-256 of the stored (possibly sealed) bytes |
| `content` | `{name, size, sha256}` \| null | clear for unencrypted archives |
| `sealed_content` | base64 \| null | AES-GCM (domain 1) of canonical `content` for encrypted archives |
| `erasure_code` | `{algorithm: cauchy-rs-gf256, data_shards K, parity_shards M, stripe_count}` | one stripe = one ECC group |
| `strand` | `{frame_format: 5, mapping, payload_bytes P, inner_ecc, inner_parity_bytes r, crc, scrambler, header_bytes: 11, frame_bytes, strand_nt}` | |
| `constraints` | `{gc_min_percent, gc_max_percent, max_homopolymer, gc_window_nt, forbidden_motifs, check_reverse_complement, max_tandem_repeat_nt}` | screening rules used by `encode` |
| `dna_manifest` | `{enabled: true, scheme: "cauchy-rs-gf256-8+8"}` | |
| `extensions` | object | the only open object (covered by the seal) |
| `seal` | `{manifest_sha256, manifest_hmac_sha256 \| null}` | over the canonical manifest without `seal`; HMAC iff encrypted |

**Features (format 5):** `stream-chunked-v2`, `outer-cauchy-rs-v1`, `inner-rs-v1`, `frame5-crc32-v1`,
`scrambler-shake128-v2`, one of `mapping-2bit|rotation3|codebook8`, and `aes-256-gcm-hkdf-v2` when encrypted.

### 2.1 Chunk index (clear; 56 bytes per chunk)

| offset | size | field |
|---|---|---|
| 0 | 8 | byte offset of the stored chunk inside the body |
| 8 | 4 | stored size |
| 12 | 4 | first ECC group (stripe) |
| 16 | 4 | number of ECC groups |
| 20 | 1 | codec: 0 none, 1 zstd, 2 zlib |
| 21 | 1 | AEAD epoch (0 for a fresh store; the n-th `store --resume` seals the chunks it writes in epoch n; always 0 when unencrypted) |
| 22 | 2 | reserved = 0 |
| 24 | 32 | SHA-256 of the stored chunk |

Validated vectorised: contiguous offsets from 0, sizes summing to `stored_size`, `stored_size ≤ chunk_size + 16`
(the auto policy never expands a chunk), `stripe_count = ⌈stored_size / (K·P)⌉`, contiguous stripes summing to
`erasure_code.stripe_count`, codecs allowed by `compression`, reserved bytes zero, epochs zero when unencrypted,
SHA-256 of the table equal to `chunk_index.sha256`.

### 2.2 Plaintext index (36 bytes per chunk)

`plain size (4) ‖ plaintext SHA-256 (32)`. Sizes must tile `content.size` (`chunk_size` each, last one shorter).
For encrypted archives the whole table is sealed with AES-256-GCM (domain 2, index 0, count 1).

### 2.3 Encryption (see [SECURITY.md](SECURITY.md))

HKDF-SHA256(master key, salt) → AEAD key (`"VNX-DNA/5 aead key"`), MAC key (`"VNX-DNA/5 manifest mac key"`), 8-byte key
check (`"VNX-DNA/5 key check"`). Chunk *i* is AES-256-GCM with nonce `t(4) ‖ i(8)` and associated data
`"VNX-DNA/5 aead" ‖ archive_id ‖ t(4) ‖ i(8) ‖ chunk_count(8)`, where `t = epoch · 256 + domain` and the epoch is the
chunk's index entry. Domains: 0 chunk, 1 sealed content, 2 sealed plaintext index (both epoch 0).

## 3. Strand frame format 5

| offset | size | field |
|---|---|---|
| 0 | 1 | scrambler variant *v* (not scrambled) |
| 1 | 1 | `(5 << 4) \| kind`: kind 0 = data/parity shard, 1 = metadata |
| 2 | 4 | archive tag = first 4 bytes of `archive_id` |
| 6 | 4 | stripe (ECC group) index, 0 … 2³²−1 |
| 10 | 1 | shard index within the group (0 … K+M−1; data first) |
| 11 | P | payload (one outer-code shard) |
| 11+P | 4 | CRC-32 (zlib/IEEE) of bytes 1 … 10+P before scrambling |
| 15+P | r | inner RS parity over GF(2⁸)/0x11D (fcr 0, generator 2) of bytes 0 … 14+P |

Bytes 1 … 14+P are XORed with `SHAKE-128("VNX-DNA/5 scrambler" ‖ v)`. The encoder picks the smallest *v* whose
mapped strand satisfies the constraints; if none of the 256 does, `encode` fails (`ConstraintError`). Default
geometry (balanced): P = 40, r = 8 → 63 bytes → **252 nt**.

**Identity.** Chunk and byte offset follow from the stripe through the authenticated chunk index: the stored byte
offset inside chunk *c* is `((stripe − first_stripe[c]) · K + shard) · P`. In each chunk's last group, data shards
lying wholly in the zero padding are not emitted (shortening): `used = ⌈(stored − (groups−1)·K·P) / P⌉`.

**What identity is in-band.** Archive (32-bit tag; the full 128-bit ID and version are in the metadata strands),
frame version (nibble 5), kind (data/metadata), ECC group (stripe) and shard. Chunk and offset follow from the stripe
through the authenticated index. There is no generation/copy field. Copies of a strand are identical by design and
are merged by duplicate resolution, and a re-encoded archive with different content has a different archive ID and
tag. All of this survives the loss of FASTA/FASTQ headers, which are never read.

**Accepting a read:** CRC passes forward, reverse-complemented, or after inner-RS errors-and-erasures decoding
(`N` and optionally low-quality bases are erasures), with the version nibble 5 and kind ≤ 1. Headers are never used.

### 3.1 Metadata strands

Stream `"VNX5" ‖ L(4) ‖ I(4) ‖ J(4) ‖ manifest ‖ chunk index ‖ plaintext index`, cut into P-byte shards, 8 data
shards per stripe, Cauchy 8+8 (any 8 of 16 strands per stripe may be lost), frame kind 1, stripes 0, 1, … Written
**first** in every strand file. A strand file alone is a complete archive.

**Geometry discovery** (no container at hand): for the most common read lengths, every mapping and even r ∈ [0, 64]
is tried on up to 400 reads; a hypothesis scores when reads pass the frame CRC. If no read is error-free, a fallback
tries inner-RS correction on a sample and needs two verified reads.

## 4. Other files

### 4.1 VXS packed strand file (version 1)

| offset | size | field |
|---|---|---|
| 0 | 8 | magic `\x89VXSTRD\n` |
| 8 | 2 | version = 1 |
| 10 | 2 | flags = 0 |
| 12 | 4 | strand length in nt |
| 16 | 4 | record bytes = ⌈nt / 4⌉ |
| 20 | 12 | reserved = 0 |
| 32 | R·n | records: 2 bits per nt (A=00 C=01 G=10 T=11, most significant first), zero-padded |
| end−56 | 8 | record count n |
| end−48 | 8 | reserved |
| end−40 | 8 | `VXSEND\0\0` |
| end−32 | 32 | SHA-256 of the record bytes |

Equal-length A/C/G/T only. With the 2bit mapping a record is exactly the frame's bytes.

### 4.2 DNA index `<strands>.vxidx`

Canonical JSON with `format: "vnx-dna-index-1"`, the archive ID and manifest SHA-256, the strand file's name,
format, byte size and SHA-256, `data_shards`, `parity_shards`, `group_strands`, the metadata strands' range, and one row per
chunk: `[first_strand, strands, byte_offset, byte_length, first_stripe, stripe_count]`. `index_sha256` covers the rest. The index only
speeds things up: chunks decoded through it are verified against the authenticated chunk index like any other.

### 4.3 Cluster file (JSON Lines, `vnx-clusters-1`)

Line 1: header `{"format": "vnx-clusters-1", "geometry": {...}, "archive_tags": [...], ...}`. Then one object per
cluster `{"id", "address": [kind, tag, stripe, shard] | null, "verified", "tentative", "reads": [...], "quals": [...]}`
with forward-oriented reads. Last line: `{"end": true, "stats": {...}}`. A file without the end record is truncated and
is rejected.

### 4.4 Store checkpoint

`<output>.partial.ckpt`: JSON (`format: "vnx-store-checkpoint-1"`) with the input's resolved path, size and mtime,
the SHA-256 of the options, encryption state, archive ID/salt/key check (encrypted), the AEAD epoch in use, chunk
count, completed chunks,
body bytes, the SHA-256 of the index sidecar prefix, and `checkpoint_sha256` over all of that.
`<output>.partial.idx` holds 92 bytes per completed chunk (chunk-index entry + plaintext-index entry).
