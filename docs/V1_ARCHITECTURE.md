# VNX-DNA architecture

> **V1 reference (archive format 4).** This document describes VNX-DNA 1.0.0, which V2 and V3 still read. For the current architecture see [ARCHITECTURE.md](ARCHITECTURE.md).

VNX-DNA is a CPU-only Python reference implementation of the digital side of DNA data storage. It turns a file
into DNA strand sequences, lets you damage those sequences in a controlled channel model, and recovers the exact
original bytes. Every step is verified. **It is software only**: no sequence it produces has been synthesised or
sequenced, and nothing here is evidence of wet-lab performance (see [Scientific scope](#scientific-scope)).

## Lifecycle

```
 user file
    │  vnx-dna store                                  container layer (vnxdna.container)
    ▼
 chunking ─▶ compression ─▶ AES-256-GCM (optional) ─▶ manifest (canonical JSON, digest / HMAC)
    │                                                  └─ written as a .vxdna file
    │  vnx-dna encode                                 storage layer (vnxdna.storage, vnxdna.ecc, vnxdna.dna)
    ▼
 striping ─▶ outer Cauchy RS (K+M) ─▶ strand frame (in-band address + CRC-32 + inner RS)
    ─▶ scrambler/constraint screening ─▶ A/C/G/T mapping ─▶ FASTA strand pool (+ metadata strands)
    │  vnx-dna simulate (optional)                    channel layer (vnxdna.channel)
    ▼
 dropout ─▶ coverage ─▶ bursts ─▶ substitutions/deletions/insertions ─▶ orientation ─▶ shuffle
    │  vnx-dna decode                                 recovery (vnxdna.storage.decoder, vnxdna.sync)
    ▼
 geometry discovery ─▶ per-read CRC / RC / inner RS (/ experimental indel realignment)
    ─▶ duplicate resolution ─▶ manifest from metadata strands ─▶ outer erasure decoding ─▶ .vxdna
    │  vnx-dna restore                                verification (vnxdna.container.reader)
    ▼
 manifest authentication ─▶ stored-chunk SHA-256 ─▶ AES-GCM tag ─▶ bounded decompression
    ─▶ chunk SHA-256 ─▶ whole-object SHA-256 (recomputed) ─▶ atomic write of the original file
```

`decode(encode(container)) == container` holds byte for byte, and `restore(store(file)) == file` holds. Both are
asserted by property tests.

## Package layout

| module | responsibility |
|---|---|
| `vnxdna.cli` | Typer CLI: argument parsing, human/JSON output and the exit-code contract. No business logic. |
| `vnxdna.api` | Public API: `store`, `encode`, `simulate`, `decode`, `restore`, `recover`, `verify`, `info`, `extract`, `pipeline`. |
| `vnxdna.container.builder` | chunking, compression, encryption and manifest creation (`StoreOptions`) |
| `vnxdna.container.manifest` | manifest schema (pydantic, strict), canonical JSON, semantic checks, feature flags |
| `vnxdna.container.crypto` | HKDF key separation, AES-256-GCM, HMAC-SHA256, key check |
| `vnxdna.container.compression` | zlib / zstd / none, decompression bounded by the authenticated size |
| `vnxdna.container.vxdna` | the `.vxdna` single-file container and atomic writes |
| `vnxdna.container.reader` | manifest authentication and the verification chain, independent of the chunk source |
| `vnxdna.ecc.gf256` / `cauchy` / `inner_rs` | GF(2⁸) arithmetic, outer MDS erasure code, per-strand RS |
| `vnxdna.dna.mapping` | binary↔DNA mappings (2bit, rotation3, codebook8) |
| `vnxdna.dna.constraints` | biological/physical *screening rules* (GC, homopolymers, motifs), separate from mapping |
| `vnxdna.dna.strand` | strand frame format 4: in-band identity, CRC-32, inner RS, scrambler screening |
| `vnxdna.dna.reads` | FASTA / FASTQ / plain read I/O (headers are never trusted) |
| `vnxdna.storage.encoder` | container → strand pool (striping, shortening, metadata strands, efficiency accounting) |
| `vnxdna.storage.decoder` | reads → validated shards → stored chunks (random access by chunk) |
| `vnxdna.sync.indel` | **experimental** RS-assisted indel realignment (opt-in) |
| `vnxdna.channel` | deterministic synthetic channel with event log |
| `vnxdna.legacy.v0_1` | explicit read-only decoder for V0.1 archives |
| `vnxdna.bench`, `vnxdna.provenance` | benchmarks and environment capture |

The concerns the directive lists map onto these modules as follows. The container format is `container.vxdna`
together with `container.manifest`. Compression, cryptography and metadata each have their own module (`compression`,
`crypto`, `manifest`). Chunking lives in `builder`. Redundancy and ECC are split between `storage.encoder` and `ecc.*`.
DNA encoding is `dna.mapping`, and biological constraints are `dna.constraints`. Channel simulation is `channel`,
synchronization is `sync`, and decoding plus recovery are `storage.decoder`. Integrity verification is
`container.reader` together with `api.verify`. Random access is `ContainerReader.read_chunks` fed by
`ReadsSource`. The CLI is `cli`.

## Architecture decisions

| # | decision | reason |
|---|---|---|
| AD-1 | **Cauchy Reed–Solomon** outer code, systematic `[I; C]` over GF(2⁸)/0x11D | Provably MDS (every square Cauchy submatrix is invertible), unlike the V0.1 generator. Systematic, so undamaged data needs no decoding. |
| AD-2 | Two-level coding: CRC-32 + inner RS per strand, outer erasure code across strands | The inner code fixes substitutions inside a strand. The CRC turns every other failure into an *erasure*, which the outer code recovers at full MDS efficiency. |
| AD-3 | All addressing is inside the DNA (frame format 4) | FASTA headers are not part of a biological channel. |
| AD-4 | Manifest stored twice: in the `.vxdna` file and as metadata strands (Cauchy 8+8) | A FASTA pool alone is a complete archive. The metadata stripes tolerate 50 % loss, so they are more robust than data stripes at the default 20 %. |
| AD-5 | Chunks are compressed/encrypted independently and occupy whole stripes | Enables random access (decode only one chunk's stripes) and bounds the damage from a lost stripe to one chunk. |
| AD-6 | Shortened last stripe per chunk | Small files and chunk tails do not pay for a full stripe of padding strands, and the code stays MDS. |
| AD-7 | Encryption before ECC | ECC/decoding never needs the key, and `decode` recovers ciphertext exactly. Authentication covers everything the key protects. |
| AD-8 | AES-256-GCM + HKDF + HMAC over a canonical manifest (from `cryptography`) | Established primitives only. Fresh salt and archive ID per encrypted archive give nonce uniqueness. |
| AD-9 | Strict schema, `required_features`, and a single open `extensions` object | Old decoders reject what they cannot interpret instead of guessing. |
| AD-10 | Indel repair is experimental and opt-in | Reed–Solomon does not correct indels. Realignment is validated for single indels only. |
| AD-11 | Pure-Python/numpy CPU reference; no GPU path | Profiling (docs/BENCHMARKS.md) shows modest data sizes are fast enough. Correctness must not depend on accelerators. |
| AD-12 | Legacy V0.1 support is a separate, read-only decoder with explicit dispatch | V0.1 archives are never interpreted by the new decoder, or the other way round. |

## Where each guarantee comes from

| property | mechanism | where checked |
|---|---|---|
| any M lost strands of a stripe are recoverable | Cauchy MDS code (+ shortening) | `ecc/cauchy.py`, `tests/unit/test_ecc_cauchy.py` (exhaustive), `tests/integration/test_full_dna_storage.py` (worst case) |
| substitutions inside a strand | inner RS, `2e + f ≤ nsym` | `dna/strand.py`, `tests/unit/test_strand.py` |
| no corrupt strand is accepted as valid | CRC-32 re-check after correction (≈2⁻³² per corrupt read) | `dna/strand.py` |
| no wrong output is ever written | stored SHA-256 → AEAD → plaintext SHA-256 → object SHA-256, then an atomic write | `container/reader.py`, fuzz tests |
| confidentiality and tamper detection (with a key) | AES-256-GCM per chunk, HMAC-SHA256 manifest | `container/crypto.py` |
| deterministic output (unencrypted) | content-derived archive ID, deterministic screening | property tests |

## Scientific scope

Everything here is computational. The constraint layer applies common *heuristics* (GC range, homopolymer limit,
forbidden motifs). It is not a synthesis-vendor specification, and it models no secondary structure, primer design or
biochemistry. The channel is a simple i.i.d. model with bursts, not a model fitted to any sequencing platform. See
[DNA_CODEC.md](DNA_CODEC.md) and [CHANNEL_MODEL.md](CHANNEL_MODEL.md).
