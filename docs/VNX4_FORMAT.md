# VNX4 format specification (container 4.0, strand frame 4, superblock 1)

Status: **IMPLEMENTED** (reference implementation `src/vnxdna/v4/`). This document is normative: an independent
reader can be written from it alone. Conventions: all integers are **big-endian** and unsigned unless stated;
`‖` is concatenation; SHA-256, HMAC-SHA256, HKDF-SHA256, AES-256-GCM, scrypt and CRC-32 (IEEE 802.3, the
`zlib.crc32` polynomial) are the standard algorithms. "MUST reject" means: raise a format error, publish nothing.

V3's format 5 (`.vxdna`, frame format 5) is a different format, specified in [V2_FORMAT.md](V2_FORMAT.md) and
[STORAGE_FORMAT.md](STORAGE_FORMAT.md). It is unchanged and still read by `vnx-dna`. A VNX4 reader that meets the
V3 magic `\x89VXDNA` MUST report "unsupported version" and point to the V3 tool.

Contents: 1 container layout · 2 header · 3 chunk table · 4 file table · 5 reference table · 6 encryption ·
7 manifest · 8 trailer · 9 validation · 10 strand frame · 11 DNA mapping and markers · 12 superblock ·
13 groups and outer codes · 14 strand files · 15 compatibility rules.

## 1. Container layout (`.vnx`)

```
offset         size   section
0              16     header (§2)
16             B      body: stored chunks, concatenated in chunk-table order
16+B           C      chunk table (§3), 84 bytes per stored chunk
16+B+C         F      file table (§4), sealed when encrypted
16+B+C+F       R      reference table (§5), sealed when encrypted
16+B+C+F+R     M      manifest (§7), canonical JSON, 1 ≤ M ≤ 1 MiB
end−112        112    trailer (§8)
```

## 2. Header (16 bytes)

| offset | size | field | value |
|---|---|---|---|
| 0 | 8 | magic | `89 56 4E 58 34 0D 0A 1A` (`\x89VNX4\r\n\x1a`) |
| 8 | 2 | major version | `4` |
| 10 | 2 | minor version | `0` (a reader MUST reject a minor version above its own) |
| 12 | 4 | flags | `0` (nonzero MUST be rejected: no flags are defined) |

## 3. Chunk table (84 bytes per stored chunk)

| offset | size | field |
|---|---|---|
| 0 | 8 | byte offset of the stored chunk inside the body (body offset 0 = file offset 16) |
| 8 | 4 | stored size (bytes) |
| 12 | 4 | plaintext size (bytes), 1 … `chunk_size` |
| 16 | 1 | codec: `0` none, `1` zstd |
| 17 | 3 | reserved, MUST be zero |
| 20 | 32 | SHA-256 of the stored bytes |
| 52 | 32 | chunk ID (below) |

**Stored bytes.** `stored = AEAD(codec(plaintext))`. When encrypted, AEAD is AES-256-GCM (§6) and the 16-byte tag
is appended. The codec is zstd only if the zstd frame is shorter than the plaintext, otherwise `none`
(keep-if-smaller). So `stored ≤ plain + 16` (encrypted) or `stored ≤ plain` (clear).

**Chunk ID.** Unencrypted: `SHA-256("VNX4 chunk\0" ‖ plaintext)`, a content address. Encrypted:
`HMAC-SHA256(chunk_id_key, plaintext)` (§6), so that someone without the key cannot confirm guessed content.

**Deduplication.** A writer that enables `dedup-content-address` stores each distinct chunk ID once. Files then
reference chunks by index (§5). Every stored chunk MUST be referenced at least once.

**Chunk index order.** Chunks are numbered in first-reference order (§4 order of files, then chunk order within a
file). The table MUST tile the body exactly: entry 0 at offset 0, each next entry at `offset + stored` of the
previous one, and the last ends at B.

## 4. File table (variable-length records, sorted)

One record per entry, concatenated with no padding, sorted strictly ascending by the UTF-8 bytes of the path.
Duplicates and wrong order MUST be rejected.

