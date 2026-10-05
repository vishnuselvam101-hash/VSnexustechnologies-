# VNX-DNA specification, version 6.0 (DRAFT)

- Document: `VNX-DNA-SPEC` 6.0, draft of 2026-10-05, V6 directive Phase 1 (architecture and formal specification).
- Base: `work/v6-spec` at 081697b, which is the same commit as `build/v6-p1`. File and line citations refer to this commit.
- Scope: every on-disk and in-DNA format that VNX-DNA 6.x writes or reads, the version axes, the canonical encode and decode
  pipelines, conformance, correction limits, interoperability mappings and the physical-provider interface.
- Companion document: [`docs/V6_ARCHITECTURE.md`](../V6_ARCHITECTURE.md) (packages, public API, schemas, migration, phases).
- This document **does not replace** [`docs/VNX4_FORMAT.md`](../VNX4_FORMAT.md) (container 4.0, frame 4, superblock 1) or
  [`docs/V6_OUTER_CODE.md`](../V6_OUTER_CODE.md) (superblock 2). Those stay normative for what they define. This document
  refers to them by section (for example "VNX4 §12") and states every field that changes or is added.

## 0. Conventions and status labels

- Integers are big-endian and unsigned unless stated otherwise. `‖` means concatenation. "MUST reject" means: raise a typed
  error with the code given in §10 and publish nothing. The keywords MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.
- Every statement carries one of these status labels, either inline or in its section heading:

| Label | Meaning |
|---|---|
| **IMPLEMENTED** | Existing, tested behaviour at 081697b. A file:line citation is given. |
| **SPECIFIED (V6 Phase n)** | Normative for 6.x. Not implemented yet; the phase named in `V6_ARCHITECTURE.md` §8 implements it. |
| **SPECIFIED, NOT IMPLEMENTED (THEORETICAL)** | Normative layout, fixed now so that later versions (V7/V8) are additive. No code exists yet, and every number about its behaviour is arithmetic, not measurement. |
| **RESERVED** | A value or field that MUST NOT be used until a later spec version defines it. |
| **SIMULATED** | A result measured on software strands and a software channel. No DNA has been synthesised, stored or sequenced by VNX-DNA. |

- Evidence sources: the V6 Phase 0 static audit (lab file `results/v6-audit/INVENTORY.md`, cited as "audit §n"), the baseline
  note `research/2026-10-05-vnx-baseline-and-readiness.md` (cited as "baseline"), and the research-gate documents on branch
  `work/research-gate`: `docs/COMPETITIVE_GAP_ANALYSIS.md` ("gap §n"), `docs/VNX_GLOBAL_ROADMAP.md` ("roadmap") and
  `docs/VNX_PRODUCT_STRATEGY.md` ("strategy §n"), plus `research/competitive-2026-10-05/30-companies.md` ("30-co §n").

## 1. Terminology and version axes

| Term | Definition |
|---|---|
| container | A `.vnx` file in the VNX4 container format (VNX4 §1–§9). |
| frame | The byte string that carries one outer-code symbol plus address, CRC and inner parity (VNX4 §10; frame 6 in §3.5). |
| strand | The nucleotide sequence of one frame, after scrambling, inner coding, 2-bit mapping, marker insertion and, where used, primer flanks (§3.6). |
| read | A sequenced (or simulated) copy of a strand, possibly erroneous, reverse-complemented or truncated. |
| pool | A set of strands from one or more archives that is handled together physically. |
| superblock | The self-describing metadata record carried in kind-1 frames (VNX4 §12, V6_OUTER_CODE §1, §3.8). |
| group / row | One outer-code block of K data and M parity symbols (VNX4 §13). |
| stripe | D data rows plus Mc column-parity rows (V6_OUTER_CODE §2). |
| profile | Overloaded today (audit §5.6). This spec uses **strand profile** (layout plus default outer code, VNX4 §11), **redundancy profile** (`v6/profiles.py:22-26`) and **performance profile** (`v4/config.py:33-40`), always qualified. |
| frame family | All frames scrambled in the `"VNX4 scrambler"` domain (frame 4, and frame 6 from §3.5). |

VNX-DNA versions nine things independently. Section 4 says where each one is recorded and how a reader handles an unknown value.

| Axis | Values today | Defined in |
|---|---|---|
| software (package) version | `5.0.0` at 081697b (`src/vnxdna/_version.py:2`); see §4.2 | `_version.py` |
| specification version | 6.0 (this document) | this document |
| container format | VNX4 4.0 (`v4/version.py`: `FORMAT_VERSION = (4, 0)`) | VNX4 §2, §7, §15 |
| strand frame version | 4 (`v4/version.py`: `FRAME_VERSION = 4`); 6 specified in §3.5 | VNX4 §10, §3 here |
| superblock version | 1 and 2 (`v4/encoder.py:41-42`); 3 specified in §3.8 | VNX4 §12, V6_OUTER_CODE §1, §3.8 here |
| codec identifier | derived, not stored: `f<frame>-sb<superblock>-<outer>` (§4.1) | §4.1 |
| channel-model version | per model `"version"` (e.g. `experiments/v6/channel/models/illumina-like.json:3`) plus a model schema version (§4.5) | §4.5 |
| provider-adapter version | none today; specified in §9 | §9 |
| report, event, result and manifest schema versions | none today (audit §4, §7.2); specified in §4.5 | §4.5, V6_ARCHITECTURE §5 |

## 2. Archive (container) format

### 2.1 What stays normative unchanged

**IMPLEMENTED, unchanged in 6.x:** the container layout, header, chunk table, file table, reference table, encryption,
manifest, trailer and validation rules of VNX4 §1–§9. That covers magic `\x89VNX4\r\n\x1a`, major 4, minor 0, flags 0, the
84-byte chunk entries, fixed chunking (4 KiB … 64 MiB), content-addressed chunk IDs, deduplication, zstd keep-if-smaller,
AES-256-GCM with HKDF-SHA256 subkeys, canonical-JSON manifest ≤ 1 MiB, the RFC 6962 Merkle tree and the 112-byte trailer.
A 6.x writer produces container version **4.0**. **No new container version is introduced in 6.0.**

### 2.2 Evaluation of the container fields (keep or change)

| Element | Assessment | Decision |
|---|---|---|
| Header (16 B), trailer (112 B), section order body → tables → manifest → trailer | Streaming write with one pass (VNX4 §1). The index sits at the end, so DNA-level selection must decode the tail groups first (`v4/decoder.py:1295-1297`). That is adequate at GB scale. At PB scale a flat chunk table is 80 GB (gap §10.2 step 2, THEORETICAL). | Keep. A hierarchical index is V8 work and needs a new superblock version (§3.8.4 reserves bytes for its root pointer). |
| Fixed chunking, 84-byte chunk entries, dedup by chunk ID | Correct and simple. Dedup works only for identical aligned chunks. Content-defined chunking would break the VNX4 §4 rule that every chunk except the last is `chunk_size` long. | Keep. CDC would need a new required feature (`chunk-cdc-*`), and older readers would refuse it. Deferred to V9. |
| zstd keep-if-smaller, compress before AEAD | Leaks each chunk's compressed size (`docs/SECURITY.md:11`). | Keep (documented leak). |
| AES-256-GCM per chunk, one master key per archive | Sound (VNX4 §6). Crypto-erasure of one object needs per-object key domains (strategy §13), which this layout cannot express. | Keep. Key domains are V9+ work and need a new required feature. |
| Manifest `encoder.version` | Informational (VNX4 §7). It says `"5.0.0"` on V6 output (audit §4). | Fix the value (§4.2). The field and its meaning do not change. |
| Manifest `archive_id`, unencrypted | Deterministic from options, paths, sizes, mode and mtime only, not from content (`v4/archive.py:216-222`). Two different datasets with equal names and sizes get the same ID, and so the same DNA tag (audit §5.5). | Add an opt-in content-derived ID (§2.3.2). The current derivation stays the default in 6.x, because changing it changes every default output byte. |
| Manifest `extensions` | The only open field. Readers ignore unknown keys, and the manifest MAC covers it (VNX4 §7). | Use it for writer provenance (§2.3.1). Readers lose nothing by ignoring it. |
| `required_features` | The correct mechanism for any new required behaviour (VNX4 §15). | Keep. 6.0 adds no required feature. |

### 2.3 Changes in 6.x

#### 2.3.1 Writer provenance in `extensions.vnx` (SPECIFIED, V6 Phase 2)

A 6.x writer MUST add this object to the manifest `extensions`:

```json
"extensions": {"vnx": {"spec": "6.0", "software": "6.0.0", "archive_id_derivation": "options-v1"}}
```

| Key | Type | Meaning |
|---|---|---|
| `spec` | string `"<major>.<minor>"` | Version of this specification that the writer implements. |
| `software` | string (PEP 440) | Package version. MUST equal `encoder.version`. |
| `archive_id_derivation` | `"options-v1"` \| `"content-v1"` \| `"random"` | How `archive_id` was produced (§2.3.2). `"random"` is used for every encrypted archive. |

- Readers MUST NOT reject an archive for the absence, presence or content of `extensions.vnx`. A 4.x or 5.x reader ignores it
  (VNX4 §7), so containers written by 6.x stay readable by 4.0 and 5.0.
- Adding this object changes the bytes of every new container. Archive-builder byte identity with 5.0.0 for default options
  is therefore given up deliberately, together with the version fix of §4.2. The **format** does not change. The test that
  pins the 5.0.0 container SHA-256 (`tests/v6/test_outer_pipeline.py:70-80`) must be restated; V6_ARCHITECTURE §8 Phase 2,
  item 2.4 describes how, and the founder has to approve it.

#### 2.3.2 Archive ID derivations (SPECIFIED, V6 Phase 6)

