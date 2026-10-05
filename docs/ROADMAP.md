# Roadmap

> **Forward plan:** [VNX_GLOBAL_ROADMAP.md](VNX_GLOBAL_ROADMAP.md) (V6 → V12 → V50, proposed 2026-10-05 from the research gate; decisions in [VNX_STRATEGIC_DECISION_REPORT.md](VNX_STRATEGIC_DECISION_REPORT.md)). This file keeps the release history and the original V2-era backlog below; where they differ, the global roadmap wins. Released since this file was last updated: V3.0.0, V4.0.0, V5.0.0 (see CHANGELOG.md).

## History

| version | content | status |
|---|---|---|
| V0.1 | R&D prototype: two parallel pipelines and a non-MDS shard code; `simulate` was broken; unauthenticated manifest | baseline, tagged `v0.1-baseline`, archived under `research/legacy/` |
| V0.2 → V1.0 | Cauchy MDS outer code; authenticated format 4; in-band strand frame; `.vxdna` container; channel simulator; explicit legacy decoder; CLI/API; property, fuzz, adversarial and clean-room tests; benchmarks and experiments | tagged `v1.0.0` |
| V3.0 | V2 audit (48 code/test defects and 13 documentation errors fixed, incl. two AES-GCM nonce-reuse paths on resume), vectorised bounded-distance RS decoder behind an ECC engine interface, single-read burst resynchronisation, burst channel + `simulate-errors` sweeps, multi-archive pools, bounded input parsing, V2↔V3 compatibility fixtures; format 5 kept | see [V3_AUDIT.md](V3_AUDIT.md) |
| V2.0 | streaming format 5 (footer index, chunked AEAD, resumable store), frame format 5 (32-bit addresses), packed VXS strands, chunk-parallel encoder, two-pass disk-backed decoder, sequencing simulator with coverage and qualities, clustering, consensus, experiment engine, 1–10 GB scalability and corruption acceptance, V1 read compatibility and migration | see [PROJECT_STATE.md](PROJECT_STATE.md) |

The intermediate milestones of the original plan (V0.3 channel, V0.4 sync, V0.5 random access, V0.6 security/CLI,
V0.7 performance, V0.8 fuzzing, V0.9 freeze) were developed in one consolidation. They were not released separately.
The capabilities each milestone named are present, and their evidence is listed in PROJECT_STATE.md.

## Next (compatible with format 5)

- **Faster clustering and consensus**: still Python-bound per cluster (measured in [BENCHMARKS.md](BENCHMARKS.md)).
  A batched or compiled path would let the full sequencing chain run at multi-gigabyte scale.
- **Vectorised outer decoding across erasure patterns** and a parallel pass 2 in the decoder.
- **Resume for `encode` and `decode`** (only `store` checkpoints today; the others restart and are pure functions).
- **Optional padding** to hide size and compressibility in encrypted archives.
- **Burst repair for two bursts per read** (F² hypotheses) and combined indel + burst hypotheses.

## Research (new format version or new required features)

- **Random per-run nonce component** in the chunk AEAD nonce (uses the chunk index's reserved bytes under a new
  required feature). This removes the last resume limitation in [SECURITY.md](SECURITY.md#known-limitations):
  nonce freshness after a full snapshot rollback.
- **Synchronization markers / watermark or VT codes** inside strands, for indel channels at coverage 1 beyond one
  indel or one burst per read.
- **Molecular addressing**: PCR primer design per chunk or partition, and a simulated selection step.
- **Secondary structure** screening, melting temperature, synthesis-cost models.
- **Channel models fitted to published data** (position-dependent errors, truncated synthesis, GC-dependent dropout).
- **Fountain / LT outer codes** and cross-chunk interleaving for correlated dropout. The ECC engine
  (`vnxdna.ecc.engine`) is the integration point; nothing is registered until it is implemented and tested.
- **Wet-lab validation.** Only physical experiments can support any claim about real DNA. None have been done.

GPU acceleration is not planned until profiling shows a bottleneck that vectorised CPU code cannot address. The CPU
path remains the reference implementation.
