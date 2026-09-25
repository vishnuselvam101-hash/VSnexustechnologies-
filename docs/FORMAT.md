# VNX-DNA formats

## VNX-DNA-1 (backward compatible)
A VNX-DNA-1 directory contains canonical `manifest.json` and ASCII `strands.fasta`. A strand header is `VNX1|dataset_uuid|index|total|payload_bytes|payload_sha256`; payload is a four-bases-per-byte two-bit A/C/G/T sequence. Version 1 requires `ecc=none` and fails safely on missing/corrupt strands.

## VNX-DNA-2 (ECC extension)
VNX-DNA-2 has `format_version=2`, `ecc=reed_solomon`, and a manifest `ecc` object with algorithm, K data shards, M parity shards, total shards, stripe count, and maximum erasures per stripe. A header is `VNX2|dataset_uuid|stripe_index|shard_index|K|M|payload_bytes|payload_sha256`. Shard indexes `[0,K)` are data; `[K,K+M)` are parity. The manifest remains authoritative; a decoder rejects metadata mismatch rather than reinterpreting VNX1.
