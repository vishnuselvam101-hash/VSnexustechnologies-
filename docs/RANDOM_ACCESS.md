# Random access

**What it means here.** The plaintext is split into chunks of `chunk_size` bytes (default 256 KiB). Each chunk is
compressed and encrypted independently, and it occupies its own whole stripes of strands, whose stripe range is
recorded in the manifest. `vnx-dna extract SRC --chunk N` or `--start S --end E` recovers only the chunks it needs.
It outer-decodes only their stripes, and it verifies only their AES-GCM tags and SHA-256 values. It does not need
strands belonging to other chunks. In a test, every strand of every other chunk is deleted and the selected chunk is
still recovered (`tests/integration/test_matrix_and_random_access.py`).

The `extract` report includes `stripes_decoded` of `stripes_total`, the chunk's plaintext SHA-256 (from the
authenticated manifest), and the SHA-256 of what was written.

**What it does not mean.**
- It is not *molecular* random access. A real system would select strands physically, for example by PCR with
  chunk-specific primers. VNX-DNA assigns no primers. It still parses and validates every read it is given, then uses
  only the relevant ones, so the saving is in outer decoding, decryption and decompression, not in read parsing.
- The manifest is always needed. It comes from the `.vxdna` file or is reconstructed from the metadata strands, which
  must be present in the read set.
- Granularity is one chunk. A byte range spanning several chunks decodes all of them.

Benchmark rows `random_access_one_chunk_from_reads` against `recovery_outer_decrypt_decompress_verify` in
[BENCHMARKS.md](BENCHMARKS.md) show the difference between selective and full decoding on the same scanned reads.