| Derivation | Definition | Status |
|---|---|---|
| `options-v1` | `SHA-256("VNX4 archive-id\0" ‖ options ‖ entries)[:16]`, exactly as `v4/archive.py:217-222` | IMPLEMENTED; the default |
| `content-v1` | `SHA-256("VNX6 archive-id\0" ‖ options ‖ merkle_root (32 B) ‖ SHA-256(file table as stored) (32 B))[:16]`, where `options` is the same UTF-8 string as in `options-v1` (`archive.py:219`) | SPECIFIED; opt-in (`ArchiveOptions.archive_id="content"`, `vnx archive --archive-id content`) |
| `random` | 16 bytes from the OS CSPRNG (`archive.py:202`) | IMPLEMENTED; required for encrypted archives (AEAD associated data, VNX4 §6) |

- `content-v1` can be computed after the body: for an unencrypted archive nothing before the manifest depends on
  `archive_id`, because the `Sealer` exists only for encrypted archives (`archive.py:213`). The implementer MUST verify this
  with a test that builds the same input under both derivations and compares the chunk table and body bytes.
- Identical inputs give identical IDs under `content-v1`. Mixing two such pools is harmless, because their strands are identical.
- A reader that sees `archive_id_derivation = "content-v1"` SHOULD recompute the ID in `vnx verify --full` and report a
  mismatch as the warning `ARCHIVE_ID_DERIVATION_MISMATCH`. It MUST NOT treat a mismatch as an integrity failure, because the
  ID is a name, not a digest. Integrity is covered by the trailer hash and MAC.

#### 2.3.3 Behaviour that 6.x MUST state explicitly (clarification, no format change)

- **Encrypted archive decoded without a key.** DNA decoding never decrypts (audit §2.5). It verifies the container SHA-256
  from the superblock and the container structure (`v4/decoder.py:1430-1442`), and then publishes the *ciphertext*
  container with status SUCCESS. 6.x MUST keep that behaviour, but the result MUST carry `"encrypted": true` and
  `"content_verified": false` (V6_ARCHITECTURE §4.3). Content is verified only when files are extracted with the key.
- **`vnx encode` on a non-container input** builds an archive with default `ArchiveOptions` (`v4/cli.py:296-300`), ignoring
  archive options (audit §5.4). In 6.x the SDK `encode()` MUST pass caller archive options through, or refuse them with
  `CONFIGURATION_ERROR`. It MUST NOT ignore them silently.

### 2.4 Deferred container work (not in 6.x; stated so that the 6.x layout does not block it)

- A hierarchical, partition-placed index whose root is referenced from a superblock (gap §10.2 step 5). Planned for V8.
- Append-only versions and tombstones (strategy §13). Planned for V9.
- Per-object key domains for crypto-erasure (strategy §13). Planned for V9.
- Content-defined chunking. Planned for V9.

Each of these MUST arrive either as a new required feature (container 4.x) or as a new container major version. None of them
may change how an existing 4.0 container is read.

## 3. Strand formats

### 3.1 Frame-family invariants (normative for frame 4 and every later frame in the `"VNX4 scrambler"` domain)

Frame 4 already satisfies these invariants (IMPLEMENTED: `v4/frame.py:177-188, 191-219, 168`). Every future frame version
MUST keep them, so that a reader can identify the frame version of a read without knowing its layout.

1. **I-1 Byte 0** is the scrambler variant `v`, transmitted unscrambled.
2. **I-2 Byte 1** is `(frame_version << 4) | flags`, XORed with byte 0 of `SHAKE-128("VNX4 scrambler" ‖ byte(v))`.
   SHAKE-128 is an extendable-output function, so byte 0 of the keystream is the same for every frame length.
3. **I-3** The first 8 nt of a frame's DNA (bytes 0 and 1) are never interrupted by a marker. This holds because
   `marker_period ≥ 8` (`v4/frame.py:70`). With primers (§3.6), these 8 nt directly follow the forward primer.
4. **I-4** The CRC-32 covers the unscrambled header without byte 0, plus the payload. Inner Reed–Solomon parity covers every
   byte before it, as transmitted.
5. **I-5** Accepting a frame always requires a CRC that verifies after any correction, and a known version nibble.

### 3.2 Frame-version registry (normative)

A frame is identified by the pair **(scrambler domain, version nibble)**, and never by the nibble alone: nibble 4 is already
used in two domains.

| Scrambler domain string | Nibble | Format | Reader | Status |
|---|---|---|---|---|
| `"VNX-DNA/4 scrambler"` | 4 | V1 frame format 4 (`docs/V1_FORMAT.md` §3; `dna/strand.py:15,53`) | `vnx-dna` (legacy) | IMPLEMENTED, frozen |
| `"VNX-DNA/5 scrambler"` | 5 | V2/V3 frame format 5 (`docs/V2_FORMAT.md` §3; `v2/frame.py:15,53`) | `vnx-dna` (legacy) | IMPLEMENTED, frozen |
| `"VNX4 scrambler"` | 4 | VNX4 frame 4 (VNX4 §10; `v4/frame.py:145`) | `vnx` | IMPLEMENTED |
| `"VNX4 scrambler"` | 5 | — | — | **RESERVED, never to be assigned** (avoids confusion with V3 "frame format 5") |
| `"VNX4 scrambler"` | 6 | frame 6 (§3.5) | `vnx` ≥ 7.0 | SPECIFIED, NOT IMPLEMENTED (THEORETICAL) |
| `"VNX4 scrambler"` | 0–3, 7–15 | — | — | RESERVED |

The directive asks for "frame version 5". The new frame is numbered **6** instead: in the shared history of `vnx-dna` and
`vnx`, nibble 5 already means the V3 frame, and a document, report or person saying "frame 5" would be ambiguous.

### 3.3 Evaluation of frame 4 against the V6 requirements

Frame 4 (VNX4 §10–§11): 10-byte header (variant 1, version/kind 1, tag 2, group 4, symbol 2), payload P, CRC-32, inner RS r.
For `v4-balanced`, P = 40 and r = 16, so the frame is 70 B, i.e. 280 nt plus 11 three-nt markers: **313 nt** (`v4/frame.py:127-135`).

| Element | Cost at v4-balanced | Assessment | Decision |
|---|---|---|---|
| Archive tag, 2 B = `archive_id[0:2]` | 2.9 % of the frame | With random IDs, the probability of a tag collision in one pool reaches 50 % at about √(2·ln2·2¹⁶) ≈ 301 archives (gap §10.2, THEORETICAL). Unencrypted IDs collide whenever names and sizes match (§2.2). The decoder pools the superblock symbols of all archives sharing a tag and makes one decode attempt per tag (`v4/decoder.py:757-771`). A collision therefore yields at most one superblock, and the data groups of both archives mix. The result fails closed (SHA-256), but it is reported as missing redundancy or a hash mismatch, never as a collision. | Keep for frame 4. Add (a) a pool-composition check that refuses equal tags (§3.9), (b) a pool-assigned tag in superblock 3 (§3.8), and (c) an ambiguity error (§3.10 step 7). |
| Group index, 4 B | 5.7 % | 2³² groups × K·P = 2³² × 2,560 B ≈ **11.0 TB** per archive at v4-balanced. Column-parity groups use the same index space (V6_OUTER_CODE §2). | Keep for archives below the limit. Frame 6 wide class for larger archives (§3.5). |
| Symbol index, 2 B | 2.9 % | Cauchy RS needs K + M ≤ 256, and the superblock needs 4·Ks ≤ 256, so one byte suffices. The second byte serves only the EXPERIMENTAL LT fountain (VNX4 §13). | Keep in frame 4 (it cannot change). Frame 6 uses 1 B and forbids LT. |
| Variant byte | 1.4 % | Needed for constraint screening by scrambler variant (`v4/frame.py:201-218`). | Keep. |
| CRC-32 | 5.7 % | Detects RS miscorrection and accepts clean frames without decoding (`frame.py:242-247`). Removing it would make false frames reach the outer code more often. They would still fail closed at the SHA-256, but availability would suffer. | Keep in every frame version. |
| Inner RS, r = 16 | 22.9 % | 2e + f ≤ 16 per frame (VNX4 §10). | Keep. r is a profile parameter. |
| Markers, 3 nt every 24 nt | 33 of 313 nt (10.5 %) | EXP-0009: 10/10 against 2/10 recoveries at 0.2 % insertions + 0.2 % deletions, coverage 1, for +5.8 % nucleotides (`frame.py:129-131`, SIMULATED). | Keep. Markers are a profile parameter. |
| Strand length, 313 nt | — | Fits 350-nt synthesis limits without primers. With two 20-nt primers it is 353 nt, over the 350-nt Twist/IDT pool maximum (strategy §5.4, gap §10.2 step 3). Public benchmarks use 110–157 nt (roadmap V7). | Keep for primer-less pools. Short profiles for vendor and primer use (§3.7). |
| No primer region | — | Molecular random access is impossible (gap §10.1). | Optional primer flanks as a layout element (§3.6). |

**Result.** Frame 4 is kept unchanged, and every frame-4 archive stays decodable. A new frame version is justified for two
needs that frame 4 cannot meet without changing its bytes:

- (N1) short strands with primers within vendor limits, which need a smaller header;
- (N2) archives above about 11 TB, and pools of more than about 300 randomly tagged archives, which need wider fields.

Both needs are V7/V8 roadmap items (roadmap V7 and V8 tables). Frame 6 is specified now so that those versions only add
code, and its status is **SPECIFIED, NOT IMPLEMENTED (THEORETICAL)**. The 6.x releases implement the reader-side dispatch
(§3.10), so that a 6.x reader refuses frame-6 pools correctly (exit 6, not retryable) instead of misreporting them.

### 3.4 Frame 4 (IMPLEMENTED; unchanged)

As VNX4 §10–§11. One clarification is added: the decoder's acceptance check `(plain[:,0] >> 4) == FRAME_VERSION`
(`v4/frame.py:274`) and `tentative_address` (`frame.py:292`) remain the reference behaviour for frame 4.

### 3.5 Frame 6 (SPECIFIED, NOT IMPLEMENTED — THEORETICAL; implementation in V7 for the compact class and V8 for the wide class)

#### 3.5.1 Layout

