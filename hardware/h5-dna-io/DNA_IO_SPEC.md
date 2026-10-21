# H5 DNA I/O interface description

**STATUS: SPECIFICATION ONLY. NOTHING WAS SYNTHESISED, STORED, SEQUENCED OR MEASURED.**

No oligo was ordered, no DNA was synthesised by a vendor or in a lab, no sample
was stored, no sequencer was run, no read was obtained from a physical
instrument. Every number in this document is either (a) a *stated assumption*
chosen for the design, or (b) a value *derived by calculation* from the
project's own committed source code. Nothing here is a measurement of a
physical system. Where a real quantity would eventually be needed (oligo
length, GC window, coverage, yield), the document says what the value would
have to mean and marks it ASSUMPTION or THEORETICAL; no value is presented as
observed.

**Evidence rung: THEORETICAL.** Category A (software codec) plus category D
(simulated DNA channel) only, in the sense that the parts of this document
that describe the software side are pinned by the committed code, while every
part that describes the physical side is an assumption.

---

## 0. Scope and relation to the other hardware items

| item | what it covers |
|---|---|
| H1 | carrier board (a PCB around a Raspberry Pi CM4) — `hardware/h1-carrier/netlist.md` |
| H3 | the GF(256) Reed–Solomon decode accelerator in Verilog — `hardware/h3-fpga/` |
| **H5** | **this document**: the interface between a VNX-DNA archive and DNA material — the oligo plate format, the strand addressing, the barcode scheme, and the synthesis/sequencing round trip |

H5 is a *description*, not a device. The electrodes, the microfluidics and the
nanopore read electronics are separate work (a research partnership, per the
V15 finalization plan §5D) and are out of scope here.

Everything in this document describes the **format-4 VNX-DNA strand format**
(`docs/STORAGE_FORMAT.md`, `src/vnxdna/dna/strand.py`,
`src/vnxdna/storage/encoder.py`, `src/vnxdna/storage/decoder.py`). It is a
normative description of the *logical* interface: what a strand is, how it is
addressed, what a plate of oligos must contain, and what has to be true for a
sequencer's output to be decodable.

---

## 1. Terminology

| term | meaning |
|---|---|
| **byte** | 8 bits, the unit of the VNX-DNA frame |
| **strand** | one DNA molecule as designed by the encoder: a single logical unit of `strand_nt` nucleotides carrying one outer-code shard |
| **frame** | the byte-level content of a strand before DNA mapping: header, payload, CRC-32, inner RS parity |
| **pool** | the full set of strands of one archive |
| **plate** | one physical plate of synthesised oligos, holding a subset of a pool's strands |
| **read** | one sequencing observation of one strand (a molecule, possibly with errors) |
| **barcode** | a short index sequence attached to a read by the sequencing platform, used to group reads by source well / molecule / strand |

---

## 2. The strand: bytes before nucleotides

One strand is a frame of `frame_bytes` bytes:

```
offset  size  field
0       1     variant v          screening/scrambler selector (not scrambled)
1       1     kind/version       high nibble = 4 (frame format), low nibble = kind (0 data, 1 metadata)
2       3     archive tag        first 3 bytes of the archive ID
5       3     stripe index       0 .. 2^24-1
8       1     shard index        0 .. K+M-1
9       P     payload            one outer-code shard
9+P     4     CRC-32             zlib/IEEE CRC over bytes 1 .. 8+P (unscrambled)
13+P    r     inner RS parity    RS over GF(256) of bytes 0 .. 12+P
```

`P` (payload bytes), `r` (inner parity bytes) and the mapping are recorded in
the archive manifest (`strand.payload_bytes`, `strand.inner_parity_bytes`,
`strand.mapping`), so a reader learns the geometry from the archive itself and
never from a file name.

Bytes 1 .. 12+P are XORed with the keystream
`SHAKE128("VNX-DNA/4 scrambler" ‖ v)`. The encoder tries `v = 0, 1, 2, …`
until the mapped sequence satisfies the archive's constraint specification
(GC window, homopolymer limit, forbidden motifs). The decoder reads `v` out of
the frame, so **the constraint settings are needed to encode but not to
decode**.

Pinned by: `src/vnxdna/dna/strand.py` (constants `FRAME_FORMAT`,
`HEADER_BYTES`, `CRC_BYTES`, `KEYSTREAMS`).

