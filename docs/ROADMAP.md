# Roadmap

## History

| version | content | status |
|---|---|---|
| V0.1 | R&D prototype: two parallel pipelines and a non-MDS shard code; `simulate` was broken; unauthenticated manifest | baseline, tagged `v0.1-baseline`, archived under `research/legacy/` |
| V0.2 → V1.0 | Cauchy MDS outer code; authenticated format 4; in-band strand frame; `.vxdna` container; channel simulator; explicit legacy decoder; CLI/API; property, fuzz, adversarial and clean-room tests; benchmarks and experiments | see [PROJECT_STATE.md](PROJECT_STATE.md) |

The intermediate milestones of the original plan (V0.3 channel, V0.4 sync, V0.5 random access, V0.6 security/CLI,
V0.7 performance, V0.8 fuzzing, V0.9 freeze) were developed in one consolidation. They were not released separately.
The capabilities each milestone named are present, and their evidence is listed in PROJECT_STATE.md.

## V1.x (compatible with format 4)

- Streaming encode/decode, so memory is no longer about 20× the input for large files (see BENCHMARKS.md). The format
  already permits it.
- Faster inner decoding: a vectorized Berlekamp–Massey instead of the per-read `reedsolo` call. This is the dominant
  cost at high error rates.
- Optional input padding to hide size and compressibility in encrypted archives.
- A `--manifest` option to decode reads with a known container when every metadata stripe is lost.

## V2 research (a new format version, or new required features)

- **Synchronization:** marker- or watermark-based codes, and edit-distance decoding of consensus reads (multiple
  alignment across coverage), to replace the experimental RS-assisted realignment, which handles single indels
  reliably.
- **Consensus:** combine noisy copies before inner decoding instead of voting on validated payloads.
- **Secondary structure:** hairpin/self-complementarity screening, primer/adapter design and melting-temperature
  constraints.
- **Channel models** fitted to published synthesis/sequencing error profiles, such as position-dependent errors and
  truncated synthesis.
- **Fountain / LT outer codes** for very large archives, and interleaving across chunks for correlated dropout.
- **Wet-lab validation.** Only physical experiments can support any claim about real DNA. None have been done.

GPU acceleration is not planned until profiling shows a bottleneck that vectorized CPU code cannot address. CPU
remains the reference implementation.