| size | field |
|---|---|
| 2 | path length L (1 … 4096) |
| L | path: UTF-8, relative POSIX form (rules below) |
| 1 | type: `0` regular file, `1` directory (empty directories are stored explicitly) |
| 8 | file size in bytes (`0` for directories) |
| 4 | mode (permission bits; `0` unless the writer used `--preserve-metadata`) |
| 8 | mtime in ns since the epoch, **signed** (`0` unless preserved) |
| 8 | first index into the reference table |
| 4 | number of chunk references = ⌈size / chunk_size⌉ (`0` for directories) |
| 32 | SHA-256 of the file content (SHA-256 of the empty string for directories and empty files) |

**Path rules** (MUST be enforced by readers and writers): not empty, at most 4096 bytes, valid UTF-8, no leading
`/`, no `\`, no NUL or other control character (< 0x20), no empty component, no `.` or `..` component.

**Reference contiguity.** File records reference the reference table in file order without gaps. A record's first
index equals the sum of the reference counts of all earlier records. All chunks of a file except the last have
plaintext size `chunk_size`, and the plaintext sizes add up to the file size.

## 5. Reference table

`u32` chunk indices, 4 bytes each, in file order (§4). Every value MUST be less than the number of chunk-table
entries.

## 6. Encryption

Order of operations: **plaintext chunk → zstd (if smaller) → AES-256-GCM**. Compression has to come first because
ciphertext does not compress. The resulting leak (each chunk's compressed size) is documented in
[SECURITY.md](SECURITY.md).

**Master key.** It is either (a) a 32-byte key file, or (b) `scrypt(passphrase, salt, N, r, p, dkLen = 32)` with the
parameters recorded in the manifest (N a power of two ≤ 2²⁰, r ≤ 32, p ≤ 16).
**Salt.** 16 random bytes per archive (manifest `encryption.salt`).
**Derived keys.** `HKDF-SHA256(ikm = master, salt = salt, info = "VNX4 " ‖ label)` with these labels:

| label | length | use |
|---|---|---|
| `aead key` | 32 | AES-256-GCM key |
| `mac key` | 32 | HMAC of the manifest |
| `chunk id key` | 32 | keyed chunk IDs |
| `key check` | 16 | stored in clear as `encryption.key_check` (hex); a wrong key is detected before any decryption |

**AEAD.** nonce = `domain (4) ‖ index (8)`, and associated data = `"VNX4 aead\0" ‖ archive_id (16) ‖ domain (4) ‖
index (8) ‖ count (8)`.

| domain | item | index | count |
|---|---|---|---|
| 0 | stored chunk | chunk index | 0 (streamed; the number of chunks is authenticated through the manifest MAC) |
| 1 | file table | 0 | 1 |
| 2 | reference table | 0 | 1 |

Nonces never repeat under one key: the key is derived from a fresh random salt, and each (domain, index) pair is
used once. The archive ID of an encrypted archive is 16 random bytes.

## 7. Manifest

Canonical JSON: UTF-8/ASCII, keys sorted, separators `,` and `:`, no whitespace, no floats, no NaN, no duplicate
keys. The bytes MUST equal their own canonical re-serialisation. Fields (all required):

| field | type / value |
|---|---|
| `format` | `"VNX4"` |
| `format_version` | `[4, 0]` (major MUST equal 4; minor MUST be ≤ the reader's) |
| `archive_id` | 32 lowercase hex characters (16 bytes). Unencrypted: `SHA-256("VNX4 archive-id\0" ‖ options ‖ entry list)[:16]` (deterministic); encrypted: random |
| `created_at` | `null` (reserved; a timestamp would break determinism) |
| `encoder` | `{"name": "vnxdna", "version": "4.0.0"}` (informational) |
| `required_features` | sorted list from: `vnx4-container`, `chunk-fixed`, `merkle-rfc6962-sha256`, `dedup-content-address`, `zstd`, `aes-256-gcm`, `kdf-hkdf-sha256`, `kdf-scrypt`. An unknown feature MUST be rejected as unsupported |
| `chunking` | `{"algorithm": "fixed", "chunk_size": 4096 … 67108864}` |
| `compression` | `{"algorithm": "zstd" or "none", "level": int, "policy": "keep-if-smaller"}` |
| `encryption` | `{"algorithm": "none"}`, or `{"algorithm": "AES-256-GCM", "kdf": "key-file-hkdf-sha256" or "scrypt-hkdf-sha256", "salt": hex32, "key_check": hex32, "scrypt": {"n","r","p"} (scrypt only)}` |
| `counts` | `{"files", "chunks" (unique stored chunks), "chunk_refs", "content_bytes" (sum of file sizes), "stored_bytes" (= B)}` |
| `tables` | `{"chunk_table": {"entry_bytes": 84, "entries", "sha256"}, "file_table": {"bytes", "sha256"}, "refs": {"bytes", "sha256"}}`; the SHA-256s are over the bytes **as stored** (sealed when encrypted) |
| `integrity` | `{"merkle": "rfc6962-sha256", "leaf": "chunk-table-entry", "merkle_root": hex64}` |
| `extensions` | object; the only open field. Unknown keys inside it MUST be ignored, and it is covered by the manifest MAC |

**Merkle tree** (RFC 6962 / RFC 9162). leaf_i = `SHA-256(0x00 ‖ chunk-table entry i (84 bytes))`, and an interior
node = `SHA-256(0x01 ‖ left ‖ right)`. A tree of n > 1 leaves splits at the largest power of two k < n. The empty
tree hashes to `SHA-256("")`. Audit paths follow RFC 9162 §2.1.3. `vnx verify --chunk i` checks one chunk against
`merkle_root` with ⌈log₂ n⌉ hashes.

**Integrity hierarchy.** file SHA-256 (file table) → chunk ID + stored SHA-256 (chunk table) → Merkle root
(manifest) → manifest (trailer MAC field) → whole-file SHA-256 (trailer).

## 8. Trailer (112 bytes)

| offset | size | field |
|---|---|---|
| 0 | 8 | B (body length) |
| 8 | 8 | C (chunk-table length) |
| 16 | 8 | F (file-table length as stored) |
| 24 | 8 | R (reference-table length as stored) |
| 32 | 8 | M (manifest length) |
| 40 | 32 | manifest authenticator: `SHA-256(manifest)` (clear) or `HMAC-SHA256(mac_key, "VNX4 manifest\0" ‖ manifest)` (encrypted) |
| 72 | 8 | trailer magic `VNX4END\0` |
| 80 | 32 | SHA-256 of every byte of the file before this field |

## 9. Validation rules (reader MUST reject)

1. File shorter than 128 bytes, a wrong magic, an unsupported major/minor version or nonzero flags.
2. A missing trailer magic (truncated or unfinished file), or `16 + B + C + F + R + M + 112 ≠ file size`,
   `C mod 84 ≠ 0`, `M = 0` or `M > 1 MiB`.
3. A manifest that is not canonical JSON, or is missing any field or has a wrong type in one; an unknown required
   feature; out-of-range chunk size or scrypt parameters.
4. A manifest authenticator mismatch (clear: SHA-256; encrypted: HMAC, checked after the key check).
5. A table length or SHA-256 that differs from the manifest; `counts.stored_bytes ≠ B`;
   `counts.chunks ≠ C / 84`.
6. A chunk table that does not tile the body, has plaintext size 0 or > `chunk_size`, stored size > plaintext
   (+16 when encrypted), a codec the manifest does not allow, or nonzero reserved bytes.
7. A Merkle root ≠ `integrity.merkle_root`.
8. With the file table readable (clear, or decrypted with the key): reference values ≥ number of chunks; a
   reference table length ≠ `4 · counts.chunk_refs`; any path, order, type, count, contiguity or size rule of §4
   violated; unreferenced chunks; file sizes not adding up to `counts.content_bytes`.
9. On content access: stored SHA-256 mismatch, AES-GCM failure, zstd output longer than the table's plaintext size
   (decompression MUST be bounded by that size), decoded size ≠ plaintext size, chunk-ID mismatch, file SHA-256
   mismatch. Extraction writes a temporary file and renames it into place **only** after the file SHA-256 verifies.

`vnx verify` (full) additionally recomputes the trailer's whole-file SHA-256 and every file's SHA-256.

## 10. Strand frame (frame version 4)

A frame carries one outer-code symbol of P bytes.

| offset | size | field |
|---|---|---|
| 0 | 1 | scrambler variant v (transmitted unscrambled) |
| 1 | 1 | `(4 << 4) │ kind`: kind 0 = data symbol, 1 = superblock symbol |
| 2 | 2 | archive tag = `archive_id[0:2]` |
| 4 | 4 | group (outer-code block) index |
| 8 | 2 | symbol index within the group |
| 10 | P | payload |
| 10+P | 4 | CRC-32 of the **unscrambled** bytes 1 … 9+P (header without the variant, plus payload) |
| 14+P | r | inner Reed–Solomon parity over bytes 0 … 13+P **as transmitted** |

* **Scrambling.** Bytes 1 … 13+P are XORed with `SHAKE-128("VNX4 scrambler" ‖ byte(v))`, taking the first 13+P
  bytes. The encoder uses the smallest v ∈ [0, 255] whose final DNA strand (§11) satisfies the configured
  constraints. If no v works, encoding fails; an unscreened strand is never emitted.
* **Inner code.** Systematic RS(n = 14+P+r, k = 14+P) over GF(2⁸) with primitive polynomial 0x11D, generator 2
  and first consecutive root 0. This is identical to V3 and to `reedsolo`. Requires n ≤ 255 and an even r ≤ 64.
* **Acceptance.** A received frame is accepted iff (a) it passes the CRC as received with no flagged erasures, or
  (b) bounded-distance RS decoding succeeds (2e + f ≤ r) and the CRC then verifies. In both cases the version
  nibble must be 4 and kind ≤ 1.

## 11. DNA mapping and synchronisation markers

* **Bits → bases.** Each frame byte becomes 4 bases, most significant bit pair first: `00 A, 01 C, 10 G, 11 T`.
  The frame occupies `4·(14+P+r)` bases.
* **Markers.** With `marker_period = S` (a multiple of 4, 8 … 256) and `marker_len = ℓ`, a marker is inserted after
  each complete run of S frame bases, except after the final run. Marker k (k = 0, 1, …) is
  `TABLE_ℓ[k mod 4]`:

  | ℓ | table |
  |---|---|
  | 1 | A, C, G, T |
  | 2 | AC, GT, CA, TG |
  | 3 | ACG, TGC, CAT, GTA |
  | 4 | ACGT, TGCA, CATG, GTAC |
  | 5 | ACGTC, TGCAG, CATGA, GTACT |
  | 6 | ACGTCA, TGCAGT, CATGAC, GTACTG |

  Strand length = `4·n + ℓ·(⌈4n / S⌉ − 1)`. `S = 0, ℓ = 0` means no markers.
* **Profiles** (layout; outer code K + M):

  | profile | P | r | S | ℓ | frame bytes | strand nt | outer |
  |---|---|---|---|---|---|---|---|
  | `v4-balanced` | 40 | 16 | 24 | 3 | 70 | 313 | Cauchy RS 64 + 16 |
  | `v4-dense` | 44 | 12 | 0 | 0 | 70 | 280 | Cauchy RS 64 + 16 |
  | `v4-indel` | 36 | 20 | 24 | 3 | 70 | 313 | Cauchy RS 48 + 16 |
  | `v4-archival` | 36 | 20 | 24 | 3 | 70 | 313 | Cauchy RS 32 + 32 |

  Several profiles can share a strand length (v4-balanced, v4-indel and v4-archival are all 313 nt). Readers that
  auto-detect the layout MUST disambiguate by verifying frames, and the superblock's layout fields are authoritative.
  (Round 1 of the V4 experiments used v4-balanced = P 40, r 16, S 32, ℓ 2, 296 nt; results record the layout used.)
* **Reverse complement.** Reads can arrive reverse-complemented (A↔T, C↔G, reversed). Readers SHOULD try both
  orientations.

## 12. Superblock (version 1)

The superblock makes a strand pool self-describing. It is 96 bytes:

| offset | size | field |
|---|---|---|
| 0 | 6 | magic `VNX4SB` |
| 6 | 1 | superblock version `1` |
| 7 | 1 | outer code: `1` cauchy-rs, `2` lt-fountain (EXPERIMENTAL) |
| 8 | 2 | K (data symbols per full group) |
| 10 | 2 | M (parity symbols, or repair droplets, per full group) |
| 12 | 2 | P |
| 14 | 1 | r |
| 15 | 1 | marker period S / 4 |
| 16 | 1 | marker length ℓ |
| 17 | 1 | fountain distribution: `0` dense, `1` robust-soliton |
| 18 | 4 | fountain seed |
| 22 | 16 | archive ID |
| 38 | 8 | container size |
| 46 | 32 | container SHA-256 (the whole `.vnx` file) |
| 78 | 8 | index offset = 16 + B (start of the chunk table) |
| 86 | 4 | group count = ⌈container size / (K·P)⌉ (MUST match) |
| 90 | 2 | reserved, zero |
| 92 | 4 | CRC-32 of bytes 0 … 91 |

The 96 bytes are zero-padded to `Ks·P` bytes with `Ks = ⌈96 / P⌉`. They are cut into Ks symbols and Cauchy-RS
encoded with `Ms = 3·Ks` parity symbols, so any Ks of the 4·Ks superblock strands suffice. Superblock frames use
kind 1, group 0 and symbol index 0 … 4Ks−1. A decoder MUST check that the superblock's layout matches the layout
used to parse the reads, and that `archive_id[0:2]` equals the frames' tag.

## 13. Groups and outer codes

Group g covers container bytes `[g·K·P, (g+1)·K·P)`. Its source symbols are those bytes in P-byte rows; the last
group is zero-padded and holds `k_g = ⌈(size − (G−1)·K·P) / P⌉` source symbols (every other group has K).

* **cauchy-rs.** Symbols 0 … k_g−1 are the source symbols and k_g … k_g+M−1 are parity. Parity is
  `C · d` over GF(2⁸)/0x11D with the Cauchy matrix `C[i][j] = 1 / ((K + i) ⊕ j)` (i < M, j < K) for a *full*
  group. A short group is the shortened code: the missing data rows are zeros and are not transmitted. Any k_g of
  the k_g + M symbols decode (MDS). This is the V3 code ([ECC.md](ECC.md)).
* **lt-fountain** (EXPERIMENTAL). Symbols 0 … k_g−1 are the source symbols. Droplet j ≥ k_g is the XOR of the
  source symbols in N(g, j). Let `t = "VNX4 LT\0" ‖ seed (4) ‖ g (4) ‖ j (4)`.
  * *dense:* source i ∈ N iff bit i of `SHAKE-128(t)` is 1, MSB-first within each byte (`i = 8·byte + (7 − bit)`).
    If no bit is set, N = {j mod k_g}.
  * *robust-soliton:* `h = SHA-256(t)`. Degree = 1 + searchsorted(CDF, `h[0:8] / 2⁶⁴`) for Luby's robust-soliton
    CDF with c = 0.05, δ = 0.5. Neighbours come from an xorshift64* stream seeded with `h[8:16] | 1` (shifts 12,
    25, 27; multiplier 0x2545F4914F6CDD1D; value mod k_g; duplicates rejected).

  Droplets per group: `k_g + M` for full groups and `k_g + max(1, ⌈M·k_g / K⌉)` for the last one.

## 14. Strand files

FASTA or FASTQ, one strand per record. The header `vnx4|<tag hex4>|<kind>|<group>|<symbol>` is informational only;
**decoders never trust headers**. Records are written in this order: superblock strands, then groups 0, 1, … with
symbols in index order. The records for group g therefore start at `4·Ks + g·(K+M)` (all groups before the last
are full). `vnx locate --dna-profile` prints these ranges. Read files for decoding can be FASTA (single- or
multi-line), FASTQ (four-line records, Phred+33) or plain text with one sequence per line. A read longer than
100,000 nt MUST be rejected. BAM is not supported.

## 15. Compatibility and versioning rules

* A new **minor** container version can only add optional manifest content inside `extensions` or new optional
  features. A new required behaviour MUST be a new entry in `required_features`, which older readers refuse.
* A new **major** version changes the magic or major field. A 4.x reader refuses it.
* Frame version 4 is fixed by the version nibble. Another frame layout needs a new nibble value.
* The superblock version byte governs §12. Unknown versions MUST be refused.
* V3 (format 5) and V4 (VNX4) are separate formats. Converting means extracting with one tool and archiving with the
  other: `vnx-dna restore`, then `vnx archive`.