---

## 3. Nucleotide mapping

Three mappings are defined by the format. The archive manifest names the one
in use (`strand.mapping`), and the name is one of the manifest's
`required_features`, so a reader that does not know the mapping refuses the
archive instead of guessing.

| name | nt per byte | bits per nt | property |
|---|---|---|---|
| `2bit` | 4 | 2.00 | A=00 C=01 G=10 T=11, most significant bit first |
| `rotation3` | 6 | 1.33 | rotating ternary code; no two equal adjacent bases |
| `codebook8` | 8 | 1.00 | 256 format-defined 8-mers, each with exactly 4 G/C and no run longer than 3 |

Under `2bit`, one nucleotide substitution corrupts exactly one byte, which is
the case the inner Reed–Solomon code is sized for. Under `rotation3` and
`codebook8` a substitution can damage two bytes, so the effective error
budget per strand is halved.

**ASSUMPTION (physical):** the mapping is chosen so that a strand survives
the constraints of the *synthesis vendor the project has not yet selected*.
This is a placeholder decision, not a vendor qualification. Nothing has been
ordered from any vendor.

---

## 4. Strand addressing

A strand's address is the triple `(archive tag, kind, stripe, shard)`, all of
which are carried **inside the DNA sequence** and covered by the CRC-32 and
the inner RS code. FASTA headers are labels only and are never trusted by the
decoder.

| field | width | meaning |
|---|---|---|
| archive tag | 3 bytes | the first 3 bytes of the archive ID; separates pools that are mixed in one read file |
| kind | 1 nibble | `0` = data strand, `1` = metadata strand |
| stripe | 3 bytes | index of the outer-code stripe the shard belongs to, 0 .. 2²⁴−1 |
| shard | 1 byte | index within the stripe, 0 .. K+M−1 (0..K−1 data, K..K+M−1 parity) |

The address is what makes the pool **self-describing**: a reader given only
the sequence text can place every strand without an index file, and the CRC
tells it whether the placement is trustworthy.

**Consequence for random access (CC-5, not implemented here):** because the
address is in-band, a primer-based or probe-based selection scheme can pick a
subset of strands by their address prefix. The selection *mechanism* is a
wet-lab design (H5) and is out of scope; the *address space* that such a
mechanism would target is defined above.

---

## 5. Oligo plate format

### 5.1 What a plate holds

A plate is a physical realisation of a **subset of one pool's strands**. The
subset is chosen by the operator (by archive, by stripe range, by plate size);
the *contents* of a strand are not negotiable — a strand is what the encoder
produced, byte for byte.

Each row of a plate is one oligo. The row carries:

| field | content | notes |
|---|---|---|
| plate/well id | assigned by the operator | e.g. `P03-W017`; used only for provenance, not for decoding |
| strand id | the archive tag, kind, stripe and shard | **written into the oligo itself** (§4) and repeated in the well label for the operator |
| sequence | the `strand_nt` nucleotides | the only thing the synthesis vendor needs |
| length | `strand_nt` | implied by the sequence |

The oligo plate therefore has **no separate index**: the index *is* the
sequence. A plate is a plain list of sequences; the operator's spreadsheet
maps wells to strand ids for handling and provenance only, and losing it does
not lose data.

### 5.2 Plate geometry — ASSUMPTION

No vendor has been selected, so the plate geometry is a placeholder chosen
only so that the arithmetic in the rest of this section is concrete.