Let H be the header length of the address class (table below).

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | scrambler variant v (unscrambled) |
| 1 | 1 | `(6 << 4) │ flags`. flags bit 0 = kind (0 data, 1 superblock); bits 1–2 = address class A; bit 3 = 0 (RESERVED; MUST reject if set) |
| 2 | H − 3 | address: tag ‖ group ‖ (width per class) |
| H − 1 | 1 | symbol index (0 … 255) |
| H | P | payload |
| H + P | 4 | CRC-32 (IEEE, the `zlib.crc32` polynomial) of the **unscrambled** bytes 1 … H − 1 + P |
| H + P + 4 | r | inner RS parity over bytes 0 … H + P + 3 **as transmitted** |

| A | Name | Tag | Group | Symbol | H | Groups per archive | Status |
|---|---|---|---|---|---|---|---|
| 0 | compact | 1 B | 3 B (u24) | 1 B | 7 | 2²⁴ | SPECIFIED (V7) |
| 1 | wide | 4 B | 6 B (u48) | 1 B | 13 | 2⁴⁸ | SPECIFIED (V8) |
| 2, 3 | — | — | — | — | — | — | RESERVED; MUST reject |

- **Scrambling.** Bytes 1 … H + P + 3 are XORed with the first H + P + 3 bytes of `SHAKE-128("VNX4 scrambler" ‖ byte(v))`.
  This is the same domain as frame 4 (invariant I-2). Variant selection is as in VNX4 §10: the smallest v whose final strand,
  **including primer flanks** (§3.6), satisfies the constraints. An unscreened strand MUST NOT be emitted.
- **Inner code.** Systematic RS(n = H + P + 4 + r, k = H + P + 4) over GF(2⁸), primitive polynomial 0x11D, generator 2,
  first consecutive root 0, as in frame 4. Requires n ≤ 255 and an even r with 2 ≤ r ≤ 64.
- **Acceptance.** As VNX4 §10, plus: the nibble MUST be 6, flags bit 3 MUST be 0, and A MUST equal the superblock's
  address class (§3.8.3, byte 18).
- **Outer code.** Only `cauchy-rs` is allowed (K + M ≤ 256). The LT fountain is forbidden with frame 6, as it already is
  with superblock 2 (V6_OUTER_CODE §1).
- **Mapping and markers.** As VNX4 §11 (2 bits per nt, marker tables ℓ = 1 … 6).

#### 3.5.2 Why two classes and not one variable-width header

- One fixed wide header costs short strands too much: 13 + 4 bytes of a 36-byte frame is 47 %.
- A per-strand length field would add bytes and parsing states.
- Two classes, chosen per archive and confirmed by the superblock, keep the reader simple. The header length comes from
  2 bits that every reader can probe (I-2).

### 3.6 Strand layout elements: primer flanks (SPECIFIED, NOT IMPLEMENTED — THEORETICAL; V7)

```
strand = [F (Lf nt)] ‖ body ‖ [Rrc (Lr nt)]
body   = frame DNA with markers (VNX4 §11)
Rrc    = reverse complement of the reverse primer
```

- Primers are an optional layout element. Superblock 3 (§3.8) records them as Lf, Lr and a primer ID. A superblock-1/2
  archive has no primers (Lf = Lr = 0).
- If an archive has primers, every strand carries them, superblock strands included, so that PCR selection of a partition
  also retrieves its superblock.
- Lengths: Lf, Lr ∈ {0} ∪ [15, 32]. The primer ID is the first 4 bytes of `SHA-256("VNX primers\0" ‖ F ‖ 0x00 ‖ R)`, with
  F and R as ASCII A/C/G/T.
- The primer sequences themselves are **not** stored in the superblock. A reader needs them before decoding, so they travel in
  the export and import package manifests (§9.3). The decoder checks the hash.
- Encoder constraint screening MUST run on the complete strand, so that the primer–body junctions also satisfy the GC,
  homopolymer and motif rules.
- Primer design, orthogonality and payload-collision checks are a V7 primer module. This spec fixes only the layout.

### 3.7 Strand-profile registry

**Frame 4 profiles (IMPLEMENTED; `v4/frame.py:127-135`, VNX4 §11).** v4-balanced 313 nt, v4-dense 280 nt, v4-indel 313 nt,
v4-archival 313 nt.

**Frame 6 candidate profiles (THEORETICAL).** Every number below is arithmetic. Markers use S = 24 and ℓ = 3 where present.
Strand length is `4·F + ℓ·(⌈4F/S⌉ − 1)`, and F = H + P + 4 + r.

| Candidate | A | P | r | Markers | F (B) | Body nt | With 2 × 20-nt primers | nt per payload byte (with primers) | Archive capacity at K = 64 |
|---|---|---|---|---|---|---|---|---|---|
| `f6-s255` | compact | 32 | 14 | 24/3 | 57 | 228 + 27 = **255** | **295** (≤ 300) | 9.22 | 2²⁴ × 2,048 B ≈ 34.4 GB |
| `f6-s160m` | compact | 15 | 10 | 24/3 | 36 | 144 + 15 = **159** | **199** | 13.3 | 2²⁴ × 960 B ≈ 16.1 GB |
| `f6-s160d` | compact | 21 | 8 | none | 40 | **160** | **200** | 9.52 | 2²⁴ × 1,344 B ≈ 22.5 GB |
| `f6-w313` | wide | 37 | 16 | 24/3 | 70 | **313** | (353: over the vendor limit) | 8.46 (no primers) | 2⁴⁸ × 2,368 B ≈ 6.7 × 10¹⁷ B |

For comparison, frame 4 v4-balanced uses 313/40 = 7.83 nt per payload byte without primers.

- **Freeze rule (normative).** A candidate profile gets a registered name only after the experiment EXP-F6-1
  (V6_ARCHITECTURE §9) has measured it, and only together with its golden fixtures. Until then, its parameters MAY change
  and no encoder may write it.
- An encoder MUST select a wide-class profile only if the archive needs more than 2³² total groups, or if the user asks for
  it explicitly. A wide frame costs 3 bytes more than frame 4 for the same frame length.

### 3.8 Superblock registry

| Version | Size | Used with | Status |
|---|---|---|---|
| 1 | 96 B | frame 4 | IMPLEMENTED (VNX4 §12; `v4/encoder.py:138-196`) |
| 2 | 96 B | frame 4, V6 outer code | IMPLEMENTED, opt-in (V6_OUTER_CODE §1; `encoder.py:141-144, 185-196`) |
| 3 | 128 B | frame 4 or frame 6 | SPECIFIED, NOT IMPLEMENTED (THEORETICAL; V7) |
| 0, 4–255 | — | — | RESERVED; MUST reject as unsupported |

#### 3.8.1 Superblock 1 and 2: assessment

- The CRC-32 and the Cauchy (Ks, 3Ks) code, with any Ks of 4·Ks strands, are adequate (VNX4 §12).
- Limits that superblock 3 removes:
  - the group count is a u32;
  - the tag is tied to `archive_id[0:2]` (VNX4 §12, last sentence; `decoder.py:770`);
  - there is no writer spec or software version;
  - there are no primer fields.
- Superblocks 1 and 2 stay exactly as specified. Their unknown-version behaviour is IMPLEMENTED and kept:
  `Superblock.unpack` raises `VNXUnsupportedVersionError` (`encoder.py:160-162`), and the decoder re-raises it only if no
  other tag candidate decodes (`decoder.py:766-776`).

#### 3.8.2 Superblock coding (all versions)

- The superblock bytes are zero-padded to Ks·P with Ks = ⌈size / P⌉ and encoded with Cauchy RS (Ks, 3·Ks) (VNX4 §12).
- For superblock 3, size = 128, so Ks = ⌈128 / P⌉ (P = 40 gives Ks = 4, i.e. 16 strands).
- 4·Ks ≤ 256 MUST hold, which means P ≥ 2.
- Superblock frames use kind 1, group 0 and symbol indices 0 … 4·Ks − 1.

#### 3.8.3 Superblock 3 layout (128 bytes)

| Offset | Size | Field | Rule |
|---|---|---|---|
| 0 | 6 | magic `VNX4SB` | |
| 6 | 1 | superblock version `3` | |
| 7 | 1 | outer code | MUST be 1 (cauchy-rs) |
| 8 | 2 | K | 1 ≤ K; K + M ≤ 256 |
| 10 | 2 | M | |
| 12 | 2 | P | 1 … 200 (frame-4 range, `frame.py:64`) |
| 14 | 1 | r | even, ≤ 64 |
| 15 | 1 | marker period S / 4 | 0, or 2 … 64 |
| 16 | 1 | marker length ℓ | 0 if S = 0, else 1 … 6 |
| 17 | 1 | frame version | 4 or 6 |
| 18 | 1 | address class | 2 for frame 4 (tag 2, group 4, symbol 2); 0 or 1 for frame 6 |
| 19 | 1 | strand order | 0 sequential, 1 interleaved (V6_OUTER_CODE §3) |
| 20 | 2 | stripe depth D | 1 … 65535 |
| 22 | 1 | column parity Mc | 0 … 255; if Mc > 0 then D + Mc ≤ 256 |
| 23 | 1 | Lf (forward primer nt) | 0 or 15 … 32 |
| 24 | 1 | Lr (reverse primer nt) | 0 or 15 … 32; Lf = 0 iff Lr = 0 |
| 25 | 1 | reserved | 0 |
| 26 | 4 | primer ID | 0 iff Lf = 0 (§3.6) |
| 30 | 4 | pool tag | the frame tag, right-aligned. High bytes beyond the class's tag width MUST be 0 |
| 34 | 16 | archive ID | |
| 50 | 8 | container size | |
| 58 | 32 | container SHA-256 | |
| 90 | 8 | index offset | ≤ container size |
| 98 | 8 | data group count G | = ⌈size / (K·P)⌉; G + S·Mc < 2^(8·group width) |
| 106 | 1 | spec major | e.g. 7 |
| 107 | 1 | spec minor | |
| 108 | 1 | software major | |
| 109 | 1 | software minor | |
| 110 | 2 | software patch | |
| 112 | 12 | reserved | MUST be 0 (a later version may define an index-root pointer here, §2.4) |
| 124 | 4 | CRC-32 of bytes 0 … 123 | |

