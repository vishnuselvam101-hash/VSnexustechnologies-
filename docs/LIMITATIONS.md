# Limitations

## Scientific scope

* **Software and simulation only.** No DNA has been synthesised, stored, amplified or sequenced for this project.
  Strands are software-generated sequences. The channel is a seeded simulation. "Recovery" means recovery from
  simulated reads. Nothing in this repository is evidence of physical DNA-storage performance, laboratory
  feasibility, sequencing accuracy, long-term preservation or commercial archival capability.
* **The channel is not fitted to a platform.** Error rates are stress parameters. Real synthesis and sequencing have
  position-, context- and GC-dependent errors, homopolymer slippage, strand breakage, chimeras, primer and adapter
  sequences and PCR bias, none of which are modelled ([ERROR_MODEL.md](ERROR_MODEL.md#channel)).
* **No molecular random access.** VNX-DNA assigns no PCR primers. Random access from DNA reads selected records of a
  strand *file* through a DNA index, which is the software analogue of primer selection, not a demonstration of it.
* **Sequence constraints are screening rules** (GC content, homopolymers, tandem repeats, motifs). There is no
  secondary-structure, melting-temperature or synthesis-cost model.
* **Density figures are information-theoretic counts** of nucleotides per input byte for software strands, including
  all ECC and metadata. They are not physical densities (bytes per gram) and say nothing about synthesis yield.

## Correction guarantees

* Guarantees hold **per ECC group** (any M of K + M strands) and **per strand** (`2e + f ≤ r`). An archive is
  guaranteed to recover only if every group is within its guarantee. Under random damage, success is a probability,
  measured in [ERROR_MODEL.md](ERROR_MODEL.md) and [EXPERIMENTS.md](EXPERIMENTS.md), not proven.
* **Indels at coverage 1** are repaired only for one indel (or up to three of the same net direction within the search
  budget) or one contiguous burst per read, and only when enabled. Reads whose indels cancel in length (+1 −1) are not
  repaired. Several bursts in one read are not repaired.
* Indels present in **every** molecule of a strand (a synthesis error in the species) cannot be corrected by
  consensus; the strand becomes an erasure.
* There is **no cross-group code**: correlated loss of more than M strands in one group loses that chunk, even if
  other groups have spare redundancy. Fountain codes and cross-chunk interleaving are research items
  ([ROADMAP.md](ROADMAP.md)).

## Performance and scale

* Throughput is CPU-bound Python/NumPy. The inner RS decoder is vectorised since V3, but clustering and consensus remain
  the slowest stages (per-cluster Python work; [BENCHMARKS.md](BENCHMARKS.md)). The full sequencing → clustering →
  consensus chain is measured on representative inputs (up to 10 MB in the stage benchmark), not at 10 GB.
* Nothing here is RAM- or DRAM-class storage: decoding is a batch process measured in MB/s.
* Peak memory depends on the chunk size and the worker count, not on the file size, for store, restore, encode and
  decode ([LARGE_FILES.md](LARGE_FILES.md)). The sequencing simulator's batches shrink with coverage since V3, which bounds its memory,
  and one very large cluster (extreme coverage of one strand) is held in memory by consensus.
* Single-read indel repair is expensive for two or three indels (tens to thousands of milliseconds per read;
  [SYNCHRONIZATION.md](SYNCHRONIZATION.md#v3-measurements)), so it is opt-in.

## Security

* Unencrypted archives detect accidents, not deliberate modification.
* Encrypted archives leak the approximate size (chunk count), per-chunk compressibility and every ECC parameter
  ([SECURITY.md](SECURITY.md#what-is-encrypted-what-is-visible)). There is no padding.
* There is no password-based key derivation and no key escrow. A lost key is a lost archive. Python cannot reliably
  erase keys from memory.
* A malicious strand pool can make decoding fail (denial of service). It cannot make decoding output wrong bytes
  without breaking SHA-256, or AES-GCM and HMAC-SHA256 when encrypted.

## Compatibility

* VNX-DNA 2.0 cannot read encrypted archives whose store was resumed by V3. They declare `final-seal-epoch-v3`, and
  V2 refuses them with exit 6 ([STORAGE_FORMAT.md](STORAGE_FORMAT.md#3-optional-feature-final-seal-epoch-v3)).
* VNX-DNA 2.0 store checkpoints cannot be resumed by V3 (start over without `--resume`).
* Shuffled read output of `sequence`/`simulate` can differ from V2 for the same seed on large pools or VXS input, and
  above the coverage-batch threshold ([CHANNEL_MODEL.md](CHANNEL_MODEL.md)). Unshuffled output and ordinary channels
  are byte-identical.

## Operations

* Do not run two `store` commands for the **same output** at the same time. A resumable store keeps its work files
  under fixed names next to the output (`<output>.partial`, `.partial.ckpt`, `.partial.idx`); a concurrent second
  store can make the first fail (exit 70). Stores to different outputs are independent.
* Outputs are created with mode 0600; restored files do not keep the original file's permissions or timestamps.
* The `benchmark` commands' `--output` JSON files are replaced without `--force` (they are measurement results).

## Not implemented

Molecular primers and PCR selection, in-strand synchronisation markers or watermark/VT codes, fountain outer codes,
cross-chunk interleaving, secondary-structure screening, password KDF, size padding, GPU acceleration, and resume for
`encode`/`decode` (they restart; both are pure functions of their inputs).