* **ASSUMPTION:** one plate holds at most **384** distinct oligos (a 96-well
  plate at 384 oligos per well is *not* assumed — this is a placeholder
  capacity figure, to be replaced by the selected vendor's specification).
* **ASSUMPTION:** a plate's oligos are **all distinct sequences**. The
  encoder guarantees this is not required for correctness (the address is
  unique per strand), but a pool of duplicate sequences is indistinguishable
  after synthesis and is therefore avoided by construction: the pool index
  `(stripe, shard)` is unique inside a pool.
* **DERIVED:** with the default production profile (`payload_bytes = 40`,
  `inner_parity_bytes = 8`, mapping `2bit`), `frame_bytes = 13 + 40 + 8 = 61`
  and `strand_nt = 61 × 4 = 244` nucleotides per strand.

### 5.3 Strand length across the profiles

`frame_bytes = 13 + payload_bytes + inner_parity_bytes`, and
`strand_nt = frame_bytes × nt_per_byte`. The table below is **DERIVED from the
manifest schema bounds** (`payload_bytes` 1..242, `inner_parity_bytes` 0..64,
even; `frame_bytes` 14..255; `strand_nt` 1..2040), not from a measurement.

| mapping | nt per byte | shortest strand (P=1, r=0 → frame 14) | longest strand (P=242, r=0 → frame 255) | default profile (P=40, r=8 → frame 61) |
|---|---|---|---|---|
| `2bit` | 4 | 56 nt | 1020 nt | 244 nt |
| `rotation3` | 6 | 84 nt | 1530 nt | 366 nt |
| `codebook8` | 8 | 112 nt | 2040 nt | 488 nt |

**THEORETICAL constraint, not a measurement:** the numbers above are the
*format's* bounds. Whether a given length is synthesable, and at what yield
or cost, is a property of a synthesis vendor that the project has not
engaged. No length in this document has been ordered.

### 5.4 Pool-level capacity arithmetic — DERIVED, THEORETICAL

For an archive of `S` original bytes stored with the default profile
(`K = 64` data shards, `M = 16` parity shards, `P = 40` payload bytes,
`r = 8` inner parity bytes, mapping `2bit`):

```
stripe_bytes            = K × P                    = 64 × 40        = 2560 bytes
nucleotides per strand  = (13 + P + r) × 4         = 244 nt
outer expansion         = (K + M) / K              = 80/64          = 1.25
```

so a stripe of 64 data shards occupies 80 strands = 80 × 244 = 19 520
nucleotides, and carries 2 560 stored bytes.

These are **algebraic consequences of the manifest parameters**, not
measurements of a pool. A real pool's nucleotide count would additionally
include the metadata strands (kind 1), the vendor's own per-order
overheads, and any duplicates the operator chooses to order.

---

## 6. Barcode scheme

### 6.1 Two different things are called a barcode

The word is used for two distinct mechanisms in DNA storage, and conflating
them is a common source of bugs. This document separates them:

**(a) The in-band strand address (§4).** Part of the payload. 3-byte archive
tag + 1-nibble kind + 3-byte stripe + 1-byte shard = 7.5 bytes of address
inside every strand. It is written by the VNX-DNA encoder, is covered by the
CRC-32 and the inner RS parity, and is the *only* thing the VNX-DNA decoder
uses to place a strand.

**(b) The platform barcode.** A short index sequence a sequencing platform
attaches to each read to say where the molecule came from (flow cell, lane,
well, or molecule). The platform owns it. VNX-DNA does not write it, does not
parse it and does not depend on it. A reader that is given FASTQ with
platform barcodes strips them before handing the sequence to the VNX-DNA
decoder; a reader given bare FASTA has nothing to strip.

### 6.2 The rule

> **A VNX-DNA decode MUST NOT depend on a platform barcode.** The decoder
> consumes sequence only. Barcodes, if present, are an index for the
> operator's convenience and for the sequencer's demultiplexing; the archive
> is recoverable from its sequences alone.

This is not a stylistic preference. It is what makes the archive readable by
a future party using a different platform, a different instrument generation
or a different laboratory — which is the whole point of the format freeze
(LR-1) and of the reference decoder (LR-3).

**THEORETICAL (unverified in a real sequencer):** demultiplexing on a
platform barcode is the platform's own mechanism and is outside this
project's control; this document asserts only that the *VNX-DNA* layer does
not need it.

### 6.3 Collision handling

Two strands of the same pool never carry the same address, so two different
strands of one pool cannot be confused by the decoder. Two strands of
*different* pools can share a sequence; the 3-byte archive tag separates
them, and a pool with more than one archive tag present in one read file is
rejected with a typed error rather than guessed.

---

## 7. The synthesis / sequencing round trip

### 7.1 The intended loop

```
        ┌──────────────────────────────────────────────────────────────────┐
        │                                                                  │
   (1)  │  VNX-DNA encoder: container → frames → mapping → screening      │
        │  (software, committed)                                         │
        ▼                                                                  │
   (2)  │  pool index: (tag, kind, stripe, shard) per oligo                │
        │  (software, committed)                                          │
        ▼                                                                  │
   (3)  │  ORDER: oligo plates, one row per strand                        │
        │  (PHYSICAL — NOT DONE, NOT ORDERED)                             │
        ▼                                                                  │
   (4)  │  SYNTHESIS: vendor returns oligos                               │
        │  (PHYSICAL — NOT DONE)                                          │
        ▼                                                                  │
   (5)  │  POOLING / library prep: oligos → a sequencer library           │
        │  (PHYSICAL — NOT DONE)                                          │
        ▼                                                                  │
   (6)  │  SEQUENCING: instrument emits reads (FASTA/FASTQ)               │
        │  (PHYSICAL — NOT DONE, NOT RUN)                                 │
        ▼                                                                  │
   (7)  │  VNX-DNA decoder: reads → validated shards → outer decode →      │
        │  container → verified original bytes                            │
        │  (software, committed)                                          │
        ▼                                                                  │
   (8)  │  VERIFY: recompute SHA-256 of the recovered bytes and compare   │
        │  with the manifest's recorded digest; refuse on mismatch         │
        │  (software, committed)                                          │
        ▼                                                                  │
        └──────────────────────────────────────────────────────────────────┘
```

Steps (1), (2), (7) and (8) are software and are pinned by the committed test
suite. Steps (3) to (6) are physical and **have not been performed by this
project**. The arrow from (2) to (3) and from (6) to (7) is where the
physical world enters, and it is the part of this document that is entirely
hypothetical.

### 7.2 What a sequencing platform must deliver for step (7) to work

These are the requirements the software places on the physical layer. They
are stated as requirements on an *interface*, not as observations of an
instrument.

| # | requirement | why |
|---|---|---|
| R1 | every strand of the pool is read **at least once**, at least once per surviving molecule | the outer code needs `K` of `K+M` shards per stripe; a strand never read is a lost shard |
| R2 | a read's **length** matches `strand_nt`, or the read is discarded | a length mismatch means an insertion or deletion; the inner code cannot shift symbols. Reads of the wrong length are dropped, not repaired, unless the experimental indel-repair path is enabled |
| R3 | a read's **alphabet** is A/C/G/T (and optionally N) | any other symbol makes the read invalid; the decoder discards it rather than guessing |
| R4 | reads may be **reverse-complemented** | the decoder tries the forward orientation and then the reverse complement, and the frame's own version nibble decides which one is right |
| R5 | reads may arrive in **any order** and may be **duplicated** | addressing is in-band; identical copies of one address are merged |
| R6 | the platform's own barcodes, if any, are **strippable** | §6.2: the decoder consumes sequence only |
| R7 | reads are **not** required to be quality-tagged | the format uses a CRC-32 and an inner RS code, not per-base quality scores; a FASTQ quality string is discarded before decoding |

R1 is the only requirement that is a *coverage* statement, and it is the one
the physical layer is least able to guarantee: a strand that is never read is
indistinguishable from a strand that was never synthesised.

### 7.3 The error budget the inner code assumes

The inner Reed–Solomon code corrects `e` byte errors and `f` flagged
erasures whenever `2e + f ≤ r`, where `r` is `inner_parity_bytes`.

* `2bit` mapping: one substitution = one damaged byte, so `e` = number of
  substituted nucleotides in the strand.
* `rotation3` / `codebook8`: one substitution can damage two bytes, so the
  effective budget is halved.

**ASSUMPTION (physical):** the substitution rate a platform will deliver is
not known to this project. The code is sized by the manifest's
`inner_parity_bytes`, and the *choice* of that number is a design decision
made against an assumed channel, not against a measured one. The
deterministic software channel simulator
(`src/vnxdna/channel.py`, evidence class SIMULATED) is the only channel this
project has any evidence about.

### 7.4 What is checked before any bytes are returned

The decoder's contract (the "never returns wrong data" rule) is enforced by
a chain of independent checks, each recomputed from bytes in hand:

1. per-strand CRC-32 over the frame body;
2. inner RS syndromes of the corrected frame must be zero;
3. outer Cauchy RS: any `M` lost shards of a stripe are reconstructed;
4. per-chunk SHA-256 of the stored bytes against the manifest record;
5. AES-256-GCM tag per chunk (encrypted archives only);
6. per-chunk plaintext SHA-256 against the manifest's content description;
7. whole-object SHA-256 against the manifest's recorded digest.

Any failure is a typed refusal. No step returns "best effort" bytes.

---

## 8. Interface to the H3 FPGA accelerator

The H3 accelerator (see `hardware/h3-fpga/rs_decode.v`) implements step 2 of
the inner code: the GF(256) Reed–Solomon errors-and-erasures decode of one
strand's frame. The interface is:

| signal | width | direction | meaning |
|---|---|---|---|
| `in_valid` / `in_ready` | 1 | in | input stream handshake |
| `in_data` | 8 | in | one byte of the codeword, most significant coefficient first |
| `in_erase` | 1 | in | this byte is a known erasure (an unreadable nucleotide) |
| `in_last` | 1 | in | last byte of the codeword |
| `in_nsym` | 8 | in | parity bytes `r` (0..64) |
| `in_n` | 8 | in | codeword length in bytes (1..255) |
| `out_valid` / `out_ready` | 1 | out | output stream handshake |
| `out_data` | 8 | out | one byte of the corrected codeword |
| `out_ok` | 1 | out | the decode was accepted |
| `out_errata` | 8 | out | number of located errata (errors + erasures) |
| `busy` | 1 | out | the core is working |

The accelerator is **bit-exact against the software reference**: the same
`GF(256)` field (primitive polynomial `0x11D`, generator 2, first consecutive
root `α⁰`), the same conventions and the same acceptance tests, so a decoded
codeword is byte-for-byte identical to `vnxdna.codec.rs_batch`. The
acceptance test is `hardware/h3-fpga/differential_check.py`, which generates
random codewords with the project's own encoder, corrupts them, decodes them
with the project's own decoder, runs the Verilog testbench and compares byte
for byte.

**Not covered by the accelerator (software only):** the frame CRC-32, the
outer Cauchy code, the DNA mapping, the consensus over duplicate copies and
the container layer.

**Not verified (explicit):** the RTL has been elaborated and simulated with
Icarus Verilog and linted with Verilator. It has **not** been synthesised, no
place-and-route has been run, no timing closure exists, and no FPGA has been
built, programmed or measured. Every cycle-count figure that appears anywhere
in this project is a THEORETICAL count derived from the RTL's structure, not a
measurement.

---

## 9. Open items (this description is not closed)

| id | item | why it is open |
|---|---|---|
| H5-1 | synthesis vendor and its oligo length limits | not selected; §5.3 gives the format's own bounds only |
| H5-2 | plate capacity and well geometry | ASSUMPTION placeholder in §5.2 |
| H5-3 | the physical random-access (CC-5) primer/probe scheme | the address space exists (§4); the selection mechanism does not |
| H5-4 | the platform barcode convention | deliberately out of scope (§6.2): the decoder does not need it |
| H5-5 | the electrode / microfluidic write path and the nanopore read path | separate research partnership; not described here |
| H5-6 | a Sector Zero / Sector One (DDSA) bootstrap layer | LR-2; not part of this description |

---

## 10. Sources

* `src/vnxdna/dna/strand.py` — frame layout, keystream, mapping, screening
* `src/vnxdna/dna/mapping.py` — the three mappings and their properties
* `src/vnxdna/dna/constraints.py` — the constraint specification and its
  screening rule
* `src/vnxdna/storage/encoder.py` — striping, outer code, shortening, framing,
  metadata strands
* `src/vnxdna/storage/decoder.py` — scanning, consensus, outer recovery
* `src/vnxdna/container/manifest.py` — the manifest schema and its bounds
* `src/vnxdna/channel.py` — the deterministic software channel (SIMULATED)
* `src/vnxdna/codec/gf256.py`, `src/vnxdna/codec/rs_batch.py`,
  `src/vnxdna/codec/cauchy.py` — the field, the inner code and the outer code
* `hardware/h3-fpga/rs_decode.v` — the RTL implementation of the inner code
* `docs/STORAGE_FORMAT.md`, `docs/LAB_INTERFACE.md`, `docs/ECC.md`
* `docs/V15_FINALIZATION_PLAN.md` §5D (the H-series) and §1 (LR-1..LR-6)