- Superblock 3 is a superset of superblock 2. D = 1 with Mc = 0 is the plain V4 row geometry.
- The **pool tag** replaces the rule "tag = `archive_id[0:2]`". By default the encoder sets it to the leading tag-width bytes
  of `archive_id`. A pool composer (§3.9) MAY assign it, so that tags in a pool are unique by construction.
- A reader MUST check that the tag of every frame it groups under this superblock equals the pool tag.
- A reader MUST reject a superblock 3 as `FORMAT_ERROR` if any rule in the table fails. A CRC-valid but inconsistent
  superblock is a format error for that candidate, and the decoder moves on to the next one (as `encoder.py:174-175`).

### 3.9 Address space and pools (normative rules; arithmetic is THEORETICAL)

| Configuration | Max total groups | Capacity at the given K·P | Tag space |
|---|---|---|---|
| frame 4 (all superblocks) | 2³² | 11.0 TB (v4-balanced, 2,560 B/group); 4.95 TB (v4-archival, 1,152 B) | 2¹⁶ |
| frame 6 compact | 2²⁴ | 16–34 GB (table §3.7) | 2⁸ |
| frame 6 wide | 2⁴⁸ | ≈ 6.7 × 10¹⁷ B (f6-w313) | 2³² |

- **Archive size cap.** A single archive above the frame-6 wide capacity is out of scope. EB-scale logical stores are
  **pools of archives**, partitioned physically (containers or capsules) and molecularly (primer partitions) as gap §10.2
  steps 3–5 recommend. The pool and catalogue layer is V8/V9 work.
- **Pool composition rule (SPECIFIED, V6 Phase 7).** A tool that combines archives into one pool (the export package with
  several archives, §9.3) MUST refuse two archives whose frame tags are equal and whose primer IDs are equal, unless their
  container SHA-256s are also equal. It MUST report `ARCHIVE_TAG_COLLISION`. This needs no format change and protects
  frame-4 pools now.
- **Birthday bounds** for randomly drawn tags (50 % collision): 1 B ≈ 19, 2 B ≈ 301 and 4 B ≈ 77,163 archives per
  primer partition. Compact-class archives sharing a partition SHOULD use assigned pool tags.

### 3.10 Decode-side dispatch (normative)

**Status.** Steps 1–3 and 6 are SPECIFIED for V6 Phase 2. Steps 4–5 apply once frame 6 or primers exist. Step 7 is SPECIFIED
for V6 Phase 2. Today `detect_layout` (`v4/decoder.py:125-170`) does layout detection only. With a single length candidate
it returns without verifying any frame (`decoder.py:154-155`). That is why a pool of an unknown frame version ends as
"no superblock could be decoded", exit 5, retryable (`decoder.py:774-776`; audit §4).

1. **Sample.** Read up to 20,000 reads, as `detect_layout` does now.
2. **Probe.**
   - For each sampled read, in both orientations, and for each primer hypothesis (none, plus the primers named by the
     caller or the import manifest), take 8 nt at the frame start: offset 0, or Lf after a forward primer found within
     edit distance ⌊Lf/5⌋.
   - Map them to 2 bytes b0 b1.
   - For each domain in §3.2, compute `nib_d = (b1 ⊕ SHAKE-128(domain ‖ b0)[0]) >> 4`.
   - Build a histogram per domain.
3. **Candidates.** Candidates are every registered (frame version, strand profile) whose strand length, primers included,
   is within the alignment band of a length that holds at least 10 % of the sample. This is the existing rule
   (`decoder.py:146-150`), extended to frame-6 profiles.
4. **Verify.**
   - Score each candidate by how many sampled exact-length reads its own `decode_frames` accepts, in both orientations.
     This is the existing vote (`decoder.py:158-167`), now applied **always**, including when there is a single candidate.
   - The highest score wins if it is > 0.
5. **Primers.** If the winning candidate has primers, the reads are trimmed before pass 1 (stage D2, §5.2). Reads whose
   primer ID differs are dropped and counted as `other_partition`.
6. **Refusal when nothing verifies.** Let n be the number of sampled reads.
   - (a) If, in the `"VNX4 scrambler"` domain, one nibble value u ∉ {4, 6 if supported} holds ≥ 50 % of the probed reads,
     with n ≥ 64, the reader MUST raise `FRAME_VERSION_UNSUPPORTED`: exit 6, `retryable = false`, details
     `{frame_version: u, supported: [...]}`.
   - (b) Otherwise, if the `"VNX-DNA/5 scrambler"` domain gives nibble 5, or the `"VNX-DNA/4 scrambler"` domain gives
     nibble 4, for ≥ 50 % of probed reads, the reader MUST raise `LEGACY_FORMAT`: exit 6, hint "decode with `vnx-dna`".
   - (c) Otherwise it raises `LAYOUT_UNDETECTED`: exit 3, as `decoder.py:151-153, 168-169` do today.
   - For random sequences, each nibble value is expected at about 1/16 of reads, so the 50 % threshold gives a large margin.
     EXP-PROBE-1 measures the false-refusal rate, and its acceptance criterion is 0 false refusals on supported pools
     (V6_ARCHITECTURE §9).
7. **After pass 1.**
   - If pass 1 accepted no frame of the chosen version, the probe is re-run on all pass-1 tentative headers, and step 6
     decides before any superblock error is raised.
   - **Tag collision.** If a tag has superblock symbol conflicts, the reader MUST also decode the superblock from the
     minority values: any Ks mutually consistent conflicting symbols. If that yields a second valid superblock with a
     different container SHA-256, the reader MUST raise `ARCHIVE_TAG_AMBIGUOUS` (exit 3) and MUST NOT pick one.
     Today a tag gets exactly one decode attempt over pooled symbols (`decoder.py:757-771`), so a collision is never
     identified as such.
   - If the selected archive's data symbols conflict at more than 1 % of the addresses that have duplicates, the reader
     SHOULD add the hint "possible tag collision" to any later failure.
   - Superblock symbol conflicts are reported in the result, as now (`decoder.py:785`).

**Implementation notes (6.0.0.dev0, V6 Phase 2; `vnxdna.recovery.probe`).** Measured on the probe test pools and in
EXP-PROBE-1 (SIMULATED):

- *Consistency, not frequency.* Byte 1 is `(version << 4 | kind) ⊕ keystream(domain, b0)[0]`, and most strands use
  scrambler variant 0, so a structured pool shows one nibble in about half of its reads in every *foreign* domain too
  (0.39–0.57 measured; the V3 test pool shows nibble 4 in the `"VNX4 scrambler"` domain in 55 % of its reads). A domain
  therefore "shows" nibble u only if u holds ≥ 30 % of the reads *and* ≥ 30 % of the reads whose variant byte is not
  the most common one (own domain ≈ 1.0, foreign ≤ 0.1, random ≈ 1/8). 30 % instead of 50 %: noisy own-domain shares
  are 0.5–0.6 (nanopore-like).
- *n counts molecules.* Step 6 needs ≥ 64 distinct molecules (reads keyed by their first and last 24 nt), so coverage
  copies of a few random strands are not refused as a frame version.
- *Step 4 fallback.* If no exact-length read verifies, the vote is repeated through the sync path (marker alignment).
  If still nothing verifies but frame version 4 is evident, the best-fitting candidate is used instead of a refusal; the
  decoder verifies every frame as always (equal-length layouts are then indistinguishable, and such pools do not decode).

**Complexity.** The probe costs O(n · d · h): n sampled reads, d = 3 domains, h primer hypotheses. Each probe is one byte
lookup from a 256 × 3 table of precomputed keystream bytes. Verification is unchanged: O(candidates × min(n, 4,000)) frame
decodes. Primer trimming in pass 1 costs O(reads × (Lf + Lr) × band) with banded edit distance.

## 4. Versioning

### 4.1 Where each version is recorded, and how readers treat it

| Axis | Recorded in | Checked by | Unknown or newer value |
|---|---|---|---|
| Software version | `_version.py`; manifest `encoder.version` and `extensions.vnx.software`; superblock 3 bytes 108–111; every result, report and event (§4.5) | nobody (informational) | Never a reason to refuse |
| Spec version | `extensions.vnx.spec`; superblock 3 bytes 106–107; results | nobody (informational) | Never a reason to refuse; reported |
| Container format | header major/minor/flags; manifest `format_version`, `required_features` | `read_header_trailer` (`container.py:259-270`), `validate_manifest` (`container.py:370-380`) | IMPLEMENTED: exit 6 `CONTAINER_VERSION_UNSUPPORTED` / `FEATURE_UNSUPPORTED` |
| Frame version | nibble in byte 1 of every frame, per domain (§3.2) | `decode_frames`; probe (§3.10) | SPECIFIED: exit 6 `FRAME_VERSION_UNSUPPORTED` or `LEGACY_FORMAT`. Today: misreported as exit 5 |
| Superblock version | superblock byte 6 | `Superblock.unpack` | IMPLEMENTED: exit 6 `SUPERBLOCK_VERSION_UNSUPPORTED` if no candidate decodes |
| Codec identifier | derived: `"f" + frame ‖ "-sb" + superblock ‖ "-" + outer` (e.g. `f4-sb1-cauchy-rs`, `f4-sb2-cauchy-rs`, `f4-sb1-lt-fountain`) | reports only | n/a (derived from fields that are themselves checked) |
| Strand profile | not stored by name; the layout fields in the superblock are authoritative (VNX4 §11) | `decoder.py:770` (`sb.layout == lay`) | Unknown name: `CONFIGURATION_ERROR` |
| Channel model | model JSON `name`, `version` (semver), `schema` (§4.5) | `vnxdna.simulation` (V6 Phase 3) | Unknown schema major: refuse (`UNSUPPORTED_FORMAT`). A model version is an identifier, never refused |
| Provider adapter | adapter `name`, `version`, `interface` (`vnx.provider/1`) in export/import manifests (§9) | provider registry | Unknown interface major: refuse |
| Result, report, event and manifest schemas | `"schema": "vnx.<kind>/<major>"` | consumers | Consumers MUST refuse an unknown major and MUST ignore unknown keys within a known major |

