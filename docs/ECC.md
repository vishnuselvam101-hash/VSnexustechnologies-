# ECC and Recovery (VNX-DNA-2)

VNX-DNA-1 (`format_version=1`, `ecc=none`) remains unchanged and detects corruption/missing strands before failing. VNX-DNA-2 (`format_version=2`, `ecc=reed_solomon`) adds systematic Reed–Solomon erasure coding over GF(256), with an independently implemented Vandermonde generator matrix.

For each stripe, **K** `data_shards` carry transformed bytes and **M** `parity_shards` carry redundant bytes; total shards are `K + M`. All shards are fixed-size in a stripe. A decoder treats a missing FASTA record or a record with invalid DNA, length, or SHA-256 checksum as an erasure. It can reconstruct exactly when at least K distinct shards are available, i.e. up to **M known erasures per stripe**. Duplicates are one observation, not additional recovery capacity, and ordering is irrelevant.

This is shard-level erasure recovery, not nucleotide-level correction: substitutions that make a shard checksum fail may be recovered as one erasure if capacity remains. Insertions/deletions break baseline sequence alignment and are only detected as invalid/corrupt shard erasures; Reed–Solomon does **not** correct indels.
