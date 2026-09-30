# Roadmap

## History

| version | content | status |
|---|---|---|
| V0.1 | R&D prototype: two parallel pipelines and a non-MDS shard code; `simulate` was broken; unauthenticated manifest | baseline, tagged `v0.1-baseline`, archived under `research/legacy/` |
| V0.2 → V1.0 | Cauchy MDS outer code; authenticated format 4; in-band strand frame; `.vxdna` container; channel simulator; explicit legacy decoder; CLI/API; property, fuzz, adversarial and clean-room tests; benchmarks and experiments | tagged `v1.0.0` |
| V2.0 | streaming format 5 (footer index, chunked AEAD, resumable store), frame format 5 (32-bit addresses), packed VXS strands, chunk-parallel encoder, two-pass disk-backed decoder, sequencing simulator with coverage and qualities, clustering, consensus, experiment engine, 1–10 GB scalability and corruption acceptance, V1 read compatibility and migration | see [PROJECT_STATE.md](PROJECT_STATE.md) |

The intermediate milestones of the original plan (V0.3 channel, V0.4 sync, V0.5 random access, V0.6 security/CLI,
V0.7 performance, V0.8 fuzzing, V0.9 freeze) were developed in one consolidation. They were not released separately.
The capabilities each milestone named are present, and their evidence is listed in PROJECT_STATE.md.

## Next (compatible with format 5)

- **Vectorised inner RS decoding** (batched syndromes and Berlekamp–Massey). The per-read `reedsolo` decoder is
  the dominant cost for heavily damaged read sets.
- **Faster clustering and consensus**: the address scan and the per-cluster JSON are Python-bound (measured in
  BENCHMARKS.md). A compiled or batched path would let the full sequencing chain run at multi-gigabyte scale.
- **Resume for `encode` and `decode`** (only `store` checkpoints today; the others restart and are pure functions).
- **Optional padding** to hide size and compressibility in encrypted archives.
- **Parallel pass 2** in the decoder (chunk assembly is single-process; it is not the bottleneck today).

## Research (new format version or new required features)

- **Synchronization markers / watermark or VT codes** inside strands, for coverage-1 indel channels.
- **Secondary structure** screening, primer/adapter design, melting temperature.
- **Channel models fitted to published data** (position-dependent errors, truncated synthesis, GC-dependent dropout).
- **Fountain / LT outer codes** and cross-chunk interleaving for correlated dropout.
- **Wet-lab validation.** Only physical experiments can support any claim about real DNA. None have been done.

GPU acceleration is not planned until profiling shows a bottleneck vectorised CPU code cannot address. The CPU path
remains the reference implementation.