### 4.2 Software version rules (SPECIFIED, V6 Phase 2; fixes audit §4 "5.0.0 on V6 output")

- At the start of Phase 2, `src/vnxdna/_version.py` MUST become a PEP 440 development version of the next release:
  `"6.0.0.dev0"`, incremented per merge to the build branch. The release sets `"6.0.0"`.
- Output written by a development tree therefore never claims a released version.
- `vnx version` MUST report every axis. It replaces the hard-coded `"frame_version": 4` (`v4/cli.py:126`):

```json
{"schema": "vnx.version/1", "software": "6.0.0", "spec": "6.0",
 "container": {"read": [[4, 0]], "write": [4, 0]},
 "frame": {"read": [4], "write": [4], "legacy_detected": ["v1-frame4", "v3-frame5"]},
 "superblock": {"read": [1, 2], "write": [1, 2]},
 "codecs": ["f4-sb1-cauchy-rs", "f4-sb2-cauchy-rs", "f4-sb1-lt-fountain"],
 "backends": {"align": {"active": "native", "abi": 2}, "reads": {"active": "native", "abi": 1},
              "rs": {"active": "native", "level": "avx2", "abi": 1}},
 "python": "3.12.x", "platform": "linux-x86_64"}
```

### 4.3 "Can I read this?" (SPECIFIED, V6 Phase 2)

`vnx inspect PATH` (SDK `inspect()`) answers without decoding the payload. The input kind is detected from the content: the
container magic, the V3 magic, or FASTA/FASTQ/plain.

| Input | Procedure | Answer |
|---|---|---|
| container | header and trailer, then manifest validation (IMPLEMENTED, `container.py:259-270, 370-380`) | `readable: "yes"`, or `"no"` with the code; V3 magic gives `"legacy"` |
| reads or strands | sample, probe and verify (§3.10 steps 1–6) | `"yes"` (frame version, profile, primers), `"no"` (code), `"legacy"`, or `"unknown"` (LAYOUT_UNDETECTED) |
| reads, `--deep` | also run pass 1 restricted to kind-1 frames and decode the superblock (O(reads)) | adds superblock version, archive ID, geometry and codec identifier |

Result schema: `vnx.probe/1` (V6_ARCHITECTURE §4.3).

### 4.4 "Which specification and software version generated this?"

| Evidence available | Answer the reader MUST give |
|---|---|
| container with `extensions.vnx` | exact: `spec`, `software` |
| container without it | "VNX4 4.0 written by vnxdna `<encoder.version>`". Note: 6.0.0.dev trees before Phase 2 wrote `"5.0.0"` (audit §4) |
| DNA with superblock 3 | exact: spec major.minor and software version from bytes 106–111 |
| DNA with superblock 2 | "VNX-DNA ≥ 6.0 development tree (V6 outer code); exact version not recorded in DNA" |
| DNA with superblock 1 | "VNX4 frame 4, superblock 1: written by VNX-DNA 4.x, 5.x or 6.x with default outer options. These outputs are byte-identical by design (`tests/v6/test_outer_pipeline.py:70-80`), so the DNA cannot distinguish them" |
| strand FASTA labels `vnx4\|…` | informational only; decoders never trust labels (VNX4 §14) |

### 4.5 Schema-versioned artefacts (SPECIFIED, V6 Phases 2–3)

