# Streaming and bounded memory

No V2 command loads a whole input into memory. Every stage reads bounded blocks, processes them, emits bounded
blocks and continues. This page lists the memory model of each command and the streaming Python API. Measured
values are in [LARGE_FILES.md](LARGE_FILES.md).

## Memory model per command

`W` = workers (default: CPU count − 1, at most 8), `C` = chunk size (1 MiB in the balanced profile), `S` = strand bytes
per chunk (≈ 2·C for the 2bit mapping after outer parity and framing).

| command | what is in memory at once | grows with the file? |
|---|---|---|
| `store` | ≤ 2W chunks in flight (plaintext + compressed + sealed), the zstd context per thread, the index (92 B per chunk) | index only: 0.009 % of the input at C = 1 MiB |
| `restore`, `extract` (container) | ≤ 2W chunks in flight; the index | index only |
| `verify` (container) | as restore, plus streaming SHA-256 of the whole file | index only |
| `encode` | per worker: one chunk, its strands in batches of 16,384 (codes + screening candidates); ≤ 2W serialised chunks queued | no |
| `decode`, `recover` (pass 1) | per worker: one read batch (32,768 reads); ≤ 2W result batches queued; metadata records | metadata records only (≈ coverage × metadata strands) |
| `decode`, `recover` (pass 2) | ordered input: one chunk's records; shuffled input: one bucket (≤ 64 MiB of records) | no |
| `sequence`, `simulate` | one strand batch (8,192 strands) and its reads; bucket write buffers | no |
| `cluster` | one read batch per worker; one bucket (≤ 64 MiB) while emitting clusters; orphan reads (capped, default 200,000) | only the orphan list, capped |
| `consensus` | one block (≤ 16,384 reads or 2,048 clusters) and its alignment matrices | no |

What *does* grow with the file is disk: the output itself, the decoder's spill file (`(10 + P)` bytes per validated
read, twice that while bucket-sorting), and the channel's shuffle buckets. Use `--temp-dir` to put these on a large
volume.

## Why it stays bounded

* **Chunk-at-a-time pipelines with a bounded in-flight window.** `ordered_map` (threads) and the encoder/decoder
  process pools submit at most `2 × workers` items and consume results in order before submitting more, so a fast
  reader can never run ahead of the workers.
* **Footer-indexed container.** Nothing about chunk *n* must be known before chunk *n* is written.
* **Incremental hashing.** Whole-object, body and file SHA-256 are updated block by block.
* **Out-of-core algorithms.** The decoder spills validated shards and bucket-sorts them by ECC group, and the channel
  shuffles through random bucket files. Both are standard external-memory techniques with memory bounded by the
  bucket size.
* **No giant strings.** Strands are produced and written per chunk (VXS records or FASTA text per batch). Nothing
  builds a single DNA string for the archive.

## Streaming Python API

```python
from vnxdna.v2 import api
from vnxdna.v2.archive import open_container, container_stored_chunks, verified_plaintext
from vnxdna.v2.decoder import ReadsArchive
from vnxdna.v2.strandio import iter_batches, StrandWriter, ReadBatch

# whole files, streaming inside
api.store("big.bin", "big.vxdna", workers=8)             # resumable: resume=True
api.encode("big.vxdna", "big.vxs")                       # packed strands + big.vxs.vxidx
api.recover("big.vxs", "big.out")                        # two-pass, disk-backed decoder

# chunk iterator over a container (random access; only the chunks you ask for are read)
cf, loaded = open_container("big.vxdna", key=None)
for index, plaintext in verified_plaintext(loaded, container_stored_chunks(cf, loaded, [10, 11, 12]), workers=4):
    ...                                                  # each chunk authenticated and SHA-256 verified

# strand/read iterator (FASTA, FASTQ, plain, VXS), bounded batches
for batch in iter_batches("reads.fastq", batch_reads=65536):
    batch.codes, batch.lengths, batch.quals              # flat numpy arrays

# recovery iterator from DNA
with ReadsArchive("big.vxs", key=None) as archive:      # pass 1 runs here
    for index, stored in archive.stored_chunks():        # pass 2, chunk by chunk, SHA-256 verified
        ...

# writer (atomic: nothing appears under the final name unless commit() succeeds)
with StrandWriter("out.vxs", "vxs", strand_nt=252) as writer:
    writer.write_batch(ReadBatch.from_matrix(codes))
    writer.commit()
```

## Crash safety

Every output (container, strand file, reads, clusters, consensus, restored file) is written to a same-directory
temporary name, fsynced, and renamed into place. A killed process never leaves a file under the final name, and the
temporary files have no valid trailer (containers, VXS) or end record (cluster files). `store --resume` continues an
interrupted store from its last validated checkpoint ([V2_FORMAT.md §4.4](V2_FORMAT.md#44-store-checkpoint)).
The other commands restart from the beginning. They are pure functions of their inputs, so a rerun is safe.
`pipeline --resume` skips stages whose outputs are complete.
