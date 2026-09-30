# DNA codec and sequence constraints

VNX-DNA separates three concerns:

1. **Binary→DNA mapping** (`vnxdna.dna.mapping`): a deterministic, reversible, versioned bijection from bytes to
   A/C/G/T.
2. **Constraint screening** (`vnxdna.dna.constraints` + the scrambler in `vnxdna.dna.strand`): software rules applied
   to every strand, which either pass or make encoding fail loudly.
3. **Physical reality**: synthesis, storage and sequencing chemistry. It is *not modelled* and *not validated*.

## Mappings

| name | nt/byte | raw bits/nt | property guaranteed by construction | decoding of bad symbols |
|---|---|---|---|---|
| `2bit` (default) | 4 | 2.00 | none (screening does the work) | `N` → byte erasure |
| `rotation3` | 6 | 1.33 | no two equal adjacent bases (no homopolymers) | impossible transitions and `N` → erasure |
| `codebook8` | 8 | 1.00 | every word has exactly 4 G/C (50 %) and runs ≤ 3, including across words | unknown words and `N` → erasure |

- **2bit:** A=00, C=01, G=10, T=11, most significant bits first (`0x1B` → `ACGT`).
- **rotation3:** each byte becomes 6 base-3 digits. Digit *d* is written as the *d*-th base, in ACGT order, among the
  three bases that differ from the previous one. The strand starts as if the previous base were A.
- **codebook8:** the first 256 words `A·xxxxxx·T` in `itertools.product` order that have exactly 4 G/C and runs ≤ 3.
  The fixed A…T boundaries stop runs from joining across words.

The tables are part of the format. They never depend on user settings. Each mapping is tested on all 256 byte values.

## Constraint layer

`ConstraintSpec` has these fields (defaults in parentheses):
- `gc_min_percent` / `gc_max_percent` (40 / 60): whole-strand GC bounds;
- `gc_window_nt` (0 = off): the same bounds in every window of that length;
- `max_homopolymer` (4; 0 = off);
- `forbidden_motifs` (none): each motif is checked on both strands when `check_reverse_complement` is true.

The spec is recorded in the manifest. Minimum/maximum strand length is set by the geometry; `store --max-strand-nt`
refuses geometries that are too long.

**Screening.** Each frame is scrambled with one of 256 SHAKE-128 keystreams, selected by the variant byte *v*. The
encoder takes the lowest *v* whose mapped strand satisfies the spec. If no variant works for some strand, encoding
raises `ConstraintError` (exit 7). The encoder **never** emits a strand that violates the requested constraints. The
decoder does not need the spec, because *v* is carried in the frame.

Typical cost with the defaults (2bit, GC 40–60 %, homopolymer ≤ 4, 244 nt): about 1 scrambler variant on average, at
most about 12 per archive (measured in `encode` reports as `screening_mean_variant` / `screening_max_variant`).
Tighter specs (e.g. GC 45–55 % with a window) can become unsatisfiable. That surfaces as an explicit error rather
than a silent violation.

**Not implemented:** secondary-structure / hairpin prediction, primer and adapter design, melting temperature and
synthesis-vendor rule sets. These are listed in [ROADMAP.md](ROADMAP.md).

## Software constraints are not wet-lab validation

Passing these checks means only that a sequence meets common heuristics from the DNA-storage literature. It is **not**
evidence that the sequence can be synthesised, amplified or sequenced, or that it is stable. No VNX-DNA output has
been tested physically. Physical experiments would also need primers/adapters, platform-specific error profiles,
coverage models and handling of synthesis truncations, none of which this repository provides.

## Density

At the default geometry, a strand is 61 bytes: 9 header + 40 payload + 4 CRC + 8 inner parity. That is 244 nt carrying
40 payload bytes, so 1.31 payload bits/nt before outer parity and metadata strands. The outer 64+16 code adds 25 %
parity on top of the stored bytes. `vnx-dna info` reports the end-to-end `bases_per_original_byte` and
`net_bits_per_base` for each archive, with every overhead included. Compression can make the net figure look better
for compressible input. That is compression gain, and it is reported separately as `compression_ratio`.