| Artefact | Schema ID | Today |
|---|---|---|
| command result (stdout JSON) | `vnx.result/1` | no version (`cli.py:43-47`) |
| error (stderr JSON) | `vnx.error/1` | no version; `error_class` is a Python class name (`v4/errors.py:37`) |
| decode report (`--report`) | `vnx.decode-report/1` | no version (`decoder.py:1386-1452`) |
| encode report | `vnx.encode-report/1` | no version (`encoder.py:354-369`) |
| events (`--events`, JSONL) | `vnx.event/1` | no version (`v6/observe.py:45-50`) |
| probe / inspect | `vnx.probe/1` | new |
| version | `vnx.version/1` | ad-hoc (`cli.py:126`) |
| config file (`--config`) | `vnx.config/1`; a file without `schema` is read as `vnx.config/0` (today's format) | no version (`v4/config.py`) |
| channel model | `vnx.channel-model/1`; the 14 existing JSON files (name/version/classification) are read as `/0` | `experiments/v6/channel/models/*.json` |
| channel config (`vnx channel simulate`) | `vnx.channel-config/1`; a file without `schema` is read as `/0` | no version (`v4/channel.py:44-66`) |
| export package | `vnx.export-package/1` | new (§9.3) |
| import package | `vnx.import-package/1` | new (§9.3) |
| physical record | `record_version: "1"` (unchanged; evidence class `PUBLIC-DATA-DERIVED` added in V6 Phase 7) | `src/vnxdna/physical/schemas/record.schema.json` (old path `experiments/v6/physical/schema/` is a link) |
| conformance | `vnx.conformance/1`, `vnx.conformance-vector/1` | new (§6) |
| experiment manifest | `vnx.experiment/1` | ad-hoc per experiment |

Field-level definitions are in V6_ARCHITECTURE §4–§5. All of them are JSON Schema documents shipped as package data.

### 4.6 Compatibility rules (normative)

1. **Never reinterpret.** An existing version value (container major/minor, frame nibble per domain, superblock version,
   schema major) MUST NOT change meaning. New behaviour needs a new value or a new required feature.
2. **Reader support matrix for 6.x.** It MUST decode or open, bit-exactly:
   - VNX4 containers 4.0;
   - frame 4 with superblock 1 and 2, written by 4.0.0, 5.0.0 and 6.x;
   - the stored fixtures `tests/fixtures/v4_0`, `v5_0` and (from Phase 2) `v6_0`.
   V1–V3 data stays decodable by the `vnx-dna` CLI, kept as legacy (V6_ARCHITECTURE §6).
3. **Default output.** With default options, 6.x strand output MUST be byte-identical to 5.0.0 *for the same container
   bytes*, i.e. frame 4, superblock 1 and the same order. The container bytes themselves differ only in the informational
   manifest fields of §2.3.1.
4. **Old readers refuse new formats, and never return wrong data.** A 5.0.0 reader refuses superblock 2 (V6_OUTER_CODE §1).
   A 6.x reader refuses superblock 3 and frame 6 with exit 6 until it implements them.
5. **Opt-in.** Every new format element is written only on an explicit option: superblock 2 (V6 outer options), the
   `content-v1` archive ID, superblock 3, frame 6, primers.

## 5. Canonical pipeline

Stage names are normative identifiers. They are used in reports (`stage_seconds`), events (`stage`), errors (`stage`) and
conformance vectors (`stage`). "Current" cites the code at 081697b. "Target" names the module of V6_ARCHITECTURE §2.

### 5.1 Encode

| Stage | Input → output | Invariants | Current | Target |
|---|---|---|---|---|
| E0 `collect` | paths → sorted entries | Paths valid (VNX4 §4 rules); no symlink escape; stable UTF-8 byte order | `archive.collect_entries` (`v4/archive.py:71`), `_walk` (`112`) | `archive.collect` |
| E1 `chunk` | entries → (index, plaintext chunk) | Fixed size; the last chunk of a file may be short | `_chunk_source` (`archive.py:162`) | `archive.chunking` |
| E2 `identify` | chunk → chunk ID | SHA-256 domain-separated, or HMAC when encrypted (VNX4 §3) | `archive.py:223,229` | `archive.chunking` |
| E3 `dedup` | IDs → unique chunk set plus reference list | Every stored chunk referenced at least once | `archive.py:259` | `archive.chunking` |
| E4 `compress` | chunk → stored codec bytes | keep-if-smaller | `archive.py:231-239` | `archive.compression` |
| E5 `seal` | codec bytes → AEAD bytes | Unique nonce per (domain, index) under a fresh key (VNX4 §6) | `archive.py:265`; `crypto.py:137-138` | `archive.crypto` |
| E6 `container` | sealed chunks and tables → `.vnx` | Tables tile; Merkle root; canonical manifest; trailer SHA; atomic output | `ContainerWriter` (`container.py:141`), `base_manifest` (`187`) | `archive.container` |
| E7 `plan` | container size, options → geometry (K, M, D, Mc, order, layout, frame, superblock version) | Deterministic; V6 options switch to superblock 2 (`encoder.py:72-75`) | `DNAOptions.resolve` (`encoder.py:77-107`), `v6.encoder.resolve_geometry` (`v6/encoder.py:32`), `v6.outer.plan` (`v6/outer.py:478`) | `codec.plan` |
| E8 `outer` | container rows → row codewords (+ column parity) | Systematic, MDS per row and column | `_encode_task` (`encoder.py:245`), `v6/encoder._encode_task` (`v6/encoder.py:86`), `v6.outer.row_codewords/column_parity_rows` (`outer.py:203,209`) | `codec.outer` |
| E9 `superblock` | geometry and container hash → superblock bytes → (Ks + 3Ks) symbols | Version 1 unless V6 options (byte identity); CRC-32 | `Superblock.pack` (`encoder.py:138-152`) | `codec.superblock` |
| E10 `frame` | (kind, tag, group, symbol, payload) → plain rows with CRC | Version nibble per §3.2 | `frame.plain_rows` (`frame.py:177-188`) | `dnaenc.frame4` (`dnaenc.frame6` in V7) |
| E11 `scramble+inner` | plain rows, variant v → frame bytes | RS parity over the transmitted bytes | `build_strands` loop (`frame.py:201-207`) | `dnaenc.frame4`, `codec.inner` |
| E12 `map` | frame bytes → nt with markers [+ primers] | 2 bits per nt; first 8 nt free of markers (I-3) | `bytes_to_nt`, `insert_markers` (`frame.py:151,168`) | `dnaenc.mapping`, `dnaenc.markers`, `dnaenc.primers` (V7) |
| E13 `screen` | strands → accepted strands or the next variant | Never emit an unscreened strand; failure raises `CONSTRAINT_ERROR` | `satisfied_batch` in `build_strands` (`frame.py:208-218`) | `dnaenc.constraints` |
| E14 `order+write` | strands → FASTA/FASTQ in strand order | Order is a property of the file only (V6_OUTER_CODE §3); atomic write | `_serialize` (`encoder.py:231`), `v6/encoder._records` (`v6/encoder.py:49`), `v2.strandio.StrandWriter` | `dnaenc.order`, `dnaenc.strandio` |
| E15 `export` | strand file → export package (§9.3) | Vendor checks recorded; SHA-256 of every file | absent | `physical.packages` (V6 Phase 7) |

### 5.2 Decode

| Stage | Input → output | Invariants | Current | Target |
|---|---|---|---|---|
| D0 `ingest` | read file(s) → batches (codes, quals) | Reads ≤ 100,000 nt (`v4/reads.py:24`); bounded memory; same output for native and reference parsers | `v6.native_reads.iter_reads` (`v6/native_reads.py:181`), `v4/reads.iter_reads` (`v4/reads.py:101`) | `dnaenc.reads` + `native.reads` |
| D1 `probe+layout` | sample → (frame version, layout, primers) or refusal | §3.10; always verifies frames | `detect_layout` (`decoder.py:125-170`) | `recovery.probe` |
| D2 `trim` | reads → bodies (primers removed) and `other_partition` count | Only when primers are declared | absent | `dnaenc.primers` (V7) |
| D3 `orient` | bodies → oriented bodies | Marker agreement decides; reverse complement retried | `_orientation` (`decoder.py:310-329`), retry (`350-368`) | `recovery.pass1` |
| D4 `sync` | bodies → frame bases plus erasures | An indel becomes erasures, never silent shifts that would pass CRC | `TemplateAligner.project` (`v4/sync.py:89`), native `align.c` | `sync.aligner` + `native.align` |
| D5 `inner` | frame bytes and erasures → accepted frames | Accept iff CRC verifies after any correction, and nibble and kind are valid (I-5) | `decode_frames` (`frame.py:234-277`), RS backends (`codecs.py:338`, `v6/native_rs.py`) | `dnaenc.frame4` + `codec.inner` |
| D6 `spill` | accepted, pending and orphan records → bucket files | Output independent of the bucket count (claim UNVERIFIED, audit §5.6; test in Phase 2) | `Spill` (`decoder.py:433-500`) | `recovery.spill` |
| D7 `recover` | pending reads → more accepted frames (smart/soft rounds S/A/B) | Every frame recovered here passes the same CRC and inner check; budgets enforced | `_deferred_recovery` (`decoder.py:1042`), `v5/indel/*`, `v5/soft/*`, `v6/recovery.py` | `sync.indel`, `recovery.soft`, `recovery.schedule`, `recovery.planner` |
| D8 `superblock` | kind-1 symbols → superblock and the chosen archive | Strict-majority duplicates; tag candidates; unknown version only if nothing decodes; ambiguity is refused (§3.10 step 7) | `_decode_superblock` (`decoder.py:745-786`) | `recovery.superblock` |
| D9 `consensus` | duplicates and pending reads → one symbol per address | Strict majority (`resolve_duplicates`, `decoder.py:527`); snapping is at most one header byte and re-verified (`decoder.py:793`) | `_consensus_symbols` (`839`) | `recovery.consensus` |
| D10 `outer` | symbols per row → rows | MDS erasure decode; a row is written only if decoded | `_pass2` (`decoder.py:1264`) | `recovery.pass2` + `codec.outer` |
| D11 `stripe` | failed rows → rows via column parity | Erasure decoding never creates information (V6_OUTER_CODE §4) | `StripeRecovery` (`v6/decode.py:62-199`) | `recovery.stripes` + `codec.outer.product` |
| D12 `verify` | reconstructed container → verified container | SHA-256 equals the superblock, then structural, manifest and Merkle validation; otherwise nothing is published | `decoder.py:1430-1442` | `recovery.publish` |
| D13 `publish` | verified container → output path; or PARTIAL files | Atomic rename; PARTIAL publishes only files whose chunks are all verified | `decoder.py:1446-1448`, `_partial` (`1455`), `_selective` (`1498-1524`) | `recovery.publish` |
| D14 `extract` | container, key → files | Decrypt, bounded zstd, per-chunk and per-file SHA-256; rename only after verification (VNX4 §9.9) | `archive.extract` (`archive.py:507`) | `archive.extract` |

**Selective decode.** `--select` runs D0–D7 over every read (audit §6), then D8–D13 only for the index tail groups, group 0
and the selected files' groups or stripes. Phase 2 fixes job #56: D7 round S (superblock rescue) MUST run before D8 in select
mode as well (audit §6 hypothesis 1).

## 6. Conformance (SPECIFIED, V6 Phase 8; the directory layout is fixed now)

### 6.1 Vector layout

```
tests/conformance/
  index.json                      schema vnx.conformance-index/1: every vector ID, stage, formats, kind, path
  stage/<stage-id>/<vector-id>/   vector.json + input/expected files (small, hex or binary)
  e2e/<format-id>/<vector-id>/    references to tests/fixtures/<set> files by path and SHA-256 (no copies)
  negative/<vector-id>/           malformed or unsupported inputs and the expected error
```

Each `vector.json` (`vnx.conformance-vector/1`):

```json
{"schema": "vnx.conformance-vector/1", "id": "frame4.build.balanced.001", "stage": "E10-E13",
 "formats": {"frame": 4, "superblock": null, "container": null}, "kind": "positive",
 "operation": "frame4.build", "params": {"profile": "v4-balanced", "tag": 4660, "kind": 0, "group": 7, "symbol": 3},
 "inputs": {"payload": {"path": "payload.bin", "sha256": "…"}},
 "expected": {"outputs": {"strand": {"path": "strand.txt", "sha256": "…"}, "variant": 0}},
 "since_spec": "6.0", "evidence": "SYNTHETIC SOFTWARE TEST"}
```

Negative vectors carry `"expected": {"error": {"code": "FRAME_VERSION_UNSUPPORTED", "category": "UNSUPPORTED_FORMAT", "exit_code": 6, "retryable": false}}`.
An independent implementation needs only these files and this spec. Operations are named functions over byte strings, with
no Python objects involved.

### 6.2 Required vectors (minimum set for 6.0)

| Area | Positive vectors | Negative vectors |
|---|---|---|
| GF(256) and RS | encode and errata decode at r ∈ {8, 12, 16, 20}; reuse `tests/v6/native/native_rs_golden.json` by reference | > r/2 errors: must report failure and not miscorrect silently (CRC decides) |
| CRC-32, scrambler | CRC of fixed rows; keystream bytes 0 … 15 for v ∈ {0, 1, 255}; byte 0 for all 256 v and all 3 domains (§3.2) | — |
| mapping, markers | bytes → nt for the 4 frame-4 profiles; marker tables ℓ = 1 … 6 | — |
| frame 4 build/parse | E10–E13 per profile, kind 0 and 1; variant > 0 case; constraint failure case | nibble 7; kind 2; CRC-valid frame with a wrong nibble |
| superblock 1, 2 | pack/unpack (`tests/v6/test_outer_pipeline.py:84-` provides v1 bytes) | version 0, 3 (until implemented), 255; v2 with D = 0; D + Mc > 256; order 2; CRC-valid forged fields (`encoder.py:173-196`) |
| outer code | Cauchy rows (K, M) ∈ {(64,16), (32,32), (48,16)}; short last group; column parity (D, Mc) ∈ {(4,2), (8,2)}; iterative stripe decode | erasure pattern beyond the bound → reported failure |
| strand order | sequential and interleaved record order for one small geometry | — |
| container | build with fixed archive ID and salt; Merkle proofs; canonical manifest; AEAD with fixed key/salt | VNX4 §9 items 1–9, one vector each (wrong magic, major 5, minor 1, flags ≠ 0, truncated trailer, non-canonical manifest, unknown required feature, MAC mismatch, Merkle mismatch, chunk-ID mismatch, zstd overflow) |
| end to end | `tests/fixtures/v4_0`, `v5_0` (existing) and `v6_0` (Phase 2): container → strands SHA-256; reads → container SHA-256 and files | frame-nibble-7 pool → exit 6 not retryable; V3 frame-5 pool → `LEGACY_FORMAT`; two archives with one tag → `ARCHIVE_TAG_AMBIGUOUS`; read > 100,000 nt; wrong key; key for an unencrypted archive (`7450ede`) |
| version reporting | `vnx version` validates against `vnx.version/1` | — |

When frame 6 or superblock 3 is implemented (V7/V8), its vectors are added in the same layout. The negative vectors
"superblock 3 unsupported" and "frame 6 unsupported" then move to a `negative/legacy-reader/` set, run against the stored
6.x answers.

### 6.3 `vnx conformance` answer (`vnx.conformance/1`)

```json
{"schema": "vnx.conformance/1", "software": "6.0.0", "spec": "6.0", "vectors_dir": "…", "index_sha256": "…",
 "backends": {"align": "native", "reads": "native", "rs": "avx2"},
 "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
 "by_stage": {"E10-E13": {"passed": 0, "failed": 0}},
 "results": [{"id": "…", "stage": "…", "kind": "positive", "status": "PASS|FAIL|SKIP",
              "expected": {}, "observed": {}, "seconds": 0.0}],
 "verdict": "CONFORMANT|NONCONFORMANT"}
```

- Exit codes: 0 if every vector passes, 1 (`VERIFICATION_FAILED`) otherwise.
- A vector may be skipped only with a reason that names a missing optional component, for example a native backend.
  Skipping never counts as conformant, so the verdict is `NONCONFORMANT` if any vector is skipped.
- `--backend reference|native` runs the vectors once per backend. Both MUST pass.

## 7. Error correction and limits (F)

### 7.1 What the code guarantees (deterministic; IMPLEMENTED unless marked)

| Mechanism | Guarantee | Source |
|---|---|---|
| Inner RS per frame | Corrects any pattern with 2e + f ≤ r byte errata (e errors, f erasures) | VNX4 §10; `frame.py:234-277` |
| CRC-32 after correction | A frame is accepted only if its CRC verifies. For a corrupted frame, the CRC-first accept path passes with probability about 2⁻³² (`frame.py:243-244` comment) | `frame.py:274` |
| Outer Cauchy RS (K + M) | Any k_g of the k_g + M symbols of a row reconstruct the row (MDS) | VNX4 §13 |
| Superblock | Any Ks of the 4·Ks superblock strands suffice | VNX4 §12 |
| V6 stripes (D, Mc) | A stripe decodes if at most Mc of its rows have more than M missing symbols. Iteration can recover more, but that is not guaranteed | V6_OUTER_CODE §2, §4 |
| Burst tolerance | `burst_tolerance(geometry)` is exact for the given geometry and file order (brute-force tested) | V6_OUTER_CODE §6; `v6/outer.py:401` |
| Fail-closed publish | See §7.3 | `decoder.py:1430-1448` |

### 7.2 Per-profile statements

| Profile | Inner (per frame) | Outer (per full row) | Indels | Superblock |
|---|---|---|---|---|
| v4-balanced | 2e + f ≤ 16 | any 16 of 80 strands lost (20 %) | Markers 3 nt / 24 nt: no worst-case guarantee; SIMULATED (EXP-0009) | 3 of 12 strands |
| v4-dense | 2e + f ≤ 12 | 16 of 80 | **No markers: no indel localisation.** An indel shifts all following bases, and the read is usually lost | 3 of 12 |
| v4-indel | 2e + f ≤ 20 | 16 of 64 (25 %) | as v4-balanced | 3 of 12 |
| v4-archival | 2e + f ≤ 20 | 32 of 64 (50 %) | as v4-balanced | 3 of 12 |
| redundancy `maximum-recovery` (v4-archival + D 8, Mc 2, interleaved; `v6/profiles.py:25`) | 2e + f ≤ 20 | 32 of 64 per row, and per stripe ≤ 2 rows beyond that | as v4-balanced | 3 of 12 |
| f6-* candidates | 2e + f ≤ r (table §3.7) | as the chosen K, M | THEORETICAL | ⌈128/P⌉ of 4× that |

**SIMULATED results** (not guarantees; `experiments/v6/phase1/summary.md`, 20 seeds per cell, 0 false SUCCESS, baseline A.4):

- i.i.d. strand-dropout threshold: V5 0.07; V6-plan-seq/adaptive 0.16 at about equal overhead.
- Poisson coverage threshold: V5 3.0; V6 2.0.
- Burst loss with 2 % i.i.d. dropout: V5 fails at every burst ≥ 64 strands. V6-rows255 and V6-adaptive survive
  4,096-strand bursts.

These numbers come from software channels that are not fitted to any platform, and they MUST be quoted with that label.

### 7.3 Fail-closed invariants (normative for every 6.x decoder path, including the SDK, providers and conformance)

1. **FC-1** The output container is published, by atomic rename, only after its SHA-256 equals the superblock's and the
   structural, manifest and Merkle validation passes (`decoder.py:1430-1448`).
2. **FC-2** PARTIAL publishes only files all of whose chunks lie outside lost ranges, each verified by its SHA-256
   (`_partial`, `decoder.py:1455`).
3. **FC-3** Extraction renames a file into place only after its SHA-256 verifies (VNX4 §9.9).
4. **FC-4** Any unknown container, frame, superblock or schema version, or unknown required feature, gives exit 6 and
   publishes nothing. A version problem is never reported as `INSUFFICIENT_REDUNDANCY` (fixes audit §4).
5. **FC-5** A budget that is exceeded ends in FAILURE or PARTIAL with the budget named, never in unverified bytes
   (`v6/recovery.py:41`).
6. **FC-6** Observers, report writers and providers cannot change decode output (`v6/observe.py` docstring). If one of
   them fails, it is reported and detached.
7. **FC-7** The encoder never emits a strand that fails screening (`frame.py:213-218`).
8. **FC-8 (scope).** For an unencrypted archive, the container SHA-256 in the superblock protects against accidental
   corruption only. Someone who can write the whole pool can make a consistent forgery. Authenticity exists only for
   encrypted archives (manifest HMAC, per-chunk AEAD), and is checked on open or extract with the key (VNX4 §6–§7).

## 8. Interoperability (G)

### 8.1 DNA Data Storage Alliance Sector Zero and Sector One (mapping only; byte and base layouts to be aligned with the published specification)

Public descriptions (30-co §3; gap §19) state that Sector Zero v1.0 is 70 bases, 35 identifying the vendor and 35 the codec,
and that Sector One v1.0 carries archive metadata: content description, file table and sequencer parameters. **The
specification texts were not available for this design.** Their exact layouts are therefore **TO BE ALIGNED WITH THE
PUBLISHED SPEC**, and nothing below defines Sector Zero or Sector One contents.

| DDSA element | VNX source | Mapping rule |
|---|---|---|
| Sector Zero vendor ID | none today; needs a DDSA allocation (gap §22.8) | Configuration value of the writer, not derived |
| Sector Zero codec ID | VNX **codec family**: `vnx-frame4` (frame 4, superblocks 1–3) and `vnx-frame6` (frame 6, superblock 3) | One DDSA codec ID per family, not per profile. Profile, geometry and version are self-described by the superblock, which a family reader finds by the §3.10 probe |
| Sector One archive metadata | superblock fields (archive ID, container size and SHA-256, geometry, codec identifier, spec and software version), plus manifest `format`, `format_version`, `required_features`, `counts`, `encoder`, `extensions.vnx` | Field-to-field table to be written against the published spec |
| Sector One file table | VNX4 file table (VNX4 §4) | **Unencrypted archives only.** For encrypted archives a writer MUST NOT place names, sizes or hashes in Sector One, because Sector One is readable without a key by design (gap §22.8); only counts permitted by the archive's policy |
| Sector One sequencer parameters | export package `dna` block (§9.3): strand length, primers, recommended coverage | Copied, not recomputed |

**Placement rule.** Sector Zero and Sector One molecules are separate strands added to the pool by the export step (§9.3,
role `ddsa-sector-zero` / `ddsa-sector-one`). A VNX reader that does not parse them sees reads that fail the VNX frame check.
Those reads are counted as orphans, so adding them is additive and decode results do not change. A later VNX reader MAY use
Sector One to skip layout detection (§3.10 steps 1–4). It MUST still verify frames and the superblock.

### 8.2 SNIA Swordfish DNA working draft (management resources)

The provider operations of §9 map onto the draft's "DNA process" resources (encode, synthesise, store, retrieve, sequence,
decode, verify; gap §19). The resource names and schemas are **to be aligned with the draft**. VNX defines no Swordfish
resource in 6.x.

