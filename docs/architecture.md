# Architecture

VNX-DNA-1 separates stable public objects (`Encoder`, `Decoder`, `ChannelSimulator`) from dataset mechanics. `core.pipeline` owns the encode/decode transaction; `compression`, `crypto`, `encoding`, `validation`, `metadata`, `storage`, and `indexing` provide narrow typed boundaries.

**Encoding:** bytes → configurable compression → optional Fernet authenticated encryption → fixed-size payload chunks → two-bit A/C/G/T baseline encoding → constraint diagnostics/validation → `strands.fasta` plus canonical JSON manifest.

**Decoding:** manifest parse/version validation → FASTA parse → dataset/index/duplicate classification → per-strand constraint, decode, length, and SHA-256 verification → ordered reconstruction → decrypt → decompress → final SHA-256 equality. Missing and corrupt payloads fail explicitly; VNX-DNA-1 does not claim indel correction or strand-erasure recovery.
