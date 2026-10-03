# Archive engine (VNX4 container)

Status: **IMPLEMENTED**. Code: `src/vnxdna/v4/archive.py`, `container.py`, `merkle.py`, `crypto.py`.
Format: [VNX4_FORMAT.md](VNX4_FORMAT.md) §1–9. Tests: `tests/v4/test_container_v4.py`, `tests/v4/test_fuzz_v4.py`.

## Commands

```bash
vnx archive ./dataset more.txt archive.vnx [--chunk-size 1048576] [--compression zstd|none] [--level 3]
            [--no-dedup] [--preserve-metadata] [--workers N] [--key-file k | --passphrase-env VAR]
vnx inspect archive.vnx          # manifest, sizes, chunk statistics (works without a key)
vnx list archive.vnx             # entries (needs the key when encrypted: the file table is sealed)
vnx verify archive.vnx           # trailer SHA-256, manifest, tables, Merkle root, every chunk, every file
vnx verify archive.vnx --chunk 7 # one chunk via its Merkle inclusion proof (⌈log₂ n⌉ hashes)
vnx locate archive.vnx ds/f.bin [--dna-profile v4-balanced]   # chunks, byte ranges, DNA groups / strand records
vnx extract archive.vnx out/ [--file ds/f.bin ...] [--force] [--apply-metadata]
```

## Design

* **Inputs.** A file is stored under its base name and a directory under its own name (tar-like). The walk is
  deterministic (UTF-8 byte order of archive paths). Symbolic links and special files are **skipped, not followed**,
  and listed as warnings. Empty directories are stored. Non-UTF-8 names and unsafe paths are refused.
* **Chunking.** Fixed-size chunks (4 KiB to 64 MiB, default 1 MiB). A file's last chunk is shorter. Every chunk is
  independently identifiable (chunk ID), verifiable (stored SHA-256, chunk ID, Merkle proof) and retrievable (its
  byte range from the chunk table).
* **Deduplication.** Chunks with the same ID are stored once, and files reference chunk indices. This is the
  foundation for cross-archive dedup and versioning (an archive version could reference an earlier archive's chunks
  by ID; **PLANNED**).
* **Streaming / bounded memory.** One pass over the input. At most `2 × workers` chunks are in flight. The writer
  keeps 84 bytes per unique chunk plus the file records. For 1 MiB chunks that is about 84 MB per TB of unique
  content. Extraction reads one chunk at a time with `pread`.
* **Parallelism.** Worker threads compute chunk IDs and compression. The parent assigns indices, encrypts and writes
  strictly in input order, so the container is byte-identical for every worker count (tested for 1, 2 and 4).
* **Verify before publish.** Extraction writes each file to a temporary name and renames it into place only after
  every chunk (stored SHA-256, AES-GCM, bounded decompression, chunk ID) and the file SHA-256 verify. Existing files
  are not overwritten without `--force`. Extraction refuses to write through symlinks or outside the output
  directory.
* **Random access.** `locate` and `extract --file` touch only the chunks of the requested file. A lookup is a binary
  search over the sorted file table.

## Measured

See [PERFORMANCE.md](PERFORMANCE.md): archive throughput, dedup effect, lookup time, index size (EXP-0012, EXP-0015).