### 8.3 JPEG DNA (ISO/IEC 25508-1)

JPEG DNA is an image payload codec (gap §19). It needs no VNX format change: its output is carried as an ordinary file in a
VNX archive. No claim of JPEG DNA conformance is made.

## 9. Physical abstraction and provider interface (H)

### 9.1 Status table

| Item | Interface specified | Interface implemented | Provider integration tested |
|---|---|---|---|
| `DNAWriter`, `DNAReader`, `DNAProvider` protocols (§9.2) | yes (here) | **yes** (V6 Phase 7, `vnxdna.providers.base`) | software only |
| `ReferenceSimulatorProvider` | yes | **yes** (V6 Phase 7, `vnxdna.providers.reference`) | **software only, SIMULATED** |
| Export and import package manifests (§9.3) | yes | **yes** (V6 Phase 7, `vnxdna.providers.packages`; schemas `vnx.export-package/1`, `vnx.import-package/1`) | software only |
| Physical record schema and validator | yes | **yes** (`vnxdna.physical`, moved from `experiments/v6/physical/` in V6 Phase 7; `docs/V6_PHYSICAL_VALIDATION_INTERFACE.md`) | no physical run has occurred |
| Any synthesis or sequencing vendor adapter | no | no | **no**; V11 |

### 9.2 Operations (normative signatures; Python protocols in `vnxdna.providers.base`)

```python
class DNAWriter(Protocol):
    def prepare(self, strands: StrandSource, *, archive: ArchiveRef, profile: StrandProfile,
                primers: PrimerPair | None = None, pool_tag: int | None = None) -> ExportPackage: ...
    def write(self, package: ExportPackage) -> WriteReceipt: ...          # order / synthesise / store (async physically)

class DNAReader(Protocol):
    def retrieve(self, pool: PoolRef, *, selection: Selection | None = None,
                 sequencing: SequencingRequest) -> ImportPackage: ...       # sample, amplify, sequence
    def read(self, package: ImportPackage) -> Iterator[ReadBatch]: ...      # stream reads to stage D0

class DNAProvider(DNAWriter, DNAReader, Protocol):
    name: str; version: str; interface: Literal["vnx.provider/1"]
    def capabilities(self) -> ProviderCapabilities: ...   # max strand nt, pool size, GC/homopolymer limits, primers
    def list(self) -> list[PoolRef]: ...                  # pools known to this provider
    def search(self, *, archive_id: bytes | None = None, pool_tag: int | None = None,
               primer_id: int | None = None) -> list[PoolRef]: ...
    def close(self) -> None: ...
```

- **Semantics.**
  - `prepare` is pure and deterministic: it validates against `capabilities()` and builds the export package.
  - `write` and `retrieve` may take days physically, so they return receipts or handles. The SDK exposes them as jobs.
  - `read` streams reads to stage D0.
  - No operation changes archive bytes.
  - Every package records the provider `name`, `version` and `interface`.
