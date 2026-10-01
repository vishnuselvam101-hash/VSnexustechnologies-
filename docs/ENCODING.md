# Encoding: from bytes to DNA and back (V3)

The V3 encoder writes exactly what V2 wrote; the data strands are byte-identical (checked against the 2.0.0 fixtures
in `tests/v3/test_compat_v3.py`). This page describes the whole encoding in one place. The byte layouts are in
[V2_FORMAT.md](V2_FORMAT.md) §3, and the V1 mapping tables in [DNA_CODEC.md](DNA_CODEC.md), which V3 uses unchanged.

## 1. Binary → stored chunk

```
input file ─▶ fixed plaintext chunks (profile: 256 KiB … 4 MiB) ─▶ zstd (kept only if smaller) ─▶ AES-256-GCM (optional)
```

Each stored chunk is SHA-256-hashed. The hash goes into the binary chunk index, and the whole index into the
manifest ([STORAGE_FORMAT.md](STORAGE_FORMAT.md)).

## 2. Stored chunk → ECC groups → frames

A stored chunk of `s` bytes is cut into shards of `P` payload bytes. Groups of `K` data shards get `M` Cauchy RS
parity shards ([ECC.md](ECC.md)). Every shard becomes one **frame** (big-endian):

```
offset  size  field
0       1     scrambler variant v (not scrambled)
1       1     version/kind: high nibble 5 (frame format), low nibble 0 data / 1 metadata
2       4     archive tag (first 4 bytes of the archive ID)
6       4     stripe index (ECC group)
10      1     shard index (0 … K+M−1; data first)
11      P     payload (one outer-code shard)
11+P    4     CRC-32 over bytes 1 … 10+P (before scrambling)
15+P    r     inner RS parity over bytes 0 … 14+P
```

Bytes 1 … 14+P are XORed with `SHAKE128("VNX-DNA/5 scrambler" ‖ v)`. The strand's address lives inside the frame,
covered by the CRC and the inner code, so decoding never depends on FASTA/FASTQ headers or read order.

## 3. Frame → nucleotides (binary → 2-bit → DNA)

| mapping | nt per byte | raw bits per nt | property by construction | profile use |
|---|---|---|---|---|
| `2bit` | 4 | 2.00 | none (the screening enforces the constraints) | all profiles |
| `rotation3` | 6 | 1.33 | no two equal adjacent bases | optional |
| `codebook8` | 8 | 1.00 | 50 % GC per word, runs ≤ 3 | optional |

`2bit` is A=00, C=01, G=10, T=11, most significant bits first, so the four 2-bit symbols of a byte are its
nucleotides (`0x1B` → `ACGT`). One nucleotide substitution corrupts exactly one frame byte, which the inner RS code
sees as one symbol error.

## 4. Constraint screening

The encoder tries scrambler variants v = 0, 1, … for each frame, until the mapped strand satisfies every rule
recorded in the manifest:

* GC content (whole strand, optionally per window; balanced profile 40–60 %);
* the longest homopolymer (balanced: 4);
* the longest period-2/3 tandem repeat (optional);
* forbidden motifs on both strands (optional).

If no variant among 256 works, encoding fails loudly (`ConstraintError`). A strand that violates the recorded rules
is never emitted. For `2bit`, RS parity and the mapping are XOR-linear, so each extra variant costs one XOR with a
constant mask. The result is identical to building each variant directly (tested).

## 5. Decoding: DNA → 2-bit → binary

For every read, in this order:

1. read validation: symbols outside ACGTN (IUPAC codes, `.`, `-`) become erasures (V3; V2 dropped the read);
   over-long lines are cut while reading (V3);
2. demap to frame bytes (`N` and erasure symbols → byte erasures);
3. descramble with the variant byte, check the frame version and the CRC-32;
4. if the CRC fails: reverse complement, then the vectorised inner RS decoder (V3) on all failing reads of the batch,
   in both orientations, accepted only if the CRC then passes;
5. if the read length is off: single-read indel realignment or burst resynchronisation (opt-in;
   [SYNCHRONIZATION.md](SYNCHRONIZATION.md));
6. validated shards are spilled to disk, sorted by ECC group, merged across copies, and outer-decoded. Each rebuilt
   stored chunk must match its SHA-256.

Encoding is deterministic: the same container gives byte-identical strand files (tested in V2 and V3).

## Density (measured, software)

Nucleotides per input byte, including every ECC and metadata strand, for the balanced profile on mixed data, are in
[BENCHMARKS.md](BENCHMARKS.md#v3-density). These are counts of software-generated nucleotides. They are not a
physical storage density.