- **Errors.**
  - A capability violation in `prepare` raises `CONFIGURATION_ERROR` with a code such as `VENDOR_MAX_LENGTH` and publishes
    nothing.
  - A provider failure in `retrieve` raises `PROVIDER_ERROR`, a new category with exit 10 (V6_ARCHITECTURE §4.4). It is never
    mapped to `INSUFFICIENT_REDUNDANCY`.
- **ReferenceSimulatorProvider.**
  - `write` stores the strand file in a provider directory, which stands in for a tube, and records its SHA-256.
  - `retrieve` applies a named channel model (`vnx.channel-model/1`, V6 Phase 3) with an explicit seed and coverage, and
    writes FASTQ.
  - Every import package it produces has `evidence_class: "SIMULATED"`, and the model name, version, schema and seed are
    recorded.
  - It MUST be deterministic for equal (strand file SHA-256, model@version, seed, coverage), independent of the worker count.

### 9.3 Packages: VNX Export Package → laboratory → sequencing data → VNX Import Package

**Export package** (directory `<id>.vnxexp/`):

```
manifest.json   strands.fasta[.gz]   order.csv   SHA256SUMS   [sector-zero.fasta, sector-one.fasta when §8.1 is implemented]
```

`manifest.json` (`vnx.export-package/1`):

```json
{"schema": "vnx.export-package/1", "package_id": "<64 hex: SHA-256 of the canonical manifest without this field>",
 "created_by": {"software": "6.0.0", "spec": "6.0"},
 "provider_target": {"name": "reference-simulator", "version": "1.0.0", "interface": "vnx.provider/1"},
 "archives": [{"archive_id": "<32 hex>", "container_sha256": "<64 hex>", "container_size": 0, "encrypted": false,
               "codec": "f4-sb1-cauchy-rs", "frame_version": 4, "superblock_version": 1, "strand_profile": "v4-balanced",
               "layout": {"P": 40, "r": 16, "marker_period": 24, "marker_len": 3},
               "outer": {"K": 64, "M": 16, "D": 1, "Mc": 0, "order": "sequential"},
               "pool_tag": 0, "primers": null, "strand_count": 0, "strand_nt": {"min": 313, "max": 313}}],
 "checks": [{"id": "vendor-max-length", "limit": 350, "value": 313, "status": "PASS"},
            {"id": "constraints", "config": {}, "status": "PASS"},
            {"id": "pool-tag-unique", "status": "PASS"}],
 "files": [{"name": "strands.fasta", "role": "strands", "sha256": "…", "bytes": 0}],
 "evidence_class": "SYNTHETIC SOFTWARE TEST"}
```

- A package with more than one archive is a **pool**. The pool-composition rule of §3.9 is enforced here.
- The container is not included by default. Only its SHA-256 and size are, because the container may be confidential.
- `order.csv` is a generic name,sequence sheet. Vendor-specific formats belong to V11 adapters.

**Import package** (directory `<id>.vnximp/`): `manifest.json`, `reads/*.fastq[.gz]`, `SHA256SUMS`. `manifest.json`
(`vnx.import-package/1`):

```json
{"schema": "vnx.import-package/1", "export_package_id": "<64 hex>",
 "provider": {"name": "reference-simulator", "version": "1.0.0", "interface": "vnx.provider/1"},
 "evidence_class": "SIMULATED",
 "simulation": {"model": "illumina-like", "model_version": "1.0.0", "model_schema": "vnx.channel-model/1",
                "seed": 7, "coverage": 10.0, "simulator_software": "6.0.0"},
 "sequencing": {"platform": null, "run_id": null, "read_length": null, "layout": "single", "primers_trimmed": false},
 "selection": {"primer_id": null}, "files": [{"name": "reads/r1.fastq.gz", "sha256": "…", "bytes": 0, "reads": 0}]}
```

- `evidence_class` ∈ {`SIMULATED`, `SYNTHETIC SOFTWARE TEST`, `PUBLIC-DATA-DERIVED`, `REAL PHYSICAL RESULT`}.
- `REAL PHYSICAL RESULT` requires the provider, order and run fields that the physical-record rules require
  (`docs/V6_PHYSICAL_VALIDATION_INTERFACE.md`, "Evidence classes"). `PUBLIC-DATA-DERIVED` was added to the record schema in V6
  Phase 7 (it needs a dataset accession, the SHA-256 of every downloaded file and a DOI or `unpublished`).
- An import package plus its decode report converts to a physical record (`record_version "1"`). The converter fills the
  `synthesis`, `sequencing` and `decode` sections and leaves the attestations empty, which gives `INCOMPLETE` until a person
  attests.

## 10. Error codes (SPECIFIED, V6 Phase 2; the exit codes are the IMPLEMENTED taxonomy of `src/vnxdna/errors.py:8-21`)

| Code (stable string) | Category | Exit | Retryable | Raised when |
|---|---|---|---|---|
| `CONTAINER_VERSION_UNSUPPORTED` | UNSUPPORTED_FORMAT | 6 | no | header major/minor/flags (VNX4 §9.1) |
| `FEATURE_UNSUPPORTED` | UNSUPPORTED_FORMAT | 6 | no | unknown required feature |
| `LEGACY_FORMAT` | UNSUPPORTED_FORMAT | 6 | no | V3 container magic; V1/V3 frame probe (§3.10) |
| `FRAME_VERSION_UNSUPPORTED` | UNSUPPORTED_FORMAT | 6 | no | §3.10 step 6a |
| `SUPERBLOCK_VERSION_UNSUPPORTED` | UNSUPPORTED_FORMAT | 6 | no | `encoder.py:160-162` when no candidate decodes |
| `SCHEMA_UNSUPPORTED` | UNSUPPORTED_FORMAT | 6 | no | unknown schema major in a config, model or package |
| `FORMAT_ERROR` | INVALID_INPUT | 3 | no | malformed container or superblock fields |
| `LAYOUT_UNDETECTED` | INVALID_INPUT | 3 | no | §3.10 step 6c |
| `ARCHIVE_TAG_AMBIGUOUS` | INVALID_INPUT | 3 | no | two distinct superblocks under one tag |
| `ARCHIVE_TAG_NOT_FOUND`, `MULTIPLE_ARCHIVES` | INVALID_INPUT | 3 | no | `decoder.py:779-783` |
| `NO_SUPERBLOCK` | INSUFFICIENT_REDUNDANCY | 5 | yes | too few superblock strands (frame version verified) |
| `INSUFFICIENT_REDUNDANCY` | INSUFFICIENT_REDUNDANCY | 5 | yes | rows or stripes unrecoverable |
| `BUDGET_EXCEEDED` | INSUFFICIENT_REDUNDANCY | 5 | yes (with a larger budget) | `v6/recovery.py:41` |
| `CONTAINER_HASH_MISMATCH` | VERIFICATION_FAILED | 1 | no | `decoder.py:1440` |
| `WRONG_KEY`, `KEY_FOR_UNENCRYPTED` | AUTHENTICATION_FAILED | 4 | no | key check; `7450ede` |
| `CONSTRAINT_ERROR` | CONFIGURATION_ERROR | 7 | no | `frame.py:215` |
| `CONFIGURATION_ERROR`, `VENDOR_MAX_LENGTH` | CONFIGURATION_ERROR | 7 | no | includes unknown `--performance` (today exit 70, audit §5.4) |
| `ARCHIVE_TAG_COLLISION` | CONFIGURATION_ERROR | 7 | no | pool composition (§3.9) |
| `OUTPUT_ERROR` | OUTPUT_ERROR | 8 | no | |
| `PROVIDER_ERROR` | PROVIDER_ERROR (new) | 10 | provider-defined | §9.2 |
| `INTERNAL_ERROR` | INTERNAL_ERROR | 70 | no | bug |

Codes added by the implementation for errors the table does not name (category and exit code as shown):
`ADDRESS_ERROR` (INVALID_INPUT, 3), `RESOURCE_LIMIT` (INVALID_INPUT, 3), `INTEGRITY_ERROR` (VERIFICATION_FAILED, 1;
any digest mismatch other than the whole-container one) and `INTERNAL_ERROR` for unexpected exceptions. `KEY_REQUIRED`
does not exist: a missing key is `WRONG_KEY`. Errors of the frozen V1–V3 classes use their category as the code.

During the deprecation period, the error JSON keeps `error_class` (`v4/errors.py:37`) in addition to `code`. Only `code`
is stable.

## 11. Security notes specific to this specification

- Frame-6 pool tags, primer IDs, primer sequences, Sector Zero and Sector One are readable without a key. In the export and
  import manifests they reveal structure: archive count, sizes, partitioning. `docs/SECURITY.md` "What an encrypted VNX4
  archive still reveals" MUST be extended when these elements are implemented.
- The probe (§3.10) and package parsers read untrusted input. Their size limits are those of D0, plus 1 MiB for any
  manifest JSON. They are fuzz targets in V6 Phase 6.
- `content-v1` IDs of unencrypted archives are deterministic from content. Someone with a candidate file set can confirm
  that a pool contains exactly that set. This is no more than the clear chunk IDs already allow (VNX4 §3).

## 12. Open questions for the implementer and the founder

1. Restating the byte-identity test `tests/v6/test_outer_pipeline.py:70-80` once `encoder.version` and `extensions.vnx`
   change (V6_ARCHITECTURE §8, item 2.4). This needs founder approval.
2. Whether a released 6.x should write `extensions.vnx` by default (as specified) or only with an option. The default
   gives provenance; an option keeps container bytes stable across versions.
3. DDSA membership and vendor/codec ID allocation (§8.1). These block any Sector Zero writer.
4. Freezing the frame-6 candidate profiles needs EXP-F6-1 on fitted channel models. Those models are V7 work, so the freeze
   cannot happen in V6.
5. Whether the frame-6 compact class should allow CRC-16 for the ~150-nt profiles. Rejected in this draft, because
   availability, not correctness, would suffer. To be revisited with EXP-F6-1 data.
6. Exit code 10 for `PROVIDER_ERROR` extends the stable taxonomy (`errors.py:8-21`). It needs a decision before Phase 7.
